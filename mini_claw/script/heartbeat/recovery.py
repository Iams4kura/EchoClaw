"""异常自愈 — 分身卡住或死亡时的恢复策略。"""

import logging
from typing import Optional

from ..avatar.manager import AvatarManager
from ..avatar.models import AvatarStatus

logger = logging.getLogger(__name__)


class RecoveryManager:
    """分身异常时的自愈策略。"""

    def __init__(self, avatar_manager: AvatarManager) -> None:
        self._avatar_mgr = avatar_manager

    async def handle_stuck(self, avatar_id: str) -> None:
        """处理卡住的分身。

        策略：
        1. 记录当前状态
        2. 如果是临时分身 → 直接回收
        3. 如果是常驻分身 → 休眠后唤醒（重建引擎）
        """
        runner = self._avatar_mgr.get_runner(avatar_id)
        if not runner:
            return

        avatar = runner.avatar
        logger.warning(
            "Handling stuck avatar: %s (tasks=%s)",
            avatar_id, avatar.current_tasks,
        )

        if avatar.config.type.value == "ephemeral":
            await self._avatar_mgr.reclaim(avatar_id)
            logger.info("Stuck ephemeral avatar %s reclaimed", avatar_id)
        else:
            # 常驻分身：休眠 → 唤醒（重建引擎，清理状态）
            await runner.sleep()
            await runner.wake()
            logger.info("Stuck resident avatar %s restarted", avatar_id)

    async def handle_dead(self, avatar_id: str) -> None:
        """处理死亡的分身。

        策略：
        1. 回收旧实例
        2. 如果是常驻分身 → 用原配置重新启动
        """
        runner = self._avatar_mgr.get_runner(avatar_id)
        if not runner:
            return

        config = runner.avatar.config
        logger.error("Handling dead avatar: %s", avatar_id)

        # 回收
        await self._avatar_mgr.reclaim(avatar_id)

        # 常驻分身自动重启
        if config.type.value == "resident":
            logger.info("Restarting dead resident avatar: %s", avatar_id)
            await self._avatar_mgr.start_resident(config)
