import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Chunk, Document, Memory
from app.services.ollama import OllamaClient
from app.services.text_extract import chunk_text, extract_text


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    return doc.id, n


def search_chunks(db: Session, ollama: OllamaClient, query: str, top_k: int) -> list[dict[str, Any]]:
    qemb = ollama.embed(query[:8000])
    dist_expr = Chunk.embedding.cosine_distance(qemb)
    stmt = (
        select(Chunk, Document, dist_expr.label("dist"))
        .join(Document, Chunk.document_id == Document.id)
        .order_by(dist_expr)
        .limit(top_k)
    )
    rows = db.execute(stmt).all()
    out: list[dict[str, Any]] = []
    for ch, doc, dist in rows:
        dist_f = float(dist)
        score = max(0.0, min(1.0, 1.0 - dist_f / 2.0))
        page = ch.meta.get("page") if isinstance(ch.meta, dict) else None
        snippet = ch.content[:400] + ("…" if len(ch.content) > 400 else "")
        out.append(
            {
                "chunk_id": ch.id,
                "source": doc.source,
                "page": page,
                "score": score,
                "snippet": snippet,
                "full_content": ch.content,
            }
        )
    return out


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
