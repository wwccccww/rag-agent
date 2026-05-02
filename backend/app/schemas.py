from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.kb import validate_kb_collection_optional

DocType = Literal["tutorial", "api", "requirements", "general"]


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
    kb_collection: str | None = Field(default=None, max_length=64)
    doc_types: list[DocType] | None = None

    @field_validator("kb_collection", mode="before")
    @classmethod
    def _v_kb_collection(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return validate_kb_collection_optional(str(v))

    @field_validator("doc_types", mode="before")
    @classmethod
    def _v_doc_types(cls, v: object) -> list[DocType] | None:
        if v is None:
            return None
        if isinstance(v, list) and len(v) == 0:
            return None
        if not isinstance(v, list):
            return None
        out: list[DocType] = []
        allowed = {"tutorial", "api", "requirements", "general"}
        for x in v[:8]:
            if isinstance(x, str) and x in allowed and x not in out:
                out.append(x)  # type: ignore[arg-type]
        return out or None


class AgentChatRequest(BaseModel):
    """Agent 模式请求，LLM 自主决策是否调用工具。"""
    user_id: str = Field(default="demo")
    session_id: UUID | None = None
    message: str
    top_k: int | None = None
    kb_collection: str | None = Field(default=None, max_length=64)
    doc_types: list[DocType] | None = None

    @field_validator("kb_collection", mode="before")
    @classmethod
    def _v_kb_collection_agent(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return validate_kb_collection_optional(str(v))

    @field_validator("doc_types", mode="before")
    @classmethod
    def _v_doc_types_agent(cls, v: object) -> list[DocType] | None:
        if v is None:
            return None
        if isinstance(v, list) and len(v) == 0:
            return None
        if not isinstance(v, list):
            return None
        out: list[DocType] = []
        allowed = {"tutorial", "api", "requirements", "general"}
        for x in v[:8]:
            if isinstance(x, str) and x in allowed and x not in out:
                out.append(x)  # type: ignore[arg-type]
        return out or None


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
