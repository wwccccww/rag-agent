from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.kb import normalize_doc_type, validate_kb_collection_optional


class HealthResponse(BaseModel):
    db: dict[str, Any]
    ollama: dict[str, Any]
    models: dict[str, str]


class IngestResponse(BaseModel):
    document_id: UUID
    chunks_created: int


def _validate_kb(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return validate_kb_collection_optional(str(v))


def _validate_doc_types(v: object) -> list[str] | None:
    if v is None:
        return None
    if isinstance(v, list) and len(v) == 0:
        return None
    if not isinstance(v, list):
        return None
    out: list[str] = []
    for x in v[:8]:
        if not isinstance(x, str) or not x.strip():
            continue
        try:
            n = normalize_doc_type(x)
        except ValueError as e:
            raise ValueError(str(e)) from e
        if n not in out:
            out.append(n)
    return out or None


class ChatStreamRequest(BaseModel):
    user_id: str = Field(default="demo")
    session_id: UUID | None = None
    message: str
    top_k: int | None = None
    kb_collection: str | None = Field(default=None, max_length=64)
    doc_types: list[str] | None = None

    @field_validator("kb_collection", mode="before")
    @classmethod
    def _v_kb_collection(cls, v: object) -> str | None:
        return _validate_kb(v)

    @field_validator("doc_types", mode="before")
    @classmethod
    def _v_doc_types(cls, v: object) -> list[str] | None:
        return _validate_doc_types(v)


class ChatContinueRequest(BaseModel):
    """用户点击“继续生成”后续写上一条回答。"""
    user_id: str = Field(default="demo")
    session_id: UUID
    top_k: int | None = None
    kb_collection: str | None = Field(default=None, max_length=64)
    doc_types: list[str] | None = None

    @field_validator("kb_collection", mode="before")
    @classmethod
    def _v_kb_collection_continue(cls, v: object) -> str | None:
        return _validate_kb(v)

    @field_validator("doc_types", mode="before")
    @classmethod
    def _v_doc_types_continue(cls, v: object) -> list[str] | None:
        return _validate_doc_types(v)


class AgentChatRequest(BaseModel):
    """Agent 模式请求，LLM 自主决策是否调用工具。"""
    user_id: str = Field(default="demo")
    session_id: UUID | None = None
    message: str
    top_k: int | None = None
    kb_collection: str | None = Field(default=None, max_length=64)
    doc_types: list[str] | None = None

    @field_validator("kb_collection", mode="before")
    @classmethod
    def _v_kb_collection_agent(cls, v: object) -> str | None:
        return _validate_kb(v)

    @field_validator("doc_types", mode="before")
    @classmethod
    def _v_doc_types_agent(cls, v: object) -> list[str] | None:
        return _validate_doc_types(v)


class PlanExecuteRequest(BaseModel):
    """Plan & Execute 模式请求：先规划子任务，再逐步执行，最后综合生成。"""
    user_id: str = Field(default="demo")
    session_id: UUID | None = None
    message: str
    top_k: int | None = None
    kb_collection: str | None = Field(default=None, max_length=64)
    doc_types: list[str] | None = None

    @field_validator("kb_collection", mode="before")
    @classmethod
    def _v_kb_collection_pe(cls, v: object) -> str | None:
        return _validate_kb(v)

    @field_validator("doc_types", mode="before")
    @classmethod
    def _v_doc_types_pe(cls, v: object) -> list[str] | None:
        return _validate_doc_types(v)


class SourceItem(BaseModel):
    chunk_id: UUID
    source: str | None
    page: int | None
    section_heading: str | None = None
    score: float
    snippet: str


class MemoryCreate(BaseModel):
    user_id: str = "demo"
    kind: Literal[
        "fact", "identity", "preference", "skill", "relation", "event", "goal"
    ] = "fact"
    content: str


class MemoryItem(BaseModel):
    id: UUID
    kind: str
    content: str
    confidence: float = 1.0
    valid_until: str | None = None
    created_at: str


class KGEntityItem(BaseModel):
    id: UUID
    name: str
    entity_type: str
    attrs: dict
    created_at: str


class KGRelationItem(BaseModel):
    id: UUID
    subject_id: UUID
    subject_name: str
    predicate: str
    object_id: UUID
    object_name: str
    confidence: float
    created_at: str
