from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Message, SessionModel

router = APIRouter(prefix="/v1", tags=["sessions"])


class MessageItem(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: str


class SessionItem(BaseModel):
    id: UUID
    user_id: str
    summary: str | None
    created_at: str


class SessionUpdate(BaseModel):
    summary: str


@router.get("/sessions", response_model=list[SessionItem])
def list_sessions(user_id: str = Query("demo"), limit: int = Query(30, ge=1, le=100)) -> list[SessionItem]:
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(SessionModel)
                .where(SessionModel.user_id == user_id)
                .order_by(SessionModel.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            SessionItem(
                id=s.id,
                user_id=s.user_id,
                summary=s.summary,
                created_at=s.created_at.isoformat(),
            )
            for s in rows
        ]
    finally:
        db.close()


@router.patch("/sessions/{session_id}", response_model=SessionItem)
def rename_session(session_id: UUID, body: SessionUpdate) -> SessionItem:
    db = SessionLocal()
    try:
        sess = db.get(SessionModel, session_id)
        if not sess:
            raise HTTPException(404, "session not found")
        sess.summary = body.summary.strip()[:200]
        db.commit()
        db.refresh(sess)
        return SessionItem(
            id=sess.id,
            user_id=sess.user_id,
            summary=sess.summary,
            created_at=sess.created_at.isoformat(),
        )
    finally:
        db.close()


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: UUID) -> Response:
    db = SessionLocal()
    try:
        sess = db.get(SessionModel, session_id)
        if not sess:
            raise HTTPException(404, "session not found")
        db.delete(sess)
        db.commit()
        return Response(status_code=204)
    finally:
        db.close()


@router.get("/sessions/{session_id}/messages", response_model=list[MessageItem])
def get_messages(session_id: UUID, limit: int = Query(100, ge=1, le=500)) -> list[MessageItem]:
    db = SessionLocal()
    try:
        sess = db.get(SessionModel, session_id)
        if not sess:
            raise HTTPException(404, "session not found")
        rows = (
            db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            MessageItem(id=m.id, role=m.role, content=m.content, created_at=m.created_at.isoformat())
            for m in rows
        ]
    finally:
        db.close()
