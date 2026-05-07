import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.services.agent import run_agent
from app.services.ollama import OllamaClient

logger = logging.getLogger(__name__)


_PLAN_PROMPT = """\
你是一个多智能体调度器（Supervisor）。请把用户问题拆解成最多 4 个步骤，分配给不同 worker 执行：
- retriever：收集证据（知识库/长期记忆），不得联网，不得执行代码
- solver：计算/推导（可用 calculate 或 python_repl），不得联网，不得访问知识库
- critic：审稿与找漏洞（不调用工具）
- synth：最终汇总（不调用工具）

只输出纯 JSON，不要任何其他文字。
格式：
{
  "goal": "一句话目标",
  "steps": [
    {"id": 1, "worker": "retriever", "task": "...", "inputs": {"query": "..."}},
    {"id": 2, "worker": "solver", "task": "...", "inputs": {"expression": "..."} },
    {"id": 3, "worker": "critic", "task": "...", "inputs": {}},
    {"id": 4, "worker": "synth", "task": "最终回答", "inputs": {}}
  ]
}

约束：
- steps 必须包含 retriever 和 synth；solver/critic 可选
- id 从 1 开始递增
"""


@dataclass(frozen=True)
class WorkerResult:
    worker: str
    ok: bool
    text: str
    sources: list[dict[str, Any]]
    steps_trace: list[dict[str, Any]]


def _parse_plan(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.startswith("```")).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def _run_worker_agent(
    *,
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    session_id: UUID,
    request_id: str,
    worker: str,
    task: str,
    allowed_tools: set[str],
    tool_max_calls: int,
    kb_collection: str | None,
    doc_types: list[str] | None,
) -> WorkerResult:
    """用现有 run_agent 跑一个受限 worker（不同 allowlist/budget）。"""
    steps_trace: list[dict[str, Any]] = []
    all_sources: list[dict[str, Any]] = []
    final_messages: list[dict] = []
    try:
        # 只给 worker 自己需要的 system summary；历史不共享，避免污染
        hist: list[dict] = []
        for event in run_agent(
            db=db,
            ollama=ollama,
            user_id=user_id,
            message=task,
            history=hist,
            top_k=int(getattr(settings, "rag_top_k", 8) or 8),
            session_summary=None,
            kb_collection=kb_collection,
            doc_types=doc_types,
            session_id=session_id,
            request_id=request_id,
            mode="multi",
            worker=worker,
            allowed_tools_override=allowed_tools,
            tool_max_calls_override=tool_max_calls,
        ):
            if event.get("type") == "agent_step":
                steps_trace.append({k: v for k, v in event.items() if k != "type"})
            elif event.get("type") == "result":
                all_sources = event.get("sources", []) or []
                final_messages = event.get("messages", []) or []
        # 将 final_messages 的最后一条 assistant content 作为 worker 输出
        text = ""
        for m in reversed(final_messages):
            if m.get("role") == "assistant":
                text = str(m.get("content") or "")
                break
        if not text:
            text = "(无输出)"
        return WorkerResult(worker=worker, ok=True, text=text, sources=all_sources, steps_trace=steps_trace)
    except Exception as e:
        return WorkerResult(worker=worker, ok=False, text=f"worker failed: {e}", sources=[], steps_trace=steps_trace)


def run_multi_agent(
    *,
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    session_id: UUID,
    request_id: str,
    message: str,
    kb_collection: str | None,
    doc_types: list[str] | None,
) -> tuple[dict[str, Any], list[WorkerResult], dict[str, Any], list[dict[str, str]]]:
    """
    返回：
      plan_obj,
      worker_results（含 retriever/solver/critic 产物与 steps_trace）,
      synth_context（供最终回答）,
      final_messages（可直接给 chat_stream）
    """
    # 1) supervisor 产计划
    raw = ollama.chat_complete_json(
        [{"role": "system", "content": _PLAN_PROMPT}, {"role": "user", "content": message[:2000]}],
        temperature=0.0,
    )
    plan_obj = _parse_plan(raw)

    # 2) 确保至少有 retriever + synth
    steps = plan_obj.get("steps") if isinstance(plan_obj, dict) else None
    if not isinstance(steps, list):
        steps = []
    workers = {str(s.get("worker")) for s in steps if isinstance(s, dict)}
    if "retriever" not in workers:
        steps.insert(0, {"id": 1, "worker": "retriever", "task": "收集与问题相关的证据", "inputs": {"query": message[:60]}})
    if "synth" not in workers:
        steps.append({"id": len(steps) + 1, "worker": "synth", "task": "最终回答", "inputs": {}})
    plan_obj["steps"] = steps

    # 3) 并行跑 retriever + solver（各自独立 db/ollama）
    retriever_task = message
    solver_task = message

    def _spawn(worker: str) -> WorkerResult:
        from app.database import SessionLocal  # noqa: PLC0415
        _db2 = SessionLocal()
        _client2 = OllamaClient()
        try:
            if worker == "retriever":
                allowed = {"search_knowledge_base", "recall_user_memory", "get_current_datetime"}
                # 可选：允许 retriever 联网搜索（同时仍受全局 WEB_SEARCH_ENABLED + TOOL_POLICY_LEVEL 约束）
                if bool(getattr(settings, "multi_retriever_web_search_enabled", False)) and bool(
                    getattr(settings, "web_search_enabled", False)
                ):
                    allowed.add("web_search")
                return _run_worker_agent(
                    db=_db2,
                    ollama=_client2,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    worker="retriever",
                    task=retriever_task,
                    allowed_tools=allowed,
                    tool_max_calls=4,
                    kb_collection=kb_collection,
                    doc_types=doc_types,
                )
            if worker == "solver":
                allowed = {"calculate", "python_repl", "get_current_datetime"}
                return _run_worker_agent(
                    db=_db2,
                    ollama=_client2,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    worker="solver",
                    task=solver_task,
                    allowed_tools=allowed,
                    tool_max_calls=4,
                    kb_collection=None,
                    doc_types=None,
                )
            return WorkerResult(worker=worker, ok=False, text="unknown worker", sources=[], steps_trace=[])
        finally:
            _client2.close()
            _db2.close()

    want_solver = any(isinstance(s, dict) and s.get("worker") == "solver" for s in steps)
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_r = ex.submit(_spawn, "retriever")
        fut_s = ex.submit(_spawn, "solver") if want_solver else None
        r_res = fut_r.result()
        s_res = fut_s.result() if fut_s else None

    worker_results: list[WorkerResult] = [r_res] + ([s_res] if s_res else [])

    # 4) critic（不调用工具）
    critic_prompt = """\
你是严格的审稿人（Critic）。给定用户问题与已收集信息，请输出 JSON：\n
{\n  "conflicts": ["..."],\n  "gaps": ["..."],\n  "suggestions": ["..."]\n}\n
只输出 JSON。"""
    info_block = "\n\n".join([f"[{wr.worker}]\n{wr.text}" for wr in worker_results])
    critic_raw = ollama.chat_complete_json(
        [{"role": "system", "content": critic_prompt}, {"role": "user", "content": f"问题：{message}\n\n已收集信息：\n{info_block}"}],
        temperature=0.0,
    )
    try:
        critic_obj = _parse_plan(critic_raw)
    except Exception:
        critic_obj = {"conflicts": [], "gaps": ["critic 输出解析失败"], "suggestions": []}
    worker_results.append(WorkerResult(worker="critic", ok=True, text=json.dumps(critic_obj, ensure_ascii=False), sources=[], steps_trace=[]))

    synth_context = {
        "question": message,
        "worker_results": [
            {"worker": wr.worker, "ok": wr.ok, "text": wr.text} for wr in worker_results
        ],
        "critic": critic_obj,
    }

    # 5) synth：生成最终 messages（供流式输出）
    synth_system = (
        "你是最终回答生成器（Synthesizer）。你将基于 retriever/solver 的证据与 critic 的缺口提示生成最终回答。\n"
        "若 evidence 不足，明确说明不确定性并给出下一步建议。\n"
        "尽量引用 retriever 给出的 [Sx] 片段编号（如果存在）。"
    )
    final_messages: list[dict[str, str]] = [
        {"role": "system", "content": synth_system},
        {"role": "user", "content": json.dumps(synth_context, ensure_ascii=False)[:8000]},
    ]

    return plan_obj, worker_results, synth_context, final_messages

