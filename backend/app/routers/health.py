from typing import Any

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.schemas import HealthResponse
from app.services.ollama import OllamaClient

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok = False
    db_info: dict[str, Any] = {}
    try:
        db: Session = SessionLocal()
        try:
            # pgvector
            pgv = db.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar_one_or_none()
            # pg_trgm
            trgm = db.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='pg_trgm'")
            ).scalar_one_or_none()
            # HNSW 索引是否存在
            hnsw_chunks = db.execute(
                text("SELECT 1 FROM pg_indexes WHERE indexname='idx_chunks_embedding_hnsw'")
            ).scalar_one_or_none()
            hnsw_memories = db.execute(
                text("SELECT 1 FROM pg_indexes WHERE indexname='idx_memories_embedding_hnsw'")
            ).scalar_one_or_none()
            db_ok = bool(pgv)
            db_info = {
                "ok": db_ok,
                "pgvector_version": str(pgv) if pgv else None,
                "pg_trgm_version": str(trgm) if trgm else None,
                "hnsw_index_chunks": bool(hnsw_chunks),
                "hnsw_index_memories": bool(hnsw_memories),
            }
        finally:
            db.close()
    except Exception as e:
        db_ok = False
        db_info = {"ok": False, "error": str(e)}

    ollama_ok = False
    models_found: dict[str, str] = {}
    ollama_err: str | None = None
    try:
        client = OllamaClient()
        try:
            data: dict[str, Any] = client.tags()
            ollama_ok = True
            names = {m.get("name") for m in data.get("models", []) if isinstance(m, dict)}
            if settings.ollama_chat_model in names:
                models_found["chat"] = settings.ollama_chat_model
            if settings.ollama_embed_model in names:
                models_found["embed"] = settings.ollama_embed_model
        finally:
            client.close()
    except Exception as e:
        ollama_ok = False
        ollama_err = str(e)

    return HealthResponse(
        db=db_info,
        ollama={"ok": ollama_ok, "base_url": settings.ollama_base_url, "error": ollama_err},
        models=models_found,
    )
