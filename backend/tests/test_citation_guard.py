"""citation_guard：引用与正文短语对齐。"""
from __future__ import annotations

import unittest

from app.services.citation_guard import sanitize_assistant_citations


class CitationGuardTest(unittest.TestCase):
    def test_removes_cite_when_no_term_overlap(self) -> None:
        ans = "结论见表，请求参数含 id 与 ruleName。\n\n引用：[S1]、[S2]"
        sources = [
            {"full_content": "积分规则 id ruleName 请求参数"},
            {"full_content": "Mybatis 一级缓存 二级缓存 数据库"},
        ]
        out, removed = sanitize_assistant_citations(
            ans,
            sources,
            enabled=True,
            min_hits=2,
            min_term_frac=0.02,
            max_source_terms=50,
        )
        self.assertIn(2, removed)
        self.assertNotIn("[S2]", out)
        self.assertIn("[S1]", out)

    def test_keeps_cite_when_terms_overlap(self) -> None:
        ans = "请求参数含 id 与 ruleName。\n\n引用：[S1]"
        sources = [{"full_content": "##### 请求参数\n\n| id | ruleName |\n|---|---|"}]
        out, removed = sanitize_assistant_citations(
            ans,
            sources,
            enabled=True,
            min_hits=2,
            min_term_frac=0.02,
            max_source_terms=50,
        )
        self.assertEqual(removed, [])
        self.assertIn("[S1]", out)

    def test_disabled_noop(self) -> None:
        t = "引用：[S9]"
        out, removed = sanitize_assistant_citations(
            t,
            [{"snippet": "x"}],
            enabled=False,
            min_hits=2,
            min_term_frac=0.02,
            max_source_terms=50,
        )
        self.assertEqual(out, t)
        self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
