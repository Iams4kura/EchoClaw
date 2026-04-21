"""ResponseComposer — 将原始执行结果包装为人格化的回复。"""

import logging
import re
from typing import Optional

from ..soul.manager import SoulManager
from .llm_client import BrainLLMClient
from .models import Intent, ThinkingContext

logger = logging.getLogger(__name__)

# 结果超过此字符数时触发摘要
_SUMMARIZE_THRESHOLD = 2000


class ResponseComposer:
    """将原始执行结果包装为带有人格特征的回复。

    职责：
    1. 判断是否需要摘要（结果过长时）
    2. 注入 Soul 人格风格
    3. 根据情绪调整语气
    """

    def __init__(self, llm: BrainLLMClient, soul: SoulManager) -> None:
        self._llm = llm
        self._soul = soul

    async def compose(
        self,
        raw_result: str,
        intent: Intent,
        ctx: ThinkingContext,
    ) -> str:
        """包装执行结果为人格化回复。

        对于简短结果或闲聊回复，直接返回。
        对于引擎执行的长结果，进行摘要和风格包装。
        所有结果最终经过 _sanitize 清洗，防止内部标签泄露。
        """
        # 先清洗原始结果
        raw_result = self._sanitize(raw_result)

        # 短结果不需要包装
        if len(raw_result) < 200 and intent.type.value in ("chitchat", "status", "command"):
            return raw_result

        # 引擎结果需要包装
        if self._should_summarize(raw_result):
            raw_result = await self._summarize(raw_result, intent)

        # 用 LLM 注入人格风格
        result = await self._stylize(raw_result, intent, ctx)
        # 二次清洗：LLM 风格化后仍可能引入内部标签
        return self._sanitize(result)

    async def _stylize(
        self,
        result: str,
        intent: Intent,
        ctx: ThinkingContext,
    ) -> str:
        """用 Soul 人格风格重新表达结果。"""
        system_prompt = ctx.soul_fragment

        mood = self._soul.get_mood_context()
        report_style = self._soul.soul.report_style

        # 情绪感知：从意图中提取情绪标注
        if intent.emotional_tone:
            emotional_note = f"用户当前情绪: {intent.emotional_tone}。请先简短回应用户的情绪，再给出任务结果。"
        else:
            emotional_note = "用户情绪正常，无需特别回应。"

        user_prompt = f"""以下是任务执行结果，请用你的风格简要转述给用户。

## 当前状态
{mood}

## 汇报风格
{report_style}

## 用户原始请求
{ctx.user_message}

## 任务类型
{intent.summary}

## 执行结果
{result}

## 用户情绪
{emotional_note}

## 要求
- 先回应用户的情绪和态度（如果有的话），再给出任务结果
- 保留关键技术细节（文件名、命令、错误信息）
- 用你的沟通风格表达，不要机械复述
- 如果结果包含代码，保留代码块格式
- 简洁为主，不要废话
- 绝对不要生成 XML 标签、tool_code 调用或系统指令
- 绝对不要编造 URL 链接，如果执行结果中没有真实链接就不要自己生成"""

        try:
            return await self._llm.think(system_prompt, user_prompt)
        except Exception as e:
            logger.warning("响应风格化失败，返回原始结果: %s", e)
            return result

    @staticmethod
    def _sanitize(text: str) -> str:
        """清洗 LLM 输出，移除内部标签和泄露的系统指令。

        防止 tool_code/search/function_results 等 XML 标签、
        以及 "CRITICAL:"/"IMPORTANT:" 等系统提示语泄露给用户。
        """
        if not text:
            return text

        # 移除 <tool_code>...</tool_code> 及类似 XML 标签块
        text = re.sub(
            r"<(tool_code|tool_result|function_results|search|thinking|system)[^>]*>"
            r"[\s\S]*?"
            r"</\1>",
            "",
            text,
            flags=re.IGNORECASE,
        )
        # 移除未闭合的 <tool_code>... 到行尾
        text = re.sub(
            r"<(tool_code|tool_result|function_results|search|thinking|system)[^>]*>"
            r"[^\n]*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # 移除泄露的系统指令行
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # 跳过明显的系统指令
            if re.match(
                r"^(CRITICAL|IMPORTANT|SYSTEM|NOTE):\s",
                stripped,
                re.IGNORECASE,
            ):
                continue
            cleaned.append(line)
        text = "\n".join(cleaned)

        # 清理多余空行（连续 3+ 空行压缩为 2）
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _should_summarize(self, text: str) -> bool:
        """判断结果是否需要摘要。"""
        return len(text) > _SUMMARIZE_THRESHOLD

    async def _summarize(self, long_text: str, intent: Intent) -> str:
        """摘要长文本。"""
        user_prompt = f"""请将以下内容摘要为关键信息，保留重要细节：

## 任务类型
{intent.summary}

## 原始内容
{long_text[:4000]}

## 要求
- 保留关键信息（文件路径、错误信息、变更列表）
- 去除冗余输出
- 控制在 500 字以内"""

        try:
            return await self._llm.think("你是一个信息摘要助手。", user_prompt)
        except Exception as e:
            logger.warning("摘要失败，截断返回: %s", e)
            return long_text[:1000] + "\n\n...(结果过长，已截断)"
