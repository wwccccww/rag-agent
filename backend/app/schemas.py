from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    db: dict[str, Any]
    ollama: dict[str, Any]
    models: dict[str, str]


class IngestResponse(BaseModel):
    document_id: UUID
    chunks_created: int


class ChatStreamRequest(BaseModel):
    user_id: str = Field(default="demo")
    session_id: UUID | None = None
    message: str
    top_k: int | None = None


class SourceItem(BaseModel):
    chunk_id: UUID
    source: str | None
    page: int | None
    score: float
    snippet: str


class MemoryCreate(BaseModel):
    user_id: str = "demo"
    kind: Literal["fact", "profile", "decision"] = "fact"
    content: str


class MemoryItem(BaseModel):
    id: UUID
    kind: str
    content: str
    created_at: str
