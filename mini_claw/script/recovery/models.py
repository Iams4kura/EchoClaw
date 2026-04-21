"""自愈结果数据模型。"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class SelfHealResult:
    """自愈流程的结果。"""

    error_id: str
    analysis: str = ""
    fix_output: str = ""
    fix_ok: bool = False
    verified: bool = False
    needs_restart: bool = False
    reloaded_modules: List[str] = field(default_factory=list)
