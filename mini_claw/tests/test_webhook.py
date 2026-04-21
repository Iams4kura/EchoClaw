"""测试 Webhook 适配器。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from script.engine_session import EngineSession, SessionManager
from script.gateway.adapters.webhook import WebhookAdapter


def _make_mock_session_manager() -> SessionManager:
    """创建 mock SessionManager。"""
    mgr = MagicMock(spec=SessionManager)
    mock_session = MagicMock(spec=EngineSession)
    mock_session.handle = AsyncMock(return_value="Mock response")
    mgr.get_or_create = AsyncMock(return_value=mock_session)
    mgr.reset = AsyncMock(return_value=True)
    mgr.active_count = 1
    return mgr


class TestWebhookHealth:
    def test_health(self) -> None:
        mgr = _make_mock_session_manager()
        adapter = WebhookAdapter(mgr)
        client = TestClient(adapter.app)

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_status(self) -> None:
        mgr = _make_mock_session_manager()
        adapter = WebhookAdapter(mgr)
        client = TestClient(adapter.app)

        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_sessions" in data
        assert "uptime_seconds" in data


class TestWebhookMessage:
    def test_message(self) -> None:
        mgr = _make_mock_session_manager()
        adapter = WebhookAdapter(mgr)
        client = TestClient(adapter.app)

        resp = client.post("/message", json={"content": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Mock response"
        assert "duration_ms" in data

    def test_message_with_user_id(self) -> None:
        mgr = _make_mock_session_manager()
        adapter = WebhookAdapter(mgr)
        client = TestClient(adapter.app)

        resp = client.post(
            "/message", json={"user_id": "test_user", "content": "hello"}
        )
        assert resp.status_code == 200
        mgr.get_or_create.assert_called_with("test_user")

    def test_message_default_user_id(self) -> None:
        mgr = _make_mock_session_manager()
        adapter = WebhookAdapter(mgr)
        client = TestClient(adapter.app)

        resp = client.post("/message", json={"content": "hello"})
        assert resp.status_code == 200
        mgr.get_or_create.assert_called_with("default")


class TestWebhookReset:
    def test_reset(self) -> None:
        mgr = _make_mock_session_manager()
        adapter = WebhookAdapter(mgr)
        client = TestClient(adapter.app)

        resp = client.post("/reset/test_user")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reset"
        mgr.reset.assert_called_with("test_user")
