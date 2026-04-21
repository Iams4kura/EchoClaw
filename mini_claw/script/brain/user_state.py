"""Per-user processing state — 消息队列、思考追踪、/btw 打断。"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ThinkingStep:
    """认知循环中的一个步骤状态。"""

    step_number: int  # 1-7
    step_name: str  # "build_context", "classify_intent", ...
    status: str  # "running" | "done" | "cancelled"
    detail: str  # 人类可读描述
    timestamp: float = field(default_factory=time.time)


@dataclass
class UserProcessingState:
    """单个用户的处理状态：队列、锁、思考进度、取消信号。"""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # (UnifiedMessage, Future)
    is_processing: bool = False
    current_message: Any = None  # Optional[UnifiedMessage]
    original_message: Optional[str] = None  # 原始用户输入（/btw 组合用）
    thinking_steps: List[ThinkingStep] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    partial_result: str = ""  # 执行中间结果

    def update_thinking(
        self, step: int, name: str, status: str, detail: str
    ) -> None:
        """更新或追加一个思考步骤。"""
        entry = ThinkingStep(
            step_number=step,
            step_name=name,
            status=status,
            detail=detail,
        )
        # 替换同编号步骤或追加
        self.thinking_steps = [
            s for s in self.thinking_steps if s.step_number != step
        ]
        self.thinking_steps.append(entry)
        self.thinking_steps.sort(key=lambda s: s.step_number)

    def check_cancelled(self) -> None:
        """检查是否被 /btw 取消，抛出 CancelledError。"""
        if self.cancel_event.is_set():
            raise asyncio.CancelledError("Interrupted by /btw")

    def reset_for_new_message(self, message_content: str) -> None:
        """为新消息重置状态。"""
        self.is_processing = True
        self.original_message = message_content
        self.thinking_steps = []
        self.partial_result = ""
        self.cancel_event.clear()

    def format_thinking_snapshot(self) -> str:
        """格式化当前思考步骤快照（供 /btw 组合使用）。"""
        if not self.thinking_steps:
            return "(无思考记录)"
        lines = []
        for s in self.thinking_steps:
            lines.append(f"Step {s.step_number} ({s.step_name}): [{s.status}] {s.detail}")
        return "\n".join(lines)
