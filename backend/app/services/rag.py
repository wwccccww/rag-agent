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
from app.services.text_extract import chunk_text, extract_text
from app.telemetry import telemetry


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_MIN_CHUNK_CHARS = 30  # 过短的 chunk 不含实质信息，跳过


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

    pairs = chunk_text(
        text,
        settings.chunk_max_chars,
        settings.chunk_overlap,
        filename=filename,
        markdown_by_heading=settings.chunk_markdown_by_heading,
        markdown_fence_aware=settings.chunk_markdown_fence_aware,
        merge_intro_before_fence_max_chars=settings.chunk_merge_intro_before_fence_max_chars,
        fence_continuation_prefix=settings.chunk_fence_continuation_prefix,
        continuation_title_max_chars=settings.chunk_continuation_title_max_chars,
    )
    # 过滤无实质内容的短片段
    pairs = [(c, m) for c, m in pairs if len(c.strip()) >= _MIN_CHUNK_CHARS]
    if not pairs:
        db.rollback()
        raise ValueError("document has no usable content after chunking")

    n = 0
    for content, meta in pairs:
        emb = ollama.embed(content[:8000], apply_embed_budget=False)
        meta_out = dict(meta) if isinstance(meta, dict) else {}
        meta_out["doc_type"] = dtype
        meta_out["kb_collection"] = coll
        ch = Chunk(
            document_id=doc.id,
            chunk_index=int(meta.get("chunk_index", n)),
            content=content,
            meta=meta_out,
            embedding=emb,
        )
        db.add(ch)
        n += 1
    db.commit()
    logging.info("[RAG] ingested %s → %d chunks (filtered short chunks)", filename, n)
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
    candidate = top_k * mult  # 初步召回候选数

    # ── 1. 向量检索（带相关性阈值过滤）─────────────────────────
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

    # ── 2. 三元组文本检索（pg_trgm word_similarity）────────────
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
    ][: top_k * 2]
    if settings.rag_gate_relax_fill and len(prefetch_ids) < top_k:
        before = len(prefetch_ids)
        for cid in ordered_rrf:
            if cid not in prefetch_ids:
                prefetch_ids.append(cid)
            if len(prefetch_ids) >= top_k * 2:
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
    chunk_map = {ch.id: (ch, doc) for ch, doc in rows}

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
        if len(top_ids) >= top_k:
            break

    top_ids = _maybe_swap_same_doc_from_prefetch(
        top_ids, prefetch_ids, chunk_map, rrf, vec_similarity, trgm_similarity, max_per_doc
    )

    out: list[dict[str, Any]] = []
    for cid in top_ids:
        ch, doc = chunk_map[cid]
        page = ch.meta.get("page") if isinstance(ch.meta, dict) else None
        snippet = ch.content[:400] + ("…" if len(ch.content) > 400 else "")
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
        if isinstance(ch.meta, dict):
            raw_h = ch.meta.get("section_heading")
            if isinstance(raw_h, str) and raw_h.strip():
                sec_h = raw_h.strip()[:200]
        out.append(
            {
                "chunk_id": ch.id,
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
                "full_content": ch.content,
            }
        )
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


def search_memories(db: Session, ollama: OllamaClient, user_id: str, query: str, top_k: int = 5) -> list[str]:
    try:
        qemb = ollama.embed(query[:8000])
    except Exception as e:
        logging.info("[Memory] embed failed, skip memory recall: %s", e)
        telemetry.record_timing("memory.embed_failed_ms", 0.0)
        return []
    dist_expr = Memory.embedding.cosine_distance(qemb)
    stmt = (
        select(Memory, dist_expr.label("dist"))
        .where(Memory.user_id == user_id)
        .order_by(dist_expr)
        .limit(top_k)
    )
    rows = db.execute(stmt).all()
    lines: list[str] = []
    for mem, _dist in rows:
        lines.append(f"- ({mem.kind}) {mem.content}")
    return lines
