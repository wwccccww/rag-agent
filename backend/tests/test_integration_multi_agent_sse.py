"""集成冒烟：Multi-Agent SSE 事件序列（DB/LLM 全 mock）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.multi_agent import WorkerResult


@pytest.mark.integration
def test_multi_agent_stream_sse_sequence(api_client) -> None:
    # 1) mock DB
    sess = MagicMock()
    sess.id = uuid4()
    sess.user_id = "demo"
    sess.updated_at = datetime.now(timezone.utc)

    db = MagicMock()
    db.get.return_value = None

    # add(SessionModel) 后 flush 会给 sess.id，简单起见直接让路由创建的 sess 可用
    def _add(obj):
        # SessionModel 或 Message 都会走这里；不做事即可
        return None

    db.add.side_effect = _add
    db.flush.side_effect = lambda: None
    db.commit.side_effect = lambda: None

    # 2) mock run_multi_agent 输出（不触发真实 agent/检索）
    plan = {"goal": "g", "steps": [{"id": 1, "worker": "retriever", "task": "t", "inputs": {}}]}
    wrs = [
        WorkerResult(worker="retriever", ok=True, text="R", sources=[], steps_trace=[]),
        WorkerResult(worker="critic", ok=True, text="{}", sources=[], steps_trace=[]),
    ]
    synth_msgs = [{"role": "user", "content": "x"}]

    # 3) mock OllamaClient.chat_stream
    class FakeOllama:
        def chat_stream(self, _messages, temperature: float = 0.0) -> Iterator[str]:
            yield "A"
            yield "B"

        def close(self) -> None:  # pragma: no cover
            return None

    with patch("app.routers.multi_agent.SessionLocal", return_value=db), patch(
        "app.routers.multi_agent.OllamaClient", return_value=FakeOllama()
    ), patch("app.routers.multi_agent.run_multi_agent", return_value=(plan, wrs, {}, synth_msgs)):
        r = api_client.post("/v1/chat/multi_agent/stream", json={"user_id": "demo", "message": "hi"})

    assert r.status_code == 200
    raw = r.text
    # 事件名粗校验：必须包含 ma_plan / ma_worker_result / token / final
    assert "event: ma_plan" in raw
    assert "event: ma_worker_result" in raw
    assert "event: token" in raw
    assert "event: final" in raw

    # token 的 data 必须可 JSON 解析，且包含 t
    blocks = [b for b in raw.split("\n\n") if b.strip()]
    token_blocks = [b for b in blocks if b.startswith("event: token")]
    assert token_blocks, "expected token blocks"
    for b in token_blocks:
        line = [ln for ln in b.splitlines() if ln.startswith("data:")][0]
        obj = json.loads(line[5:].strip())
        assert "t" in obj and isinstance(obj["t"], str)

