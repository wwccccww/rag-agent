import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kb_collection: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embed_dim), nullable=False)

    # Parent-Child 分块：子块携带 parent_chunk_id，is_index_chunk=True 的块参与向量检索
    # 父块 is_index_chunk=False，仅在子块命中后被拉取喂给模型
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_index_chunk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    document: Mapped["Document"] = relationship(back_populates="chunks")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["SessionModel"] = relationship(back_populates="messages")


class Memory(Base):
    """
    用户长期记忆（扁平文本 + 向量）。

    kind 枚举：
      fact        - 通用事实（兜底）
      identity    - 身份：我叫/我是
      preference  - 偏好：我喜欢/我讨厌
      skill       - 技能：我会/我擅长
      relation    - 人际：我的同事/朋友/上级
      event       - 事件：我昨天做了/正在做
      goal        - 目标：我的计划/想要
    """
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="fact")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embed_dim), nullable=False)
    # 置信度 0.0–1.0（LLM 提取时评分）
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # 有效期（event 类可设过期时间，NULL 表示永久有效）
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KGEntity(Base):
    """
    知识图谱实体节点。
    每个 (user_id, name, type) 唯一（按向量相似度去重）。

    type 枚举：
      person / project / technology / organization / concept / event / other
    """
    __tablename__ = "kg_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # 实体类型
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    # 额外属性（JSON）：描述、别名等
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embed_dim), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 作为主语的关系
    outgoing: Mapped[list["KGRelation"]] = relationship(
        "KGRelation", foreign_keys="KGRelation.subject_id", back_populates="subject", cascade="all, delete-orphan"
    )
    # 作为宾语的关系
    incoming: Mapped[list["KGRelation"]] = relationship(
        "KGRelation", foreign_keys="KGRelation.object_id", back_populates="object_entity"
    )


class KGRelation(Base):
    """
    知识图谱关系边：subject --[predicate]--> object
    predicate 为自然语言谓词，如"负责"、"使用"、"同事"、"属于"。
    """
    __tablename__ = "kg_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    predicate: Mapped[str] = mapped_column(String(128), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # 来源记忆 ID（溯源用，可 NULL）
    source_memory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    subject: Mapped["KGEntity"] = relationship("KGEntity", foreign_keys=[subject_id], back_populates="outgoing")
    object_entity: Mapped["KGEntity"] = relationship("KGEntity", foreign_keys=[object_id], back_populates="incoming")
