"""分身管理器 — 管理所有分身的生命周期。"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import Avatar, AvatarConfig, AvatarStatus, AvatarType
from .runner import AvatarRunner
from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

# 预置分身配置目录
PRESETS_DIR = Path(__file__).parent / "presets"


class AvatarManager:
    """管理所有分身的生命周期。"""

    def __init__(
        self,
        memory_store: MemoryStore,
        on_heartbeat: Optional[Callable] = None,
        personal_mode: bool = False,
    ) -> None:
        self._runners: Dict[str, AvatarRunner] = {}
        self._memory_store = memory_store
        self._on_heartbeat = on_heartbeat
        self._personal_mode = personal_mode
        self._lock = asyncio.Lock()

    async def start_resident(self, config: AvatarConfig) -> AvatarRunner:
        """启动常驻分身。"""
        async with self._lock:
            if config.id in self._runners:
                logger.warning("Avatar %s already running", config.id)
                return self._runners[config.id]

            avatar = Avatar(config=config)
            runner = AvatarRunner(
                avatar=avatar,
                memory_store=self._memory_store,
                on_heartbeat=self._on_heartbeat,
            )
            await runner.start()
            self._runners[config.id] = runner
            return runner

    async def spawn_ephemeral(
        self,
        base_config: AvatarConfig,
        task_id: str,
        config_override: Optional[Dict[str, Any]] = None,
    ) -> AvatarRunner:
        """派生临时分身（基于已有配置，自动回收）。"""
        # 生成唯一 ID
        ephemeral_id = f"ephemeral_{task_id}_{int(time.time())}"

        # 合并配置
        data = {
            "id": ephemeral_id,
            "name": f"{base_config.name} (临时#{task_id[:6]})",
            "type": "ephemeral",
            "system_prompt": base_config.system_prompt,
            "tools_whitelist": base_config.tools_whitelist,
            "memory_namespace": base_config.memory_namespace,
            "model": base_config.model,
            "working_dir": base_config.working_dir,
            "permission_mode": base_config.permission_mode,
            "max_concurrent_tasks": 1,
            "heartbeat_interval": base_config.heartbeat_interval,
            "max_idle_time": 600,  # 临时分身 10 分钟超时
        }
        if config_override:
            data.update(config_override)

        config = AvatarConfig.from_dict(data)
        return await self.start_resident(config)

    async def reclaim(self, avatar_id: str) -> None:
        """回收分身（停止并移除）。"""
        async with self._lock:
            runner = self._runners.pop(avatar_id, None)
            if runner:
                await runner.stop()
                logger.info("Avatar %s reclaimed", avatar_id)

    async def sleep_avatar(self, avatar_id: str) -> None:
        """休眠空闲分身（释放引擎资源，保留配置）。"""
        runner = self._runners.get(avatar_id)
        if runner and runner.avatar.status == AvatarStatus.IDLE:
            await runner.sleep()

    async def wake_avatar(self, avatar_id: str) -> None:
        """唤醒休眠分身。"""
        runner = self._runners.get(avatar_id)
        if runner and runner.avatar.status == AvatarStatus.SLEEPING:
            await runner.wake()

    def get_runner(self, avatar_id: str) -> Optional[AvatarRunner]:
        """获取分身运行器。"""
        return self._runners.get(avatar_id)

    def list_available(self) -> List[AvatarRunner]:
        """列出可接受任务的分身。"""
        return [r for r in self._runners.values() if r.avatar.is_available]

    def list_all(self) -> List[AvatarRunner]:
        """列出所有分身。"""
        return list(self._runners.values())

    def get_status(self) -> Dict[str, Any]:
        """获取所有分身状态概览。"""
        return {
            "total": len(self._runners),
            "avatars": [
                {
                    "id": r.avatar.config.id,
                    "name": r.avatar.config.name,
                    "type": r.avatar.config.type.value,
                    "status": r.avatar.status.value,
                    "current_tasks": r.avatar.current_tasks,
                    "idle_seconds": round(r.avatar.idle_seconds, 1),
                }
                for r in self._runners.values()
            ],
        }

    async def auto_sleep_idle(self) -> List[str]:
        """自动休眠超时空闲的常驻分身。"""
        slept = []
        for runner in self._runners.values():
            avatar = runner.avatar
            if (
                avatar.status == AvatarStatus.IDLE
                and avatar.config.type == AvatarType.RESIDENT
                and avatar.idle_seconds > avatar.config.max_idle_time
            ):
                await runner.sleep()
                slept.append(avatar.config.id)
        return slept

    async def auto_reclaim_ephemeral(self) -> List[str]:
        """自动回收已完成的临时分身。"""
        to_reclaim = []
        for avatar_id, runner in self._runners.items():
            avatar = runner.avatar
            if (
                avatar.config.type == AvatarType.EPHEMERAL
                and avatar.status == AvatarStatus.IDLE
                and avatar.idle_seconds > avatar.config.max_idle_time
            ):
                to_reclaim.append(avatar_id)

        for avatar_id in to_reclaim:
            await self.reclaim(avatar_id)
        return to_reclaim

    async def shutdown(self) -> None:
        """关闭所有分身。"""
        for avatar_id in list(self._runners.keys()):
            await self.reclaim(avatar_id)
        logger.info("All avatars shut down")

    async def start_personal(self) -> Optional[AvatarRunner]:
        """Personal 模式：加载并启动统一分身。"""
        unified_path = PRESETS_DIR / "unified.yaml"
        if not unified_path.exists():
            logger.error("unified.yaml not found in %s", PRESETS_DIR)
            return None
        config = AvatarConfig.from_yaml(str(unified_path))
        return await self.start_resident(config)

    @staticmethod
    def load_presets(presets_dir: Optional[str] = None) -> List[AvatarConfig]:
        """加载预置分身配置。"""
        d = Path(presets_dir) if presets_dir else PRESETS_DIR
        configs = []
        if d.exists():
            for f in sorted(d.glob("*.yaml")):
                try:
                    configs.append(AvatarConfig.from_yaml(str(f)))
                except Exception as e:
                    logger.warning("Failed to load preset %s: %s", f, e)
        return configs
