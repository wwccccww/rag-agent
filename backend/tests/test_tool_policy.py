from __future__ import annotations

import unittest

from app.config import settings
from app.services.tool_policy import get_tool_policy


class ToolPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_level = settings.tool_policy_level
        self._orig_web = settings.web_search_enabled

    def tearDown(self) -> None:
        settings.tool_policy_level = self._orig_level
        settings.web_search_enabled = self._orig_web

    def test_high_is_offline_only(self) -> None:
        settings.tool_policy_level = "high"
        settings.web_search_enabled = True
        p = get_tool_policy()
        self.assertEqual(p.level, "high")
        self.assertIn("search_knowledge_base", p.allowed_tools)
        self.assertNotIn("web_search", p.allowed_tools)
        self.assertNotIn("python_repl", p.allowed_tools)

    def test_medium_blocks_python_allows_fetch(self) -> None:
        settings.tool_policy_level = "medium"
        settings.web_search_enabled = False
        p = get_tool_policy()
        self.assertEqual(p.level, "medium")
        self.assertIn("fetch_url", p.allowed_tools)
        self.assertNotIn("python_repl", p.allowed_tools)
        self.assertNotIn("web_search", p.allowed_tools)

    def test_medium_web_search_toggle(self) -> None:
        settings.tool_policy_level = "medium"
        settings.web_search_enabled = True
        p = get_tool_policy()
        self.assertIn("web_search", p.allowed_tools)

    def test_low_allows_all(self) -> None:
        settings.tool_policy_level = "low"
        settings.web_search_enabled = False
        p = get_tool_policy()
        self.assertIn("python_repl", p.allowed_tools)
        self.assertIn("fetch_url", p.allowed_tools)
        self.assertIn("web_search", p.allowed_tools)


if __name__ == "__main__":
    unittest.main()

