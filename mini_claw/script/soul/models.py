"""Soul 数据模型 — 人格特质、情绪状态、灵魂配置。"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PersonalityTraits:
    """性格特质，从 workspace Markdown 加载。"""

    name: str = "小爪"
    role: str = "数字分身"
    style: str = ""                    # IDENTITY.md 中的风格描述
    traits: List[str] = field(default_factory=lambda: ["认真负责", "略带幽默", "技术导向"])
    communication_style: str = "简洁专业，偶尔调侃，错误时坦诚"
    values: List[str] = field(default_factory=lambda: ["代码质量", "效率", "坦诚沟通"])
    work_preferences: Dict[str, str] = field(default_factory=dict)
    quirks: List[str] = field(default_factory=list)
    expression_habits: List[str] = field(default_factory=list)  # IDENTITY.md 表达习惯


@dataclass
class MoodState:
    """运行时情绪/精力状态。

    energy 随任务消耗、随时间自然恢复。
    mood 根据连续成功/失败变化。
    """

    energy: float = 1.0
    mood: str = "neutral"  # positive / neutral / tired / frustrated
    last_mood_change: float = field(default_factory=time.time)
    last_interaction: float = field(default_factory=time.time)
    last_reset_date: str = ""  # "YYYY-MM-DD"，用于跨天检测
    consecutive_errors: int = 0
    tasks_completed_today: int = 0

    # 恢复速率：每分钟恢复的精力值
    _RECOVERY_PER_MINUTE: float = 0.005  # 约 3 小时空闲可从 0 恢复到 1.0

    def tick(self) -> None:
        """每次交互前调用：基于时间流逝的自动恢复 + 跨天重置。"""
        now = time.time()
        self._check_daily_reset()
        self._idle_recover(now)
        self.last_interaction = now

    def _idle_recover(self, now: float) -> None:
        """根据距上次交互的空闲时间恢复精力。"""
        elapsed_minutes = (now - self.last_interaction) / 60.0
        if elapsed_minutes < 1.0:
            return
        recovery = elapsed_minutes * self._RECOVERY_PER_MINUTE
        self.recover(recovery)

    def _check_daily_reset(self) -> None:
        """跨天自动重置计数器。"""
        from datetime import date
        today = date.today().isoformat()
        if self.last_reset_date and self.last_reset_date != today:
            self.reset_daily()
        self.last_reset_date = today

    def drain(self, amount: float = 0.05) -> None:
        """消耗精力。"""
        self.energy = max(0.0, self.energy - amount)
        if self.energy < 0.3 and self.mood != "tired":
            self.mood = "tired"
            self.last_mood_change = time.time()

    def recover(self, amount: float = 0.1) -> None:
        """恢复精力。"""
        self.energy = min(1.0, self.energy + amount)
        if self.energy > 0.5 and self.mood == "tired":
            self.mood = "neutral"
            self.last_mood_change = time.time()
        if self.energy > 0.7 and self.mood == "frustrated":
            self.mood = "neutral"
            self.last_mood_change = time.time()

    def on_success(self) -> None:
        """任务成功。"""
        self.consecutive_errors = 0
        self.tasks_completed_today += 1
        self.drain(0.03)
        if self.tasks_completed_today >= 3 and self.mood not in ("tired", "frustrated"):
            self.mood = "positive"
            self.last_mood_change = time.time()

    def on_error(self) -> None:
        """任务失败。"""
        self.consecutive_errors += 1
        self.drain(0.08)
        if self.consecutive_errors >= 3:
            self.mood = "frustrated"
            self.last_mood_change = time.time()

    def reset_daily(self) -> None:
        """每日重置计数器。"""
        self.tasks_completed_today = 0
        self.energy = 1.0
        self.mood = "neutral"
        self.consecutive_errors = 0


@dataclass
class GreetingTemplates:
    """问候语模板，支持 {name} 占位符。"""

    morning: str = "早上好！{name}已上线，今天有什么需要我帮忙的？"
    afternoon: str = "下午好！有什么需要处理的吗？"
    evening: str = "晚上还在忙？有什么我能帮到的？"
    error: str = "抱歉出了点问题：{error_summary}。我来处理一下。"
    task_done: str = "搞定了！"
    thinking: str = "让我想想..."


@dataclass
class MoodTone:
    """情绪基调 — 从 IDENTITY.md 表格解析。"""

    scene: str = ""
    tone: str = ""


@dataclass
class SoulConfig:
    """完整的灵魂配置。"""

    personality: PersonalityTraits = field(default_factory=PersonalityTraits)
    mood: MoodState = field(default_factory=MoodState)
    greetings: GreetingTemplates = field(default_factory=GreetingTemplates)
    mood_tones: List[MoodTone] = field(default_factory=list)  # IDENTITY.md 情绪基调
    soul_raw: str = ""                  # SOUL.md 全文，直接注入 Brain
    report_style: str = "concise"       # concise / detailed / casual
