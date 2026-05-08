"""问答审计：对话轮次结束时写入 qa_audit_logs（失败不影响主流程）。"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models import QaAuditLog


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def chunk_ids_from_sources(sources: list[dict] | None) -> list[str]:
    if not sources:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in sources:
        cid = s.get("chunk_id")
        if cid is None:
            continue
        t = str(cid)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def try_record_qa_audit(
    db: Session,
    *,
    user_id: str,
    session_id: UUID | None,
    kb_collection: str,
    mode: str,
    request_id: str | None,
    user_message: str,
    assistant_content: str | None,
    sources: list[dict] | None,
) -> None:
    if not getattr(settings, "qa_audit_enabled", True):
        return
    try:
        chunk_ids = chunk_ids_from_sources(sources)
        row = QaAuditLog(
            user_id=user_id[:128],
            session_id=session_id,
            kb_collection=kb_collection[:64],
            mode=mode[:32],
            request_id=request_id[:64] if request_id else None,
            user_message=_truncate(user_message, 8000),
            assistant_preview=_truncate(assistant_content, 4000) if assistant_content and assistant_content.strip() else None,
            cited_chunk_ids=chunk_ids,
            sources_count=len(chunk_ids),
        )
        db.add(row)
        db.commit()
    except Exception as e:
        logging.warning("[QaAudit] record failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
