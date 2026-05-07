import logging
import time
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ToolAuditLog
from app.services.tool_policy import sha256_json

logger = logging.getLogger(__name__)


def _preview(text: str | None) -> str | None:
    if text is None:
        return None
    s = str(text)
    n = int(getattr(settings, "tool_audit_preview_chars", 800) or 800)
    if len(s) <= n:
        return s
    return s[:n] + f"\n…（已截断，共 {len(s)} 字符）"


def record_tool_audit(
    db: Session,
    *,
    user_id: str,
    session_id: UUID | None,
    mode: str,
    request_id: str | None,
    worker: str | None = None,
    tool: str,
    tool_args: dict[str, Any],
    status: str,
    error: str | None = None,
    elapsed_ms: float | None = None,
    result_preview: str | None = None,
    sources_count: int = 0,
) -> None:
    try:
        row = ToolAuditLog(
            user_id=user_id,
            session_id=session_id,
            mode=(mode or "agent")[:32],
            request_id=(request_id[:64] if isinstance(request_id, str) and request_id else None),
            worker=(worker[:32] if isinstance(worker, str) and worker else None),
            tool=(tool or "unknown")[:64],
            tool_args=tool_args or {},
            args_sha256=sha256_json(tool_args or {}),
            status=(status or "ok")[:16],
            error=_preview(error),
            elapsed_ms=elapsed_ms,
            result_preview=_preview(result_preview),
            sources_count=int(sources_count or 0),
        )
        db.add(row)
        db.commit()
    except Exception as e:
        # 审计落库失败不能影响主流程
        logger.warning("[Audit] record_tool_audit failed: %s", e)


class ToolAuditSpan:
    """工具调用 span：自动统计耗时，调用方负责写入成功/失败状态。"""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

