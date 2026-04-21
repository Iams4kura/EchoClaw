"""测试 Routine 自驱调度系统。"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from script.routine.builtin import BUILTIN_ROUTINES, get_builtin_routines
from script.routine.models import RoutineFrequency, RoutineJob
from script.routine.scheduler import RoutineScheduler


# ── RoutineJob 测试 ──────────────────────────────────────────

class TestRoutineJob:
    def test_default_values(self) -> None:
        job = RoutineJob(name="test", description="test", prompt="do something")
        assert job.frequency == RoutineFrequency.DAILY
        assert job.enabled is True
        assert job.executor == "brain"

    def test_system_job(self) -> None:
        job = RoutineJob(name="sys_test", description="", prompt="")
        assert job.is_system_job is True

    def test_non_system_job(self) -> None:
        job = RoutineJob(name="custom_task", description="", prompt="")
        assert job.is_system_job is False


# ── 内置任务测试 ─────────────────────────────────────────────

class TestBuiltinRoutines:
    def test_builtins_exist(self) -> None:
        assert len(BUILTIN_ROUTINES) >= 3

    def test_all_have_sys_prefix(self) -> None:
        for r in BUILTIN_ROUTINES:
            assert r.name.startswith("sys_"), f"{r.name} 应以 sys_ 开头"

    def test_get_builtin_filters_disabled(self) -> None:
        enabled = get_builtin_routines()
        assert all(r.enabled for r in enabled)


# ── RoutineScheduler 测试 ────────────────────────────────────

class TestRoutineScheduler:
    def test_initial_state(self) -> None:
        s = RoutineScheduler()
        assert s.job_count == 0

    def test_load_builtin(self) -> None:
        s = RoutineScheduler()
        s.load_builtin()
        assert s.job_count >= 3

    def test_load_from_config(self) -> None:
        s = RoutineScheduler()
        s.load_from_config([
            {
                "name": "test_job",
                "prompt": "test prompt",
                "description": "test desc",
                "frequency": "daily",
                "hour": 10,
            },
        ])
        assert s.job_count == 1

    def test_load_invalid_config(self) -> None:
        s = RoutineScheduler()
        s.load_from_config([{"invalid": "data"}])  # 缺少 name/prompt
        assert s.job_count == 0

    def test_list_jobs(self) -> None:
        s = RoutineScheduler()
        s.load_builtin()
        jobs = s.list_jobs()
        assert len(jobs) >= 3
        assert all("name" in j for j in jobs)


class TestSchedulerCronMatch:
    def test_wildcard(self) -> None:
        assert RoutineScheduler._match_cron("* * * * *", datetime(2026, 4, 14, 9, 0))

    def test_exact_minute_hour(self) -> None:
        assert RoutineScheduler._match_cron("0 9 * * *", datetime(2026, 4, 14, 9, 0))
        assert not RoutineScheduler._match_cron("0 9 * * *", datetime(2026, 4, 14, 10, 0))

    def test_step(self) -> None:
        assert RoutineScheduler._match_cron("*/5 * * * *", datetime(2026, 4, 14, 9, 0))
        assert RoutineScheduler._match_cron("*/5 * * * *", datetime(2026, 4, 14, 9, 15))
        assert not RoutineScheduler._match_cron("*/5 * * * *", datetime(2026, 4, 14, 9, 3))

    def test_invalid_expr(self) -> None:
        assert not RoutineScheduler._match_cron("bad", datetime.now())
        assert not RoutineScheduler._match_cron("1 2 3", datetime.now())


class TestSchedulerShouldRun:
    def test_once_never_ran(self) -> None:
        s = RoutineScheduler()
        job = RoutineJob(name="once_job", description="", prompt="", frequency=RoutineFrequency.ONCE)
        assert s._should_run(job, datetime.now()) is True

    def test_once_already_ran(self) -> None:
        s = RoutineScheduler()
        job = RoutineJob(name="once_job", description="", prompt="", frequency=RoutineFrequency.ONCE)
        s._last_run["once_job"] = 1000.0
        assert s._should_run(job, datetime.now()) is False

    def test_hourly_interval(self) -> None:
        import time
        s = RoutineScheduler()
        job = RoutineJob(
            name="hourly_job", description="", prompt="",
            frequency=RoutineFrequency.HOURLY, interval_minutes=30,
        )
        # 从未运行 → 应该运行
        assert s._should_run(job, datetime.now()) is True

        # 刚运行过 → 不应该运行
        s._last_run["hourly_job"] = time.time()
        assert s._should_run(job, datetime.now()) is False

        # 31 分钟前运行过 → 应该运行
        s._last_run["hourly_job"] = time.time() - 31 * 60
        assert s._should_run(job, datetime.now()) is True

    async def test_trigger_calls_callback(self) -> None:
        callback = AsyncMock()
        s = RoutineScheduler(on_trigger=callback)

        job = RoutineJob(name="test", description="test", prompt="do it")
        await s._trigger(job)

        callback.assert_called_once()
        msg = callback.call_args[0][0]
        assert msg.platform == "routine"
        assert msg.content == "do it"
