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
from app.schemas import AgentChatRequest, ChatStreamRequest
from app.routers.memory import maybe_auto_memory
from app.services.ollama import OllamaClient
from app.services.citation_guard import sanitize_assistant_citations
from app.services.rag import multi_query_search, search_memories
from app.telemetry import telemetry
from app.services.agent import run_agent

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
        sec_part = f" · 节：{sec}" if isinstance(sec, str) and sec.strip() else ""
        body = s.get("full_content", s.get("snippet", ""))
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

    strict_rule = (
        "你是一个文档摘录助手，只能从上方【知识库片段】中提取信息作答。\n\n"
        "【铁律，不得违反】\n"
        "A. 每一个出现在回答里的字段名、路径、参数、值，都必须能在上方某个片段中原文找到。\n"
        "   如果找不到原文，就不写——禁止用自己的「常识」或「推测」补充任何内容。\n"
        "B. 片段中明确写了的内容（如请求路径、字段是否必须），必须原样呈现，不得修改或加注「可能」「通常」等猜测词。\n"
        "C. 禁止在片段没有提到的情况下说「文档未提供」——如果片段里有这个信息，直接用；如果片段里真的没有，才写「知识库中未找到该信息」。\n"
        "D. 回答末尾标注引用编号 [S1] [S2]，编号与上方片段序号一致。\n"
        "E. 若片段数为 0 或所有片段均与问题无关，只说：「知识库中没有找到相关内容。」不做任何补充。\n"
        "F. 纯创作/翻译/计算类问题不受以上限制，正常完成即可。\n"
    )

    return (
        context_block
        + "\n\n---\n\n"
        + strict_rule
    )


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
                        return multi_query_search(
                            db,
                            client,
                            body.message,
                            top_k,
                            body.kb_collection,
                            list(body.doc_types) if body.doc_types else None,
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
                sources = fut_sources.result()
                mem_lines = fut_mem.result()

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

            # ── 知识库无内容且无记忆时，代码层直接拦截，不调用 LLM ──
            if not sources and not mem_lines:
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

            # ── 流式生成，统计 tok/s ──────────────────────────────────
            full = ""
            token_count = 0
            t_stream_start = time.perf_counter()
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
