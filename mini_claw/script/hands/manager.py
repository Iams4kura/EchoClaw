"""HandsManager — 管理所有用户的 mini_claude 引擎实例池。

替代 SessionManager 的引擎管理职责。
Brain 通过 HandsManager 委派编码/文件任务给 mini_claude。
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from .engine_executor import EngineExecutor
from .models import ExecutionResult

logger = logging.getLogger(__name__)


class HandsManager:
    """引擎实例池管理器。

    按 user_id 维护 EngineExecutor 实例。
    支持懒初始化、空闲回收、全局关闭。
    """

    _OWNER_KEY = "__owner__"

    def __init__(
        self,
        working_dir: str = ".",
        permission_mode: str = "auto",
        model: Optional[str] = None,
        system_prompt_extra: str = "",
        max_idle_seconds: float = 3600.0,
        personal_mode: bool = False,
    ) -> None:
        self._working_dir = working_dir
        self._permission_mode = permission_mode
        self._model = model
        self._system_prompt_extra = system_prompt_extra
        self._max_idle_seconds = max_idle_seconds
        self._personal_mode = personal_mode
        self._executors: Dict[str, EngineExecutor] = {}
        self._last_active: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        # AskUser 交互挂起状态（per-user）
        self._pending_questions: Dict[str, asyncio.Future] = {}
        self._current_question: Dict[str, Dict[str, Any]] = {}

    def _uid(self, user_id: str) -> str:
        """personal 模式下所有用户映射到同一个引擎实例。"""
        return self._OWNER_KEY if self._personal_mode else user_id

    async def execute(
        self,
        user_id: str,
        prompt: str,
        cancel_event: Optional["asyncio.Event"] = None,
    ) -> ExecutionResult:
        """获取或创建用户的执行器，执行 prompt。

        cancel_event: 可选取消信号，执行前检查，已取消则跳过执行。
        """
        if cancel_event and cancel_event.is_set():
            return ExecutionResult(success=False, output="", error="Cancelled before execution")
        uid = self._uid(user_id)
        executor = await self._get_or_create(uid)
        self._last_active[uid] = time.time()
        return await executor.execute(prompt)

    async def reset_user(self, user_id: str) -> bool:
        """重置指定用户的引擎。"""
        uid = self._uid(user_id)
        if uid in self._executors:
            await self._executors[uid].reset()
            logger.info("用户 %s 的引擎已重置", uid)
            return True
        return False

    async def remove_user(self, user_id: str) -> None:
        """销毁指定用户的引擎。"""
        uid = self._uid(user_id)
        async with self._lock:
            if uid in self._executors:
                await self._executors[uid].teardown()
                del self._executors[uid]
                self._last_active.pop(uid, None)

    async def cleanup_idle(self) -> int:
        """清理空闲超时的引擎实例，返回清理数量。"""
        now = time.time()
        to_remove = [
            uid
            for uid, last in self._last_active.items()
            if now - last > self._max_idle_seconds
        ]
        for uid in to_remove:
            await self.remove_user(uid)
            logger.info("清理空闲引擎: user_id=%s", uid)
        return len(to_remove)

    async def shutdown(self) -> None:
        """关闭所有引擎实例。"""
        async with self._lock:
            for executor in self._executors.values():
                await executor.teardown()
            self._executors.clear()
            self._last_active.clear()
            logger.info("HandsManager 已关闭")

    @property
    def active_count(self) -> int:
        return len(self._executors)

    def get_status(self) -> Dict[str, Any]:
        """返回引擎池状态概要。"""
        return {
            "active_executors": self.active_count,
            "users": list(self._executors.keys()),
            "config": {
                "working_dir": self._working_dir,
                "model": self._model,
                "max_idle_seconds": self._max_idle_seconds,
            },
        }

    # ── AskUser 交互支持 ──────────────────────────────────────

    def _make_ask_user_callback(self, user_id: str):
        """为指定用户创建 AskUser 回调，挂起等待 Web 前端回答。"""

        async def _on_ask_user(question: str, options: list) -> str:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[str] = loop.create_future()
            self._current_question[user_id] = {
                "question": question,
                "options": options,
            }
            self._pending_questions[user_id] = fut
            logger.info("AskUser 挂起等待: user_id=%s, question=%s", user_id, question[:80])
            try:
                answer = await asyncio.wait_for(fut, timeout=300.0)
            except asyncio.TimeoutError:
                logger.warning("AskUser 超时 (5min): user_id=%s", user_id)
                return "（用户未回复，已超时跳过）"
            finally:
                self._pending_questions.pop(user_id, None)
                self._current_question.pop(user_id, None)
            logger.info("AskUser 收到回答: user_id=%s, answer=%s", user_id, answer[:80])
            return answer

        return _on_ask_user

    def get_pending_question(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户当前挂起的问题，无则返回 None。"""
        return self._current_question.get(self._uid(user_id))

    def submit_answer(self, user_id: str, answer: str) -> bool:
        """提交用户对挂起问题的回答，resolve Future。返回是否成功。"""
        uid = self._uid(user_id)
        fut = self._pending_questions.get(uid)
        if fut is None or fut.done():
            return False
        fut.set_result(answer)
        return True

    async def _get_or_create(self, user_id: str) -> EngineExecutor:
        """获取或创建用户的 EngineExecutor。"""
        if user_id not in self._executors:
            async with self._lock:
                # 双重检查
                if user_id not in self._executors:
                    executor = EngineExecutor(
                        working_dir=self._working_dir,
                        permission_mode=self._permission_mode,
                        model=self._model,
                        system_prompt_extra=self._system_prompt_extra,
                        on_ask_user=self._make_ask_user_callback(user_id),
                    )
                    await executor.initialize()
                    self._executors[user_id] = executor
                    logger.info("创建新引擎: user_id=%s", user_id)
        return self._executors[user_id]
