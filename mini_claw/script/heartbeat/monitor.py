"""心跳监控 — 收集心跳、检测卡住和死亡分身。"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..avatar.models import Avatar, AvatarStatus

logger = logging.getLogger(__name__)


@dataclass
class HeartbeatRecord:
    """一条心跳记录。"""
    avatar_id: str
    timestamp: float
    status: AvatarStatus
    current_tasks: List[str] = field(default_factory=list)
    progress: Optional[str] = None


class HeartbeatMonitor:
    """收集和判定心跳状态。"""

    def __init__(
        self,
        stuck_threshold: float = 300.0,   # 5 分钟无活动 → 卡住
        dead_threshold: float = 120.0,    # 2 分钟无心跳 → 死亡
        on_stuck: Optional[Callable] = None,
        on_dead: Optional[Callable] = None,
    ) -> None:
        self._records: Dict[str, HeartbeatRecord] = {}
        self._stuck_threshold = stuck_threshold
        self._dead_threshold = dead_threshold
        self._on_stuck = on_stuck
        self._on_dead = on_dead
        self._monitor_task: Optional[asyncio.Task] = None

    async def receive(self, avatar: Avatar) -> None:
        """接收心跳。"""
        self._records[avatar.config.id] = HeartbeatRecord(
            avatar_id=avatar.config.id,
            timestamp=time.time(),
            status=avatar.status,
            current_tasks=list(avatar.current_tasks),
        )

    def check_stuck(self) -> List[str]:
        """检测卡住的分身（长时间 BUSY 且无进展）。"""
        stuck = []
        now = time.time()
        for avatar_id, record in self._records.items():
            if (
                record.status == AvatarStatus.BUSY
                and (now - record.timestamp) > self._stuck_threshold
            ):
                stuck.append(avatar_id)
        return stuck

    def check_dead(self) -> List[str]:
        """检测已死分身（长时间无心跳）。"""
        dead = []
        now = time.time()
        for avatar_id, record in self._records.items():
            if (
                record.status not in (AvatarStatus.SLEEPING, AvatarStatus.DEAD)
                and (now - record.timestamp) > self._dead_threshold
            ):
                dead.append(avatar_id)
        return dead

    async def start(self, check_interval: float = 30.0) -> None:
        """启动监控循环。"""
        self._monitor_task = asyncio.create_task(self._monitor_loop(check_interval))

    async def stop(self) -> None:
        """停止监控。"""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self, interval: float) -> None:
        """定期检查分身状态。"""
        while True:
            try:
                await asyncio.sleep(interval)

                stuck = self.check_stuck()
                for avatar_id in stuck:
                    logger.warning("Avatar %s appears stuck", avatar_id)
                    if self._on_stuck:
                        await self._on_stuck(avatar_id)

                dead = self.check_dead()
                for avatar_id in dead:
                    logger.error("Avatar %s appears dead", avatar_id)
                    if self._on_dead:
                        await self._on_dead(avatar_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Monitor loop error: %s", e)

    def get_status(self) -> Dict[str, Any]:
        """获取心跳监控状态。"""
        now = time.time()
        return {
            "tracked": len(self._records),
            "stuck": self.check_stuck(),
            "dead": self.check_dead(),
            "records": {
                aid: {
                    "status": r.status.value,
                    "age_seconds": round(now - r.timestamp, 1),
                    "tasks": r.current_tasks,
                }
                for aid, r in self._records.items()
            },
        }
