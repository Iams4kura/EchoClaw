"""测试 Hands 执行层。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from script.hands.models import ExecutionResult
from script.hands.manager import HandsManager


# ── ExecutionResult 测试 ─────────────────────────────────────

class TestExecutionResult:
    def test_success_result(self) -> None:
        r = ExecutionResult(success=True, output="done", duration_ms=100)
        assert r.success
        assert r.output == "done"
        assert r.error is None

    def test_failure_result(self) -> None:
        r = ExecutionResult(success=False, output="", error="timeout")
        assert not r.success
        assert r.error == "timeout"

    def test_default_values(self) -> None:
        r = ExecutionResult(success=True, output="ok")
        assert r.duration_ms == 0
        assert r.tool_calls_count == 0


# ── HandsManager 测试 ───────────────────────────────────────

class TestHandsManager:
    def test_initial_state(self) -> None:
        mgr = HandsManager(working_dir="/tmp/test")
        assert mgr.active_count == 0
        assert mgr.get_status()["active_executors"] == 0

    async def test_execute_creates_executor(self) -> None:
        """execute() 应该自动创建引擎实例。"""
        mgr = HandsManager(working_dir="/tmp/test")

        # Mock EngineExecutor
        mock_executor = MagicMock()
        mock_executor.initialize = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=ExecutionResult(
            success=True, output="result", duration_ms=50,
        ))

        with patch("script.hands.manager.EngineExecutor", return_value=mock_executor):
            result = await mgr.execute("user1", "do something")

        assert result.success
        assert result.output == "result"
        assert mgr.active_count == 1

    async def test_reset_user(self) -> None:
        """reset_user() 应该重置引擎。"""
        mgr = HandsManager()

        mock_executor = MagicMock()
        mock_executor.initialize = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=ExecutionResult(success=True, output="ok"))
        mock_executor.reset = AsyncMock()

        with patch("script.hands.manager.EngineExecutor", return_value=mock_executor):
            await mgr.execute("user1", "test")
            result = await mgr.reset_user("user1")

        assert result is True
        mock_executor.reset.assert_called_once()

    async def test_reset_nonexistent_user(self) -> None:
        mgr = HandsManager()
        result = await mgr.reset_user("nobody")
        assert result is False

    async def test_shutdown(self) -> None:
        """shutdown() 应该关闭所有引擎。"""
        mgr = HandsManager()

        mock_executor = MagicMock()
        mock_executor.initialize = AsyncMock()
        mock_executor.execute = AsyncMock(return_value=ExecutionResult(success=True, output="ok"))
        mock_executor.teardown = AsyncMock()

        with patch("script.hands.manager.EngineExecutor", return_value=mock_executor):
            await mgr.execute("user1", "test")
            await mgr.shutdown()

        assert mgr.active_count == 0
        mock_executor.teardown.assert_called_once()

    async def test_multiple_users(self) -> None:
        """不同用户应获得独立的引擎实例。"""
        mgr = HandsManager()

        executors = []

        def make_executor(**kwargs: object) -> MagicMock:
            e = MagicMock()
            e.initialize = AsyncMock()
            e.execute = AsyncMock(return_value=ExecutionResult(success=True, output="ok"))
            executors.append(e)
            return e

        with patch("script.hands.manager.EngineExecutor", side_effect=make_executor):
            await mgr.execute("user1", "test1")
            await mgr.execute("user2", "test2")

        assert mgr.active_count == 2
        assert len(executors) == 2

    async def test_same_user_reuses_executor(self) -> None:
        """同一用户多次调用应复用引擎。"""
        mgr = HandsManager()

        call_count = 0

        def make_executor(**kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            e = MagicMock()
            e.initialize = AsyncMock()
            e.execute = AsyncMock(return_value=ExecutionResult(success=True, output="ok"))
            return e

        with patch("script.hands.manager.EngineExecutor", side_effect=make_executor):
            await mgr.execute("user1", "test1")
            await mgr.execute("user1", "test2")

        assert call_count == 1  # 只创建了一次
