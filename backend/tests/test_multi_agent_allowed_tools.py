from __future__ import annotations

import unittest

from app.config import settings
from app.services.multi_agent import get_retriever_allowed_tools, get_solver_allowed_tools


class MultiAgentAllowedToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_multi = getattr(settings, "multi_retriever_web_search_enabled", False)
        self._orig_web = getattr(settings, "web_search_enabled", False)

    def tearDown(self) -> None:
        settings.multi_retriever_web_search_enabled = self._orig_multi
        settings.web_search_enabled = self._orig_web

    def test_retriever_default_no_web(self) -> None:
        settings.web_search_enabled = True
        settings.multi_retriever_web_search_enabled = False
        allowed = get_retriever_allowed_tools()
        self.assertIn("search_knowledge_base", allowed)
        self.assertNotIn("web_search", allowed)

    def test_retriever_web_requires_both_toggles(self) -> None:
        settings.web_search_enabled = True
        settings.multi_retriever_web_search_enabled = True
        allowed = get_retriever_allowed_tools()
        self.assertIn("web_search", allowed)

        settings.web_search_enabled = False
        settings.multi_retriever_web_search_enabled = True
        allowed2 = get_retriever_allowed_tools()
        self.assertNotIn("web_search", allowed2)

    def test_solver_never_has_kb_or_web(self) -> None:
        allowed = get_solver_allowed_tools()
        self.assertIn("calculate", allowed)
        self.assertNotIn("search_knowledge_base", allowed)
        self.assertNotIn("web_search", allowed)


if __name__ == "__main__":
    unittest.main()

