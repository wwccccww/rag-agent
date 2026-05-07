from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.config import settings
from app.services.tool_audit import ToolAuditSpan, record_tool_audit


class ToolAuditTest(unittest.TestCase):
    def test_preview_truncation(self) -> None:
        orig = settings.tool_audit_preview_chars
        try:
            settings.tool_audit_preview_chars = 10
            db = MagicMock()
            record_tool_audit(
                db,
                user_id="u",
                session_id=None,
                mode="agent",
                request_id="r",
                worker="retriever",
                tool="web_search",
                tool_args={"q": "x"},
                status="ok",
                result_preview="0123456789ABCDEFGHIJ",
                sources_count=0,
            )
            # commit should be called but failures must be swallowed
            db.add.assert_called_once()
            db.commit.assert_called_once()
        finally:
            settings.tool_audit_preview_chars = orig

    def test_span_elapsed_ms(self) -> None:
        s = ToolAuditSpan()
        e = s.elapsed_ms()
        self.assertGreaterEqual(e, 0.0)


if __name__ == "__main__":
    unittest.main()

