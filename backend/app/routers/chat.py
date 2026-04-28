import json
import logging
from collections.abc import Iterator
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
from app.services.rag import multi_query_search, search_memories
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
        body = s.get("full_content", s.get("snippet", ""))
        rag_lines.append(f"[S{i}] ({src}{pg})\n{body}")

    rag_block = "\n\n".join(rag_lines) if rag_lines else "(无检索片段)"
    mem_block = "\n".join(memory_lines) if memory_lines else "(无长期记忆)"

    summary_section = (
        f"\n3.【历史对话摘要】：本次会话早期对话的压缩摘要，可作为背景参考。\n{session_summary}"
        if session_summary else ""
    )

    return (
        "你是知识库助手，拥有以下上下文：\n"
        "1.【长期记忆】：关于用户个人身份、偏好、背景的事实，优先用于回答用户问自身情况的问题。\n"
        "2.【知识库片段】：文档检索结果，回答知识/内容相关问题时用（见[S1][S3]）标注引用。"
        + ("" if not session_summary else "\n3.【历史对话摘要】：本次会话前段对话摘要，提供背景脉络。")
        + "\n若上述内容均无法回答，请明确说明，不要编造。\n\n"
        f"【长期记忆】\n{mem_block}\n\n"
        f"【知识库片段】\n{rag_block}"
        + (f"\n\n【历史对话摘要】\n{session_summary}" if session_summary else "")
    )


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

            sources = multi_query_search(db, client, body.message, top_k)
            mem_lines = search_memories(db, client, body.user_id, body.message, top_k=5)

            pub_sources = [
                {
                    "chunk_id": str(s["chunk_id"]),
                    "source": s.get("source"),
                    "page": s.get("page"),
                    "score": s.get("score"),
                    "snippet": s.get("snippet"),
                }
                for s in sources
            ]
            yield _sse("sources", {"session_id": str(sid), "sources": pub_sources})

            system_prompt = _build_system_prompt(sources, mem_lines, sess.summary or None)
            ollama_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
            for m in hist:
                if m.role in ("user", "assistant"):
                    ollama_messages.append({"role": m.role, "content": m.content})

            full = ""
            for delta in client.chat_stream(ollama_messages, temperature=0.3):
                full += delta
                yield _sse("token", {"delta": delta})

            db.add(Message(session_id=sid, role="assistant", content=full))
            db.commit()

            mem_written = maybe_auto_memory(db, client, body.user_id, body.message)

            yield _sse(
                "final",
                {
                    "session_id": str(sid),
                    "memory_writes": [mem_written] if mem_written else [],
                },
            )

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
            # ── 会话管理（与普通模式相同）──────────────────────────────
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

            for event in run_agent(
                db=db,
                ollama=client,
                user_id=body.user_id,
                message=body.message,
                history=hist,
                top_k=top_k,
                session_summary=sess.summary or None,
            ):
                etype = event.get("type", "")

                if etype == "agent_step":
                    yield _sse("agent_step", {k: v for k, v in event.items() if k != "type"})

                elif etype == "result":
                    final_messages = event["messages"]
                    agent_sources = event["sources"]

            # ── 发送 sources 事件 ─────────────────────────────────────
            pub_sources = [
                {
                    "chunk_id": str(s["chunk_id"]),
                    "source": s.get("source"),
                    "page": s.get("page"),
                    "score": s.get("score"),
                    "snippet": s.get("snippet"),
                }
                for s in agent_sources
            ]
            yield _sse("sources", {"session_id": str(sid), "sources": pub_sources})

            # ── 流式生成最终回复 ─────────────────────────────────────
            full = ""
            for delta in client.chat_stream(final_messages, temperature=0.3):
                full += delta
                yield _sse("token", {"delta": delta})

            db.add(Message(session_id=sid, role="assistant", content=full))
            db.commit()

            mem_written = maybe_auto_memory(db, client, body.user_id, body.message)

            yield _sse(
                "final",
                {
                    "session_id": str(sid),
                    "memory_writes": [mem_written] if mem_written else [],
                },
            )

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
