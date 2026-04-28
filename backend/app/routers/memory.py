import json
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Memory
from app.schemas import MemoryCreate, MemoryItem
from app.services.ollama import OllamaClient

router = APIRouter(prefix="/v1", tags=["memory"])


@router.post("/memory")
def create_memory(body: MemoryCreate) -> dict:
    db = SessionLocal()
    client = OllamaClient()
    try:
        emb = client.embed(body.content[:8000])
        m = Memory(user_id=body.user_id, kind=body.kind, content=body.content.strip(), embedding=emb)
        db.add(m)
        db.commit()
        db.refresh(m)
        return {"id": str(m.id)}
    finally:
        client.close()
        db.close()


@router.get("/memory", response_model=list[MemoryItem])
def list_memory(user_id: str = Query("demo"), limit: int = Query(50, ge=1, le=200)) -> list[MemoryItem]:
    db = SessionLocal()
    try:
        rows = db.execute(select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at.desc()).limit(limit)).scalars().all()
        return [
            MemoryItem(id=m.id, kind=m.kind, content=m.content, created_at=m.created_at.isoformat())
            for m in rows
        ]
    finally:
        db.close()


@router.delete("/memory/{memory_id}")
def forget_memory(memory_id: UUID, user_id: str = Query("demo")) -> dict:
    db = SessionLocal()
    try:
        m = db.get(Memory, memory_id)
        if not m or m.user_id != user_id:
            raise HTTPException(404, "not found")
        db.delete(m)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


def maybe_auto_memory(db: Session, client: OllamaClient, user_id: str, user_text: str) -> None:
    if not re.search(r"记住|我是|我叫|我的偏好|我喜欢|我在做", user_text):
        return
    prompt = (
        "从用户这句话提取一条可长期保存的记忆（事实/偏好/身份），只输出JSON，不要其它文字。"
        '格式：{"content":"..."} 若无可保存内容则 {"content":null}\n用户：'
        + user_text[:2000]
    )
    raw = client.chat_complete(
        [{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    content: str | None = None
    try:
        obj = json.loads(raw)
        c = obj.get("content")
        if isinstance(c, str) and c.strip():
            content = c.strip()[:2000]
    except json.JSONDecodeError:
        return
    if not content:
        return
    emb = client.embed(content[:8000])
    db.add(Memory(user_id=user_id, kind="fact", content=content, embedding=emb))
    db.commit()
