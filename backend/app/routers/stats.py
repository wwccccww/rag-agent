from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Chunk, Document, Memory, Message, SessionModel

router = APIRouter(prefix="/v1", tags=["stats"])


class StatsResponse(BaseModel):
    documents: int
    chunks: int
    sessions: int
    messages: int
    memories: int
    avg_chunks_per_doc: float


@router.get("/stats", response_model=StatsResponse)
def get_stats(user_id: str = Query("demo")) -> StatsResponse:
    db = SessionLocal()
    try:
        documents = db.execute(select(func.count()).select_from(Document)).scalar() or 0
        chunks = db.execute(select(func.count()).select_from(Chunk)).scalar() or 0
        sessions = db.execute(
            select(func.count()).select_from(SessionModel).where(SessionModel.user_id == user_id)
        ).scalar() or 0
        messages = db.execute(
            select(func.count()).select_from(Message).join(
                SessionModel, Message.session_id == SessionModel.id
            ).where(SessionModel.user_id == user_id)
        ).scalar() or 0
        memories = db.execute(
            select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
        ).scalar() or 0
        avg = round(chunks / documents, 1) if documents > 0 else 0.0
        return StatsResponse(
            documents=documents,
            chunks=chunks,
            sessions=sessions,
            messages=messages,
            memories=memories,
            avg_chunks_per_doc=avg,
        )
    finally:
        db.close()
