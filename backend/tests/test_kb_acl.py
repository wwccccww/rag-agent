"""kb_collection 访问控制（DB 表 user_kb_collections）。"""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.config import settings
from app.models import UserKbCollection
from app.services.kb_acl import (
    assert_document_collection_readable,
    effective_kb_collection,
    list_allowed_collections,
)


@pytest.fixture
def local_db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # 仅测 ACL 表，不建带 vector 的表
    Base.metadata.create_all(
        bind=engine,
        tables=[UserKbCollection.__table__],
    )
    S = sessionmaker(bind=engine)
    db = S()
    try:
        yield db
    finally:
        db.close()


def test_effective_when_acl_off(local_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", False, raising=False)
    out = effective_kb_collection(local_db, "any", "other")
    assert out == "other"


def test_effective_denied(local_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", True, raising=False)
    local_db.add(UserKbCollection(user_id="u1", kb_collection="default"))
    local_db.commit()
    with pytest.raises(HTTPException) as ei:
        effective_kb_collection(local_db, "u1", "secret")
    assert ei.value.status_code == 403


def test_effective_allowed(local_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", True, raising=False)
    local_db.add(UserKbCollection(user_id="u1", kb_collection="hr"))
    local_db.commit()
    assert effective_kb_collection(local_db, "u1", "hr") == "hr"


def test_list_allowed(local_db: Session) -> None:
    local_db.add(UserKbCollection(user_id="u2", kb_collection="b"))
    local_db.add(UserKbCollection(user_id="u2", kb_collection="a"))
    local_db.commit()
    assert list_allowed_collections(local_db, "u2") == ["a", "b"]


def test_effective_no_rows_raises(local_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", True, raising=False)
    with pytest.raises(HTTPException) as ei:
        effective_kb_collection(local_db, "nobody", "default")
    assert ei.value.status_code == 403


def test_effective_empty_request_uses_default_when_in_allowed(
    local_db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", True, raising=False)
    monkeypatch.setattr(settings, "default_kb_collection", "default", raising=False)
    local_db.add(UserKbCollection(user_id="u3b", kb_collection="default"))
    local_db.add(UserKbCollection(user_id="u3b", kb_collection="hr"))
    local_db.commit()
    assert effective_kb_collection(local_db, "u3b", None) == "default"


def test_effective_empty_request_fallback_when_default_not_allowed(
    local_db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未传 kb_collection 时：若 DEFAULT 不在授权内，回落为授权列表字典序第一项。"""
    monkeypatch.setattr(settings, "kb_acl_enabled", True, raising=False)
    monkeypatch.setattr(settings, "default_kb_collection", "prod", raising=False)
    local_db.add(UserKbCollection(user_id="u3c", kb_collection="hr"))
    local_db.add(UserKbCollection(user_id="u3c", kb_collection="sales"))
    local_db.commit()
    out = effective_kb_collection(local_db, "u3c", None)
    assert out == "hr"


def test_assert_document_readable_acl_off(local_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", False, raising=False)
    assert_document_collection_readable(local_db, "any", "secret")  # no-op


def test_assert_document_readable_denied(local_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", True, raising=False)
    local_db.add(UserKbCollection(user_id="u4", kb_collection="a"))
    local_db.commit()
    with pytest.raises(HTTPException) as ei:
        assert_document_collection_readable(local_db, "u4", "other")
    assert ei.value.status_code == 403


def test_assert_document_readable_ok(local_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kb_acl_enabled", True, raising=False)
    local_db.add(UserKbCollection(user_id="u5", kb_collection="x"))
    local_db.commit()
    assert_document_collection_readable(local_db, "u5", "x")
