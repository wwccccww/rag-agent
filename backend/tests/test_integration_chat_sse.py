"""集成冒烟：chat.py 三种 SSE 端点事件序列（DB/LLM 全 mock）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


class FakeOllama:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    def chat_stream(self, _messages, temperature: float = 0.0) -> Iterator[str]:
        for d in self._deltas:
            yield d

    def chat_complete(self, _messages, temperature: float = 0.0) -> str:
        return "标题"

    def close(self) -> None:  # pragma: no cover
        return None


def _mk_db() -> MagicMock:
    sess = MagicMock()
    sess.id = uuid4()
    sess.user_id = "demo"
    sess.summary = None
    sess.updated_at = datetime.now(timezone.utc)

    db = MagicMock()
    db.get.return_value = None
    db.add.side_effect = lambda _obj: None
    db.flush.side_effect = lambda: None
    db.commit.side_effect = lambda: None

    # hist rows：返回空列表即可
    exec_ret = MagicMock()
    exec_ret.scalars.return_value.all.return_value = []
    db.execute.return_value = exec_ret
    return db


@pytest.mark.integration
def test_rag_chat_stream_sse_sequence(api_client) -> None:
    db = _mk_db()
    sources = [{"chunk_id": "1", "source": "doc", "snippet": "x"}]
    with patch("app.routers.chat.SessionLocal", return_value=db), patch(
        "app.routers.chat.OllamaClient", return_value=FakeOllama(["A", "B"])
    ), patch("app.routers.chat.multi_query_search", return_value=sources), patch(
        "app.routers.chat.search_memories", return_value=[]
    ), patch("app.routers.chat.maybe_auto_memory", return_value=None), patch(
        "app.routers.chat.sanitize_assistant_citations", side_effect=lambda t, *_a, **_k: (t, [])
    ):
        r = api_client.post("/v1/chat/stream", json={"user_id": "demo", "message": "hi"})

    assert r.status_code == 200
    raw = r.text
    assert "event: sources" in raw
    assert "event: token" in raw
    assert "event: final" in raw
    # token data must be json with delta
    token_blocks = [b for b in raw.split("\n\n") if b.startswith("event: token")]
    assert token_blocks
    for b in token_blocks[:2]:
        line = [ln for ln in b.splitlines() if ln.startswith("data:")][0]
        obj = json.loads(line[5:].strip())
        assert "delta" in obj and isinstance(obj["delta"], str)


@pytest.mark.integration
def test_agent_chat_stream_sse_sequence(api_client) -> None:
    db = _mk_db()

    def fake_run_agent(**_kwargs):
        yield {"type": "agent_step", "step": 1, "tool": "search_knowledge_base", "status": "calling", "label": "x"}
        yield {"type": "agent_step", "step": 1, "tool": "search_knowledge_base", "status": "done", "label": "x"}
        yield {"type": "result", "sources": [{"chunk_id": "1", "source": "doc", "snippet": "x"}], "messages": [{"role": "user", "content": "q"}], "steps_trace": []}

    with patch("app.routers.chat.SessionLocal", return_value=db), patch(
        "app.routers.chat.OllamaClient", return_value=FakeOllama(["A"])
    ), patch("app.routers.chat.run_agent", side_effect=fake_run_agent), patch(
        "app.routers.chat.maybe_auto_memory", return_value=None
    ), patch("app.routers.chat.sanitize_assistant_citations", side_effect=lambda t, *_a, **_k: (t, [])):
        r = api_client.post("/v1/chat/agent/stream", json={"user_id": "demo", "message": "hi"})

    assert r.status_code == 200
    raw = r.text
    assert "event: agent_step" in raw
    assert "event: sources" in raw
    assert "event: token" in raw
    assert "event: final" in raw


@pytest.mark.integration
def test_plan_execute_chat_stream_sse_sequence(api_client) -> None:
    db = _mk_db()

    def fake_run_plan_execute(**_kwargs):
        yield {"type": "plan", "goal": "g", "steps": [], "plan_ms": 1}
        yield {"type": "plan_step_start", "step_id": 1, "description": "d", "tool": "search_knowledge_base"}
        yield {"type": "agent_step", "step": 1, "tool": "search_knowledge_base", "status": "calling", "label": "x"}
        yield {"type": "plan_step_done", "step_id": 1, "description": "d", "success": True, "result_summary": "ok", "elapsed_ms": 1}
        yield {"type": "result", "sources": [{"chunk_id": "1", "source": "doc", "snippet": "x"}], "messages": [{"role": "user", "content": "q"}], "steps_trace": []}

    with patch("app.routers.chat.SessionLocal", return_value=db), patch(
        "app.routers.chat.OllamaClient", return_value=FakeOllama(["A"])
    ), patch("app.routers.chat.run_plan_execute", side_effect=fake_run_plan_execute), patch(
        "app.routers.chat.maybe_auto_memory", return_value=None
    ), patch("app.routers.chat.sanitize_assistant_citations", side_effect=lambda t, *_a, **_k: (t, [])):
        r = api_client.post("/v1/chat/plan_execute/stream", json={"user_id": "demo", "message": "hi"})

    assert r.status_code == 200
    raw = r.text
    assert "event: plan" in raw
    assert "event: plan_step_start" in raw
    assert "event: plan_step_done" in raw
    assert "event: sources" in raw
    assert "event: token" in raw
    assert "event: final" in raw

