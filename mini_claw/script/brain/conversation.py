"""ConversationStore — 每用户的对话历史管理。

Brain 拥有对话历史（而非 mini_claude 引擎），
这样 Brain 可以在每次请求时主动选择注入哪些上下文。
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Turn:
    """一轮对话。"""

    role: str                     # "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    intent_type: Optional[str] = None  # 记录该轮的意图类型


class ConversationStore:
    """管理所有用户的对话历史。

    替代 EngineSession 中的 _conversation 列表。
    每个用户独立的对话窗口，支持容量限制和自动清理。
    """

    _OWNER_KEY = "__owner__"

    def __init__(self, max_history: int = 20, personal_mode: bool = False) -> None:
        self._max_history = max_history
        self._personal_mode = personal_mode
        self._conversations: Dict[str, List[Turn]] = defaultdict(list)

    def _key(self, user_id: str) -> str:
        """personal 模式下所有用户映射到同一个 key。"""
        return self._OWNER_KEY if self._personal_mode else user_id

    def add(
        self,
        user_id: str,
        role: str,
        content: str,
        intent_type: Optional[str] = None,
    ) -> None:
        """记录一轮对话。超出上限时丢弃最早的记录。"""
        key = self._key(user_id)
        turns = self._conversations[key]
        turns.append(Turn(role=role, content=content, intent_type=intent_type))
        if len(turns) > self._max_history:
            self._conversations[key] = turns[-self._max_history :]

    def get_recent(self, user_id: str, n: int = 10) -> List[Dict[str, str]]:
        """获取最近 n 轮对话，返回 [{role, content}] 格式。"""
        turns = self._conversations.get(self._key(user_id), [])
        return [{"role": t.role, "content": t.content} for t in turns[-n:]]

    def get_full(self, user_id: str) -> List[Turn]:
        """获取完整对话历史（Turn 对象列表）。"""
        return list(self._conversations.get(self._key(user_id), []))

    def clear(self, user_id: str) -> List[Turn]:
        """清空用户对话历史，返回被清除的内容（用于记忆提取）。"""
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
