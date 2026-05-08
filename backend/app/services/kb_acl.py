"""用户维度 kb_collection 授权（企业内部知识库分区隔离）。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.kb import resolve_kb_collection
from app.models import UserKbCollection


def list_allowed_collections(db: Session, user_id: str) -> list[str]:
    rows = db.execute(
        select(UserKbCollection.kb_collection).where(UserKbCollection.user_id == user_id)
    ).scalars().all()
    return sorted({str(r) for r in rows})


def effective_kb_collection(db: Session, user_id: str, requested_raw: str | None) -> str:
    """
    解析并校验当前用户可检索/入库的分区名。
    - KB_ACL_ENABLED=false：与原先 resolve_kb_collection 行为一致。
    - 未传分区：优先 default_kb_collection；若用户无权则回落到其授权列表中的第一项（字典序）。
    """
    if not settings.kb_acl_enabled:
        return resolve_kb_collection(requested_raw)
    allowed = list_allowed_collections(db, user_id)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                f"用户 {user_id!r} 未配置任何可访问的知识库分区；"
                "请在 user_kb_collections 表中授权或调用 POST /v1/kb-access"
            ),
        )
    if requested_raw is None or not str(requested_raw).strip():
        base = resolve_kb_collection(None)
        if base in allowed:
            return base
        return allowed[0]
    coll = resolve_kb_collection(requested_raw)
    if coll not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"无权访问知识库分区 {coll!r}；当前允许: {allowed}",
        )
    return coll


def assert_document_collection_readable(db: Session, user_id: str, doc_kb_collection: str) -> None:
    """文档详情 / 删除 / 改分区前：校验用户对文档所在分区有读权限。"""
    if not settings.kb_acl_enabled:
        return
    allowed = set(list_allowed_collections(db, user_id))
    if doc_kb_collection not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"无权访问该文档所在分区 {doc_kb_collection!r}；当前允许: {sorted(allowed)}",
        )
