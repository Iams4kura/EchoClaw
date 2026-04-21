"""任务路由 — 意图识别 + 分身分配。"""

import logging
import re
from typing import List, Optional, Tuple

from ..avatar.manager import AvatarManager
from ..avatar.runner import AvatarRunner
from ..gateway.models import UnifiedMessage
from .models import Task, TaskStatus

logger = logging.getLogger(__name__)

# 显式指定分身的模式：@分身名 消息内容
MENTION_PATTERN = re.compile(r"^@(\S+)\s+(.+)", re.DOTALL)

# 关键词 → 分身 ID 映射（简单意图识别）
KEYWORD_ROUTES = {
    "coder": ["代码", "code", "编程", "debug", "bug", "重构", "refactor", "测试", "test",
              "函数", "function", "class", "import", "git", "commit", "pr"],
    "ops": ["部署", "deploy", "运维", "监控", "日志", "log", "docker", "k8s",
            "服务器", "server", "nginx", "进程", "process", "端口", "port"],
}


class TaskRouter:
    """根据消息内容路由到合适的分身。"""

    def __init__(
        self,
        avatar_manager: AvatarManager,
        personal_mode: bool = False,
    ) -> None:
        self._avatar_mgr = avatar_manager
        self._personal_mode = personal_mode

    async def route(self, msg: UnifiedMessage) -> Tuple[Optional[AvatarRunner], Task]:
        """路由消息到分身，返回 (runner, task)。

        路由优先级：
        1. Personal 模式: 直接路由到 unified 分身
        2. 显式指定: "@代码手 xxx" → 直接路由
        3. 关键词匹配: 消息中包含特定关键词 → 对应分身
        4. 默认分身: general
        """
        task = Task(
            source_message=msg,
            status=TaskStatus.PENDING,
        )

        # Personal 模式：直接路由到 unified
        if self._personal_mode:
            runner = self._avatar_mgr.get_runner("unified")
            if runner:
                task.assigned_avatar = "unified"
                return runner, task

        # 1. 检查显式 @mention
        mention_match = MENTION_PATTERN.match(msg.content)
        if mention_match:
            target_name = mention_match.group(1)
            runner = self._find_by_name_or_id(target_name)
            if runner:
                task.assigned_avatar = runner.avatar.config.id
                logger.info("Route by mention: @%s -> %s", target_name, runner.avatar.config.id)
                return runner, task

        # 2. 关键词匹配
        content_lower = msg.content.lower()
        best_avatar_id = None
        best_score = 0

        for avatar_id, keywords in KEYWORD_ROUTES.items():
            score = sum(1 for kw in keywords if kw in content_lower)
            if score > best_score:
                best_score = score
                best_avatar_id = avatar_id

        if best_avatar_id and best_score >= 1:
            runner = self._avatar_mgr.get_runner(best_avatar_id)
            if runner and runner.avatar.is_available:
                task.assigned_avatar = best_avatar_id
                logger.info(
                    "Route by keyword: score=%d -> %s", best_score, best_avatar_id
                )
                return runner, task

        # 3. 默认：选择任意可用分身（优先 general）
        runner = self._find_default()
        if runner:
            task.assigned_avatar = runner.avatar.config.id
            logger.info("Route to default: %s", runner.avatar.config.id)
        else:
            logger.warning("No available avatar for routing")

        return runner, task

    def _find_by_name_or_id(self, name: str) -> Optional[AvatarRunner]:
        """按名称或 ID 查找分身。"""
        # 先按 ID
        runner = self._avatar_mgr.get_runner(name)
        if runner:
            return runner

        # 按显示名称
        for r in self._avatar_mgr.list_all():
            if r.avatar.config.name == name:
                return r
        return None

    def _find_default(self) -> Optional[AvatarRunner]:
        """找默认可用分身。"""
        available = self._avatar_mgr.list_available()
        if not available:
            return None

        # 优先 general
        for r in available:
            if r.avatar.config.id == "general":
                return r

        # 否则选第一个空闲的
        return available[0]
