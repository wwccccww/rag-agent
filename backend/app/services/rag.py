import hashlib
import json
import logging
import uuid
import time
from collections import defaultdict
from typing import Any
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.config import settings
from app.kb import normalize_doc_type, resolve_kb_collection, sanitize_doc_types_list
from app.models import Chunk, Document, Memory
from app.services.ollama import OllamaClient
from app.services.reranker import rerank as reranker_rerank
from app.services.text_extract import chunk_text, chunk_text_hierarchical, extract_text
from app.telemetry import telemetry


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_MIN_CHUNK_CHARS = 30  # 过短的 chunk 不含实质信息，跳过


def _linear_chunks_relaxed(text: str, **chunk_kwargs: Any) -> list[tuple[str, dict]]:
    """
    当层级分块过滤后为空（常见于粘贴文本过短 < _MIN_CHUNK_CHARS）时，
    用线性分块且允许最短 1 字符；若仍为空则将非空全文作为单条 chunk（上限放宽）。

    chunk_kwargs：与 ingest_bytes 中 chunk_text 相同（含 filename、markdown_by_heading 等）。
    """
    pairs = chunk_text(
        text,
        settings.chunk_max_chars,
        settings.chunk_overlap,
        **chunk_kwargs,
    )
    relaxed = [(c, m) for c, m in pairs if len(c.strip()) >= 1]
    if relaxed:
        return relaxed
    t = text.strip()
    if not t:
        return []
    cap = max(settings.chunk_max_chars * 4, 8000)
    return [(t[:cap], {"section_heading": "", "chunk_index": 0})]


def _trgm_word_similarity_expr(query: str):
    """正文 + 可选 section_heading 面包屑，取较大 word_similarity（利于标题里专有词）。"""
    ws_body = func.word_similarity(query, Chunk.content)
    if not settings.rag_trgm_include_section_heading:
        return ws_body
    sec = func.coalesce(Chunk.meta.op("->>")(literal("section_heading")), literal(""))
    return func.greatest(ws_body, func.word_similarity(query, sec))


def _maybe_swap_same_doc_from_prefetch(
    top_ids: list[Any],
    prefetch_ids: list[Any],
    chunk_map: dict[Any, tuple[Any, Any]],
    rrf: dict[Any, float],
    vec_similarity: dict[Any, float],
    trgm_similarity: dict[Any, float],
    max_per_doc: int,
) -> list[Any]:
    """已命中某文档且向量或文本路不差时，从 prefetch 换入同文档兄弟片段，减轻「只命中一条需求、其余被教程占满」。"""
    n_x = int(getattr(settings, "rag_same_doc_prefetch_extra", 0) or 0)
    if n_x <= 0 or not top_ids:
        return top_ids
    thr_v = float(settings.rag_same_doc_expand_min_vec)
    thr_t = float(settings.trgm_word_similarity_min) + 0.02
    merged = list(top_ids)
    merged_set = set(merged)
    best_vec_by_doc: dict[str, float] = defaultdict(float)
    best_trgm_by_doc: dict[str, float] = defaultdict(float)
    for c in merged:
        dk = str(chunk_map[c][1].id)
        v = vec_similarity.get(c)
        if v is not None:
            best_vec_by_doc[dk] = max(best_vec_by_doc[dk], float(v))
        t = trgm_similarity.get(c)
        if t is not None:
            best_trgm_by_doc[dk] = max(best_trgm_by_doc[dk], float(t))
    strong_docs = {
        d
        for d in {str(chunk_map[c][1].id) for c in merged}
        if best_vec_by_doc.get(d, 0.0) >= thr_v or best_trgm_by_doc.get(d, 0.0) >= thr_t
    }
    if not strong_docs:
        return merged
    extras: list[Any] = []
    for cid in prefetch_ids:
        if cid in merged_set:
            continue
        if cid not in chunk_map:
            continue
        dk = str(chunk_map[cid][1].id)
        if dk not in strong_docs:
            continue
        extras.append(cid)
        if len(extras) >= n_x * 4:
            break
    for x in extras[:n_x]:
        xd = str(chunk_map[x][1].id)
        cur_x = sum(1 for c in merged if str(chunk_map[c][1].id) == xd)
        if cur_x >= max_per_doc:
            continue
        victims = sorted(merged, key=lambda c: float(rrf.get(c, 0.0)))
        for v in victims:
            vd = str(chunk_map[v][1].id)
            if vd == xd:
                continue
            if float(rrf.get(x, 0.0)) + 1e-6 >= float(rrf.get(v, 0.0)) * 0.82:
                i = merged.index(v)
                merged[i] = x
                merged_set.discard(v)
                merged_set.add(x)
                break
    return merged


def _prefetch_passes_relevance_gate(
    cid: Any,
    vec_similarity: dict[Any, float],
    trgm_similarity: dict[Any, float],
) -> bool:
    """双路分数都偏弱时丢弃；仅文本路命中时要求更高的 word_similarity。"""
    if not settings.rag_dual_weak_filter:
        return True
    vs = vec_similarity.get(cid)
    ts = trgm_similarity.get(cid)
    if vs is not None and ts is not None:
        if vs < settings.rag_dual_weak_max_vec and ts < settings.rag_dual_weak_max_trgm:
            return False
    if vs is None and ts is not None:
        if ts < settings.rag_trgm_only_min_similarity:
            return False
    return True

# ── Query Rewrite 缓存（进程内，TTL）────────────────────────────────────────
_REWRITE_LOCK = threading.Lock()
_REWRITE_CACHE: dict[str, tuple[float, list[str]]] = {}  # key -> (expires_ts, variants)
_REWRITE_CACHE_MAX = 512


def _rewrite_cache_key(query: str) -> str:
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()


def _rewrite_cache_get(query: str) -> list[str] | None:
    ttl = max(0, int(settings.query_rewrite_cache_ttl_s))
    if ttl <= 0:
        return None
    now = time.time()
    key = _rewrite_cache_key(query)
    with _REWRITE_LOCK:
        item = _REWRITE_CACHE.get(key)
        if not item:
            return None
        exp, variants = item
        if exp <= now:
            _REWRITE_CACHE.pop(key, None)
            return None
        return list(variants)


def _rewrite_cache_set(query: str, variants: list[str]) -> None:
    ttl = max(0, int(settings.query_rewrite_cache_ttl_s))
    if ttl <= 0:
        return
    now = time.time()
    key = _rewrite_cache_key(query)
    with _REWRITE_LOCK:
        if len(_REWRITE_CACHE) >= _REWRITE_CACHE_MAX:
            # 简单清理：移除一个最早过期的；找不到则 pop 任意一个
            oldest_k: str | None = None
            oldest_exp = float("inf")
            for k, (exp, _v) in _REWRITE_CACHE.items():
                if exp < oldest_exp:
                    oldest_exp = exp
                    oldest_k = k
            if oldest_k is not None:
                _REWRITE_CACHE.pop(oldest_k, None)
            else:
                _REWRITE_CACHE.pop(next(iter(_REWRITE_CACHE)), None)
        _REWRITE_CACHE[key] = (now + ttl, list(variants))


def _extract_md_title(text: str) -> str | None:
    """从 Markdown 文本中提取第一个 H1 标题（# 开头的行）。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return stripped[2:].strip()[:200]
    return None


def ingest_bytes(
    db: Session,
    ollama: OllamaClient,
    filename: str,
    data: bytes,
    title: str | None,
    source: str | None,
    kb_collection: str | None = None,
    doc_type: str | None = None,
) -> tuple[uuid.UUID, int]:
    text = extract_text(filename, data)
    if not text.strip():
        raise ValueError("empty document text")

    coll = resolve_kb_collection(kb_collection)
    dtype = normalize_doc_type(doc_type)

    # Markdown 文件：若未提供 title，从 H1 标题自动提取
    if title is None and filename.lower().endswith(".md"):
        title = _extract_md_title(text)

    h = sha256_bytes(data)
    existing = db.execute(
        select(Document).where(
            Document.content_sha256 == h,
            Document.kb_collection == coll,
        )
    ).scalar_one_or_none()
    if existing:
        return existing.id, 0

    doc = Document(
        title=title or filename,
        source=source or filename,
        content_sha256=h,
        kb_collection=coll,
        doc_type=dtype,
    )
    db.add(doc)
    db.flush()

    common_kwargs = dict(
        filename=filename,
        markdown_by_heading=settings.chunk_markdown_by_heading,
        markdown_fence_aware=settings.chunk_markdown_fence_aware,
        merge_intro_before_fence_max_chars=settings.chunk_merge_intro_before_fence_max_chars,
        fence_continuation_prefix=settings.chunk_fence_continuation_prefix,
        continuation_title_max_chars=settings.chunk_continuation_title_max_chars,
    )

    use_parent_child = bool(getattr(settings, "chunk_parent_child", True))
    n = 0

    if use_parent_child:
        hierarchical = chunk_text_hierarchical(
            text,
            settings.chunk_max_chars,
            settings.chunk_overlap,
            min_parent_chars=int(getattr(settings, "chunk_parent_min_chars", 200) or 200),
            max_parent_chars=int(getattr(settings, "chunk_parent_max_chars", 1500) or 1500),
            **common_kwargs,
        )
        # 过滤父块：内容不为空
        hierarchical = [
            (pc, pm, ch_list) for pc, pm, ch_list in hierarchical if len(pc.strip()) >= _MIN_CHUNK_CHARS
        ]
        if not hierarchical:
            # 短粘贴 / 极短正文：层级父块全被过滤时用线性分块或单条兜底
            linear = _linear_chunks_relaxed(text, **common_kwargs)
            if not linear:
                db.rollback()
                raise ValueError("empty document text")
            for content, meta in linear:
                emb = ollama.embed(content[:8000], apply_embed_budget=False)
                meta_out = dict(meta) if isinstance(meta, dict) else {}
                meta_out.update({"doc_type": dtype, "kb_collection": coll})
                db.add(Chunk(
                    document_id=doc.id,
                    chunk_index=int(meta.get("chunk_index", n)),
                    content=content,
                    meta=meta_out,
                    embedding=emb,
                    is_index_chunk=True,
                ))
                n += 1
            db.commit()
            logging.info("[RAG] ingested %s → %d chunks (relaxed linear mode)", filename, n)
            return doc.id, n

        for parent_content, parent_meta, children in hierarchical:
            # 子块为空说明该节不需要父子分离，单块直接作为 is_index_chunk=True 入库
            if not children:
                emb = ollama.embed(parent_content[:8000], apply_embed_budget=False)
                meta_out: dict = dict(parent_meta)
                meta_out.update({"doc_type": dtype, "kb_collection": coll})
                meta_out.pop("is_parent", None)
                db.add(Chunk(
                    document_id=doc.id,
                    chunk_index=int(parent_meta.get("chunk_index", n)),
                    content=parent_content,
                    meta=meta_out,
                    embedding=emb,
                    is_index_chunk=True,
                ))
                n += 1
                continue

            # 先写父块（is_index_chunk=False，不参与检索）
            parent_meta_out: dict = dict(parent_meta)
            parent_meta_out.update({"doc_type": dtype, "kb_collection": coll})
            # 父块 embedding 不参与检索，仅为满足非空列约束而写入。
            # 只用「标题 + 首段正文」生成 embedding，避免大块内容超出 nomic-embed-text
            # 的 token 上限（约 2048 tokens）导致 Ollama 500。
            _parent_heading = str(parent_meta_out.get("section_heading", ""))
            _parent_snippet = (_parent_heading + "\n\n" + parent_content).strip()[:800]
            parent_emb = ollama.embed(_parent_snippet, apply_embed_budget=False)
            parent_chunk = Chunk(
                document_id=doc.id,
                chunk_index=int(parent_meta.get("chunk_index", n)),
                content=parent_content,
                meta=parent_meta_out,
                embedding=parent_emb,
                is_index_chunk=False,
            )
            db.add(parent_chunk)
            db.flush()  # 让 parent_chunk.id 可用

            # 再写子块（is_index_chunk=True，参与检索，持有 parent_chunk_id）
            valid_children = [(c, m) for c, m in children if len(c.strip()) >= _MIN_CHUNK_CHARS]
            for child_content, child_meta in valid_children:
                child_emb = ollama.embed(child_content[:8000], apply_embed_budget=False)
                child_meta_out: dict = dict(child_meta)
                child_meta_out.update({"doc_type": dtype, "kb_collection": coll})
                db.add(Chunk(
                    document_id=doc.id,
                    chunk_index=int(child_meta.get("chunk_index", n)),
                    content=child_content,
                    meta=child_meta_out,
                    embedding=child_emb,
                    parent_chunk_id=parent_chunk.id,
                    is_index_chunk=True,
                ))
                n += 1

        db.commit()
        logging.info("[RAG] ingested %s → %d index chunks (parent-child mode)", filename, n)
    else:
        pairs = chunk_text(
            text,
            settings.chunk_max_chars,
            settings.chunk_overlap,
            **common_kwargs,
        )
        pairs = [(c, m) for c, m in pairs if len(c.strip()) >= _MIN_CHUNK_CHARS]
        if not pairs:
            linear = _linear_chunks_relaxed(text, **common_kwargs)
            if not linear:
                db.rollback()
                raise ValueError("empty document text")
            pairs = linear

        for content, meta in pairs:
            emb = ollama.embed(content[:8000], apply_embed_budget=False)
            meta_out = dict(meta) if isinstance(meta, dict) else {}
            meta_out["doc_type"] = dtype
            meta_out["kb_collection"] = coll
            db.add(Chunk(
                document_id=doc.id,
                chunk_index=int(meta.get("chunk_index", n)),
                content=content,
                meta=meta_out,
                embedding=emb,
            ))
            n += 1
        db.commit()
        logging.info("[RAG] ingested %s → %d chunks", filename, n)

    return doc.id, n


def search_chunks(
    db: Session,
    ollama: OllamaClient,
    query: str,
    top_k: int,
    kb_collection: str | None = None,
    doc_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """混合检索：向量相似度（pgvector）+ 三元组文本匹配（pg_trgm），RRF 融合排序。
    若 pg_trgm 不可用则自动降级为纯向量检索。
    kb_collection：分区，默认 default_kb_collection；doc_types 非空时仅保留对应 Document.doc_type。
    """
    coll = resolve_kb_collection(kb_collection)
    dfilter = sanitize_doc_types_list(doc_types)

    # Embedding 偶发长尾：这里允许 embed 失败时降级为 trgm-only
    try:
        qemb = ollama.embed(query[:8000])
    except Exception as e:
        logging.info("[RAG] embed failed, fallback to trgm-only: %s", e)
        telemetry.record_timing("rag.embed_failed_ms", 0.0)
        qemb = None
    mult = max(2, int(getattr(settings, "rag_candidate_top_k_multiplier", 5) or 5))
    # Reranker 开启时扩大内部 top_k：先多召回 rerank_candidate_k 倍候选，精排后再截取到 top_k
    _rerank_on = bool(getattr(settings, "rag_rerank_enabled", False))
    _rerank_k = max(1, int(getattr(settings, "rag_rerank_candidate_k", 3) or 3))
    _fetch_top_k = top_k * _rerank_k if _rerank_on else top_k
    candidate = _fetch_top_k * mult  # 初步召回候选数

    # ── 1. 向量检索（带相关性阈值过滤，仅对 is_index_chunk=True 的子块）──
    vec_rows: list[Any] = []
    vec_similarity: dict[Any, float] = {}
    vec_ranks: dict[Any, int] = {}
    if qemb is not None:
        dist_expr = Chunk.embedding.cosine_distance(qemb)
        t_vec = time.perf_counter()
        vstmt = (
            select(Chunk.id, dist_expr.label("dist"))
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.kb_collection == coll)
            .where(Chunk.is_index_chunk.is_(True))
            .where(dist_expr < settings.vector_distance_threshold)
        )
        if dfilter:
            vstmt = vstmt.where(Document.doc_type.in_(dfilter))
        vec_rows = db.execute(vstmt.order_by(dist_expr).limit(candidate)).all()
        telemetry.record_timing("rag.vec_db_ms", (time.perf_counter() - t_vec) * 1000)
        if not vec_rows and not settings.hybrid_search:
            logging.info("[RAG] no chunks within distance threshold %.2f", settings.vector_distance_threshold)
            return []
        if vec_rows:
            # 保存每个 chunk 的真实余弦相似度（用于展示，而非排序）
            vec_similarity = {row.id: round(1.0 - float(row.dist), 4) for row in vec_rows}
            vec_ranks = {row.id: i + 1 for i, row in enumerate(vec_rows)}

    # ── 2. 三元组文本检索（pg_trgm word_similarity，仅 is_index_chunk=True）──
    # 用 word_similarity(query, text) 衡量「查询词作为子串出现在文档中的程度」
    # 适合短查询 vs 长文档，比 similarity() 更合适
    trgm_ranks: dict[Any, int] = {}
    trgm_similarity: dict[Any, float] = {}
    if settings.hybrid_search:
        try:
            wsim_expr = _trgm_word_similarity_expr(query)
            trgm_min = float(settings.trgm_word_similarity_min)
            t_trgm = time.perf_counter()
            tstmt = (
                select(Chunk.id, wsim_expr.label("wsim"))
                .join(Document, Chunk.document_id == Document.id)
                .where(Document.kb_collection == coll)
                .where(Chunk.is_index_chunk.is_(True))
                .where(wsim_expr > trgm_min)
            )
            if dfilter:
                tstmt = tstmt.where(Document.doc_type.in_(dfilter))
            trgm_rows = db.execute(tstmt.order_by(wsim_expr.desc()).limit(candidate)).all()
            telemetry.record_timing("rag.trgm_db_ms", (time.perf_counter() - t_trgm) * 1000)
            trgm_ranks = {row.id: i + 1 for i, row in enumerate(trgm_rows)}
            trgm_similarity = {row.id: round(float(row.wsim), 4) for row in trgm_rows}
        except Exception as e:
            logging.warning(f"[RAG] trgm search failed, falling back to vector-only: {e}")

    # ── 3. RRF 融合 ───────────────────────────────────────────────
    t_rrf = time.perf_counter()
    K = 60  # RRF 平滑系数（通常取 60）
    fallback = candidate + 1
    all_ids = set(vec_ranks) | set(trgm_ranks)
    if not all_ids:
        return []
    rrf: dict[Any, float] = {
        cid: (1 / (K + vec_ranks.get(cid, fallback)))
             + (1 / (K + trgm_ranks.get(cid, fallback)) if trgm_ranks else 0.0)
        for cid in all_ids
    }
    ordered_rrf = sorted(rrf, key=lambda x: rrf[x], reverse=True)
    prefetch_ids = [
        cid
        for cid in ordered_rrf
        if _prefetch_passes_relevance_gate(cid, vec_similarity, trgm_similarity)
    ][: _fetch_top_k * 2]
    if settings.rag_gate_relax_fill and len(prefetch_ids) < _fetch_top_k:
        before = len(prefetch_ids)
        for cid in ordered_rrf:
            if cid not in prefetch_ids:
                prefetch_ids.append(cid)
            if len(prefetch_ids) >= _fetch_top_k * 2:
                break
        if len(prefetch_ids) > before:
            logging.info(
                "[RAG] relevance gate kept %d ids; padded to %d (top_k=%d, relax_fill=true)",
                before,
                len(prefetch_ids),
                top_k,
            )
    telemetry.record_timing("rag.rrf_ms", (time.perf_counter() - t_rrf) * 1000)

    if not prefetch_ids:
        return []

    # ── 4. 拉取完整数据 ───────────────────────────────────────────
    t_fetch = time.perf_counter()
    rows = db.execute(
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.id.in_(prefetch_ids))
    ).all()
    telemetry.record_timing("rag.fetch_ms", (time.perf_counter() - t_fetch) * 1000)
    chunk_map: dict[Any, tuple[Any, Any]] = {ch.id: (ch, doc) for ch, doc in rows}

    # ── 4b. Parent-Child：将子块替换为对应父块内容（去重）─────────
    # 若命中的子块携带 parent_chunk_id，则拉取父块并用父块内容喂给模型（保留子块分数）。
    # 多个子块若共享同一父块，仅保留分数最高的一条（去重父块）。
    parent_ids_needed: set[Any] = set()
    for cid in prefetch_ids:
        ch, _ = chunk_map.get(cid, (None, None))
        if ch is not None and ch.parent_chunk_id is not None:
            parent_ids_needed.add(ch.parent_chunk_id)

    parent_chunk_map: dict[Any, Any] = {}
    if parent_ids_needed:
        t_parent = time.perf_counter()
        parent_rows = db.execute(
            select(Chunk).where(Chunk.id.in_(parent_ids_needed))
        ).scalars().all()
        telemetry.record_timing("rag.parent_fetch_ms", (time.perf_counter() - t_parent) * 1000)
        parent_chunk_map = {pc.id: pc for pc in parent_rows}
        logging.info("[RAG] parent-child: fetched %d parent chunks for %d matched children",
                     len(parent_chunk_map), len(parent_ids_needed))

    # ── 5. 来源多样性过滤：每个文档最多贡献 rag_max_chunks_per_doc 个 chunk ──
    max_per_doc = max(1, int(getattr(settings, "rag_max_chunks_per_doc", 4) or 4))
    per_doc_count: dict[str, int] = {}
    top_ids: list[Any] = []
    for cid in prefetch_ids:
        if cid not in chunk_map:
            continue
        _, doc = chunk_map[cid]
        dk = str(doc.id)
        if per_doc_count.get(dk, 0) < max_per_doc:
            per_doc_count[dk] = per_doc_count.get(dk, 0) + 1
            top_ids.append(cid)
        if len(top_ids) >= _fetch_top_k:
            break

    top_ids = _maybe_swap_same_doc_from_prefetch(
        top_ids, prefetch_ids, chunk_map, rrf, vec_similarity, trgm_similarity, max_per_doc
    )

    # Parent-Child 去重：若多个子块共享同一父块，仅保留 RRF 分最高那条
    seen_parent_ids: set[Any] = set()
    deduped_top_ids: list[Any] = []
    for cid in top_ids:
        ch, _ = chunk_map.get(cid, (None, None))
        if ch is None:
            continue
        pid = getattr(ch, "parent_chunk_id", None)
        if pid is not None and pid in parent_chunk_map:
            if pid in seen_parent_ids:
                continue  # 同父块已有更高分的子块，跳过
            seen_parent_ids.add(pid)
        deduped_top_ids.append(cid)

    out: list[dict[str, Any]] = []
    for cid in deduped_top_ids:
        ch, doc = chunk_map[cid]
        pid = getattr(ch, "parent_chunk_id", None)
        # 取父块作为展示内容（若存在）
        effective_chunk = parent_chunk_map.get(pid) if pid is not None else None
        content_chunk = effective_chunk if effective_chunk is not None else ch

        page = content_chunk.meta.get("page") if isinstance(content_chunk.meta, dict) else None
        full_content = content_chunk.content
        snippet = full_content[:400] + ("…" if len(full_content) > 400 else "")
        v = vec_similarity.get(cid)
        t = trgm_similarity.get(cid)
        # 展示分数：混合检索时向量与文本路量纲不同，取 max 避免「文本很相关却显示低向量分」误导用户
        if v is not None and t is not None:
            display_score = round(max(float(v), float(t)), 4)
        elif v is not None:
            display_score = float(v)
        elif t is not None:
            display_score = float(t)
        else:
            display_score = 0.0
        sec_h = None
        meta_src = content_chunk.meta if isinstance(content_chunk.meta, dict) else {}
        raw_h = meta_src.get("section_heading")
        if isinstance(raw_h, str) and raw_h.strip():
            sec_h = raw_h.strip()[:200]
        out.append(
            {
                "chunk_id": ch.id,
                "parent_chunk_id": str(pid) if pid is not None else None,
                "source": doc.source,
                "kb_collection": doc.kb_collection,
                "doc_type": doc.doc_type,
                "page": page,
                "section_heading": sec_h,
                "score": display_score,
                # 额外返回调试字段（前端当前未展示）
                "vec_score": v,
                "text_score": t,
                "rrf_score": round(float(rrf.get(cid, 0.0)), 6),
                "snippet": snippet,
                "full_content": full_content,
            }
        )
    # ── 6. Reranker 精排（可选）─────────────────────────────────────
    if getattr(settings, "rag_rerank_enabled", False) and out:
        t_rerank = time.perf_counter()
        out = reranker_rerank(
            query,
            out,
            model_name=str(getattr(settings, "rag_rerank_model",
                                   "cross-encoder/ms-marco-MiniLM-L-6-v2")),
            top_k=top_k,
        )
        telemetry.record_timing("rag.rerank_ms", (time.perf_counter() - t_rerank) * 1000)

    return out


def rewrite_query(ollama: OllamaClient, query: str) -> list[str]:
    """用 LLM 生成 2 个不同表达角度的搜索查询，用于多路召回以提升覆盖率。
    失败时静默降级，返回原始查询列表。
    """
    prompt = (
        "请将以下用户问题改写为 2 个搜索视角不同的知识库查询（简洁，不超过 30 字）。\n"
        "只输出 JSON 数组，不要任何其他文字。\n"
        '格式：["查询1", "查询2"]\n\n'
        "原始问题：" + query[:400]
    )
    cached = _rewrite_cache_get(query)
    if cached:
        telemetry.inc("rag_rewrite_cache_hits")
        return [query] + cached
    telemetry.inc("rag_rewrite_cache_misses")

    budget_ms = int(getattr(settings, "query_rewrite_budget_ms", 0) or 0)

    try:
        t0 = time.perf_counter()

        # 用线程 + timeout 实现 budget（不阻塞主流程）
        def _call() -> str:
            return ollama.chat_complete([{"role": "user", "content": prompt}], temperature=0.2)

        if budget_ms > 0:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call)
                try:
                    raw = fut.result(timeout=budget_ms / 1000)
                except FuturesTimeoutError:
                    telemetry.inc("rag_rewrite_timeouts")
                    telemetry.record_timing("rag.rewrite_ms", (time.perf_counter() - t0) * 1000)
                    logging.info("[RAG] query rewrite timed out (%dms), fallback to original", budget_ms)
                    return [query]
        else:
            raw = _call()

        telemetry.record_timing("rag.rewrite_ms", (time.perf_counter() - t0) * 1000)
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            variants: list[str] = json.loads(raw[start : end + 1])
            valid = [v.strip() for v in variants if isinstance(v, str) and v.strip()][:2]
            if valid:
                logging.info("[RAG] query rewrite: %s → %s", query[:40], valid)
                _rewrite_cache_set(query, valid)
                return [query] + valid
    except Exception as e:
        logging.warning("[RAG] query rewrite failed, using original: %s", e)
    return [query]


def multi_query_search(
    db: Session,
    ollama: OllamaClient,
    query: str,
    top_k: int,
    kb_collection: str | None = None,
    doc_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """多路召回：对原始查询 + 改写变体分别检索，按最高 RRF 分去重合并，返回 top_k 结果。"""
    t_total = time.perf_counter()
    # B: 仅在首次检索 0 命中时才触发改写（减少延迟与成本）
    initial: list[dict[str, Any]] = []
    t_init = time.perf_counter()
    try:
        initial = search_chunks(db, ollama, query, top_k, kb_collection, doc_types)
    finally:
        telemetry.record_timing("rag.initial_search_ms", (time.perf_counter() - t_init) * 1000)

    merged: dict[str, dict[str, Any]] = {str(r["chunk_id"]): r for r in initial}

    if not settings.query_rewrite:
        telemetry.record_timing("rag.multi_query_total_ms", (time.perf_counter() - t_total) * 1000)
        return sorted(
            merged.values(),
            key=lambda x: float(x.get("rrf_score") or x.get("score") or 0.0),
            reverse=True,
        )[:top_k]

    if settings.query_rewrite_only_on_empty and merged:
        # 有命中则直接返回（不改写）
        telemetry.record_timing("rag.multi_query_total_ms", (time.perf_counter() - t_total) * 1000)
        return sorted(
            merged.values(),
            key=lambda x: float(x.get("rrf_score") or x.get("score") or 0.0),
            reverse=True,
        )[:top_k]

    queries = rewrite_query(ollama, query)

    for q in queries:
        t_q = time.perf_counter()
        for r in search_chunks(db, ollama, q, top_k, kb_collection, doc_types):
            cid = str(r["chunk_id"])
            r_key = float(r.get("rrf_score") or r.get("score") or 0.0)
            m_key = float(merged.get(cid, {}).get("rrf_score") or merged.get(cid, {}).get("score") or 0.0)
            if cid not in merged or r_key > m_key:
                merged[cid] = r
        telemetry.record_timing("rag.search_chunks_ms", (time.perf_counter() - t_q) * 1000)

    telemetry.record_timing("rag.multi_query_total_ms", (time.perf_counter() - t_total) * 1000)
    return sorted(
        merged.values(),
        key=lambda x: float(x.get("rrf_score") or x.get("score") or 0.0),
        reverse=True,
    )[:top_k]


def _suggest_next_hop_query(
    ollama: OllamaClient,
    question: str,
    sources: list[dict[str, Any]],
) -> tuple[str | None, str]:
    """
    基于首跳命中的片段，建议下一跳检索 query（用于 multi-hop RAG）。

    Returns:
        (next_query, reason)
        next_query 为 None 表示不需要跳转或无法生成。
    """
    # 只截取少量片段与短 snippet，避免提示过长
    ctx_lines: list[str] = []
    for i, s in enumerate(sources[:6], start=1):
        src = s.get("source") or "unknown"
        sec = s.get("section_heading") or ""
        snippet = (s.get("snippet") or s.get("full_content") or "").strip()
        if len(snippet) > 260:
            snippet = snippet[:260] + "…"
        ctx_lines.append(f"[S{i}] {src} {sec}\n{snippet}")
    ctx = "\n\n".join(ctx_lines) if ctx_lines else "(无检索片段)"

    prompt = (
        "你是一个检索编排器。用户问题可能需要两跳检索：先定位实体/主语，再查询其属性/偏好/细节。\n"
        "请基于首跳检索到的片段，判断是否需要继续第二跳检索，并给出一个更具体的下一跳查询词。\n"
        "只输出 JSON（不要 markdown、不要多余文字）。\n"
        "格式：\n"
        '{'
        '"should_hop": true, '
        '"next_query": "下一跳检索词（不超过30字）", '
        '"reason": "一句话原因（不超过20字）"'
        '}\n'
        "若不需要跳转：should_hop=false 且 next_query=null。\n\n"
        f"用户问题：{question[:300]}\n\n"
        f"首跳片段：\n{ctx}\n\n"
        "输出："
    )

    try:
        raw = ollama.chat_complete_json([{"role": "user", "content": prompt}], temperature=0.0)
        obj = json.loads(raw)
        should = bool(obj.get("should_hop"))
        nq = obj.get("next_query")
        reason = str(obj.get("reason") or "").strip()[:60]
        if not should:
            return None, reason or "无需二跳"
        if not isinstance(nq, str) or not nq.strip():
            return None, reason or "二跳查询为空"
        next_query = nq.strip()[:80]
        # 防止生成与原问题完全一致导致无意义二次检索
        if next_query == question.strip():
            return None, reason or "二跳无增量"
        return next_query, reason or "二跳检索"
    except Exception as e:
        logging.warning("[RAG] suggest_next_hop_query failed: %s", e)
        return None, "二跳生成失败"


def multi_hop_search(
    db: Session,
    ollama: OllamaClient,
    question: str,
    top_k: int,
    kb_collection: str | None = None,
    doc_types: list[str] | None = None,
    *,
    max_hops: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Multi-hop RAG：最多两跳检索（首跳 + 可选二跳），并合并证据返回。

    Returns:
        (sources, hop_trace)
        hop_trace 为 [{hop, query, count, reason}]，用于前端/日志展示。
    """
    hop_trace: list[dict[str, Any]] = []

    t0 = time.perf_counter()
    first = multi_query_search(db, ollama, question, top_k, kb_collection, doc_types)
    telemetry.record_timing("rag.multihop.hop1_ms", (time.perf_counter() - t0) * 1000)
    hop_trace.append({"hop": 1, "query": question, "count": len(first), "reason": "首跳检索"})

    if max_hops <= 1 or not first:
        telemetry.record_timing("rag.multihop.total_ms", (time.perf_counter() - t0) * 1000)
        return first, hop_trace

    # 二跳：用首跳片段建议更具体的 query
    t_suggest = time.perf_counter()
    next_query, reason = _suggest_next_hop_query(ollama, question, first)
    telemetry.record_timing("rag.multihop.suggest_ms", (time.perf_counter() - t_suggest) * 1000)
    if not next_query:
        hop_trace.append({"hop": 2, "query": None, "count": 0, "reason": reason})
        telemetry.record_timing("rag.multihop.total_ms", (time.perf_counter() - t0) * 1000)
        return first, hop_trace

    t1 = time.perf_counter()
    second = multi_query_search(db, ollama, next_query, top_k, kb_collection, doc_types)
    telemetry.record_timing("rag.multihop.hop2_ms", (time.perf_counter() - t1) * 1000)
    hop_trace.append({"hop": 2, "query": next_query, "count": len(second), "reason": reason})

    # 合并：按 chunk_id 去重，保留 rrf_score/score 更高的条目
    merged: dict[str, dict[str, Any]] = {str(s["chunk_id"]): s for s in first}
    for s in second:
        cid = str(s["chunk_id"])
        new_key = float(s.get("rrf_score") or s.get("score") or 0.0)
        old_key = float(merged.get(cid, {}).get("rrf_score") or merged.get(cid, {}).get("score") or 0.0)
        if cid not in merged or new_key > old_key:
            merged[cid] = s

    out = sorted(
        merged.values(),
        key=lambda x: float(x.get("rrf_score") or x.get("score") or 0.0),
        reverse=True,
    )[:top_k]

    telemetry.record_timing("rag.multihop.total_ms", (time.perf_counter() - t0) * 1000)
    return out, hop_trace


def search_memories(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    query: str,
    top_k: int = 5,
    kg_hops: int = 2,
) -> list[str]:
    """
    两段式记忆检索：
      1. 向量检索 Memory 表（扁平记忆），过滤已过期 + 按置信度加权
      2. 知识图谱展开：向量检索 KGEntity → graph_expand N 跳邻域

    Returns:
        list[str]，每条为格式化的记忆或图谱关系描述。
    """
    from app.config import settings  # noqa: PLC0415
    from app.services.kg import search_kg  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    try:
        qemb = ollama.embed(query[:8000])
    except Exception as e:
        logging.info("[Memory] embed failed, skip memory recall: %s", e)
        telemetry.record_timing("memory.embed_failed_ms", 0.0)
        return []

    # ── 1. 扁平记忆向量检索（过期过滤 + 置信度排序）──────────────────────
    dist_expr = Memory.embedding.cosine_distance(qemb)
    now = datetime.now(timezone.utc)
    stmt = (
        select(Memory, dist_expr.label("dist"))
        .where(Memory.user_id == user_id)
        # 过滤已过期记忆（valid_until IS NULL 表示永久有效）
        .where((Memory.valid_until == None) | (Memory.valid_until > now))  # noqa: E711
        .order_by(dist_expr)
        .limit(top_k)
    )
    rows = db.execute(stmt).all()
    lines: list[str] = []
    for mem, dist in rows:
        conf_tag = f" [置信度:{mem.confidence:.1f}]" if mem.confidence < 1.0 else ""
        lines.append(f"- ({mem.kind}){conf_tag} {mem.content}")

    # ── 2. 知识图谱展开（仅在 kg_enabled 时执行）──────────────────────────
    if settings.kg_enabled:
        hops = kg_hops if kg_hops != 2 else settings.kg_graph_hops
        kg_lines = search_kg(db, ollama, user_id, query, top_k_entities=5, hops=hops)
        if kg_lines:
            lines.append("【知识图谱关系】")
            lines.extend(f"  {l}" for l in kg_lines)

    return lines
