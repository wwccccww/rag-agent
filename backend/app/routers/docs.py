from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.database import SessionLocal
from app.kb import normalize_doc_type, resolve_kb_collection
from app.models import Chunk, Document

router = APIRouter(prefix="/v1", tags=["documents"])


class DocItem(BaseModel):
    id: UUID
    title: str | None
    source: str | None
    kb_collection: str
    doc_type: str
    chunk_count: int
    created_at: str


class ChunkItem(BaseModel):
    id: UUID
    chunk_index: int
    content: str
    meta: dict


@router.get("/documents", response_model=list[DocItem])
def list_documents(
    limit: int = Query(50, ge=1, le=200),
    kb_collection: str | None = Query(None, description="仅列出该分区下的文档"),
    doc_type: str | None = Query(None, description="仅列出该文档类型（tutorial/api/requirements/general）"),
) -> list[DocItem]:
    db = SessionLocal()
    try:
        stmt = select(Document, func.count(Chunk.id).label("cnt")).outerjoin(
            Chunk, Chunk.document_id == Document.id
        )
        if kb_collection is not None and str(kb_collection).strip():
            try:
                coll = resolve_kb_collection(kb_collection)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            stmt = stmt.where(Document.kb_collection == coll)
        if doc_type is not None and str(doc_type).strip():
            stmt = stmt.where(Document.doc_type == normalize_doc_type(doc_type))
        stmt = stmt.group_by(Document.id).order_by(Document.created_at.desc()).limit(limit)
        rows = db.execute(stmt).all()
        return [
            DocItem(
                id=doc.id,
                title=doc.title,
                source=doc.source,
                kb_collection=doc.kb_collection,
                doc_type=doc.doc_type,
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
