from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Chunk, Document

router = APIRouter(prefix="/v1", tags=["documents"])


class DocItem(BaseModel):
    id: UUID
    title: str | None
    source: str | None
    chunk_count: int
    created_at: str


class ChunkItem(BaseModel):
    id: UUID
    chunk_index: int
    content: str
    meta: dict


@router.get("/documents", response_model=list[DocItem])
def list_documents(limit: int = Query(50, ge=1, le=200)) -> list[DocItem]:
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(Document, func.count(Chunk.id).label("cnt"))
                .outerjoin(Chunk, Chunk.document_id == Document.id)
                .group_by(Document.id)
                .order_by(Document.created_at.desc())
                .limit(limit)
            )
            .all()
        )
        return [
            DocItem(
                id=doc.id,
                title=doc.title,
                source=doc.source,
                chunk_count=int(cnt),
                created_at=doc.created_at.isoformat(),
            )
            for doc, cnt in rows
        ]
    finally:
        db.close()


@router.get("/documents/{doc_id}/chunks", response_model=list[ChunkItem])
def list_chunks(doc_id: UUID, limit: int = Query(100, ge=1, le=500)) -> list[ChunkItem]:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        rows = (
            db.execute(
                select(Chunk)
                .where(Chunk.document_id == doc_id)
                .order_by(Chunk.chunk_index)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            ChunkItem(id=c.id, chunk_index=c.chunk_index, content=c.content, meta=c.meta or {})
            for c in rows
        ]
    finally:
        db.close()


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: UUID) -> dict:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        db.delete(doc)
        db.commit()
        return {"ok": True}
    finally:
        db.close()
