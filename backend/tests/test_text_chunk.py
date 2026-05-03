"""text_extract.chunk_text：Markdown 围栏感知与换行切分回归。"""
from __future__ import annotations

import unittest

from app.services.text_extract import chunk_text


class ChunkTextFenceTest(unittest.TestCase):
    def test_short_fence_intact(self) -> None:
        md = "# T\n\nintro\n\n```xml\n<root/>\n```\n\noutro"
        pairs = chunk_text(md, 200, 20, filename="d.md", markdown_fence_aware=True)
        bodies = [c for c, _ in pairs]
        self.assertTrue(any("```" in c and "<root/>" in c for c in bodies))

    def test_oversized_fence_splits_on_newlines_only(self) -> None:
        lines = ["```xml"] + [f"<l>{i}</l>" for i in range(80)] + ["```"]
        md = "## H\n\n" + "\n".join(lines)
        pairs = chunk_text(md, 120, 10, filename="d.md", markdown_fence_aware=True)
        for c, _ in pairs:
            if "<l>0</l>" in c or "<l>40</l>" in c:
                # 子块不应在行中出现半截标签名（若整行很短则整行在块内）
                self.assertNotRegex(c, r"^[a-z]{1,3}>")

    def test_fence_aware_off_uses_legacy_paragraph_path(self) -> None:
        md = "# A\n\n```\nx\n```"
        a = chunk_text(md, 50, 5, filename="d.md", markdown_fence_aware=True)
        b = chunk_text(md, 50, 5, filename="d.md", markdown_fence_aware=False)
        self.assertGreaterEqual(len(a), 1)
        self.assertGreaterEqual(len(b), 1)


if __name__ == "__main__":
    unittest.main()
