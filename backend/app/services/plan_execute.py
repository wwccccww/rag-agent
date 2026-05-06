"""
Plan & Execute Agent 引擎。

流程：
  用户消息
    ↓
  [Phase 1: 规划] LLM 一次性生成结构化子任务计划（JSON）
    → SSE: plan 事件（含 goal + steps[]）
    ↓
  [Phase 2: 逐步执行] 按顺序执行每个带工具的步骤
    → SSE: plan_step_start / plan_step_done 事件
    → 步骤结果累积进共享上下文
    ↓
  [Phase 3: 综合生成] 基于所有步骤结果流式生成最终回复
    → SSE: sources / token* / final 事件（与 agent 模式相同）

与 ReAct Agent 的区别：
  - ReAct：LLM 在每轮工具执行后重新决策（是否继续调用工具）
  - Plan & Execute：LLM 先规划全部步骤，再按计划机械执行，最后综合生成
  - Plan & Execute 更适合需要多步骤、多来源信息收集的复杂任务
"""
import json
import logging
import re
import time
from typing import Any, Generator
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.services.agent import (
    AGENT_TOOLS,
    TOOL_ICONS,
    TOOL_LABELS,
    _execute_tool,  # noqa: PLC2701
)
from app.services.ollama import OllamaClient
from app.services.rag import multi_query_search, search_memories
from app.telemetry import telemetry

# ── 规划提示词 ────────────────────────────────────────────────────────────────
_PLAN_SYSTEM = """你是一个任务规划专家。仔细分析用户的复杂问题，将其拆解为 2-6 个有序子任务。

可用工具：
- search_knowledge_base：在知识库中检索相关文档片段（需要参数 query）
- recall_user_memory：查询用户的个人记忆/背景（需要参数 query）
- get_current_datetime：获取当前时间（无参数）
- web_search：在互联网搜索最新信息（需要参数 query）
- python_repl：执行 Python 代码（需要参数 code）
- fetch_url：抓取网页正文内容（需要参数 url）
- calculate：安全数学表达式求值（需要参数 expression）

输出要求：严格输出以下 JSON 格式，不要包含任何其他文字：
{
  "goal": "一句话描述总体目标（15字以内）",
  "complexity": "simple|medium|complex",
  "steps": [
    {
      "id": 1,
      "description": "步骤描述（20字以内）",
      "tool": "工具名或null",
      "tool_args": {"参数名": "参数值"},
      "purpose": "此步骤目的（10字以内）"
    }
  ]
}

约束规则：
1. steps 数量 2-6 个，最后一步 tool 必须为 null（综合分析步骤，由系统自动生成回复）
2. tool 只能是上面列出的工具名之一，或 null
3. tool_args 只包含该工具所需参数（search_knowledge_base/web_search 用 query，python_repl 用 code，fetch_url 用 url，calculate 用 expression）
4. 若问题简单（单一工具即可解决），steps 只需 2 个：[工具步骤, 综合步骤]
5. complexity 字段：simple=单一信息来源，medium=需要2-3个来源，complex=需要4个以上来源或代码
"""

_PLAN_USER_TMPL = "请为以下问题制定执行计划：\n\n{message}"

# ── 备用规划（JSON 解析失败时降级）─────────────────────────────────────────────
_FALLBACK_PLAN_SYSTEM = """你是一个任务规划专家。为用户问题制定一个简短计划。

输出格式（每行一个步骤，格式：序号. [工具名或无工具] 步骤描述）：
1. [search_knowledge_base] 查询知识库中的相关内容
2. [无工具] 综合分析并生成回答

可用工具同上。最多 5 个步骤，最后一步必须是[无工具]。"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON 对象，支持被 markdown 围栏包裹的情况。"""
    text = text.strip()
    # 去除 markdown 代码围栏
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # 直接尝试解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 提取第一个 {...} 块
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    return None


def _validate_plan(plan: dict) -> list[dict]:
    """校验并规范化计划步骤，返回合法步骤列表。"""
    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return []

    valid_tools = {t["function"]["name"] for t in AGENT_TOOLS}
    steps: list[dict] = []

    for i, s in enumerate(raw_steps[:settings.plan_max_steps], start=1):
        if not isinstance(s, dict):
            continue
        tool = s.get("tool")
        if tool is not None and tool not in valid_tools:
            tool = None  # 未知工具降级为无工具
        tool_args: dict = {}
        if isinstance(s.get("tool_args"), dict):
            tool_args = {k: str(v) for k, v in s["tool_args"].items()}

        steps.append({
            "id": i,
            "description": str(s.get("description", f"步骤 {i}"))[:60],
            "tool": tool,
            "tool_args": tool_args,
            "purpose": str(s.get("purpose", ""))[:40],
        })

    return steps


def _generate_plan(
    ollama: OllamaClient,
    message: str,
    history: list[dict],
    session_summary: str | None,
) -> tuple[str, list[dict]]:
    """
    调用 LLM 生成结构化计划。
    返回 (goal, steps)。失败时返回默认的双步计划。
    """
    # 附加历史摘要供规划参考
    system = _PLAN_SYSTEM
    if session_summary:
        system += f"\n\n【历史对话摘要】\n{session_summary}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _PLAN_USER_TMPL.format(message=message)},
    ]

    try:
        raw = ollama.chat_complete_json(messages, temperature=0.0)
        plan = _extract_json(raw)
        if plan:
            steps = _validate_plan(plan)
            if len(steps) >= 2:
                goal = str(plan.get("goal", "完成任务"))[:80]
                logging.info("[PlanExec] plan generated: goal=%r steps=%d", goal, len(steps))
                return goal, steps
            logging.warning("[PlanExec] plan validation failed (steps=%d), using fallback", len(steps))
        else:
            logging.warning("[PlanExec] JSON extraction failed, raw=%r", raw[:200])
    except Exception as e:
        logging.warning("[PlanExec] plan generation error: %s", e)

    # 降级：简单双步计划（直接搜索 + 综合）
    fallback_steps = [
        {
            "id": 1,
            "description": "搜索知识库",
            "tool": "search_knowledge_base",
            "tool_args": {"query": message[:60]},
            "purpose": "收集相关信息",
        },
        {
            "id": 2,
            "description": "综合生成回答",
            "tool": None,
            "tool_args": {},
            "purpose": "整合分析",
        },
    ]
    return "回答用户问题", fallback_steps


def run_plan_execute(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    message: str,
    history: list[dict],
    top_k: int,
    session_summary: str | None,
    kb_collection: str | None = None,
    doc_types: list[str] | None = None,
    session_id: UUID | None = None,
    request_id: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Plan & Execute 主循环 Generator。

    依次 yield 事件：
      {"type": "plan", "goal": ..., "steps": [...]}
      {"type": "plan_step_start", "step_id": N, "description": ..., "tool": ...}
      {"type": "agent_step", ...}           # 工具调用子事件（复用 agent_step 类型）
      {"type": "plan_step_done", "step_id": N, "description": ...,
       "success": bool, "result_summary": ..., "elapsed_ms": N}
      {"type": "result", "sources": [...], "messages": [...], "steps_trace": [...]}
    """
    # ── Phase 1: 生成计划 ─────────────────────────────────────────────────────
    t_plan = time.perf_counter()
    goal, steps = _generate_plan(ollama, message, history, session_summary)
    plan_ms = int((time.perf_counter() - t_plan) * 1000)
    telemetry.record_timing("plan_execute.plan_ms", plan_ms)
    logging.info("[PlanExec] plan ready in %dms: %d steps", plan_ms, len(steps))

    yield {
        "type": "plan",
        "goal": goal,
        "steps": steps,
        "plan_ms": plan_ms,
    }

    # ── Phase 2: 逐步执行工具步骤 ─────────────────────────────────────────────
    all_sources: list[dict] = []
    seen_chunk_ids: set[str] = set()
    steps_trace: list[dict] = []
    context_parts: list[str] = []  # 各步骤结果文本，注入最终生成上下文

    for step in steps:
        tool = step["tool"]
        step_id = step["id"]
        description = step["description"]
        tool_args = step.get("tool_args") or {}

        # 无工具步骤（综合分析步骤）跳过执行，由最终生成处理
        if tool is None:
            logging.info("[PlanExec] step %d [no-tool] skipped: %r", step_id, description)
            continue

        yield {
            "type": "plan_step_start",
            "step_id": step_id,
            "description": description,
            "tool": tool,
        }

        # 发送工具调用中事件（前端复用 agent_step 渲染）
        yield {
            "type": "agent_step",
            "step": step_id,
            "tool": tool,
            "icon": TOOL_ICONS.get(tool, "⚙️"),
            "label": TOOL_LABELS.get(tool, tool),
            "status": "calling",
            "args": tool_args,
            "reasoning": step.get("purpose", ""),
        }

        t0 = time.perf_counter()
        try:
            result_text, sources = _execute_tool(
                db=db,
                ollama=ollama,
                user_id=user_id,
                tool_name=tool,
                tool_args=tool_args,
                top_k=top_k,
                kb_collection=kb_collection,
                doc_types=doc_types,
                session_id=session_id,
                mode="plan",
                request_id=request_id,
            )
            success = True
        except Exception as e:
            result_text = f"工具执行出错：{e}"
            sources = []
            success = False
            logging.warning("[PlanExec] step %d tool %s failed: %s", step_id, tool, e)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        telemetry.record_tool_exec(tool, elapsed_ms)
        telemetry.record_timing("plan_execute.tool_total_ms", elapsed_ms)

        # 去重合并来源
        for s in sources:
            sid = str(s.get("chunk_id", ""))
            if sid not in seen_chunk_ids:
                seen_chunk_ids.add(sid)
                all_sources.append(s)

        result_summary = result_text[:150] + ("…" if len(result_text) > 150 else "")
        context_parts.append(f"[步骤 {step_id}：{description}]\n{result_text}")

        # 工具完成事件
        yield {
            "type": "agent_step",
            "step": step_id,
            "tool": tool,
            "icon": TOOL_ICONS.get(tool, "⚙️"),
            "label": TOOL_LABELS.get(tool, tool),
            "status": "done",
            "result_summary": result_summary,
            "source_count": len(sources),
            "elapsed_ms": elapsed_ms,
            "reasoning": step.get("purpose", ""),
        }

        yield {
            "type": "plan_step_done",
            "step_id": step_id,
            "description": description,
            "success": success,
            "result_summary": result_summary,
            "elapsed_ms": elapsed_ms,
        }

        steps_trace.append({
            "step": step_id,
            "tool": tool,
            "icon": TOOL_ICONS.get(tool, "⚙️"),
            "label": TOOL_LABELS.get(tool, tool),
            "args": tool_args,
            "result_summary": result_summary,
            "source_count": len(sources),
            "elapsed_ms": elapsed_ms,
            "reasoning": step.get("purpose", ""),
        })

    # ── Phase 3: 构建最终生成上下文 ───────────────────────────────────────────
    # 构建系统提示：包含计划目标、各步骤结果、以及 RAG 来源引用规则
    context_block = "\n\n".join(context_parts) if context_parts else "(无收集到的信息)"

    # 构建 sources 块（供引用）
    rag_lines: list[str] = []
    for i, s in enumerate(all_sources[:top_k], start=1):
        src = s.get("source") or "unknown"
        page = s.get("page")
        pg = f" p.{page}" if page else ""
        sec = s.get("section_heading")
        sec_clean = sec.lstrip("#").strip() if isinstance(sec, str) else ""
        sec_part = f" · 节：{sec_clean}" if sec_clean else ""
        body = s.get("full_content", s.get("snippet", ""))
        rag_lines.append(f"[S{i}] ({src}{pg}{sec_part})\n{body}")

    has_rag = bool(rag_lines)
    rag_block = "\n\n".join(rag_lines) if rag_lines else "(无知识库片段)"

    synthesis_system = (
        f"你是一个智能助手，正在根据以下计划的执行结果综合生成最终回答。\n\n"
        f"【计划目标】{goal}\n\n"
        f"【各步骤执行结果】\n{context_block}\n\n"
        + (f"【知识库原始片段】（共 {len(rag_lines)} 条）\n{rag_block}\n\n" if has_rag else "")
        + (
            "【引用规则】回答末尾用 [S1][S2] 标注引用的片段编号；"
            "每个字段/路径必须能在片段中原文找到，找不到就不写。\n\n"
            if has_rag else ""
        )
        + "请基于以上信息，综合、全面地回答用户的原始问题。"
    )

    if session_summary:
        synthesis_system += f"\n\n【历史对话摘要】\n{session_summary}"

    # 构建消息历史（历史 + 用户消息 + 综合指令）
    final_messages: list[dict] = [{"role": "system", "content": synthesis_system}]
    for m in history:
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            final_messages.append({"role": role, "content": content})
    final_messages.append({"role": "user", "content": message})

    yield {
        "type": "result",
        "sources": all_sources[:top_k],
        "messages": final_messages,
        "steps_trace": steps_trace,
        "plan_goal": goal,
        "plan_steps": steps,
    }
