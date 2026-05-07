from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.database import SessionLocal
from app.models import ToolAuditLog

router = APIRouter(prefix="/v1", tags=["audit"])


class ToolAuditItem(BaseModel):
    id: UUID
    created_at: str
    user_id: str
    session_id: UUID | None
    mode: str
    request_id: str | None
    worker: str | None = None
    tool: str
    status: str
    elapsed_ms: float | None
    sources_count: int
    tool_args: dict
    error: str | None = None
    result_preview: str | None = None


@router.get("/audit/tools", response_model=list[ToolAuditItem])
def list_tool_audits(
    user_id: str = Query("demo"),
    session_id: UUID | None = Query(default=None),
    request_id: str | None = Query(default=None, max_length=64),
    mode: str | None = Query(default=None, max_length=32),
    worker: str | None = Query(default=None, max_length=32),
    tool: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(100, ge=1, le=500),
) -> list[ToolAuditItem]:
    db = SessionLocal()
    try:
        q = select(ToolAuditLog).where(ToolAuditLog.user_id == user_id)
        if session_id:
            q = q.where(ToolAuditLog.session_id == session_id)
        if request_id:
            q = q.where(ToolAuditLog.request_id == request_id)
        if mode:
            q = q.where(ToolAuditLog.mode == mode)
        if worker:
            q = q.where(ToolAuditLog.worker == worker)
        if tool:
            q = q.where(ToolAuditLog.tool == tool)
        if status:
            q = q.where(ToolAuditLog.status == status)
        q = q.order_by(ToolAuditLog.created_at.desc()).limit(limit)
        rows = db.execute(q).scalars().all()
        return [
            ToolAuditItem(
                id=r.id,
                created_at=r.created_at.isoformat(),
                user_id=r.user_id,
                session_id=r.session_id,
                mode=r.mode,
                request_id=r.request_id,
                worker=r.worker,
                tool=r.tool,
                status=r.status,
                elapsed_ms=r.elapsed_ms,
                sources_count=r.sources_count,
                tool_args=r.tool_args,
                error=r.error,
                result_preview=r.result_preview,
            )
            for r in rows
        ]
    finally:
        db.close()

