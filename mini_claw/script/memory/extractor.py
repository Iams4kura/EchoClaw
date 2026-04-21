"""记忆提取器 — 对话结束后，用 LLM 从对话中提取值得记住的信息。"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .models import MemoryEntry, MemoryType

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """你是一个记忆提取助手。回顾以下对话，提取值得长期记住的信息。

## 记忆类型

- **user**: 用户画像 — 角色、目标、偏好、知识背景
- **feedback**: 行为反馈 — 用户纠正了什么、确认了什么方法有效
- **project**: 项目信息 — 进展、决策、目标、截止日期
- **reference**: 外部引用 — 文档链接、系统位置、工具地址

## 规则

1. 只提取**新信息**，不要重复已有记忆
2. 每条记忆的 name 应简洁有辨识度
3. description 是一行概要（<150 字符），用于未来判断相关性
4. content 是完整内容，feedback/project 类型需要包含 **Why:** 和 **How to apply:**
5. 不要提取可以从代码或 git 历史直接获取的信息
6. 如果对话中没有值得记住的内容，返回空数组 []

## 已有记忆（避免重复）

{existing_memories}

## 对话内容

{conversation}

## 输出格式

返回 JSON 数组，每个元素:
```json
{{
  "type": "user|feedback|project|reference",
  "name": "简洁的记忆名称",
  "description": "一行描述",
  "content": "完整内容（markdown 格式）"
}}
```

如果需要**更新**已有记忆而非新建，额外包含:
```json
{{
  "update_name": "已有记忆的 name",
  ...其他字段为更新后的值
}}
```

只输出 JSON 数组，不要其他内容。"""


class MemoryExtractor:
    """对话结束后提取记忆。

    使用 mini_claude 的 LLM 客户端进行提取。
    """

    def __init__(self, llm_client: Any = None, model: Optional[str] = None) -> None:
        """初始化提取器。

        Args:
            llm_client: mini_claude 的 LLMClient 实例。如果为 None，提取时会跳过。
            model: 用于提取的模型（建议用便宜模型如 haiku）。
        """
        self._llm = llm_client
        self._model = model

    async def extract(
        self,
        conversation: List[Dict[str, str]],
        existing_memories: Optional[List[MemoryEntry]] = None,
        source_avatar: Optional[str] = None,
        source_user: Optional[str] = None,
    ) -> List[MemoryEntry]:
        """从对话中提取记忆。

        Args:
            conversation: 对话消息列表，每条 {"role": "user"|"assistant", "content": "..."}
            existing_memories: 已有记忆（用于去重）
            source_avatar: 产生此对话的分身 ID
            source_user: 用户 ID

        Returns:
            提取出的新记忆列表
        """
        if not self._llm:
            logger.debug("No LLM client configured, skipping memory extraction")
            return []

        if not conversation:
            return []

        # 构建已有记忆摘要
        existing_text = "（无已有记忆）"
        if existing_memories:
            lines = []
            for m in existing_memories:
                lines.append(f"- [{m.type.value}] {m.name}: {m.description}")
            existing_text = "\n".join(lines)

        # 构建对话文本
        conv_lines = []
        for msg in conversation:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # 截断过长的消息
            if len(content) > 2000:
                content = content[:2000] + "...(truncated)"
            conv_lines.append(f"**{role}**: {content}")
        conv_text = "\n\n".join(conv_lines)

        # 构建提取 prompt
        prompt = EXTRACTION_PROMPT.format(
            existing_memories=existing_text,
            conversation=conv_text,
        )

        try:
            # 调用 LLM
            from src.models import Message
            messages = [Message(role="user", content=prompt)]

            response = await self._llm.complete(
                messages=messages,
                model=self._model,
                temperature=0.3,
            )

            # 解析响应
            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text += block.text

            entries = self._parse_response(
                response_text,
                source_avatar=source_avatar,
                source_user=source_user,
            )
            logger.info("Extracted %d memories from conversation", len(entries))
            return entries

        except Exception as e:
            logger.warning("Memory extraction failed: %s", e)
            return []

    def _parse_response(
        self,
        text: str,
        source_avatar: Optional[str] = None,
        source_user: Optional[str] = None,
    ) -> List[MemoryEntry]:
        """解析 LLM 返回的 JSON 数组。"""
        # 尝试从响应中提取 JSON
        text = text.strip()

        # 处理 markdown 代码块包裹
        if text.startswith("```"):
            lines = text.splitlines()
            # 去掉首尾的 ``` 行
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            # 尝试找到 JSON 数组部分
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    items = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    logger.warning("Failed to parse extraction response as JSON")
                    return []
            else:
                return []

        if not isinstance(items, list):
            return []

        entries = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                entry = MemoryEntry(
                    name=item["name"],
                    type=MemoryType(item["type"]),
                    description=item["description"],
                    content=item["content"],
                    source_avatar=source_avatar,
                    source_user=source_user,
                )
                entries.append(entry)
            except (KeyError, ValueError) as e:
                logger.warning("Skipping invalid memory item: %s", e)
                continue

        return entries

    # ── Brain 自我反思 ────────────────────────────────────────

    async def reflect(
        self,
        task_summary: str,
        outcome: str,
        brain_reasoning: str = "",
        existing_memories: Optional[List[MemoryEntry]] = None,
        source_user: Optional[str] = None,
    ) -> List[MemoryEntry]:
        """任务后反思 — Brain 完成任务后调用。

        不同于 extract（从对话提取），reflect 从任务执行结果中提取：
        - 什么方法有效/无效
        - 用户的新偏好
        - 值得记住的项目状态

        Args:
            task_summary: 任务描述概要
            outcome: 执行结果（成功/失败 + 详情）
            brain_reasoning: Brain 的决策推理过程
            existing_memories: 已有记忆（去重）
            source_user: 用户 ID

        Returns:
            提取出的 reflection 类型记忆列表
        """
        if not self._llm:
            return []

        existing_text = "（无已有记忆）"
        if existing_memories:
            lines = [
                f"- [{m.type.value}] {m.name}: {m.description}" for m in existing_memories
            ]
            existing_text = "\n".join(lines)

        prompt = f"""你是一个反思助手。分析以下任务执行过程，提取值得长期记住的经验。

## 任务概要
{task_summary}

## Brain 思考过程
{brain_reasoning or '（无记录）'}

## 执行结果
{outcome}

## 已有记忆（避免重复）
{existing_text}

## 提取规则
1. 重点关注：什么方法有效、什么方法无效、用户表达的新偏好
2. 类型使用 "reflection"（自我反思经验）
3. content 中包含 **What worked:**、**What to avoid:** 和 **Lesson:** 结构
4. 如果没有值得记住的内容，返回空数组 []

返回 JSON 数组：
```json
[{{"type": "reflection", "name": "...", "description": "...", "content": "..."}}]
```

只输出 JSON 数组，不要其他内容。"""

        try:
            from src.models import Message

            messages = [Message(role="user", content=prompt)]
            response = await self._llm.complete(
                messages=messages,
                model=self._model,
                temperature=0.3,
            )
            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text += block.text

            entries = self._parse_response(
                response_text,
                source_user=source_user,
            )
            logger.info("Reflected %d memories from task", len(entries))
            return entries
        except Exception as e:
            logger.warning("Memory reflection failed: %s", e)
            return []
