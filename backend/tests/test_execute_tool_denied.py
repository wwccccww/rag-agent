from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.agent import _execute_tool


class ExecuteToolDeniedTest(unittest.TestCase):
    def test_denied_by_override_records_audit(self) -> None:
        db = MagicMock()
        ollama = MagicMock()
        with patch("app.services.agent.record_tool_audit") as rec:
            out, sources = _execute_tool(
                db,
                ollama,
                "demo",
                "search_knowledge_base",
                {"query": "x"},
                5,
                "default",
                None,
                session_id=None,
                mode="multi",
                request_id="r",
                worker="solver",
                allowed_tools_override={"calculate"},
            )
        self.assertIn("策略禁止", out)
        self.assertEqual(sources, [])
        rec.assert_called()
        kwargs = rec.call_args.kwargs
        self.assertEqual(kwargs.get("status"), "denied")
        self.assertEqual(kwargs.get("worker"), "solver")


if __name__ == "__main__":
    unittest.main()

