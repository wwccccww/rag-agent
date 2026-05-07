from __future__ import annotations

import unittest

from app.services.agent import _extract_web_sources


class WebSourcesTest(unittest.TestCase):
    def test_extracts_multiple_blocks(self) -> None:
        raw = (
            "[W1] 标题1\n"
            "内容A\n"
            "来源: https://a.example\n\n"
            "[W2] 标题2\n"
            "内容B1\n内容B2\n"
            "来源: https://b.example\n"
        )
        out = _extract_web_sources(raw)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["kind"], "web")
        self.assertEqual(out[0]["url"], "https://a.example")
        self.assertIn("内容A", out[0]["snippet"])
        self.assertEqual(out[1]["url"], "https://b.example")
        self.assertIn("内容B2", out[1]["snippet"])

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_extract_web_sources(""), [])


if __name__ == "__main__":
    unittest.main()

