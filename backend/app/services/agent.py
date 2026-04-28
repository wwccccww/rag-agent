"""
Agent 执行引擎：LLM 自主决策工具调用，ReAct 循环。

流程：
  用户消息
    ↓
  LLM 决策 (chat_with_tools)
    ├── 需要工具 → 执行工具 → 结果注入上下文 → 再次 LLM 决策（最多 3 轮）
    └── 直接回答 → 退出循环
    ↓
  流式生成最终回复 (chat_stream)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy.orm import Session

from app.services.ollama import OllamaClient
from app.services.rag import multi_query_search, search_memories

# ── 工具定义（OpenAI function calling 格式，Ollama 兼容）────────────
AGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "在知识库中搜索与问题相关的文档片段。"
                "用于回答知识性、专业性、文档内容相关的问题。"
                "不要用于询问用户个人信息、闲聊或简单事实（如当前时间）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询词，简洁描述需要查找的内容（不超过 60 字）",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_user_memory",
            "description": (
                "查询用户的个人长期记忆（身份、偏好、技能、项目背景等）。"
                "当用户询问关于自身的问题，或需要结合用户背景来回答时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的记忆关键词",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": "获取当前日期和时间。当用户询问今天是几号、现在几点时使用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_ICONS: dict[str, str] = {
    "search_knowledge_base": "🔍",
    "recall_user_memory": "🧠",
    "get_current_datetime": "🕐",
}

TOOL_LABELS: dict[str, str] = {
    "search_knowledge_base": "搜索知识库",
    "recall_user_memory": "查询记忆",
    "get_current_datetime": "获取时间",
}


def _execute_tool(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    top_k: int,
) -> tuple[str, list[dict]]:
    """执行单个工具，返回 (文本结果, sources列表)。"""
    if tool_name == "search_knowledge_base":
        query = str(tool_args.get("query", "")).strip()
        if not query:
            return "查询词为空，无法搜索。", []
        results = multi_query_search(db, ollama, query, top_k)
        if not results:
            return "知识库中未找到与该查询相关的内容。", []
        parts = [
            f"[S{i}] 来源：{r['source']}\n{r['full_content']}"
            for i, r in enumerate(results, 1)
        ]
        return "\n\n".join(parts), results

    if tool_name == "recall_user_memory":
        query = str(tool_args.get("query", "")).strip()
        lines = search_memories(db, ollama, user_id, query, top_k=5)
        if not lines:
            return "未查找到相关的用户记忆。", []
        return "\n".join(lines), []

    if tool_name == "get_current_datetime":
        now = datetime.now(timezone.utc).strftime("%Y年%m月%d日 %H:%M (UTC)")
        return f"当前时间：{now}", []

    return f"未知工具：{tool_name}", []


def run_agent(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    message: str,
    history: list[dict],
    top_k: int,
    session_summary: str | None,
) -> Generator[dict[str, Any], None, None]:
    """
    Agent 主循环 Generator。

    逐步 yield 事件 dict，最后 yield 一个 type="result" 的结束事件：
      {"type": "agent_step", "step": N, "tool": ..., "status": "calling"|"done", ...}
      {"type": "result", "sources": [...], "messages": [...]}
    """
    system_msg = (
        "你是一个智能问答助手，可以调用工具来帮助回答用户问题。\n"
        "工具调用策略：\n"
        "  - 知识性/专业性问题 → search_knowledge_base\n"
        "  - 用户询问自身情况 → recall_user_memory\n"
        "  - 询问当前时间 → get_current_datetime\n"
        "  - 简单闲聊或已有充足信息 → 直接回答，无需工具\n"
        "可以在单次决策中同时调用多个工具。工具结果会作为上下文帮助你生成更准确的回答。"
    )
    if session_summary:
        system_msg += f"\n\n【历史对话摘要】\n{session_summary}"

    messages: list[dict] = [{"role": "system", "content": system_msg}]
    for m in history:
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    all_sources: list[dict] = []
    seen_chunk_ids: set[str] = set()
    MAX_ITERATIONS = 3

    for iteration in range(MAX_ITERATIONS):
        # ── LLM 决策：选择工具或直接回答 ────────────────────────────
        try:
            assistant_msg = ollama.chat_with_tools(messages, AGENT_TOOLS)
        except Exception as e:
            logging.warning("[Agent] chat_with_tools failed at iter %d: %s", iteration, e)
            break  # 降级：直接用现有 messages 做最终生成

        tool_calls: list[dict] = assistant_msg.get("tool_calls") or []

        if not tool_calls:
            logging.info("[Agent] no tool calls at iter %d → direct answer", iteration)
            break  # LLM 选择直接回答

        # 把 assistant 决策加入消息历史
        messages.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        # ── 执行每个工具 ──────────────────────────────────────────────
        for tc in tool_calls:
            fn = tc.get("function") or {}
            tool_name = fn.get("name", "unknown")
            raw_args = fn.get("arguments", {})
            try:
                tool_args: dict = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            except Exception:
                tool_args = {}

            yield {
                "type": "agent_step",
                "step": iteration + 1,
                "tool": tool_name,
                "icon": TOOL_ICONS.get(tool_name, "⚙️"),
                "label": TOOL_LABELS.get(tool_name, tool_name),
                "status": "calling",
                "args": tool_args,
            }

            try:
                result_text, sources = _execute_tool(
                    db, ollama, user_id, tool_name, tool_args, top_k
                )
            except Exception as e:
                result_text = f"工具执行出错：{e}"
                sources = []
                logging.warning("[Agent] tool %s failed: %s", tool_name, e)

            # 去重合并 sources
            for s in sources:
                sid = str(s.get("chunk_id", ""))
                if sid not in seen_chunk_ids:
                    seen_chunk_ids.add(sid)
                    all_sources.append(s)

            yield {
                "type": "agent_step",
                "step": iteration + 1,
                "tool": tool_name,
                "icon": TOOL_ICONS.get(tool_name, "⚙️"),
                "label": TOOL_LABELS.get(tool_name, tool_name),
                "status": "done",
                "result_summary": result_text[:120] + ("…" if len(result_text) > 120 else ""),
                "source_count": len(sources),
            }

            # 工具结果注入消息历史
            messages.append({"role": "tool", "content": result_text})

    # 最终结束事件（携带 sources 和完整消息历史供流式生成使用）
    yield {
        "type": "result",
        "sources": all_sources[:top_k],
        "messages": messages,
    }
