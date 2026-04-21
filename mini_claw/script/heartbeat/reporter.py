"""进度推送 — 定期向用户推送任务进度。"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from ..scheduler.models import Task, TaskStatus

logger = logging.getLogger(__name__)


class ProgressReporter:
    """向用户推送任务进度。

    推送策略：
    - 任务开始：立即通知
    - 执行中：每 interval 秒推送一次
    - 完成/失败：立即通知
    - 防刷屏：合并短时间内的多条更新
    """

    def __init__(
        self,
        send_fn: Optional[Callable] = None,
        interval: float = 300.0,     # 默认 5 分钟推送一次
        cooldown: float = 10.0,      # 最小推送间隔
    ) -> None:
        self._send_fn = send_fn
        self._interval = interval
        self._cooldown = cooldown
        self._last_sent: Dict[str, float] = {}  # task_id → 上次推送时间

    async def report_start(self, task: Task) -> None:
        """任务开始通知。"""
        await self._send(task, f"🔄 任务开始执行\n分身: {task.assigned_avatar}")

    async def report_complete(self, task: Task) -> None:
        """任务完成通知。"""
        duration = task.updated_at - task.created_at
        text = f"✅ 任务完成 ({duration:.1f}s)"
        if task.result and len(task.result) <= 200:
            text += f"\n结果: {task.result}"
        elif task.result:
            text += f"\n结果: {task.result[:200]}..."
        await self._send(task, text)

    async def report_failed(self, task: Task) -> None:
        """任务失败通知。"""
        text = f"❌ 任务失败"
        if task.error:
            text += f"\n错误: {task.error[:200]}"
        await self._send(task, text)

    async def report_progress(self, task: Task, progress: str) -> None:
        """进度更新（有防刷屏）。"""
        now = time.time()
        last = self._last_sent.get(task.id, 0)
        if now - last < self._cooldown:
            return  # 冷却期内不推送
        await self._send(task, f"⏳ {progress}")

    async def on_task_status_change(self, task: Task) -> None:
        """统一处理任务状态变更。"""
        if task.status == TaskStatus.RUNNING:
            await self.report_start(task)
        elif task.status == TaskStatus.COMPLETED:
            await self.report_complete(task)
        elif task.status == TaskStatus.FAILED:
            await self.report_failed(task)

    async def _send(self, task: Task, text: str) -> None:
        """发送通知。"""
        self._last_sent[task.id] = time.time()

        if self._send_fn:
            try:
                await self._send_fn(task, text)
            except Exception as e:
                logger.warning("Progress report send failed: %s", e)
        else:
            logger.info("Progress [%s]: %s", task.id[:8], text)
