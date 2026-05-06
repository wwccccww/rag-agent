import json
import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models import Message, SessionModel
from app.schemas import AgentChatRequest, ChatStreamRequest, PlanExecuteRequest
from app.routers.memory import maybe_auto_memory
from app.services.ollama import OllamaClient
from app.services.citation_guard import sanitize_assistant_citations
from app.services.rag import multi_hop_search, multi_query_search, search_memories
from app.telemetry import telemetry
from app.services.agent import run_agent
from app.services.plan_execute import run_plan_execute
from app.services.security import sanitize_external_content, sanitize_user_input

router = APIRouter(prefix="/v1", tags=["chat"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_system_prompt(
    sources: list[dict],
    memory_lines: list[str],
    session_summary: str | None = None,
) -> str:
    rag_lines: list[str] = []
    for i, s in enumerate(sources, start=1):
        src = s.get("source") or "unknown"
        page = s.get("page")
        pg = f" p.{page}" if page else ""
        sec = s.get("section_heading")
        # 剥离 Markdown 标题前缀（# / ## / ### 等），避免 LLM 在 CoT 中复现原始 # 符号
        sec_clean = sec.lstrip("#").strip() if isinstance(sec, str) else ""
        sec_part = f" · 节：{sec_clean}" if sec_clean else ""
        body = s.get("full_content", s.get("snippet", ""))
        # 间接注入扫描：文档片段可能被投毒
        body = sanitize_external_content(body, source_label=f"rag:{src}")
        rag_lines.append(f"[S{i}] ({src}{pg}{sec_part})\n{body}")

    rag_block = "\n\n".join(rag_lines) if rag_lines else "(无检索片段)"
    mem_block = "\n".join(memory_lines) if memory_lines else "(无长期记忆)"

    summary_section = (
        f"\n3.【历史对话摘要】：本次会话早期对话的压缩摘要，可作为背景参考。\n{session_summary}"
        if session_summary else ""
    )

    has_rag = bool(rag_lines)
    has_mem = bool(memory_lines)

    # 先呈现片段内容，再给指令——小模型对「先看到内容再看规则」的遵循率更高
    context_block = (
        f"【长期记忆】（用户身份/偏好）\n{mem_block}\n\n"
        f"【知识库片段】（共 {len(rag_lines)} 条，按相关度排序）\n{rag_block}"
        + (f"\n\n【历史对话摘要】\n{session_summary}" if session_summary else "")
        if has_rag else
        f"【长期记忆】\n{mem_block}\n\n【知识库片段】\n(无检索片段)"
    )

    cot_enabled = has_rag  # 只有存在知识库片段时才触发 CoT 格式

    strict_rule = (
        "你是一个严格基于知识库文档回答问题的助手。\n\n"
        "【铁律】\n"
        "A. 每一个出现在回答里的字段名、路径、参数、值，都必须能在上方某个片段中原文找到；\n"
        "   找不到原文就不写，禁止用「常识」或「推测」补充。\n"
        "B. 片段中已有的内容（路径、是否必须等）必须原样呈现，不得加「可能」「通常」等猜测词。\n"
        "C. 禁止在片段有该信息的情况下说「文档未提供」；片段里真没有才写「知识库中未找到」。\n"
        "D. 回答末尾用 [S1][S2] 标注引用的片段编号。\n"
        "E. 若片段数为 0 或全部无关，只说：「知识库中没有找到相关内容。」\n"
        "F. 纯创作/翻译/计算类问题不受以上限制。\n\n"
        + (
            f"【回答格式 — 必须严格遵守，共 {len(rag_lines)} 个片段】\n"
            f"第一步「片段摘录」：每个片段单独一行，编号必须与片段序号完全对应，格式：\n"
            + "".join(
                f"  [S{i}] → <片段{i}中与问题相关的原文内容；若该片段与问题完全无关，写「无关」>\n"
                for i in range(1, len(rag_lines) + 1)
            )
            + f"第二步「回答」：仅基于第一步摘录的内容整合作答，末尾标注引用编号。\n"
            f"禁止跳过第一步；若用户问及多个接口/功能，回答中必须分别呈现。\n"
            if cot_enabled else ""
        )
    )

    return (
        context_block
        + "\n\n---\n\n"
        + strict_rule
    )


def _cot_prefill(n: int) -> str:
    """生成 CoT prefill 文本，以 [S1] → 结尾，引导模型从第一条开始逐行填写。"""
    return f"片段摘录（共 {n} 个片段，逐一检查）：\n[S1] → "


def _generate_title(client: OllamaClient, first_message: str) -> str:
    """用 LLM 为新会话生成不超过 10 字的简洁标题。失败时返回截断文本。"""
    try:
        prompt = (
            "请为以下用户问题生成一个不超过 10 个字的简洁会话标题（直接输出标题，不加引号和标点）：\n\n"
            + first_message[:200]
        )
        title = client.chat_complete([{"role": "user", "content": prompt}], temperature=0.3)
        title = title.strip().strip("「」『』\"'").replace("\n", "")[:20]
        return title if title else first_message[:16]
    except Exception as e:
        logging.warning("[Chat] title generation failed: %s", e)
        return first_message[:16]


def _maybe_summarize(db: object, client: OllamaClient, sess: SessionModel) -> None:
    """当消息数超过阈值时自动压缩早期对话为摘要，每 10 条触发一次。"""
    try:
        count: int = db.execute(
            select(func.count()).select_from(Message).where(Message.session_id == sess.id)
        ).scalar() or 0

        if count < settings.summary_threshold or count % 10 != 0:
            return

        # 取前 (count - 6) 条消息用于摘要，保留最近 6 条原文给 LLM
        rows = (
            db.execute(
                select(Message)
                .where(Message.session_id == sess.id)
                .order_by(Message.created_at.asc())
                .limit(count - 6)
            )
            .scalars()
            .all()
        )
        if len(rows) < 4:
            return

        conv = "\n".join(f"{m.role}: {m.content[:300]}" for m in rows)
        prompt = (
            "请用简洁的中文总结以下对话的核心内容（不超过 300 字），保留关键事实、结论和用户需求：\n\n"
            + conv[:4000]
        )
        summary = client.chat_complete([{"role": "user", "content": prompt}], temperature=0.3)
        sess.summary = summary.strip()[:1000]
        db.commit()
        logging.info(f"[Chat] session {sess.id} summarized ({count} msgs)")
    except Exception as e:
        logging.warning(f"[Chat] auto-summarize failed: {e}")


@router.post("/chat/stream")
def chat_stream(body: ChatStreamRequest) -> StreamingResponse:
    top_k = body.top_k or settings.rag_top_k
    # 用户输入安全检测（日志留痕，不拦截）
    sanitize_user_input(body.message)

    def gen() -> Iterator[str]:
        db = SessionLocal()
        client = OllamaClient()
        try:
            is_new_session = not body.session_id
            if body.session_id:
                sess = db.get(SessionModel, body.session_id)
                if not sess or sess.user_id != body.user_id:
                    yield _sse("error", {"message": "session not found"})
                    return
            else:
                sess = SessionModel(user_id=body.user_id)
                db.add(sess)
                db.flush()

            sid: UUID = sess.id
            db.add(Message(session_id=sid, role="user", content=body.message))
            sess.updated_at = datetime.now(timezone.utc)
            db.commit()

            rows = (
                db.execute(
                    select(Message)
                    .where(Message.session_id == sid)
                    .order_by(Message.created_at.desc())
                    .limit(settings.chat_history_turns * 2 + 2)
                )
                .scalars()
                .all()
            )
            hist = list(reversed(rows))

            # ── 并行执行 RAG 检索 + 记忆检索 ─────────────────────────
            with ThreadPoolExecutor(max_workers=2) as ex:
                def _timed_rag():
                    t0 = time.perf_counter()
                    try:
                        if getattr(settings, "rag_multihop_enabled", False):
                            hops = int(getattr(settings, "rag_multihop_max_hops", 2) or 2)
                            sources, hop_trace = multi_hop_search(
                                db,
                                client,
                                body.message,
                                top_k,
                                body.kb_collection,
                                list(body.doc_types) if body.doc_types else None,
                                max_hops=max(1, min(2, hops)),
                            )
                            return sources, hop_trace
                        return (
                            multi_query_search(
                                db,
                                client,
                                body.message,
                                top_k,
                                body.kb_collection,
                                list(body.doc_types) if body.doc_types else None,
                            ),
                            [],
                        )
                    finally:
                        telemetry.record_timing("rag.search_ms", (time.perf_counter() - t0) * 1000)

                def _timed_mem():
                    t0 = time.perf_counter()
                    try:
                        return search_memories(db, client, body.user_id, body.message, 5)
                    finally:
                        telemetry.record_timing("memory.search_ms", (time.perf_counter() - t0) * 1000)

                fut_sources = ex.submit(_timed_rag)
                fut_mem = ex.submit(_timed_mem)
                sources, hop_trace = fut_sources.result()
                mem_lines = fut_mem.result()

            # ── Multi-hop trace（可选，前端可忽略未知事件）──────────────────────
            if hop_trace:
                for h in hop_trace:
                    yield _sse(
                        "rag_hop",
                        {
                            "session_id": str(sid),
                            "hop": h.get("hop"),
                            "query": h.get("query"),
                            "count": h.get("count"),
                            "reason": h.get("reason"),
                        },
                    )

            pub_sources = [
                {
                    "chunk_id": str(s["chunk_id"]),
                    "source": s.get("source"),
                    "page": s.get("page"),
                    "section_heading": s.get("section_heading"),
                    "score": s.get("score"),
                    "snippet": s.get("snippet"),
                }
                for s in sources
            ]
            yield _sse("sources", {"session_id": str(sid), "sources": pub_sources})

            # ── 知识库无内容时，代码层直接拦截，不调用 LLM（避免模型借助聊天历史编造答案）──
            if not sources:
                no_content_reply = "知识库中没有找到相关内容，无法回答该问题。"
                yield _sse("token", {"delta": no_content_reply})
                db.add(Message(session_id=sid, role="assistant", content=no_content_reply))
                db.commit()
                yield _sse("final", {"session_id": str(sid), "memory_writes": []})
                return

            t_prompt = time.perf_counter()
            system_prompt = _build_system_prompt(sources, mem_lines, sess.summary or None)
            telemetry.record_timing("prompt.build_ms", (time.perf_counter() - t_prompt) * 1000)
            ollama_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
            for m in hist:
                if m.role in ("user", "assistant"):
                    ollama_messages.append({"role": m.role, "content": m.content})

            # ── CoT prefill：有知识库片段时注入 assistant 开头，强制模型先做摘录步骤 ──
            use_cot = bool(sources)
            cot_text = _cot_prefill(len(sources)) if use_cot else ""
            if use_cot:
                ollama_messages.append({"role": "assistant", "content": cot_text})

            # ── 流式生成，统计 tok/s ──────────────────────────────────
            full = cot_text
            token_count = 0
            t_stream_start = time.perf_counter()
            if use_cot:
                # prefill 文本对用户可见，先主动推送
                yield _sse("token", {"delta": cot_text})
            for delta in client.chat_stream(ollama_messages, temperature=0.3):
                full += delta
                token_count += 1
                yield _sse("token", {"delta": delta})

            elapsed = time.perf_counter() - t_stream_start
            tps = round(token_count / elapsed, 1) if elapsed > 0 else 0.0

            full_out, _removed = sanitize_assistant_citations(
                full,
                sources,
                enabled=bool(sources) and settings.rag_citation_verify,
                min_hits=settings.rag_citation_min_hits,
                min_term_frac=settings.rag_citation_min_term_frac,
                max_source_terms=settings.rag_citation_max_source_terms,
            )

            db.add(Message(session_id=sid, role="assistant", content=full_out))
            db.commit()

            mem_written = maybe_auto_memory(db, client, body.user_id, body.message)

            # ── 新会话：生成语义标题 ──────────────────────────────────
            session_title: str | None = None
            if is_new_session:
                session_title = _generate_title(client, body.message)
                sess.summary = session_title
                db.commit()

            final_payload: dict = {
                "session_id": str(sid),
                "memory_writes": [mem_written] if mem_written else [],
                "stats": {"tokens": token_count, "tok_per_sec": tps},
            }
            if session_title:
                final_payload["session_title"] = session_title
            if full_out != full:
                final_payload["assistant_content"] = full_out
            yield _sse("final", final_payload)

            # 摘要在 final 之后执行，不阻塞前端解锁
            _maybe_summarize(db, client, sess)
        except Exception as e:
            yield _sse("error", {"message": str(e)})
        finally:
            client.close()
            db.close()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8", headers=headers)


@router.post("/chat/agent/stream")
def chat_agent_stream(body: AgentChatRequest) -> StreamingResponse:
    """Agent 模式：LLM 自主决策工具调用后再流式生成最终回复。

    SSE 事件序列：
      agent_step (calling) → agent_step (done) [重复N次]
      sources → token* → final
    """
    top_k = body.top_k or settings.rag_top_k
    # 用户输入安全检测（日志留痕，不拦截）
    sanitize_user_input(body.message)

    def gen() -> Iterator[str]:
        db = SessionLocal()
        client = OllamaClient()
        try:
            is_new_session = not body.session_id
            if body.session_id:
                sess = db.get(SessionModel, body.session_id)
                if not sess or sess.user_id != body.user_id:
                    yield _sse("error", {"message": "session not found"})
                    return
            else:
                sess = SessionModel(user_id=body.user_id)
                db.add(sess)
                db.flush()

            sid: UUID = sess.id
            db.add(Message(session_id=sid, role="user", content=body.message))
            sess.updated_at = datetime.now(timezone.utc)
            db.commit()

            # 加载近期消息历史
            rows = (
                db.execute(
                    select(Message)
                    .where(Message.session_id == sid)
                    .order_by(Message.created_at.desc())
                    .limit(settings.chat_history_turns * 2 + 2)
                )
                .scalars()
                .all()
            )
            hist = [{"role": m.role, "content": m.content} for m in reversed(rows)]

            # ── Agent 循环：工具决策 → 执行 ──────────────────────────
            final_messages: list[dict] = []
            agent_sources: list[dict] = []
            steps_trace: list[dict] = []

            t_agent = time.perf_counter()
            for event in run_agent(
                db=db,
                ollama=client,
                user_id=body.user_id,
                message=body.message,
                history=hist,
                top_k=top_k,
                session_summary=sess.summary or None,
                kb_collection=body.kb_collection,
                doc_types=list(body.doc_types) if body.doc_types else None,
            ):
                etype = event.get("type", "")

                if etype == "agent_step":
                    yield _sse("agent_step", {k: v for k, v in event.items() if k != "type"})

                elif etype == "result":
                    final_messages = event["messages"]
                    agent_sources = event["sources"]
                    steps_trace = event.get("steps_trace", [])

            telemetry.record_timing("agent.loop_ms", (time.perf_counter() - t_agent) * 1000)

            # ── 发送 sources 事件 ─────────────────────────────────────
            pub_sources = [
                {
                    "chunk_id": str(s["chunk_id"]),
                    "source": s.get("source"),
                    "page": s.get("page"),
                    "section_heading": s.get("section_heading"),
                    "score": s.get("score"),
                    "snippet": s.get("snippet"),
                }
                for s in agent_sources
            ]
            yield _sse("sources", {"session_id": str(sid), "sources": pub_sources})

            # ── 流式生成最终回复，统计 tok/s ─────────────────────────
            full = ""
            token_count = 0
            t_stream_start = time.perf_counter()
            for delta in client.chat_stream(final_messages, temperature=0.3):
                full += delta
                token_count += 1
                yield _sse("token", {"delta": delta})

            elapsed = time.perf_counter() - t_stream_start
            tps = round(token_count / elapsed, 1) if elapsed > 0 else 0.0

            full_out, _removed = sanitize_assistant_citations(
                full,
                agent_sources,
                enabled=bool(agent_sources) and settings.rag_citation_verify,
                min_hits=settings.rag_citation_min_hits,
                min_term_frac=settings.rag_citation_min_term_frac,
                max_source_terms=settings.rag_citation_max_source_terms,
            )

            db.add(Message(
                session_id=sid,
                role="assistant",
                content=full_out,
                extra={"agent_steps": steps_trace} if steps_trace else None,
            ))
            db.commit()

            mem_written = maybe_auto_memory(db, client, body.user_id, body.message)

            # ── 新会话：生成语义标题 ──────────────────────────────────
            session_title: str | None = None
            if is_new_session:
                session_title = _generate_title(client, body.message)
                sess.summary = session_title
                db.commit()

            final_payload: dict = {
                "session_id": str(sid),
                "memory_writes": [mem_written] if mem_written else [],
                "stats": {"tokens": token_count, "tok_per_sec": tps},
            }
            if session_title:
                final_payload["session_title"] = session_title
            if full_out != full:
                final_payload["assistant_content"] = full_out
            yield _sse("final", final_payload)

            _maybe_summarize(db, client, sess)
        except Exception as e:
            logging.exception("[Agent] stream error")
            yield _sse("error", {"message": str(e)})
        finally:
            client.close()
            db.close()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8", headers=headers)


@router.post("/chat/plan_execute/stream")
def chat_plan_execute_stream(body: PlanExecuteRequest) -> StreamingResponse:
    """Plan & Execute 模式：先生成结构化计划，再逐步执行工具，最后流式综合生成回复。

    SSE 事件序列：
      plan             → {goal, steps[], plan_ms}
      plan_step_start  → {step_id, description, tool}
      agent_step (calling/done) × N
      plan_step_done   → {step_id, description, success, result_summary, elapsed_ms}
      sources / token* / final
    """
    top_k = body.top_k or settings.rag_top_k
    # 用户输入安全检测（日志留痕，不拦截）
    sanitize_user_input(body.message)

    def gen() -> Iterator[str]:
        db = SessionLocal()
        client = OllamaClient()
        try:
            is_new_session = not body.session_id
            if body.session_id:
                sess = db.get(SessionModel, body.session_id)
                if not sess or sess.user_id != body.user_id:
                    yield _sse("error", {"message": "session not found"})
                    return
            else:
                sess = SessionModel(user_id=body.user_id)
                db.add(sess)
                db.flush()

            sid: UUID = sess.id
            db.add(Message(session_id=sid, role="user", content=body.message))
            sess.updated_at = datetime.now(timezone.utc)
            db.commit()

            rows = (
                db.execute(
                    select(Message)
                    .where(Message.session_id == sid)
                    .order_by(Message.created_at.desc())
                    .limit(settings.chat_history_turns * 2 + 2)
                )
                .scalars()
                .all()
            )
            hist = [{"role": m.role, "content": m.content} for m in reversed(rows)]

            final_messages: list[dict] = []
            pe_sources: list[dict] = []
            steps_trace: list[dict] = []
            plan_goal: str = ""
            plan_steps: list[dict] = []

            t_pe = time.perf_counter()
            for event in run_plan_execute(
                db=db,
                ollama=client,
                user_id=body.user_id,
                message=body.message,
                history=hist,
                top_k=top_k,
                session_summary=sess.summary or None,
                kb_collection=body.kb_collection,
                doc_types=list(body.doc_types) if body.doc_types else None,
            ):
                etype = event.get("type", "")

                if etype == "plan":
                    plan_goal = event.get("goal", "")
                    plan_steps = event.get("steps", [])
                    yield _sse("plan", {
                        "goal": plan_goal,
                        "steps": plan_steps,
                        "plan_ms": event.get("plan_ms", 0),
                    })

                elif etype == "plan_step_start":
                    yield _sse("plan_step_start", {
                        "step_id": event["step_id"],
                        "description": event["description"],
                        "tool": event.get("tool"),
                    })

                elif etype == "plan_step_done":
                    yield _sse("plan_step_done", {
                        "step_id": event["step_id"],
                        "description": event["description"],
                        "success": event.get("success", True),
                        "result_summary": event.get("result_summary", ""),
                        "elapsed_ms": event.get("elapsed_ms", 0),
                    })

                elif etype == "agent_step":
                    yield _sse("agent_step", {k: v for k, v in event.items() if k != "type"})

                elif etype == "result":
                    final_messages = event["messages"]
                    pe_sources = event["sources"]
                    steps_trace = event.get("steps_trace", [])

            telemetry.record_timing("plan_execute.loop_ms", (time.perf_counter() - t_pe) * 1000)

            pub_sources = [
                {
                    "chunk_id": str(s["chunk_id"]),
                    "source": s.get("source"),
                    "page": s.get("page"),
                    "section_heading": s.get("section_heading"),
                    "score": s.get("score"),
                    "snippet": s.get("snippet"),
                }
                for s in pe_sources
            ]
            yield _sse("sources", {"session_id": str(sid), "sources": pub_sources})

            full = ""
            token_count = 0
            t_stream_start = time.perf_counter()
            for delta in client.chat_stream(final_messages, temperature=0.3):
                full += delta
                token_count += 1
                yield _sse("token", {"delta": delta})

            elapsed = time.perf_counter() - t_stream_start
            tps = round(token_count / elapsed, 1) if elapsed > 0 else 0.0

            full_out, _removed = sanitize_assistant_citations(
                full,
                pe_sources,
                enabled=bool(pe_sources) and settings.rag_citation_verify,
                min_hits=settings.rag_citation_min_hits,
                min_term_frac=settings.rag_citation_min_term_frac,
                max_source_terms=settings.rag_citation_max_source_terms,
            )

            extra: dict = {}
            if steps_trace:
                extra["agent_steps"] = steps_trace
            if plan_goal:
                extra["plan_goal"] = plan_goal
            if plan_steps:
                extra["plan_steps"] = plan_steps

            db.add(Message(
                session_id=sid,
                role="assistant",
                content=full_out,
                extra=extra or None,
            ))
            db.commit()

            mem_written = maybe_auto_memory(db, client, body.user_id, body.message)

            session_title: str | None = None
            if is_new_session:
                session_title = _generate_title(client, body.message)
                sess.summary = session_title
                db.commit()

            final_payload: dict = {
                "session_id": str(sid),
                "memory_writes": [mem_written] if mem_written else [],
                "stats": {"tokens": token_count, "tok_per_sec": tps},
            }
            if session_title:
                final_payload["session_title"] = session_title
            if full_out != full:
                final_payload["assistant_content"] = full_out
            if plan_goal:
                final_payload["plan_goal"] = plan_goal
            if plan_steps:
                final_payload["plan_steps"] = plan_steps
            yield _sse("final", final_payload)

            _maybe_summarize(db, client, sess)
        except Exception as e:
            logging.exception("[PlanExec] stream error")
            yield _sse("error", {"message": str(e)})
        finally:
            client.close()
            db.close()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8", headers=headers)
