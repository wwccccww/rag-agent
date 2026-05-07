from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client() -> TestClient:
    """整应用 TestClient（会触发 lifespan；集成测试请配合 mock 使用）。"""
    from app.main import app

    with TestClient(app) as c:
        yield c
