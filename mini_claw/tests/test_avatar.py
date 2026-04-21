"""测试分身系统。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from script.avatar.models import Avatar, AvatarConfig, AvatarStatus, AvatarType
from script.avatar.manager import AvatarManager
from script.avatar.runner import AvatarRunner
from script.memory.store import MemoryStore


class TestAvatarConfig:
    """AvatarConfig 测试。"""

    def test_from_dict(self) -> None:
        data = {
            "id": "coder",
            "name": "代码手",
            "type": "resident",
            "system_prompt": "你是代码手",
            "max_concurrent_tasks": 2,
        }
        config = AvatarConfig.from_dict(data)
        assert config.id == "coder"
        assert config.name == "代码手"
        assert config.type == AvatarType.RESIDENT
        assert config.max_concurrent_tasks == 2

    def test_memory_namespace_defaults_to_id(self) -> None:
        config = AvatarConfig(id="test", name="Test")
        assert config.memory_namespace == "test"

    def test_from_yaml(self, tmp_path: str) -> None:
        yaml_content = """
id: test_bot
name: "测试分身"
type: ephemeral
system_prompt: "你是测试用分身"
max_idle_time: 600
"""
        yaml_file = Path(str(tmp_path)) / "test.yaml"
        yaml_file.write_text(yaml_content)

        config = AvatarConfig.from_yaml(str(yaml_file))
        assert config.id == "test_bot"
        assert config.type == AvatarType.EPHEMERAL
        assert config.max_idle_time == 600

    def test_load_presets(self) -> None:
        presets = AvatarManager.load_presets()
        assert len(presets) >= 3  # general, coder, ops
        ids = [p.id for p in presets]
        assert "general" in ids
        assert "coder" in ids
        assert "ops" in ids


class TestAvatar:
    """Avatar 实例测试。"""

    def test_is_available_when_idle(self) -> None:
        config = AvatarConfig(id="t", name="T", max_concurrent_tasks=2)
        avatar = Avatar(config=config, status=AvatarStatus.IDLE)
        assert avatar.is_available is True

    def test_not_available_when_sleeping(self) -> None:
        config = AvatarConfig(id="t", name="T")
        avatar = Avatar(config=config, status=AvatarStatus.SLEEPING)
        assert avatar.is_available is False

    def test_not_available_when_full(self) -> None:
        config = AvatarConfig(id="t", name="T", max_concurrent_tasks=1)
        avatar = Avatar(config=config, status=AvatarStatus.BUSY)
        avatar.current_tasks = ["task1"]
        assert avatar.is_available is False

    def test_available_when_busy_but_has_capacity(self) -> None:
        config = AvatarConfig(id="t", name="T", max_concurrent_tasks=3)
        avatar = Avatar(config=config, status=AvatarStatus.BUSY)
        avatar.current_tasks = ["task1"]
        assert avatar.is_available is True


class TestAvatarRunner:
    """AvatarRunner 测试。"""

    @patch("src.engine.headless.create_engine")
    async def test_start_and_execute(self, mock_create: AsyncMock) -> None:
        mock_engine = MagicMock()
        mock_engine.run_turn = AsyncMock(return_value="done!")
        mock_create.return_value = mock_engine

        config = AvatarConfig(id="test", name="Test")
        avatar = Avatar(config=config)
        store = MemoryStore(root="/tmp/test_avatar_mem")

        runner = AvatarRunner(avatar=avatar, memory_store=store)
        await runner.start()

        assert avatar.status == AvatarStatus.IDLE

        result = await runner.execute("task1", "hello")
        assert result == "done!"
        assert avatar.status == AvatarStatus.IDLE  # 任务完成后回到 IDLE

        await runner.stop()
        assert avatar.status == AvatarStatus.DEAD

    @patch("src.engine.headless.create_engine")
    async def test_sleep_and_wake(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()

        config = AvatarConfig(id="test", name="Test")
        avatar = Avatar(config=config)
        store = MemoryStore(root="/tmp/test_avatar_mem2")

        runner = AvatarRunner(avatar=avatar, memory_store=store)
        await runner.start()
        assert avatar.status == AvatarStatus.IDLE

        await runner.sleep()
        assert avatar.status == AvatarStatus.SLEEPING
        assert avatar.engine is None

        await runner.wake()
        assert avatar.status == AvatarStatus.IDLE
        assert avatar.engine is not None

        await runner.stop()


class TestAvatarManager:
    """AvatarManager 测试。"""

    @patch("src.engine.headless.create_engine")
    async def test_start_resident(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_mgr_mem")
        mgr = AvatarManager(memory_store=store)

        config = AvatarConfig(id="bot1", name="Bot1")
        runner = await mgr.start_resident(config)

        assert runner is not None
        assert len(mgr.list_all()) == 1
        assert mgr.get_runner("bot1") is runner

        await mgr.shutdown()

    @patch("src.engine.headless.create_engine")
    async def test_list_available(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_mgr_mem2")
        mgr = AvatarManager(memory_store=store)

        await mgr.start_resident(AvatarConfig(id="a", name="A"))
        await mgr.start_resident(AvatarConfig(id="b", name="B"))

        available = mgr.list_available()
        assert len(available) == 2

        await mgr.shutdown()

    @patch("src.engine.headless.create_engine")
    async def test_reclaim(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_mgr_mem3")
        mgr = AvatarManager(memory_store=store)

        await mgr.start_resident(AvatarConfig(id="temp", name="Temp"))
        assert len(mgr.list_all()) == 1

        await mgr.reclaim("temp")
        assert len(mgr.list_all()) == 0

    @patch("src.engine.headless.create_engine")
    async def test_get_status(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_mgr_mem4")
        mgr = AvatarManager(memory_store=store)

        await mgr.start_resident(AvatarConfig(id="bot", name="Bot"))
        status = mgr.get_status()

        assert status["total"] == 1
        assert status["avatars"][0]["id"] == "bot"
        assert status["avatars"][0]["status"] == "idle"

        await mgr.shutdown()

    @patch("src.engine.headless.create_engine")
    async def test_spawn_ephemeral(self, mock_create: AsyncMock) -> None:
        mock_create.return_value = MagicMock()
        store = MemoryStore(root="/tmp/test_mgr_mem5")
        mgr = AvatarManager(memory_store=store)

        base = AvatarConfig(id="coder", name="代码手")
        runner = await mgr.spawn_ephemeral(base, task_id="abc123")

        assert runner.avatar.config.type == AvatarType.EPHEMERAL
        assert "临时" in runner.avatar.config.name

        await mgr.shutdown()
