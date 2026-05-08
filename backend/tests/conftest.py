from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _kb_acl_off_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """集成/契约测试不连真实库时无 user_kb_collections 行，关闭 ACL 与历史行为一致。"""
    from app.config import settings

    monkeypatch.setattr(settings, "kb_acl_enabled", False, raising=False)


@pytest.fixture
def api_client() -> TestClient:
    """整应用 TestClient（会触发 lifespan；集成测试请配合 mock 使用）。"""
    from app.main import app

    with TestClient(app) as c:
        yield c
