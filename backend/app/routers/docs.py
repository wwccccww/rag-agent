from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.kb import normalize_doc_type, resolve_kb_collection, validate_kb_collection_optional
from app.models import Chunk, Document

router = APIRouter(prefix="/v1", tags=["documents"])

_BATCH_MAX = 100


def _normalize_meta_patch_dict(data: object) -> object:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for k in ("kb_collection", "doc_type"):
        if k in out and isinstance(out[k], str):
            s = out[k].strip()
            out[k] = s if s else None
    return out


def _sync_chunks_meta(db: Session, doc_id: UUID, kb_collection: str, doc_type: str) -> None:
    """与 Document 对齐，避免 chunk.meta 与文档表长期不一致。"""
    for ch in db.execute(select(Chunk).where(Chunk.document_id == doc_id)).scalars():
        m = dict(ch.meta or {})
        m["kb_collection"] = kb_collection
        m["doc_type"] = doc_type
        ch.meta = m


def _conflict_same_sha_in_collection(
    db: Session, *, content_sha256: str, kb_collection: str, exclude_id: UUID
) -> bool:
    row = db.execute(
        select(Document.id).where(
            Document.content_sha256 == content_sha256,
            Document.kb_collection == kb_collection,
            Document.id != exclude_id,
        )
    ).scalar_one_or_none()
    return row is not None


class DocumentMetaPatchBody(BaseModel):
    """至少提供一个非空字段；未提供的字段保持原值。"""

    kb_collection: str | None = None
    doc_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_blank(cls, data: object) -> object:
        return _normalize_meta_patch_dict(data)

    @model_validator(mode="after")
    def _one_field(self) -> "DocumentMetaPatchBody":
        if self.kb_collection is None and self.doc_type is None:
            raise ValueError("至少提供 kb_collection 或 doc_type 之一")
        return self


class DocumentBatchMetaPatchBody(BaseModel):
    document_ids: list[UUID] = Field(..., min_length=1, max_length=_BATCH_MAX)
    kb_collection: str | None = None
    doc_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_blank(cls, data: object) -> object:
        return _normalize_meta_patch_dict(data)

    @model_validator(mode="after")
    def _one_field(self) -> "DocumentBatchMetaPatchBody":
        if self.kb_collection is None and self.doc_type is None:
            raise ValueError("至少提供 kb_collection 或 doc_type 之一")
        return self


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
    is_index_chunk: bool = True
    parent_chunk_id: UUID | None = None


@router.get("/documents", response_model=list[DocItem])
def list_documents(
    limit: int = Query(50, ge=1, le=200),
    kb_collection: str | None = Query(None, description="仅列出该分区下的文档"),
    doc_type: str | None = Query(None, description="仅列出该 doc_type（与入库 slug 规则一致）"),
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
            try:
                dt = normalize_doc_type(doc_type)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            stmt = stmt.where(Document.doc_type == dt)
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


@router.get("/documents/catalog/doc-types")
def list_distinct_doc_types(
    kb_collection: str | None = Query(None, description="若指定则仅统计该分区下出现过的 doc_type"),
) -> dict[str, list[str]]:
    """库中已出现过的 doc_type（去重、排序），供对话页/筛选与知识库实际类型对齐。

    路径使用 `/documents/catalog/...` 而非 `/documents/types`，避免个别部署里与 `/documents/{doc_id}` 解析顺序冲突导致 **405**。
    """
    db = SessionLocal()
    try:
        stmt = select(Document.doc_type).distinct()
        if kb_collection is not None and str(kb_collection).strip():
            try:
                coll = resolve_kb_collection(kb_collection)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            stmt = stmt.where(Document.kb_collection == coll)
        stmt = stmt.order_by(Document.doc_type)
        rows = db.execute(stmt).scalars().all()
        return {"doc_types": [str(r) for r in rows]}
    finally:
        db.close()


@router.get("/documents/{doc_id}/chunks", response_model=list[ChunkItem])
def list_chunks(
    doc_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    view: str = Query("parent", description="parent=仅父块（默认）；index=仅检索子块；all=全部"),
) -> list[ChunkItem]:
    """返回文档分块列表。
    - **parent**（默认）：仅返回父块（is_index_chunk=False）；若文档无父块（旧格式入库），
      自动回落到返回所有 is_index_chunk=True 的检索子块。
    - **index**：仅返回检索子块（is_index_chunk=True）。
    - **all**：返回所有块（父块 + 子块）。
    """
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "document not found")

        base = select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.chunk_index)

        if view == "all":
            stmt = base
        elif view == "index":
            stmt = base.where(Chunk.is_index_chunk.is_(True))
        else:
            # parent 模式：优先显示父块；若无父块则回落为检索子块
            parent_rows = (
                db.execute(base.where(Chunk.is_index_chunk.is_(False)).limit(limit))
                .scalars()
                .all()
            )
            if parent_rows:
                return [
                    ChunkItem(
                        id=c.id,
                        chunk_index=c.chunk_index,
                        content=c.content,
                        meta=c.meta or {},
                        is_index_chunk=False,
                        parent_chunk_id=None,
                    )
                    for c in parent_rows
                ]
            # 无父块：显示全部检索子块
            stmt = base.where(Chunk.is_index_chunk.is_(True))

        rows = db.execute(stmt.limit(limit)).scalars().all()
        return [
            ChunkItem(
                id=c.id,
                chunk_index=c.chunk_index,
                content=c.content,
                meta=c.meta or {},
                is_index_chunk=bool(c.is_index_chunk),
                parent_chunk_id=c.parent_chunk_id,
            )
            for c in rows
        ]
    finally:
        db.close()


@router.patch("/documents/batch")
def batch_patch_documents(body: DocumentBatchMetaPatchBody) -> dict:
    """批量修改分区/类型，最多 100 条（与 _BATCH_MAX 一致）；同一事务内依次校验，任一条 409 则整批回滚。"""
    uniq_ids = list(dict.fromkeys(body.document_ids))
    db = SessionLocal()
    try:
        docs = db.execute(select(Document).where(Document.id.in_(uniq_ids))).scalars().all()
        found = {d.id for d in docs}
        missing = [str(i) for i in uniq_ids if i not in found]
        if missing:
            raise HTTPException(404, f"document not found: {', '.join(missing[:5])}")

        doc_by_id = {d.id: d for d in docs}
        meta_only = DocumentMetaPatchBody.model_validate(body.model_dump(exclude={"document_ids"}))
        for uid in uniq_ids:
            _patch_document_row(db, doc_by_id[uid], meta_only)

        db.commit()
        return {"ok": True, "updated": len(uniq_ids), "document_ids": [str(i) for i in uniq_ids]}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _patch_document_row(db: Session, doc: Document, body: DocumentMetaPatchBody) -> None:
    try:
        new_kb = (
            validate_kb_collection_optional(body.kb_collection)
            if body.kb_collection is not None
            else doc.kb_collection
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.doc_type is not None:
        try:
            new_dtype = normalize_doc_type(body.doc_type)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    else:
        new_dtype = doc.doc_type

    if new_kb != doc.kb_collection and _conflict_same_sha_in_collection(
        db,
        content_sha256=doc.content_sha256,
        kb_collection=new_kb,
        exclude_id=doc.id,
    ):
        raise HTTPException(
            409,
            "目标分区已存在相同内容（content_sha256）的文档，与入库去重规则冲突；请先删除目标分区中的重复文档或仅修改 doc_type",
        )

    doc.kb_collection = new_kb
    doc.doc_type = new_dtype
    _sync_chunks_meta(db, doc.id, new_kb, new_dtype)


@router.patch("/documents/{doc_id}", response_model=DocItem)
def patch_document(doc_id: UUID, body: DocumentMetaPatchBody) -> DocItem:
    """入库后修改文档分区（kb_collection）与/或类型（doc_type）；检索以 Document 为准，并同步各 chunk 的 meta。"""
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        _patch_document_row(db, doc, body)
        db.commit()
        db.refresh(doc)
        cnt = db.execute(select(func.count(Chunk.id)).where(Chunk.document_id == doc.id)).scalar() or 0
        return DocItem(
            id=doc.id,
            title=doc.title,
            source=doc.source,
            kb_collection=doc.kb_collection,
            doc_type=doc.doc_type,
            chunk_count=int(cnt),
            created_at=doc.created_at.isoformat(),
        )
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
