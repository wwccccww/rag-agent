from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.agent import _reflect_on_results, _self_ask_decompose


class AgentHelpersTest(unittest.TestCase):
    def test_self_ask_strips_bullets_and_limits(self) -> None:
        ollama = MagicMock()
        ollama.chat_complete.return_value = "• 子问题一是什么？\n- 子问题二是什么？\n3. 子问题三是什么？\n子问题四是什么？\n子问题五是什么？"
        qs = _self_ask_decompose(ollama, "主问题：测试")
        # 只取前 4 个，且剥离前缀
        self.assertEqual(len(qs), 4)
        self.assertTrue(all(q.startswith("子问题") for q in qs))

    def test_reflect_fallback_on_exception(self) -> None:
        ollama = MagicMock()
        ollama.chat_complete.side_effect = RuntimeError("boom")
        ok, raw = _reflect_on_results(ollama, "q", ["[x] y"])
        # 失败时默认 sufficient=True，避免阻塞循环
        self.assertTrue(ok)
        self.assertIn("评估失败", raw)


if __name__ == "__main__":
    unittest.main()

