"""用户 — kb_collection 授权管理（演示环境无单独管理员鉴权，生产应接 IAM）。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database import SessionLocal
from app.kb import resolve_kb_collection
from app.models import UserKbCollection

router = APIRouter(prefix="/v1", tags=["kb-access"])


class KbAccessGrantBody(BaseModel):
    user_id: str = Field(..., max_length=128)
    kb_collection: str = Field(..., max_length=64)


@router.get("/kb-access")
def list_kb_access(user_id: str = Query(..., max_length=128)) -> dict[str, list[str]]:
    """列出某 user_id 当前可访问的知识库分区名。"""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(UserKbCollection.kb_collection).where(UserKbCollection.user_id == user_id)
        ).scalars().all()
        return {"kb_collections": sorted({str(r) for r in rows})}
    finally:
        db.close()


@router.post("/kb-access")
def grant_kb_access(body: KbAccessGrantBody) -> dict:
    """为 user_id 增加一个可访问分区（已存在则幂等成功）。"""
    try:
        coll = resolve_kb_collection(body.kb_collection)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db = SessionLocal()
    try:
        existing = db.execute(
            select(UserKbCollection).where(
                UserKbCollection.user_id == body.user_id,
                UserKbCollection.kb_collection == coll,
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(UserKbCollection(user_id=body.user_id, kb_collection=coll))
            db.commit()
        return {"ok": True, "user_id": body.user_id, "kb_collection": coll}
    finally:
        db.close()


@router.delete("/kb-access")
def revoke_kb_access(
    user_id: str = Query(..., max_length=128),
    kb_collection: str = Query(..., max_length=64),
) -> dict:
    """撤销某用户对某分区的访问权限。"""
    try:
        coll = resolve_kb_collection(kb_collection)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db = SessionLocal()
    try:
        row = db.execute(
            select(UserKbCollection).where(
                UserKbCollection.user_id == user_id,
                UserKbCollection.kb_collection == coll,
            )
        ).scalar_one_or_none()
        if row is None:
            return {"ok": True, "deleted": False}
        db.delete(row)
        db.commit()
        return {"ok": True, "deleted": True}
    finally:
        db.close()
