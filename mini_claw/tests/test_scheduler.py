"""测试调度系统。"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from script.avatar.manager import AvatarManager
from script.avatar.models import Avatar, AvatarConfig, AvatarStatus
from script.avatar.runner import AvatarRunner
from script.gateway.models import UnifiedMessage
from script.memory.store import MemoryStore
from script.scheduler.models import Task, TaskStatus
from script.scheduler.router import TaskRouter
from script.scheduler.task_manager import TaskManager


def _make_msg(content: str, user_id: str = "u1") -> UnifiedMessage:
    return UnifiedMessage(
        platform="test",
        user_id=user_id,
        chat_id="c1",
        content=content,
        timestamp=time.time(),
    )


class TestTask:
    """Task 数据结构测试。"""

    def test_is_terminal(self) -> None:
        task = Task()
        assert task.is_terminal is False

        task.update_status(TaskStatus.COMPLETED)
        assert task.is_terminal is True

    def test_update_status(self) -> None:
        task = Task()
        t0 = task.updated_at
        task.update_status(TaskStatus.RUNNING)
        assert task.status == TaskStatus.RUNNING
        assert task.updated_at >= t0


class TestTaskRouter:
    """TaskRouter 路由测试。"""

    @patch("src.engine.headless.create_engine")
    async def test_route_by_mention(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_router1")
        mgr = AvatarManager(memory_store=store)
        await mgr.start_resident(AvatarConfig(id="coder", name="代码手"))

        router = TaskRouter(mgr)
        msg = _make_msg("@coder 帮我写个函数")
        runner, task = await router.route(msg)

        assert runner is not None
        assert task.assigned_avatar == "coder"

        await mgr.shutdown()

    @patch("src.engine.headless.create_engine")
    async def test_route_by_keyword(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_router2")
        mgr = AvatarManager(memory_store=store)
        await mgr.start_resident(AvatarConfig(id="coder", name="代码手"))
        await mgr.start_resident(AvatarConfig(id="general", name="通用"))

        router = TaskRouter(mgr)
        msg = _make_msg("帮我 debug 这个函数的 bug")
        runner, task = await router.route(msg)

        assert task.assigned_avatar == "coder"

        await mgr.shutdown()

    @patch("src.engine.headless.create_engine")
    async def test_route_default(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_router3")
        mgr = AvatarManager(memory_store=store)
        await mgr.start_resident(AvatarConfig(id="general", name="通用"))

        router = TaskRouter(mgr)
        msg = _make_msg("今天天气怎么样")
        runner, task = await router.route(msg)

        assert task.assigned_avatar == "general"

        await mgr.shutdown()

    @patch("src.engine.headless.create_engine")
    async def test_route_no_avatar(self, mock_create: AsyncMock) -> None:
        store = MemoryStore(root="/tmp/test_router4")
        mgr = AvatarManager(memory_store=store)

        router = TaskRouter(mgr)
        msg = _make_msg("hello")
        runner, task = await router.route(msg)

        assert runner is None

        await mgr.shutdown()


class TestTaskManager:
    """TaskManager 测试。"""

    @patch("src.engine.headless.create_engine")
    async def test_submit_and_get(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_taskmgr1")
        mgr = AvatarManager(memory_store=store)
        task_mgr = TaskManager(avatar_manager=mgr)

        task = Task(source_message=_make_msg("hello"))
        await task_mgr.submit(task)

        fetched = task_mgr.get(task.id)
        assert fetched is not None
        assert fetched.status == TaskStatus.PENDING

        await mgr.shutdown()

    @patch("src.engine.headless.create_engine")
    async def test_cancel(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_taskmgr2")
        mgr = AvatarManager(memory_store=store)
        task_mgr = TaskManager(avatar_manager=mgr)

        task = Task(source_message=_make_msg("hello"))
        await task_mgr.submit(task)
        assert await task_mgr.cancel(task.id) is True
        assert task.status == TaskStatus.CANCELLED

        # 已取消不能再取消
        assert await task_mgr.cancel(task.id) is False

        await mgr.shutdown()

    async def test_cancel_while_routing_does_not_start_task(self) -> None:
        """路由中的任务被取消后，不得恢复为运行态或启动执行。"""
        route_started = asyncio.Event()
        route_release = asyncio.Event()
        runner = MagicMock()
        runner.execute = AsyncMock(return_value="late result")

        async def delayed_route(message: UnifiedMessage) -> tuple:
            route_started.set()
            await route_release.wait()
            routed = Task(source_message=message, assigned_avatar="general")
            return runner, routed

        on_complete = AsyncMock()
        task_mgr = TaskManager(
            avatar_manager=MagicMock(),
            on_task_complete=on_complete,
        )
        task_mgr._router.route = delayed_route
        task = Task(source_message=_make_msg("hello"))
        await task_mgr.submit(task)

        process_task = asyncio.create_task(task_mgr.process_next())
        await route_started.wait()

        assert await task_mgr.cancel(task.id) is True
        assert task.status == TaskStatus.CANCELLED

        route_release.set()
        assert await process_task is task

        assert task.status == TaskStatus.CANCELLED
        assert task.result is None
        runner.execute.assert_not_awaited()
        assert task.id not in task_mgr._execution_tasks
        on_complete.assert_awaited_once_with(task)

    @pytest.mark.parametrize("swallow_cancel", [False, True])
    async def test_cancel_while_executing_stays_cancelled(
        self, swallow_cancel: bool,
    ) -> None:
        """执行中的取消必须清理任务，且忽略不合作执行器的迟到结果。"""
        execution_started = asyncio.Event()

        async def execute(task_id: str, content: str) -> str:
            execution_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if swallow_cancel:
                    return "late result"
                raise

        runner = MagicMock()
        runner.execute = AsyncMock(side_effect=execute)
        routed = Task(assigned_avatar="general")

        on_complete = AsyncMock()
        task_mgr = TaskManager(
            avatar_manager=MagicMock(),
            on_task_complete=on_complete,
        )
        task_mgr._router.route = AsyncMock(return_value=(runner, routed))
        task = Task(source_message=_make_msg("long task"))
        await task_mgr.submit(task)

        assert await task_mgr.process_next() is task
        await execution_started.wait()
        assert task.status == TaskStatus.RUNNING
        execution_task = task_mgr._execution_tasks[task.id]

        assert await task_mgr.cancel(task.id) is True
        try:
            await execution_task
        except asyncio.CancelledError:
            pass

        assert task.status == TaskStatus.CANCELLED
        assert task.result is None
        assert task.id not in task_mgr._execution_tasks
        on_complete.assert_awaited_once_with(task)

    @patch("src.engine.headless.create_engine")
    async def test_stats(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_taskmgr3")
        mgr = AvatarManager(memory_store=store)
        task_mgr = TaskManager(avatar_manager=mgr)

        await task_mgr.submit(Task(source_message=_make_msg("a")))
        await task_mgr.submit(Task(source_message=_make_msg("b")))

        stats = task_mgr.get_stats()
        assert stats["total"] == 2
        assert stats["by_status"]["pending"] == 2

        await mgr.shutdown()
