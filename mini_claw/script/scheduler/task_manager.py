"""任务管理器 — 任务 CRUD + 生命周期 + 内存优先级队列。"""

import asyncio
import heapq
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .models import Task, TaskStatus
from ..avatar.runner import AvatarRunner
from ..avatar.manager import AvatarManager
from .router import TaskRouter

logger = logging.getLogger(__name__)


class TaskManager:
    """管理任务的完整生命周期。

    P2 实现：纯内存，不持久化。
    """

    def __init__(
        self,
        avatar_manager: AvatarManager,
        on_task_complete: Optional[Callable] = None,
    ) -> None:
        self._avatar_mgr = avatar_manager
        self._router = TaskRouter(avatar_manager)
        self._tasks: Dict[str, Task] = {}
        # 优先级队列: (priority, created_at, task_id)
        self._queue: List[tuple] = []
        self._on_task_complete = on_task_complete
        self._lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None

    async def submit(self, task: Task) -> Task:
        """提交任务。"""
        async with self._lock:
            self._tasks[task.id] = task
            heapq.heappush(self._queue, (task.priority, task.created_at, task.id))
            logger.info("Task submitted: %s (priority=%d)", task.id, task.priority)
        return task

    async def cancel(self, task_id: str) -> bool:
        """取消任务。"""
        task = self._tasks.get(task_id)
        if not task or task.is_terminal:
            return False
        task.update_status(TaskStatus.CANCELLED)
        logger.info("Task cancelled: %s", task_id)
        return True

    def get(self, task_id: str) -> Optional[Task]:
        """获取任务信息。"""
        return self._tasks.get(task_id)

    def list_active(self) -> List[Task]:
        """列出所有活跃任务。"""
        return [t for t in self._tasks.values() if not t.is_terminal]

    def list_all(self) -> List[Task]:
        """列出所有任务。"""
        return list(self._tasks.values())

    async def process_next(self) -> Optional[Task]:
        """从队列中取出下一个任务并执行。"""
        async with self._lock:
            # 找到可执行的任务
            while self._queue:
                priority, created_at, task_id = heapq.heappop(self._queue)
                task = self._tasks.get(task_id)
                if task and task.status == TaskStatus.PENDING:
                    break
            else:
                return None

        # 路由到分身
        runner, routed_task = await self._router.route(task.source_message)

        if not runner:
            task.update_status(TaskStatus.FAILED)
            task.error = "No available avatar"
            return task

        task.assigned_avatar = routed_task.assigned_avatar
        task.update_status(TaskStatus.RUNNING)

        # 异步执行
        asyncio.create_task(self._execute_task(runner, task))
        return task

    async def _execute_task(self, runner: AvatarRunner, task: Task) -> None:
        """在分身中执行任务。"""
        try:
            content = task.source_message.content if task.source_message else ""
            result = await runner.execute(task.id, content)
            task.result = result
            task.update_status(TaskStatus.COMPLETED)
            logger.info("Task completed: %s (avatar=%s)", task.id, task.assigned_avatar)
        except Exception as e:
            task.error = str(e)
            task.update_status(TaskStatus.FAILED)
            logger.error("Task failed: %s - %s", task.id, e)

        if self._on_task_complete:
            try:
                await self._on_task_complete(task)
            except Exception as e:
                logger.warning("on_task_complete callback error: %s", e)

    async def start_worker(self) -> None:
        """启动后台工作循环，持续处理队列。"""
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop_worker(self) -> None:
        """停止工作循环。"""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _worker_loop(self) -> None:
        """持续处理任务队列。"""
        while True:
            try:
                task = await self.process_next()
                if task is None:
                    await asyncio.sleep(0.5)  # 队列空，等待
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Worker loop error: %s", e)
                await asyncio.sleep(1)

    def get_stats(self) -> Dict[str, Any]:
        """获取任务统计。"""
        statuses = {}
        for t in self._tasks.values():
            statuses[t.status.value] = statuses.get(t.status.value, 0) + 1
        return {
            "total": len(self._tasks),
            "queue_size": len(self._queue),
            "by_status": statuses,
        }
