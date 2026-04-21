"""Hands 数据模型 — 执行结果。"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    """mini_claude 引擎的执行结果。"""

    success: bool
    output: str
    duration_ms: float = 0.0
    tool_calls_count: int = 0
    error: Optional[str] = None
