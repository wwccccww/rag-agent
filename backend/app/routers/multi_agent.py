import json
import uuid
from datetime import datetime, timezone
import time
from typing import Iterator
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.database import SessionLocal
from app.models import Message, SessionModel
from app.services.multi_agent import run_multi_agent
from app.services.kb_acl import effective_kb_collection
from app.services.ollama import OllamaClient
from app.services.security import sanitize_user_input

router = APIRouter(prefix="/v1", tags=["chat"])


class MultiAgentChatRequest(BaseModel):
    user_id: str = Field(default="demo", max_length=128)
    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=20000)
    kb_collection: str | None = Field(default=None, max_length=64)
    doc_types: list[str] | None = None


def _sse(event: str, data: dict) -> str:
    # SSE payload 可能包含 UUID（如 session_id / sources），统一转为 str 避免 json 序列化失败
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/chat/multi_agent/stream")
def chat_multi_agent_stream(body: MultiAgentChatRequest) -> StreamingResponse:
    """
    多智能体（档2）：Supervisor 产计划 → retriever/solver 并行 → critic → synth 流式输出。

    SSE 事件：
      ma_plan
      ma_worker_result (retriever/solver/critic)
      agent_step (带 worker 字段，可选)
      token*
      final
    """
    sanitize_user_input(body.message)

    def gen() -> Iterator[str]:
        db = SessionLocal()
        client = OllamaClient()
        try:
            try:
                kb_coll = effective_kb_collection(db, body.user_id, body.kb_collection)
            except HTTPException as e:
                yield _sse("error", {"message": str(e.detail)})
                return
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

            # 新会话：前端用于刷新会话列表（沿用 chat.py 的行为）
            if is_new_session:
                yield _sse("session_created", {"session_id": str(sid)})

            req_id = uuid.uuid4().hex[:16]
            t0 = time.perf_counter()

            plan_obj, worker_results, _ctx, synth_messages = run_multi_agent(
                db=db,
                ollama=client,
                user_id=body.user_id,
                session_id=sid,
                request_id=req_id,
                message=body.message,
                kb_collection=kb_coll,
                doc_types=list(body.doc_types) if body.doc_types else None,
            )
            yield _sse("ma_plan", {"request_id": req_id, "plan": plan_obj})

            # worker 结果回传（用于前端面板展示）
            for wr in worker_results:
                yield _sse(
                    "ma_worker_result",
                    {
                        "request_id": req_id,
                        "worker": wr.worker,
                        "ok": wr.ok,
                        "text": wr.text,
                        "sources": wr.sources,
                        "steps_trace": wr.steps_trace,
                    },
                )

            # synth：流式输出 token
            full = ""
            for tok in client.chat_stream(synth_messages, temperature=float(getattr(settings, "chat_temperature", 0.2) or 0.2)):
                full += tok
                yield _sse("token", {"t": tok})

            # 写入 assistant 消息
            db.add(Message(session_id=sid, role="assistant", content=full))
            sess.updated_at = datetime.now(timezone.utc)
            db.commit()

            dt = time.perf_counter() - t0
            yield _sse(
                "final",
                {
                    "session_id": str(sid),
                    "request_id": req_id,
                    "stats": {"elapsed_sec": round(dt, 3)},
                },
            )
        except Exception as e:
            yield _sse("error", {"message": str(e)})
        finally:
            client.close()
            db.close()

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8", headers=headers)

