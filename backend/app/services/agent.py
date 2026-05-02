"""
Agent 执行引擎：LLM 自主决策工具调用，ReAct 循环。

流程：
  用户消息
    ↓
  LLM 决策 (chat_with_tools)
    ├── 有推理文本 → 展示 Thought（ReAct 可视化）
    ├── 需要工具   → 执行工具 → 结果注入上下文 → 再次 LLM 决策（最多 4 轮）
    └── 直接回答   → 退出循环
    ↓
  流式生成最终回复 (chat_stream)
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy.orm import Session

from app.services.ollama import OllamaClient
from app.services.rag import multi_query_search, search_memories
from app.telemetry import telemetry

# ── Web Search（多后端 + 优雅降级）──────────────────────────────────────────
def _web_search(query: str, max_results: int = 5) -> str:
    """
    搜索优先级：
      1. Tavily API（需 TAVILY_API_KEY，效果最好）
      2. SearXNG 自建实例（需 SEARXNG_URL，国内可用）
      3. DuckDuckGo（国内可能被屏蔽，作为 fallback）
    全部失败则返回提示，让 LLM 用自身知识作答。
    """
    from app.config import settings  # noqa: PLC0415

    timeout = settings.web_search_timeout

    # ── 1. Tavily ─────────────────────────────────────────────────────────────
    if settings.tavily_api_key:
        try:
            import httpx  # noqa: PLC0415
            r = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": settings.tavily_api_key, "query": query, "max_results": max_results},
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("results", [])
            if items:
                parts = [
                    f"[W{i}] {it.get('title', '')}\n{it.get('content', '')}\n来源: {it.get('url', '')}"
                    for i, it in enumerate(items, 1)
                ]
                return "\n\n".join(parts)
        except Exception as e:
            logging.warning("[Agent] Tavily search failed: %s", e)

    # ── 2. SearXNG ────────────────────────────────────────────────────────────
    if settings.searxng_url:
        try:
            import httpx  # noqa: PLC0415
            base = settings.searxng_url.rstrip("/")
            r = httpx.get(
                f"{base}/search",
                params={"q": query, "format": "json", "language": "zh-CN"},
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("results", [])[:max_results]
            if items:
                parts = [
                    f"[W{i}] {it.get('title', '')}\n{it.get('content', it.get('snippet', ''))}\n来源: {it.get('url', '')}"
                    for i, it in enumerate(items, 1)
                ]
                return "\n\n".join(parts)
        except Exception as e:
            logging.warning("[Agent] SearXNG search failed: %s", e)

    # ── 3. DuckDuckGo（国内可能不通）────────────────────────────────────────
    try:
        from duckduckgo_search import DDGS  # noqa: PLC0415
        with DDGS(timeout=timeout) as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if results:
            parts = [
                f"[W{i}] {r['title']}\n{r['body']}\n来源: {r['href']}"
                for i, r in enumerate(results, 1)
            ]
            return "\n\n".join(parts)
        return "网络搜索未返回结果，请基于已有知识回答。"
    except ImportError:
        pass
    except Exception as e:
        logging.warning("[Agent] DuckDuckGo search failed: %s", e)

    return (
        "⚠️ 网络搜索暂不可用（可能被防火墙屏蔽）。"
        "请基于你的训练知识直接回答，并告知用户信息可能不是最新的。"
    )


# ── 工具定义（OpenAI function calling 格式，Ollama 兼容）──────────────────────
AGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "在知识库中搜索与问题相关的文档片段。"
                "用于回答知识性、专业性、文档内容相关的问题。"
                "不要用于询问用户个人信息、闲聊或简单事实（如当前时间）。"
                "检索范围（分区/文档类型）由系统根据当前会话固定，只能通过 query 指定检索词，不要尝试传入其它范围参数。"
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
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "在互联网上搜索最新信息。"
                "适用于：知识库中没有相关内容、需要实时新闻/价格/事件、"
                "或用户明确要求搜索网络时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（建议使用中文或英文，不超过 60 字）",
                    }
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_ICONS: dict[str, str] = {
    "search_knowledge_base": "🔍",
    "recall_user_memory": "🧠",
    "get_current_datetime": "🕐",
    "web_search": "🌐",
}

TOOL_LABELS: dict[str, str] = {
    "search_knowledge_base": "搜索知识库",
    "recall_user_memory": "查询记忆",
    "get_current_datetime": "获取时间",
    "web_search": "网络搜索",
}


def _execute_tool(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    top_k: int,
    kb_collection: str | None,
    doc_types: list[str] | None,
) -> tuple[str, list[dict]]:
    """执行单个工具，返回 (文本结果, sources列表)。"""
    if tool_name == "search_knowledge_base":
        query = str(tool_args.get("query", "")).strip()
        if not query:
            return "查询词为空，无法搜索。", []
        results = multi_query_search(db, ollama, query, top_k, kb_collection, doc_types)
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

    if tool_name == "web_search":
        query = str(tool_args.get("query", "")).strip()
        if not query:
            return "查询词为空，无法搜索。", []
        return _web_search(query), []

    return f"未知工具：{tool_name}", []


def run_agent(
    db: Session,
    ollama: OllamaClient,
    user_id: str,
    message: str,
    history: list[dict],
    top_k: int,
    session_summary: str | None,
    kb_collection: str | None = None,
    doc_types: list[str] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Agent 主循环 Generator。

    逐步 yield 事件 dict，最后 yield 一个 type="result" 的结束事件：
      {"type": "agent_step", "step": N, "tool": ..., "status": "calling"|"done",
       "reasoning": "...", ...}
      {"type": "result", "sources": [...], "messages": [...], "steps_trace": [...]}
    """
    system_msg = (
        "你是一个智能问答助手，可以调用工具来帮助回答用户问题。\n"
        "工具调用策略：\n"
        "  - 知识性/专业性问题 → search_knowledge_base\n"
        "  - 用户询问自身情况 → recall_user_memory\n"
        "  - 询问当前时间 → get_current_datetime\n"
        "  - 需要互联网最新信息 → web_search\n"
        "  - 简单闲聊或已有充足信息 → 直接回答，无需工具\n\n"
        "在调用工具前，先用一句话说明你的推理思路（'我需要...'）。\n"
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
    steps_trace: list[dict] = []  # 持久化轨迹，随 result 事件一起返回
    MAX_ITERATIONS = 4

    for iteration in range(MAX_ITERATIONS):
        # ── LLM 决策：选择工具或直接回答 ──────────────────────────────────
        try:
            assistant_msg = ollama.chat_with_tools(messages, AGENT_TOOLS)
        except Exception as e:
            logging.warning("[Agent] chat_with_tools failed at iter %d: %s", iteration, e)
            break  # 降级：直接用现有 messages 做最终生成

        # ── P3: 捕获推理文本（部分模型在 content 中输出 CoT 思考）──────────
        reasoning: str = (assistant_msg.get("content") or "").strip()

        tool_calls: list[dict] = assistant_msg.get("tool_calls") or []

        if not tool_calls:
            logging.info("[Agent] no tool calls at iter %d → direct answer", iteration)
            break  # LLM 选择直接回答

        # 把 assistant 决策加入消息历史
        messages.append({
            "role": "assistant",
            "content": reasoning,
            "tool_calls": tool_calls,
        })

        # ── 执行每个工具 ────────────────────────────────────────────────────
        for tc in tool_calls:
            fn = tc.get("function") or {}
            tool_name = fn.get("name", "unknown")
            raw_args = fn.get("arguments", {})
            try:
                tool_args: dict = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            except Exception:
                tool_args = {}

            # 调用中事件（附带推理文本）
            calling_event: dict[str, Any] = {
                "type": "agent_step",
                "step": iteration + 1,
                "tool": tool_name,
                "icon": TOOL_ICONS.get(tool_name, "⚙️"),
                "label": TOOL_LABELS.get(tool_name, tool_name),
                "status": "calling",
                "args": tool_args,
                "reasoning": reasoning,
            }
            yield calling_event

            t0 = time.perf_counter()
            try:
                result_text, sources = _execute_tool(
                    db, ollama, user_id, tool_name, tool_args, top_k, kb_collection, doc_types
                )
            except Exception as e:
                result_text = f"工具执行出错：{e}"
                sources = []
                logging.warning("[Agent] tool %s failed: %s", tool_name, e)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            telemetry.record_tool_exec(tool_name, elapsed_ms)
            telemetry.record_timing("agent.tool_total_ms", elapsed_ms)
            elapsed_ms = int(elapsed_ms)

            # 去重合并 sources
            for s in sources:
                sid = str(s.get("chunk_id", ""))
                if sid not in seen_chunk_ids:
                    seen_chunk_ids.add(sid)
                    all_sources.append(s)

            # 完成事件
            done_event: dict[str, Any] = {
                "type": "agent_step",
                "step": iteration + 1,
                "tool": tool_name,
                "icon": TOOL_ICONS.get(tool_name, "⚙️"),
                "label": TOOL_LABELS.get(tool_name, tool_name),
                "status": "done",
                "result_summary": result_text[:150] + ("…" if len(result_text) > 150 else ""),
                "source_count": len(sources),
                "elapsed_ms": elapsed_ms,
                "reasoning": reasoning,
            }
            yield done_event

            # 记录轨迹（用于持久化）
            steps_trace.append({
                "step": iteration + 1,
                "tool": tool_name,
                "icon": TOOL_ICONS.get(tool_name, "⚙️"),
                "label": TOOL_LABELS.get(tool_name, tool_name),
                "args": tool_args,
                "result_summary": done_event["result_summary"],
                "source_count": len(sources),
                "elapsed_ms": elapsed_ms,
                "reasoning": reasoning,
            })

            # 工具结果注入消息历史
            messages.append({"role": "tool", "content": result_text})

    # 最终结束事件（携带 sources、完整消息历史和轨迹供持久化）
    yield {
        "type": "result",
        "sources": all_sources[:top_k],
        "messages": messages,
        "steps_trace": steps_trace,
    }
