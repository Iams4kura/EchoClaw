"""测试引擎会话管理。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from script.engine_session import EngineSession, SessionManager


class TestEngineSession:
    """EngineSession 单元测试。"""

    async def test_handle_calls_run_turn(self) -> None:
        engine = MagicMock()
        engine.run_turn = AsyncMock(return_value="Hello!")
        session = EngineSession(engine)

        result = await session.handle("Hi")

        assert result == "Hello!"
        engine.run_turn.assert_awaited_once_with("Hi")

    async def test_handle_updates_metadata(self) -> None:
        engine = MagicMock()
        engine.run_turn = AsyncMock(return_value="ok")
        session = EngineSession(engine)
        t0 = session.last_active

        await session.handle("test")

        assert session.turn_count == 1
        assert session.last_active >= t0

    async def test_multiple_turns(self) -> None:
        engine = MagicMock()
        engine.run_turn = AsyncMock(side_effect=["r1", "r2", "r3"])
        session = EngineSession(engine)

        assert await session.handle("q1") == "r1"
        assert await session.handle("q2") == "r2"
        assert await session.handle("q3") == "r3"
        assert session.turn_count == 3


class TestSessionManager:
    """SessionManager 单元测试。"""

    @patch("script.engine_session.create_engine")
    async def test_get_or_create_new(self, mock_create: AsyncMock) -> None:
        mock_engine = MagicMock()
        mock_engine.run_turn = AsyncMock(return_value="hello")
        mock_create.return_value = mock_engine

        mgr = SessionManager({"working_dir": "/tmp", "permission_mode": "auto"})
        session = await mgr.get_or_create("user1")

        assert isinstance(session, EngineSession)
        assert mgr.active_count == 1
        mock_create.assert_awaited_once()

    @patch("script.engine_session.create_engine")
    async def test_get_or_create_reuses_session(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()

        mgr = SessionManager({"working_dir": "."})
        s1 = await mgr.get_or_create("user1")
        s2 = await mgr.get_or_create("user1")

        assert s1 is s2
        assert mock_create.await_count == 1  # 只创建一次

    @patch("script.engine_session.create_engine")
    async def test_different_users_get_different_sessions(
        self, mock_create: AsyncMock
    ) -> None:
        mock_create.return_value = MagicMock()

        mgr = SessionManager({"working_dir": "."})
        s1 = await mgr.get_or_create("user1")
        s2 = await mgr.get_or_create("user2")

        assert s1 is not s2
        assert mgr.active_count == 2

    @patch("script.engine_session.create_engine")
    async def test_reset(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()

        mgr = SessionManager({"working_dir": "."})
        await mgr.get_or_create("user1")
        assert mgr.active_count == 1

        had = await mgr.reset("user1")
        assert had is True
        assert mgr.active_count == 0

        had = await mgr.reset("user1")
        assert had is False

    @patch("script.engine_session.create_engine")
    async def test_get_session_info(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()

        mgr = SessionManager({"working_dir": "."})
        assert mgr.get_session_info("user1") is None

        await mgr.get_or_create("user1")
        info = mgr.get_session_info("user1")
        assert info is not None
        assert info["user_id"] == "user1"
        assert info["turn_count"] == 0
