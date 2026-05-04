"""text_extract.chunk_text：Markdown 围栏感知与换行切分回归。"""
from __future__ import annotations

import unittest

from app.services.text_extract import _markdown_section_tuples, chunk_text


class ChunkTextFenceTest(unittest.TestCase):
    def test_nested_headings_produce_breadcrumb(self) -> None:
        """#### 与 ##### 并存时节名含父级，便于 API 文档锚定。"""
        md = """## 第二章 积分

#### 2.3 修改积分规则

##### 2.3.2 请求参数

| id | number |
| -- | ------ |

##### 2.3.3 响应数据

| code | number |
"""
        tuples = _markdown_section_tuples(md)
        crumbs = {b for _, b in tuples if b}
        self.assertTrue(any("2.3 修改积分规则" in c and "2.3.2 请求参数" in c for c in crumbs))
        self.assertTrue(any("2.3 修改积分规则" in c and "2.3.3 响应数据" in c for c in crumbs))
        pairs = chunk_text(md, 400, 40, filename="api.md", markdown_fence_aware=False)
        heads = [m.get("section_heading", "") for _, m in pairs]
        self.assertTrue(any("2.3 修改积分规则" in h and "2.3.2" in h for h in heads))

    def test_short_intro_merged_into_following_fence(self) -> None:
        """短「例如：」类引言与紧随围栏合并，避免单独小文本块。"""
        md = "## 五、一对多查询\n\n使用 `<collection>` 来进行连接，例如：\n\n```xml\n<a/>\n```\n"
        pairs = chunk_text(md, 720, 90, filename="d.md", markdown_fence_aware=True, merge_intro_before_fence_max_chars=320)
        bodies = [c for c, _ in pairs]
        self.assertTrue(any("一对多" in c and "<a/>" in c and "```" in c for c in bodies))

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

    def test_oversized_fence_continuation_gets_section_prefix(self) -> None:
        """超长围栏第 2 段起带 [节：… · 续]（需节标题）。"""
        lines = ["## 五、演示", "", "```xml"] + [f"<l>{i}</l>" for i in range(120)] + ["```"]
        md = "\n".join(lines)
        pairs = chunk_text(
            md,
            200,
            10,
            filename="d.md",
            markdown_fence_aware=True,
            merge_intro_before_fence_max_chars=0,
            fence_continuation_prefix=True,
            continuation_title_max_chars=40,
        )
        bodies = [c for c, _ in pairs]
        cont = [b for b in bodies if b.lstrip().startswith("[节：") and "· 续]" in b[:30]]
        self.assertTrue(cont, "expected at least one continuation-prefixed chunk")
        self.assertIn("五、演示", cont[0])

    def test_fence_aware_off_uses_legacy_paragraph_path(self) -> None:
        md = "# A\n\n```\nx\n```"
        a = chunk_text(md, 50, 5, filename="d.md", markdown_fence_aware=True)
        b = chunk_text(md, 50, 5, filename="d.md", markdown_fence_aware=False)
        self.assertGreaterEqual(len(a), 1)
        self.assertGreaterEqual(len(b), 1)


if __name__ == "__main__":
    unittest.main()
