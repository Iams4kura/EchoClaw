"""记忆加载器 — 根据当前上下文加载最相关的记忆注入 system prompt。"""

import logging
from typing import List, Optional

from .models import MemoryEntry, MemoryType
from .store import MemoryStore

logger = logging.getLogger(__name__)

# 粗略估算：1 token ≈ 4 字符（中英混合偏保守）
CHARS_PER_TOKEN = 4


class MemoryLoader:
    """根据当前上下文，加载最相关的记忆。

    加载策略：
    1. 始终加载: 全局 user + feedback 类型记忆（最重要，影响行为）
    2. 始终加载: 当前分身命名空间的所有记忆
    3. 按关键词相关性: 根据当前消息匹配其他记忆
    4. Token 预算内尽量多加载
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def load_for_context(
        self,
        current_message: str,
        avatar_id: Optional[str] = None,
        user_id: Optional[str] = None,
        max_tokens: int = 2000,
    ) -> str:
        """加载相关记忆，返回拼接好的文本（可直接注入 system prompt）。

        Args:
            current_message: 当前用户消息（用于相关性匹配）
            avatar_id: 当前分身 ID（加载分身私有记忆）
            user_id: 当前用户 ID（按用户过滤）
            max_tokens: 最大 token 预算

        Returns:
            格式化的记忆文本，可直接追加到 system prompt
        """
        max_chars = max_tokens * CHARS_PER_TOKEN
        sections: List[str] = []
        used_chars = 0

        # 1. 全局 user + feedback 记忆（最高优先级）
        global_priority = self._store.list_by_type(MemoryType.USER) + \
            self._store.list_by_type(MemoryType.FEEDBACK)

        if global_priority:
            section = self._format_section("Global Context", global_priority)
            if used_chars + len(section) <= max_chars:
                sections.append(section)
                used_chars += len(section)

        # 2. 分身私有记忆
        if avatar_id:
            avatar_memories = self._store.list_all(avatar_id)
            if avatar_memories:
                section = self._format_section(
                    f"Avatar ({avatar_id}) Context", avatar_memories
                )
                if used_chars + len(section) <= max_chars:
                    sections.append(section)
                    used_chars += len(section)

        # 3. 全局 project + reference 记忆（按相关性排序）
        global_others = self._store.list_by_type(MemoryType.PROJECT) + \
            self._store.list_by_type(MemoryType.REFERENCE)

        if global_others and current_message:
            # 按相关性排序
            ranked = self._rank_by_relevance(global_others, current_message)
            relevant = [e for e, score in ranked if score > 0]

            if relevant:
                section = self._format_section("Related Context", relevant)
                if used_chars + len(section) <= max_chars:
                    sections.append(section)
                    used_chars += len(section)

        if not sections:
            return ""

        header = "# Loaded Memories\n\n"
        return header + "\n\n".join(sections)

    def _format_section(self, title: str, entries: List[MemoryEntry]) -> str:
        """格式化一组记忆为 markdown 节。"""
        lines = [f"## {title}", ""]
        for entry in entries:
            lines.append(f"### {entry.name}")
            lines.append(f"*{entry.description}*")
            lines.append("")
            lines.append(entry.content)
            lines.append("")
        return "\n".join(lines)

    def _rank_by_relevance(
        self, entries: List[MemoryEntry], query: str
    ) -> List[tuple]:
        """按关键词相关性对记忆排序。

        简单实现：基于关键词重叠度。P2 可改用向量相似度。
        """
        query_lower = query.lower()
        # 提取查询关键词（简单分词）
        query_words = set(query_lower.split())

        scored = []
        for entry in entries:
            # 匹配名称、描述和内容
            target = f"{entry.name} {entry.description} {entry.content}".lower()
            target_words = set(target.split())

            # 计算重叠度
            overlap = len(query_words & target_words)
            # 名称和描述匹配加权
            name_match = sum(1 for w in query_words if w in entry.name.lower()) * 2
            desc_match = sum(1 for w in query_words if w in entry.description.lower())

            score = overlap + name_match + desc_match
            scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ── 主动记忆检索（供 Brain 认知循环调用） ─────────────────

    # 意图类型 → 记忆类型权重倍率
    _INTENT_WEIGHTS: dict = {
        "coding": {MemoryType.PROJECT: 2.0, MemoryType.REFLECTION: 1.5},
        "file_ops": {MemoryType.PROJECT: 2.0, MemoryType.REFERENCE: 1.5},
        "knowledge": {MemoryType.REFERENCE: 2.0, MemoryType.PROJECT: 1.5},
        "chitchat": {MemoryType.USER: 2.0},
        "status": {MemoryType.PROJECT: 2.0},
        "memory": {MemoryType.USER: 1.5, MemoryType.FEEDBACK: 1.5},
    }

    def active_recall(
        self,
        keywords: List[str],
        intent_type: str = "",
        user_id: Optional[str] = None,
        namespace: str = "global",
        max_results: int = 5,
    ) -> List[MemoryEntry]:
        """主动记忆检索 — Brain 在思考过程中调用。

        与 load_for_context() 不同：
        - 使用 Brain 提取的关键词（而非原始消息分词）
        - 按意图类型对记忆类型加权
        - 返回独立的 MemoryEntry 列表（非拼接文本）

        Args:
            keywords: Brain 从用户消息中提取的关键词
            intent_type: 意图类型（用于加权）
            user_id: 用户 ID（按 source_user 过滤）
            namespace: 记忆命名空间
            max_results: 最大返回数量

        Returns:
            按相关性排序的 MemoryEntry 列表
        """
        all_entries = self._store.list_all(namespace)
        if not all_entries:
            return []

        # 按意图类型获取权重表
        intent_w = self._INTENT_WEIGHTS.get(intent_type, {})
        # feedback 和 reflection 始终有额外权重
        base_w = {MemoryType.FEEDBACK: 1.5, MemoryType.REFLECTION: 1.5}

        query_lower = " ".join(keywords).lower()
        query_words = set(w.lower() for w in keywords if w)

        scored: List[tuple] = []
        for entry in all_entries:
            # 基础关键词匹配分
            target = f"{entry.name} {entry.description} {entry.content}".lower()
            target_words = set(target.split())
            overlap = len(query_words & target_words)
            name_bonus = sum(2 for w in query_words if w in entry.name.lower())
            desc_bonus = sum(1 for w in query_words if w in entry.description.lower())
            base_score = overlap + name_bonus + desc_bonus

            # 意图类型加权
            type_weight = intent_w.get(entry.type, base_w.get(entry.type, 1.0))
            final_score = base_score * type_weight

            # 用户匹配加分
            if user_id and entry.source_user == user_id:
                final_score *= 1.3

            if final_score > 0:
                scored.append((entry, final_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [entry for entry, _ in scored[:max_results]]
