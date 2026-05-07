"""契约测试：SSE 行格式与事件 data 最小字段集。"""
from __future__ import annotations

import json
import unittest
import uuid

from app.contracts.sse_events import (
    TERMINAL_EVENTS,
    encode_sse,
    parse_sse_blocks,
    validate_sse_payload,
)
from app.routers.chat import _sse as chat_sse
from app.routers.multi_agent import _sse as multi_sse


class SseContractTest(unittest.TestCase):
    def test_terminal_events_set(self) -> None:
        self.assertIn("final", TERMINAL_EVENTS)
        self.assertIn("error", TERMINAL_EVENTS)

    def test_parse_roundtrip(self) -> None:
        raw = (
            encode_sse("sources", {"session_id": "x", "sources": []})
            + encode_sse("token", {"delta": "hi"})
            + encode_sse("final", {"session_id": "x"})
        )
        events = parse_sse_blocks(raw)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0][0], "sources")
        self.assertEqual(events[1][1]["delta"], "hi")

    def test_validate_token_delta_or_t(self) -> None:
        validate_sse_payload("token", {"delta": "a"})
        validate_sse_payload("token", {"t": "b"})
        with self.assertRaises(ValueError):
            validate_sse_payload("token", {})

    def test_chat_sse_json_serializable(self) -> None:
        s = chat_sse("sources", {"session_id": str(uuid.uuid4()), "sources": [{"chunk_id": "1"}]})
        self.assertTrue(s.startswith("event: sources\n"))
        line = [ln for ln in s.split("\n") if ln.startswith("data:")][0]
        obj = json.loads(line[5:].strip())
        validate_sse_payload("sources", obj)

    def test_multi_sse_uuid_uses_default_str(self) -> None:
        sid = uuid.uuid4()
        s = multi_sse("ma_worker_result", {"request_id": "abc", "sources": [{"chunk_id": sid}]})
        line = [ln for ln in s.split("\n") if ln.startswith("data:")][0]
        obj = json.loads(line[5:].strip())
        self.assertIsInstance(obj["sources"][0]["chunk_id"], str)

    def test_sample_plan_and_agent_step(self) -> None:
        validate_sse_payload("plan", {"goal": "g", "steps": [], "plan_ms": 0})
        validate_sse_payload("plan_step_start", {"step_id": 1, "description": "d", "tool": None})
        validate_sse_payload(
            "plan_step_done",
            {"step_id": 1, "description": "d", "success": True, "result_summary": "", "elapsed_ms": 0},
        )
        validate_sse_payload(
            "agent_step",
            {"step": 1, "tool": "search_knowledge_base", "status": "done", "label": "x"},
        )


if __name__ == "__main__":
    unittest.main()
