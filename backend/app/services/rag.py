import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Chunk, Document, Memory
from app.services.ollama import OllamaClient
from app.services.text_extract import chunk_text, extract_text


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_MIN_CHUNK_CHARS = 30  # 过短的 chunk 不含实质信息，跳过


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
) -> tuple[uuid.UUID, int]:
    text = extract_text(filename, data)
    if not text.strip():
        raise ValueError("empty document text")

    # Markdown 文件：若未提供 title，从 H1 标题自动提取
    if title is None and filename.lower().endswith(".md"):
        title = _extract_md_title(text)

    h = sha256_bytes(data)
    existing = db.execute(select(Document).where(Document.content_sha256 == h)).scalar_one_or_none()
    if existing:
        return existing.id, 0

    doc = Document(
        title=title or filename,
        source=source or filename,
        content_sha256=h,
    )
    db.add(doc)
    db.flush()

    pairs = chunk_text(text, settings.chunk_max_chars, settings.chunk_overlap)
    # 过滤无实质内容的短片段
    pairs = [(c, m) for c, m in pairs if len(c.strip()) >= _MIN_CHUNK_CHARS]
    if not pairs:
        db.rollback()
        raise ValueError("document has no usable content after chunking")

    n = 0
    for content, meta in pairs:
        emb = ollama.embed(content[:8000])
        ch = Chunk(
            document_id=doc.id,
            chunk_index=int(meta.get("chunk_index", n)),
            content=content,
            meta=meta,
            embedding=emb,
        )
        db.add(ch)
        n += 1
    db.commit()
    logging.info("[RAG] ingested %s → %d chunks (filtered short chunks)", filename, n)
    return doc.id, n


def search_chunks(db: Session, ollama: OllamaClient, query: str, top_k: int) -> list[dict[str, Any]]:
    """混合检索：向量相似度（pgvector）+ 三元组文本匹配（pg_trgm），RRF 融合排序。
    若 pg_trgm 不可用则自动降级为纯向量检索。
    """
    qemb = ollama.embed(query[:8000])
    candidate = top_k * 3  # 初步召回候选数

    # ── 1. 向量检索（带相关性阈值过滤）─────────────────────────
    dist_expr = Chunk.embedding.cosine_distance(qemb)
    vec_rows = db.execute(
        select(Chunk.id, dist_expr.label("dist"))
        .where(dist_expr < settings.vector_distance_threshold)  # 过滤掉明显不相关的片段
        .order_by(dist_expr)
        .limit(candidate)
    ).all()
    if not vec_rows:
        logging.info("[RAG] no chunks within distance threshold %.2f", settings.vector_distance_threshold)
        return []
    # 保存每个 chunk 的真实余弦相似度（用于展示，而非排序）
    vec_similarity: dict[Any, float] = {row.id: round(1.0 - float(row.dist), 4) for row in vec_rows}
    vec_ranks: dict[Any, int] = {row.id: i + 1 for i, row in enumerate(vec_rows)}

    # ── 2. 三元组文本检索（pg_trgm word_similarity）────────────
    # 用 word_similarity(query, text) 衡量「查询词作为子串出现在文档中的程度」
    # 适合短查询 vs 长文档，比 similarity() 更合适
    trgm_ranks: dict[Any, int] = {}
    if settings.hybrid_search:
        try:
            wsim_expr = func.word_similarity(query, Chunk.content)
            trgm_rows = db.execute(
                select(Chunk.id, wsim_expr.label("wsim"))
                .where(wsim_expr > 0.2)
                .order_by(wsim_expr.desc())
                .limit(candidate)
            ).all()
            trgm_ranks = {row.id: i + 1 for i, row in enumerate(trgm_rows)}
        except Exception as e:
            logging.warning(f"[RAG] trgm search failed, falling back to vector-only: {e}")

    # ── 3. RRF 融合 ───────────────────────────────────────────────
    K = 60  # RRF 平滑系数（通常取 60）
    fallback = candidate + 1
    all_ids = set(vec_ranks) | set(trgm_ranks)
    rrf: dict[Any, float] = {
        cid: (1 / (K + vec_ranks.get(cid, fallback)))
             + (1 / (K + trgm_ranks.get(cid, fallback)) if trgm_ranks else 0.0)
        for cid in all_ids
    }
    # 多取 2 倍候选，留给来源多样性过滤使用
    prefetch_ids = sorted(rrf, key=lambda x: rrf[x], reverse=True)[: top_k * 2]

    if not prefetch_ids:
        return []

    # ── 4. 拉取完整数据 ───────────────────────────────────────────
    rows = db.execute(
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.id.in_(prefetch_ids))
    ).all()
    chunk_map = {ch.id: (ch, doc) for ch, doc in rows}

    # ── 5. 来源多样性过滤：每个文档最多贡献 MAX_PER_DOC 个 chunk ──
    MAX_PER_DOC = 3
    per_doc_count: dict[str, int] = {}
    top_ids: list[Any] = []
    for cid in prefetch_ids:
        if cid not in chunk_map:
            continue
        _, doc = chunk_map[cid]
        dk = str(doc.id)
        if per_doc_count.get(dk, 0) < MAX_PER_DOC:
            per_doc_count[dk] = per_doc_count.get(dk, 0) + 1
            top_ids.append(cid)
        if len(top_ids) >= top_k:
            break

    out: list[dict[str, Any]] = []
    for cid in top_ids:
        ch, doc = chunk_map[cid]
        page = ch.meta.get("page") if isinstance(ch.meta, dict) else None
        snippet = ch.content[:400] + ("…" if len(ch.content) > 400 else "")
        out.append(
            {
                "chunk_id": ch.id,
                "source": doc.source,
                "page": page,
                # 显示真实余弦相似度，chunk 不在向量结果中时退回 0
                "score": vec_similarity.get(cid, 0.0),
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
    try:
        raw = ollama.chat_complete([{"role": "user", "content": prompt}], temperature=0.2)
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            variants: list[str] = json.loads(raw[start : end + 1])
            valid = [v.strip() for v in variants if isinstance(v, str) and v.strip()][:2]
            if valid:
                logging.info("[RAG] query rewrite: %s → %s", query[:40], valid)
                return [query] + valid
    except Exception as e:
        logging.warning("[RAG] query rewrite failed, using original: %s", e)
    return [query]


def multi_query_search(
    db: Session, ollama: OllamaClient, query: str, top_k: int
) -> list[dict[str, Any]]:
    """多路召回：对原始查询 + 改写变体分别检索，按最高 RRF 分去重合并，返回 top_k 结果。"""
    queries = rewrite_query(ollama, query) if settings.query_rewrite else [query]

    merged: dict[str, dict[str, Any]] = {}  # chunk_id → best result
    for q in queries:
        for r in search_chunks(db, ollama, q, top_k):
            cid = str(r["chunk_id"])
            if cid not in merged or r["score"] > merged[cid]["score"]:
                merged[cid] = r

    return sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:top_k]


def search_memories(db: Session, ollama: OllamaClient, user_id: str, query: str, top_k: int = 5) -> list[str]:
    qemb = ollama.embed(query[:8000])
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
