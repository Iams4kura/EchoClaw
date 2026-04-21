"""引擎会话管理 — 按用户维护 mini_claude QueryEngine 实例。"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

# mini_claude 引擎的无头入口（通过 pip install -e ../mini_claude 安装，包名为 src）
from src.engine.headless import create_engine

from .memory.store import MemoryStore
from .memory.loader import MemoryLoader
from .memory.extractor import MemoryExtractor

logger = logging.getLogger(__name__)


class EngineSession:
    """封装一个 mini_claude QueryEngine 实例的单用户会话。"""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.created_at: float = time.time()
        self.last_active: float = time.time()
        self.turn_count: int = 0
        # 记录对话历史（用于记忆提取）
        self._conversation: List[Dict[str, str]] = []

    async def handle(self, text: str) -> str:
        """执行一轮对话，返回引擎响应文本。

        如果 text 以 / 开头且匹配一个 skill，自动将 skill 内容注入。
        """
        self.last_active = time.time()
        self.turn_count += 1

        # 拦截 skill 调用
        actual_text = self._maybe_expand_skill(text)

        self._conversation.append({"role": "user", "content": text})
        result = await self.engine.run_turn(actual_text)
        self._conversation.append({"role": "assistant", "content": result})
        return result

    @staticmethod
    def _maybe_expand_skill(text: str) -> str:
        """如果 text 是 /skillname [args]，展开为 skill 内容。"""
        if not text.startswith("/"):
            return text
        parts = text.strip().split(maxsplit=1)
        skill_name = parts[0].lstrip("/")
        args = parts[1] if len(parts) > 1 else ""

        try:
            from src.tools.skill import SkillTool
            content = SkillTool()._find_skill(skill_name)
            if content:
                prompt = f"<skill>{content}</skill>"
                if args:
                    prompt += f"\n\nUser arguments: {args}"
                return prompt
        except Exception:
            pass
        return text

    @property
    def conversation(self) -> List[Dict[str, str]]:
        """获取对话历史（只读）。"""
        return list(self._conversation)


class SessionManager:
    """按 user_id 管理引擎实例，保持对话上下文。

    P1 实现：集成记忆系统，对话开始时加载记忆，重置时提取记忆。
    """

    def __init__(
        self,
        engine_config: Dict[str, Any],
        memory_store: Optional[MemoryStore] = None,
    ) -> None:
        self._sessions: Dict[str, EngineSession] = {}
        self._engine_config = engine_config
        self._lock = asyncio.Lock()
        # 记忆系统
        self._memory_store = memory_store or MemoryStore()
        self._memory_loader = MemoryLoader(self._memory_store)
        self._memory_extractor = MemoryExtractor()

    async def get_or_create(self, user_id: str) -> EngineSession:
        """获取已有会话或创建新会话。"""
        async with self._lock:
            if user_id not in self._sessions:
                self._sessions[user_id] = await self._create_session(user_id)
            return self._sessions[user_id]

    async def reset(self, user_id: str) -> bool:
        """重置用户会话。触发记忆提取后清理。"""
        async with self._lock:
            session = self._sessions.pop(user_id, None)
            if session is None:
                return False

            # 异步提取记忆（不阻塞重置操作）
            if session.conversation:
                asyncio.create_task(
                    self._extract_memories(user_id, session.conversation)
                )

            logger.info("Session reset: user_id=%s", user_id)
            return True

    def get_session_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息（调试用）。"""
        session = self._sessions.get(user_id)
        if session is None:
            return None
        return {
            "user_id": user_id,
            "created_at": session.created_at,
            "last_active": session.last_active,
            "turn_count": session.turn_count,
        }

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    @property
    def memory_store(self) -> MemoryStore:
        return self._memory_store

    async def _create_session(self, user_id: str) -> EngineSession:
        """创建新的引擎实例，注入记忆上下文。"""
        working_dir = self._engine_config.get("working_dir", ".")
        permission_mode = self._engine_config.get("permission_mode", "auto")

        # 加载记忆作为 system prompt 扩展
        memory_context = self._memory_loader.load_for_context(
            current_message="",  # 首次创建时没有消息
            user_id=user_id,
        )

        logger.info(
            "Creating engine session: user_id=%s, working_dir=%s, memory_chars=%d",
            user_id, working_dir, len(memory_context),
        )
        engine = await create_engine(
            working_dir=working_dir,
            permission_mode=permission_mode,
            system_prompt_extra=memory_context,
        )
        return EngineSession(engine)

    async def _extract_memories(
        self, user_id: str, conversation: List[Dict[str, str]]
    ) -> None:
        """从对话中提取记忆并保存。"""
        try:
            existing = self._memory_store.list_all()
            entries = await self._memory_extractor.extract(
                conversation=conversation,
                existing_memories=existing,
                source_user=user_id,
            )
            for entry in entries:
                # 检查是否需要更新已有记忆
                existing_entry = self._memory_store.find_by_name(entry.name)
                if existing_entry:
                    existing_entry.content = entry.content
                    existing_entry.description = entry.description
                    self._memory_store.update(existing_entry)
                else:
                    self._memory_store.save(entry)

            if entries:
                logger.info(
                    "Extracted %d memories from user=%s conversation", len(entries), user_id
                )
        except Exception as e:
            logger.warning("Memory extraction failed for user=%s: %s", user_id, e)
