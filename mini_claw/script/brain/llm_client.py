"""BrainLLMClient — Brain 专用的轻量级 LLM 客户端。

复用 mini_claude 的 LLMClient 基础设施，但使用独立的模型配置。
Brain 调用不需要 tool calling，只需文本完成。
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.config import Config as MCConfig
from src.models.message import Message
from src.services.llm import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class BrainConfig:
    """Brain LLM 配置。"""

    model: str = ""                    # 为空时复用 mini_claude 的模型
    api_key: str = ""                  # 为空时复用 mini_claude 的 key
    base_url: str = ""                 # 为空时复用 mini_claude 的 url
    temperature: float = 0.3           # Brain 倾向确定性
    max_tokens: int = 1024             # Brain 不需要太长的输出
    classify_temperature: float = 0.1  # 分类任务用更低温度
    classify_max_tokens: int = 256     # 分类输出更短


class BrainLLMClient:
    """Brain 专用 LLM 客户端。

    提供两种调用方式：
    - think(): 通用思考，返回文本
    - classify(): 分类任务，返回解析后的 JSON dict
    """

    def __init__(self, config: BrainConfig, fallback_mc_config: Optional[MCConfig] = None) -> None:
        self._config = config

        # 构造 mini_claude 的 Config 对象来初始化 LLMClient
        mc_config = MCConfig()
        mc_config.model = config.model or (fallback_mc_config.model if fallback_mc_config else "")
        mc_config.api_key = config.api_key or (
            fallback_mc_config.api_key if fallback_mc_config else ""
        )
        mc_config.base_url = config.base_url or (
            fallback_mc_config.base_url if fallback_mc_config else ""
        )
        mc_config.temperature = config.temperature
        mc_config.max_tokens = config.max_tokens

        self._llm = LLMClient(mc_config)
        self._fallback_mc_config = fallback_mc_config

    async def think(self, system_prompt: str, user_prompt: str) -> str:
        """单次思考调用，返回纯文本。

        用于：闲聊回复、响应包装、知识问答。
        """
        messages = []
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))
        messages.append(Message(role="user", content=user_prompt))

        try:
            response = await self._llm.complete(
                messages=messages,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
            )
            return self._extract_text(response)
        except Exception as e:
            logger.error("Brain think 调用失败: %s", e)
            raise

    async def classify(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """分类调用，返回解析后的 JSON dict。

        用于：意图分类。温度更低，输出更短。
        """
        messages = []
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))
        messages.append(Message(role="user", content=user_prompt))

        try:
            response = await self._llm.complete(
                messages=messages,
                temperature=self._config.classify_temperature,
                max_tokens=self._config.classify_max_tokens,
            )
            text = self._extract_text(response)
            return self._parse_json(text)
        except Exception as e:
            logger.error("Brain classify 调用失败: %s", e)
            raise

    @staticmethod
    def _extract_text(response: Any) -> str:
        """从 LLMClient 响应中提取文本内容。"""
        if isinstance(response, str):
            return response
        # LLMClient.complete() 返回的可能是 LLMResponse 对象
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                # TextBlock 列表
                texts = []
                for block in content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                    elif isinstance(block, str):
                        texts.append(block)
                return "\n".join(texts)
            return str(content)
        return str(response)

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        """从 LLM 输出中解析 JSON。

        容忍 markdown 代码块包裹和前后文字。
        """
        # 尝试提取 ```json ... ``` 中的内容
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                cleaned = part.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                if cleaned.startswith("{"):
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        continue

        # 直接尝试解析
        text = text.strip()
        # 找到第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("无法从 LLM 输出解析 JSON: %s", text[:200])
        return {}
