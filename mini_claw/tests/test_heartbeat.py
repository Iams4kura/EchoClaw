"""测试心跳系统。"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from script.avatar.models import Avatar, AvatarConfig, AvatarStatus
from script.heartbeat.monitor import HeartbeatMonitor, HeartbeatRecord
from script.heartbeat.reporter import ProgressReporter
from script.scheduler.models import Task, TaskStatus


class TestHeartbeatMonitor:
    """HeartbeatMonitor 测试。"""

    async def test_receive_heartbeat(self) -> None:
        monitor = HeartbeatMonitor()
        config = AvatarConfig(id="bot1", name="Bot1")
        avatar = Avatar(config=config, status=AvatarStatus.IDLE)

        await monitor.receive(avatar)
        status = monitor.get_status()

        assert status["tracked"] == 1
        assert "bot1" in status["records"]

    async def test_check_stuck(self) -> None:
        monitor = HeartbeatMonitor(stuck_threshold=0.1)

        # 模拟一个 BUSY 且过期的心跳
        monitor._records["bot1"] = HeartbeatRecord(
            avatar_id="bot1",
            timestamp=time.time() - 1.0,  # 1 秒前
            status=AvatarStatus.BUSY,
            current_tasks=["task1"],
        )

        stuck = monitor.check_stuck()
        assert "bot1" in stuck

    async def test_not_stuck_when_idle(self) -> None:
        monitor = HeartbeatMonitor(stuck_threshold=0.1)

        monitor._records["bot1"] = HeartbeatRecord(
            avatar_id="bot1",
            timestamp=time.time() - 1.0,
            status=AvatarStatus.IDLE,  # IDLE 不算卡住
        )

        stuck = monitor.check_stuck()
        assert len(stuck) == 0

    async def test_check_dead(self) -> None:
        monitor = HeartbeatMonitor(dead_threshold=0.1)

        monitor._records["bot1"] = HeartbeatRecord(
            avatar_id="bot1",
            timestamp=time.time() - 1.0,
            status=AvatarStatus.IDLE,
        )

        dead = monitor.check_dead()
        assert "bot1" in dead

    async def test_sleeping_not_dead(self) -> None:
        monitor = HeartbeatMonitor(dead_threshold=0.1)

        monitor._records["bot1"] = HeartbeatRecord(
            avatar_id="bot1",
            timestamp=time.time() - 1.0,
            status=AvatarStatus.SLEEPING,  # 休眠中不算死亡
        )

        dead = monitor.check_dead()
        assert len(dead) == 0


class TestProgressReporter:
    """ProgressReporter 测试。"""

    async def test_report_start(self) -> None:
        sent = []

        async def mock_send(task: Task, text: str) -> None:
            sent.append(text)

        reporter = ProgressReporter(send_fn=mock_send)
        task = Task(assigned_avatar="coder")
        task.update_status(TaskStatus.RUNNING)

        await reporter.report_start(task)
        assert len(sent) == 1
        assert "开始执行" in sent[0]

    async def test_report_complete(self) -> None:
        sent = []

        async def mock_send(task: Task, text: str) -> None:
            sent.append(text)

        reporter = ProgressReporter(send_fn=mock_send)
        task = Task(result="All done")
        task.update_status(TaskStatus.COMPLETED)

        await reporter.report_complete(task)
        assert len(sent) == 1
        assert "完成" in sent[0]
        assert "All done" in sent[0]

    async def test_report_failed(self) -> None:
        sent = []

        async def mock_send(task: Task, text: str) -> None:
            sent.append(text)

        reporter = ProgressReporter(send_fn=mock_send)
        task = Task(error="Something broke")
        task.update_status(TaskStatus.FAILED)

        await reporter.report_failed(task)
        assert len(sent) == 1
        assert "失败" in sent[0]

    async def test_cooldown_prevents_spam(self) -> None:
        sent = []

        async def mock_send(task: Task, text: str) -> None:
            sent.append(text)

        reporter = ProgressReporter(send_fn=mock_send, cooldown=100.0)
        task = Task()

        await reporter.report_progress(task, "step 1")
        await reporter.report_progress(task, "step 2")  # 应被冷却阻止

        assert len(sent) == 1

    async def test_no_send_fn_logs_only(self) -> None:
        reporter = ProgressReporter(send_fn=None)
        task = Task()

        # 不应抛错
        await reporter.report_start(task)
        await reporter.report_complete(task)
