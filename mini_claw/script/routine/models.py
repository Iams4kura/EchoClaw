"""Routine 数据模型 — 自驱日程任务定义。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RoutineFrequency(str, Enum):
    """任务频率。"""

    ONCE = "once"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    CRON = "cron"


@dataclass
class RoutineJob:
    """自驱日程任务。

    Routine 触发时构造 UnifiedMessage 送入 Brain 认知循环处理。
    """

    name: str
    description: str
    prompt: str                                   # 触发时发给 Brain 的消息
    frequency: RoutineFrequency = RoutineFrequency.DAILY
    cron_expr: str = ""                           # frequency=CRON 时使用
    hour: int = 9                                 # DAILY/WEEKLY 的执行小时 (0-23)
    minute: int = 0                               # 执行分钟 (0-59)
    weekday: int = 0                              # WEEKLY 的星期几 (0=Mon, 6=Sun)
    interval_minutes: int = 30                    # HOURLY 的间隔分钟
    enabled: bool = True
    target_user: Optional[str] = None             # 结果推送给谁（None=仅日志）
    target_platform: Optional[str] = None         # 推送平台
    tags: List[str] = field(default_factory=list)
    executor: str = "brain"                       # "brain" 或 "engine"

    @property
    def is_system_job(self) -> bool:
        """是否是系统内置任务。"""
        return self.name.startswith("sys_")


@dataclass
class HeartbeatTask:
    """心跳任务：由模型自主判断是否执行。

    与 RoutineJob（系统定时任务）的区别：
    - RoutineJob: 精确 cron 调度，调度器判断 _should_run
    - HeartbeatTask: 条件式触发，模型判断是否执行
    """

    name: str                              # 任务标识（从标题生成）
    description: str                       # 标题原文
    condition: str                         # 自然语言触发条件
    prompt: str                            # 执行内容
    last_executed: Optional[str] = None    # "2026-04-17 18:00" 或 None
