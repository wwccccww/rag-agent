"""
SSE 事件数据契约（与 frontend/lib/sse.ts 消费端对齐）。

说明：
- 行格式：event: <name>\\ndata: <json>\\n\\n
- `token`：RAG/Agent/Plan 使用 {\"delta\": str}；Multi-Agent synth 使用 {\"t\": str}
"""
from __future__ import annotations

import json
from typing import Any

# 与 consumeSse 一致：收到后即停止读取
TERMINAL_EVENTS: frozenset[str] = frozenset({"final", "error"})


def encode_sse(event: str, data: dict[str, Any], *, default: Any = None) -> str:
    """与 routers 中 _sse 一致：event + JSON data。"""
    kwargs: dict[str, Any] = {"ensure_ascii": False}
    if default is not None:
        kwargs["default"] = default
    return f"event: {event}\ndata: {json.dumps(data, **kwargs)}\n\n"


def parse_sse_blocks(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """
    解析一段 SSE 文本（仅用于测试/契约校验），返回 [(event_name, data_dict), ...]。
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        data_str = "\n".join(data_lines)
        if not data_str:
            continue
        obj = json.loads(data_str)
        if not isinstance(obj, dict):
            raise TypeError(f"SSE data must be JSON object, got {type(obj)}")
        out.append((event_name, obj))
    return out


def validate_sse_payload(event: str, data: dict[str, Any]) -> None:
    """
    校验单条 SSE 事件的 data 是否满足最小契约；不满足则抛 ValueError。
    """
    if event == "error":
        if "message" not in data or not isinstance(data.get("message"), str):
            raise ValueError("error 事件必须包含字符串字段 message")
        return

    if event == "sources":
        if "sources" not in data or not isinstance(data["sources"], list):
            raise ValueError("sources 事件必须包含列表字段 sources")
        return

    if event == "token":
        has_delta = "delta" in data and isinstance(data.get("delta"), str)
        has_t = "t" in data and isinstance(data.get("t"), str)
        if not (has_delta or has_t):
            raise ValueError('token 事件必须包含字符串字段 "delta"（RAG/Agent/Plan）或 "t"（Multi-Agent）')
        return

    if event == "session_created":
        if "session_id" not in data or not isinstance(data.get("session_id"), str):
            raise ValueError("session_created 必须包含字符串 session_id")
        return

    if event == "plan":
        if "goal" not in data:
            raise ValueError("plan 必须包含 goal")
        if "steps" not in data or not isinstance(data["steps"], list):
            raise ValueError("plan 必须包含列表 steps")
        return

    if event == "plan_step_start":
        for k in ("step_id", "description"):
            if k not in data:
                raise ValueError(f"plan_step_start 必须包含 {k}")
        return

    if event == "plan_step_done":
        for k in ("step_id", "description"):
            if k not in data:
                raise ValueError(f"plan_step_done 必须包含 {k}")
        return

    if event == "agent_step":
        for k in ("tool", "status"):
            if k not in data:
                raise ValueError(f"agent_step 必须包含 {k}")
        return

    if event == "ma_plan":
        if "plan" not in data:
            raise ValueError("ma_plan 必须包含 plan")
        return

    if event == "ma_worker_result":
        for k in ("worker", "ok", "text"):
            if k not in data:
                raise ValueError(f"ma_worker_result 必须包含 {k}")
        if not isinstance(data.get("ok"), bool):
            raise ValueError("ma_worker_result.ok 必须为 bool")
        if not isinstance(data.get("text"), str):
            raise ValueError("ma_worker_result.text 必须为 str")
        return

    if event == "final":
        # final 在不同模式下字段不同；至少应为 object
        return

    # 未知事件：不阻断（允许扩展），仅保证是 dict
    return
