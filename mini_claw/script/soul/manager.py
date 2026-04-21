"""SoulManager — 加载和管理 mini_claw 的人格与情绪状态。"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .models import (
    GreetingTemplates,
    MoodState,
    PersonalityTraits,
    SoulConfig,
)

logger = logging.getLogger(__name__)


class SoulManager:
    """Soul 的运行时管理器。

    职责：
    1. 从 YAML 加载人格配置
    2. 将人格转化为 Brain LLM 的 system prompt 片段
    3. 管理情绪状态变化
    4. 提供上下文感知的问候语和错误消息
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._config_path = config_path
        self._soul = SoulConfig()

    def load(self, config_path: Optional[str] = None) -> SoulConfig:
        """从 YAML 文件加载人格配置。"""
        path = config_path or self._config_path
        if path and Path(path).exists():
            with open(path, encoding="utf-8") as f:
                data: Dict[str, Any] = yaml.safe_load(f) or {}
            self._soul = self._parse_config(data)
            logger.info("Soul 配置已加载: %s (%s)", self._soul.personality.name, path)
        else:
            logger.info("使用默认 Soul 配置")
        return self._soul

    def load_from_workspace(self, workspace: Any) -> SoulConfig:
        """从 WorkspaceLoader 加载人格配置（v0.2 workspace 模式）。

        Args:
            workspace: WorkspaceLoader 实例
        """
        self._soul = workspace.load_soul()
        self._soul.mood = workspace.load_mood()
        logger.info("Soul 从 workspace 加载: %s", self._soul.personality.name)
        return self._soul

    @property
    def soul(self) -> SoulConfig:
        return self._soul

    @property
    def name(self) -> str:
        return self._soul.personality.name

    # ── System Prompt 生成 ─────────────────────────────────────

    def get_system_prompt_fragment(self) -> str:
        """将人格特质转化为 Brain LLM 的 system prompt 片段。

        v0.2：身份头部 + SOUL.md 全文直接注入，保留原始表达力。
        """
        p = self._soul.personality
        # 身份头部（简短）
        identity_line = f"你是{p.name}，一名{p.role}。"
        if p.style:
            identity_line += f"风格：{p.style}。"

        parts = [identity_line]

        # 表达习惯
        if p.expression_habits:
            parts.append("表达习惯：" + "；".join(p.expression_habits) + "。")

        # SOUL.md 全文（核心人格指令）
        if self._soul.soul_raw:
            parts.append("")
            parts.append(self._soul.soul_raw)

        return "\n".join(parts)

    def get_mood_context(self) -> str:
        """返回当前情绪/精力状态的自然语言描述，注入 Brain 思考上下文。"""
        m = self._soul.mood
        parts = [f"当前精力：{m.energy:.0%}"]

        mood_desc = {
            "positive": "心情不错",
            "neutral": "状态正常",
            "tired": "有点疲惫",
            "frustrated": "连续遇到问题，有些沮丧",
        }
        parts.append(mood_desc.get(m.mood, "状态正常"))

        if m.tasks_completed_today > 0:
            parts.append(f"今天已完成 {m.tasks_completed_today} 个任务")
        return "，".join(parts) + "。"

    # ── 情绪更新 ──────────────────────────────────────────────

    def on_task_complete(self, success: bool) -> None:
        """任务完成后更新情绪。"""
        if success:
            self._soul.mood.on_success()
        else:
            self._soul.mood.on_error()

    def on_error(self) -> None:
        """错误时更新情绪。"""
        self._soul.mood.on_error()

    # ── 问候语 ────────────────────────────────────────────────

    def get_greeting(self) -> str:
        """根据时间和情绪生成问候语。"""
        hour = time.localtime().tm_hour
        g = self._soul.greetings
        if 5 <= hour < 12:
            template = g.morning
        elif 12 <= hour < 18:
            template = g.afternoon
        else:
            template = g.evening
        # 兼容两种格式：带 {name} 的模板 和 纯基调描述
        try:
            return template.format(name=self._soul.personality.name)
        except (KeyError, IndexError):
            return template

    def get_error_message(self, error_summary: str) -> str:
        """生成人格化的错误消息。"""
        try:
            return self._soul.greetings.error.format(
                name=self._soul.personality.name,
                error_summary=error_summary,
            )
        except (KeyError, IndexError):
            # 基调模式：用基调包装错误信息
            return f"{error_summary}"

    def get_thinking_message(self) -> str:
        """生成'正在思考'的消息。"""
        return self._soul.greetings.thinking

    # ── 配置解析 ──────────────────────────────────────────────

    @staticmethod
    def _parse_config(data: Dict[str, Any]) -> SoulConfig:
        """从字典解析 SoulConfig。"""
        p_data = data.get("personality", {})
        personality = PersonalityTraits(
            name=p_data.get("name", "小爪"),
            role=p_data.get("role", "数字员工"),
            traits=p_data.get("traits", ["认真负责", "略带幽默", "技术导向"]),
            communication_style=p_data.get(
                "communication_style", "简洁专业，偶尔调侃，错误时坦诚"
            ),
            values=p_data.get("values", ["代码质量", "效率", "坦诚沟通"]),
            work_preferences=p_data.get("work_preferences", {}),
            quirks=p_data.get("quirks", []),
        )

        g_data = data.get("greeting_templates", data.get("greetings", {}))
        greetings = GreetingTemplates(
            morning=g_data.get("morning", GreetingTemplates.morning),
            afternoon=g_data.get("afternoon", GreetingTemplates.afternoon),
            evening=g_data.get("evening", GreetingTemplates.evening),
            error=g_data.get("error", GreetingTemplates.error),
            task_done=g_data.get("task_done", GreetingTemplates.task_done),
            thinking=g_data.get("thinking", GreetingTemplates.thinking),
        )

        return SoulConfig(
            personality=personality,
            greetings=greetings,
            report_style=data.get("report_style", "concise"),
        )
