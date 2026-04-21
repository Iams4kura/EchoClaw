"""分身运行器 — 单个分身的任务执行循环。"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .models import Avatar, AvatarStatus
from ..memory.store import MemoryStore
from ..memory.loader import MemoryLoader
from ..memory.extractor import MemoryExtractor

logger = logging.getLogger(__name__)


class AvatarRunner:
    """单个分身的执行环境，封装引擎调用 + 记忆 + 心跳上报。"""

    def __init__(
        self,
        avatar: Avatar,
        memory_store: MemoryStore,
        on_heartbeat: Optional[Callable] = None,
    ) -> None:
        self._avatar = avatar
        self._memory_store = memory_store
        self._memory_loader = MemoryLoader(memory_store)
        self._memory_extractor = MemoryExtractor()
        self._on_heartbeat = on_heartbeat
        self._conversation: List[Dict[str, str]] = []
        self._heartbeat_task: Optional[asyncio.Task] = None

    @property
    def avatar(self) -> Avatar:
        return self._avatar

    async def start(self) -> None:
        """启动分身（初始化引擎 + 开始心跳）。"""
        if self._avatar.engine is None:
            await self._init_engine()

        self._avatar.status = AvatarStatus.IDLE
        self._avatar.touch()

        # 启动心跳循环
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Avatar %s (%s) started", self._avatar.config.id, self._avatar.config.name)

    async def stop(self) -> None:
        """停止分身。"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # 提取记忆
        if self._conversation:
            await self._extract_memories()

        self._avatar.status = AvatarStatus.DEAD
        logger.info("Avatar %s stopped", self._avatar.config.id)

    async def execute(self, task_id: str, text: str) -> str:
        """执行一个任务。"""
        avatar = self._avatar
        avatar.current_tasks.append(task_id)
        avatar.status = AvatarStatus.BUSY
        avatar.touch()

        try:
            self._conversation.append({"role": "user", "content": text})
            result = await avatar.engine.run_turn(text)
            self._conversation.append({"role": "assistant", "content": result})
            return result
        finally:
            if task_id in avatar.current_tasks:
                avatar.current_tasks.remove(task_id)
            if not avatar.current_tasks:
                avatar.status = AvatarStatus.IDLE
            avatar.touch()

    async def sleep(self) -> None:
        """休眠分身（释放引擎资源但保留配置和记忆）。"""
        # 先提取记忆
        if self._conversation:
            await self._extract_memories()
            self._conversation.clear()

        self._avatar.engine = None
        self._avatar.status = AvatarStatus.SLEEPING
        logger.info("Avatar %s sleeping", self._avatar.config.id)

    async def wake(self) -> None:
        """唤醒分身。"""
        if self._avatar.status != AvatarStatus.SLEEPING:
            return

        await self._init_engine()
        self._avatar.status = AvatarStatus.IDLE
        self._avatar.touch()
        logger.info("Avatar %s woke up", self._avatar.config.id)

    async def _init_engine(self) -> None:
        """初始化引擎实例，注入记忆和分身角色 prompt。"""
        from src.engine.headless import create_engine

        config = self._avatar.config

        # 加载记忆
        memory_context = self._memory_loader.load_for_context(
            current_message="",
            avatar_id=config.memory_namespace,
        )

        # 组合 system prompt：角色设定 + 记忆
        extra_parts = []
        if config.system_prompt:
            extra_parts.append(config.system_prompt)
        if memory_context:
            extra_parts.append(memory_context)

        system_prompt_extra = "\n\n".join(extra_parts)

        self._avatar.engine = await create_engine(
            working_dir=config.working_dir,
            permission_mode=config.permission_mode,
            system_prompt_extra=system_prompt_extra,
        )

    async def _extract_memories(self) -> None:
        """从对话中提取记忆。"""
        try:
            namespace = self._avatar.config.memory_namespace
            existing = self._memory_store.list_all(namespace)
            entries = await self._memory_extractor.extract(
                conversation=self._conversation,
                existing_memories=existing,
                source_avatar=self._avatar.config.id,
            )
            for entry in entries:
                existing_entry = self._memory_store.find_by_name(entry.name, namespace)
                if existing_entry:
                    existing_entry.content = entry.content
                    existing_entry.description = entry.description
                    self._memory_store.update(existing_entry, namespace)
                else:
                    self._memory_store.save(entry, namespace)
        except Exception as e:
            logger.warning("Memory extraction failed for avatar %s: %s",
                           self._avatar.config.id, e)

    async def _heartbeat_loop(self) -> None:
        """定期发送心跳。"""
        interval = self._avatar.config.heartbeat_interval
        while True:
            try:
                await asyncio.sleep(interval)
                self._avatar.last_heartbeat = time.time()
                if self._on_heartbeat:
                    await self._on_heartbeat(self._avatar)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat error for %s: %s", self._avatar.config.id, e)
