"""测试 Webhook 适配器。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from script.engine_session import EngineSession, SessionManager
from script.gateway.adapters.webhook import WebhookAdapter
from script.gateway.models import BotResponse


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

    def test_message_uses_middleware_handler(self) -> None:
        mgr = _make_mock_session_manager()
        middleware = AsyncMock(return_value=BotResponse(text="filtered response"))
        adapter = WebhookAdapter(mgr, message_handler=middleware)
        client = TestClient(adapter.app)

        resp = client.post(
            "/message", json={"user_id": "test_user", "content": "hello"}
        )

        assert resp.status_code == 200
        assert resp.json()["text"] == "filtered response"
        message = middleware.await_args.args[0]
        assert message.platform == "webhook"
        assert message.user_id == "test_user"
        assert message.content == "hello"
        mgr.get_or_create.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [
            {"content": ""},
            {"content": "x" * 20_001},
            {"user_id": "u" * 129, "content": "hello"},
        ],
    )
    def test_message_rejects_invalid_input(self, payload: dict[str, str]) -> None:
        adapter = WebhookAdapter(_make_mock_session_manager())
        client = TestClient(adapter.app)

        resp = client.post("/message", json=payload)

        assert resp.status_code == 422

    def test_message_does_not_leak_engine_errors(self) -> None:
        mgr = _make_mock_session_manager()
        session = mgr.get_or_create.return_value
        session.handle = AsyncMock(
            side_effect=RuntimeError("secret provider token")
        )
        adapter = WebhookAdapter(mgr)
        client = TestClient(adapter.app, raise_server_exceptions=False)

        resp = client.post("/message", json={"content": "hello"})

        assert resp.status_code == 500
        assert "secret provider token" not in resp.text
        assert session.handle.await_count == 1


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
