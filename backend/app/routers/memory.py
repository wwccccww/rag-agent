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


def _extract_json(raw: str) -> dict:
    """兼容 LLM 把 JSON 包在 ```json ... ``` 里的情况"""
    text = raw.strip()
    # 剥掉 markdown 代码块
    if text.startswith("```"):
        lines = text.splitlines()
        inner = [l for l in lines if not l.startswith("```")]
        text = "\n".join(inner).strip()
    # 找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def maybe_auto_memory(db: Session, client: OllamaClient, user_id: str, user_text: str) -> str | None:
    """提取并保存长期记忆，相似度 > 0.85 则更新已有记忆而非重复新增。返回写入内容，未写入返回 None。"""
    _MEMORY_TRIGGER = re.compile(
        r"记住|我是|我叫|我的|我有|我在|我会|我喜欢|我讨厌|我不喜欢|我偏好|我擅长|我负责|我用|"
        r"我做|我参与|我学|我工作|我住|我来自|帮我记|请记住|我的目标|我的项目|我的团队|"
        r"我的公司|我的职位|我的爱好|我的习惯|我的背景|我的经验|我叫做|我的名字"
    )
    if not _MEMORY_TRIGGER.search(user_text):
        return None
    prompt = (
        "请从下面这句用户的话中提取一条可长期保存的个人信息（身份/偏好/技能/正在做的事）。\n"
        "只输出纯 JSON，绝对不要输出其他任何文字或 markdown。\n"
        '格式：{"content": "提取到的信息"}\n'
        '如果没有值得保存的信息，输出：{"content": null}\n\n'
        "用户：" + user_text[:2000]
    )
    try:
        raw = client.chat_complete(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        obj = _extract_json(raw)
        c = obj.get("content")
        if not isinstance(c, str) or not c.strip():
            return None
        content = c.strip()[:2000]
        emb = client.embed(content[:8000])

        # ── 去重合并：余弦距离 < 0.15（相似度 > 0.85）则更新已有记忆 ──
        dist_expr = Memory.embedding.cosine_distance(emb)
        dup_row = db.execute(
            select(Memory, dist_expr.label("dist"))
            .where(Memory.user_id == user_id)
            .where(dist_expr < 0.15)
            .order_by(dist_expr)
            .limit(1)
        ).first()

        if dup_row:
            existing_mem, dist = dup_row
            existing_mem.content = content
            existing_mem.embedding = emb
            db.commit()
        else:
            db.add(Memory(user_id=user_id, kind="fact", content=content, embedding=emb))
            db.commit()

        return content
    except Exception:
        return None
