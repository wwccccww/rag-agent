"""集成测试：HTTP + DB mock（不依赖真实 PostgreSQL）。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.integration
def test_list_audit_tools_with_mock_db(api_client) -> None:
    row = MagicMock()
    row.id = uuid4()
    row.created_at = datetime.now(timezone.utc)
    row.user_id = "demo"
    row.session_id = None
    row.mode = "multi"
    row.request_id = "req1"
    row.worker = "retriever"
    row.tool = "search_knowledge_base"
    row.status = "ok"
    row.elapsed_ms = 12.5
    row.sources_count = 3
    row.tool_args = {"query": "q"}
    row.error = None
    row.result_preview = "preview"

    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = [row]

    with patch("app.routers.audit.SessionLocal", return_value=mock_db):
        r = api_client.get("/v1/audit/tools", params={"user_id": "demo", "mode": "multi"})

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["tool"] == "search_knowledge_base"
    assert data[0]["worker"] == "retriever"
    assert data[0]["mode"] == "multi"
    mock_db.close.assert_called_once()
