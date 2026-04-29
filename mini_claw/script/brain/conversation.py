"""ConversationStore — 每用户的对话历史管理。

Brain 拥有对话历史（而非 mini_claude 引擎），
这样 Brain 可以在每次请求时主动选择注入哪些上下文。
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    """一轮对话。"""

    role: str                     # "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    intent_type: Optional[str] = None  # 记录该轮的意图类型
    metadata: Optional[Dict] = None  # 额外元数据（如 system_origin 标记）


class ConversationStore:
    """管理所有用户的对话历史。

    替代 EngineSession 中的 _conversation 列表。
    每个用户独立的对话窗口，支持容量限制、上下文压缩和会话边界检测。
    """

    _OWNER_KEY = "__owner__"

    def __init__(
        self,
        max_history: int = 20,
        personal_mode: bool = False,
        compression_threshold: int = 12,
        keep_recent_rounds: int = 3,
        session_gap_seconds: float = 3600,
    ) -> None:
        self._max_history = max_history
        self._personal_mode = personal_mode
        self._compression_threshold = compression_threshold
        self._keep_recent_rounds = keep_recent_rounds
        self._session_gap_seconds = session_gap_seconds
        self._conversations: Dict[str, List[Turn]] = defaultdict(list)
        self._compress_fn: Optional[Callable[[str], Awaitable[str]]] = None
        self._topic_check_fn: Optional[Callable[[str, str], Awaitable[bool]]] = None
        self._compression_cache: Dict[str, tuple[int, List[Dict[str, str]]]] = {}

    def set_compress_fn(self, fn: Callable[[str], Awaitable[str]]) -> None:
        self._compress_fn = fn

    def set_topic_check_fn(self, fn: Callable[[str, str], Awaitable[bool]]) -> None:
        self._topic_check_fn = fn

    def _key(self, user_id: str) -> str:
        """personal 模式下所有用户映射到同一个 key。"""
        return self._OWNER_KEY if self._personal_mode else user_id

    def _invalidate_cache(self, user_id: str) -> None:
        self._compression_cache.pop(self._key(user_id), None)

    def add(
        self,
        user_id: str,
        role: str,
        content: str,
        intent_type: Optional[str] = None,
        metadata: Optional[Dict] = None,
        platform: str = "webhook",
    ) -> None:
        """记录一轮对话。只记录 webhook 平台的消息，超出上限时丢弃最早的记录。"""
        if platform != "webhook":
            return
        key = self._key(user_id)
        turns = self._conversations[key]
        turns.append(Turn(role=role, content=content, intent_type=intent_type, metadata=metadata))
        if len(turns) > self._max_history:
            self._conversations[key] = turns[-self._max_history:]
        self._invalidate_cache(user_id)

    def get_recent(self, user_id: str, n: int = 10) -> List[Dict[str, str]]:
        """获取最近 n 轮对话，返回 [{role, content}] 格式。"""
        turns = self._conversations.get(self._key(user_id), [])
        return [{"role": t.role, "content": t.content} for t in turns[-n:]]

    def get_full(self, user_id: str) -> List[Turn]:
        """获取完整对话历史（Turn 对象列表）。"""
        return list(self._conversations.get(self._key(user_id), []))

    async def check_session_boundary(self, user_id: str, new_message: str) -> bool:
        """检测是否应该开启新会话（时间间隔 + 话题关联判断）。

        Returns True 表示旧历史已被清空（新会话开始）。
        """
        key = self._key(user_id)
        turns = self._conversations.get(key, [])
        if not turns:
            return False

        gap = time.time() - turns[-1].timestamp
        if gap < self._session_gap_seconds:
            return False

        if self._topic_check_fn is None:
            return False

        recent_text = "\n".join(
            f"{t.role}: {t.content[:150]}" for t in turns[-6:]
        )
        try:
            is_related = await self._topic_check_fn(recent_text, new_message)
        except Exception:
            logger.warning("话题关联判断失败，保留当前会话")
            return False

        if not is_related:
            self._conversations[key] = []
            self._invalidate_cache(user_id)
            logger.info("会话边界检测：用户 %s 开启新会话（间隔 %.0f 秒）", user_id, gap)
            return True

        return False

    async def get_compressed_messages(self, user_id: str) -> List[Dict[str, str]]:
        """返回压缩后的对话消息列表。

        策略：总轮次 <= threshold 时原样返回；
        否则保留首条 + LLM 摘要中间部分 + 最近 N 轮。
        """
        key = self._key(user_id)
        turns = self._conversations.get(key, [])
        if not turns:
            return []

        turn_count = len(turns)
        cached = self._compression_cache.get(key)
        if cached and cached[0] == turn_count:
            return cached[1]

        if turn_count <= self._compression_threshold:
            result = [{"role": t.role, "content": t.content} for t in turns]
            self._compression_cache[key] = (turn_count, result)
            return result

        tail_count = self._keep_recent_rounds * 2
        first_turn = turns[0]
        tail_turns = turns[-tail_count:]
        middle_turns = turns[1:-tail_count]

        if not middle_turns:
            result = [{"role": t.role, "content": t.content} for t in turns]
            self._compression_cache[key] = (turn_count, result)
            return result

        middle_text = "\n".join(f"{t.role}: {t.content[:200]}" for t in middle_turns)
        if self._compress_fn:
            try:
                summary = await self._compress_fn(middle_text)
            except Exception:
                logger.warning("对话压缩失败，使用截断")
                summary = middle_text[:300] + "..."
        else:
            summary = middle_text[:300] + "..."

        result = [{"role": first_turn.role, "content": first_turn.content}]
        result.append({"role": "system", "content": f"[对话摘要]\n{summary}"})
        result.extend({"role": t.role, "content": t.content} for t in tail_turns)
        self._compression_cache[key] = (turn_count, result)
        return result

    def clear(self, user_id: str) -> List[Turn]:
        """清空用户对话历史，返回被清除的内容（用于记忆提取）。"""
        self._invalidate_cache(user_id)
        turns = self._conversations.pop(self._key(user_id), [])
        return turns

    @property
    def all_users(self) -> List[str]:
        """所有有对话历史的用户 ID。"""
        return list(self._conversations.keys())

    @property
    def total_turns(self) -> int:
        """所有用户的对话轮次总数。"""
        return sum(len(turns) for turns in self._conversations.values())
