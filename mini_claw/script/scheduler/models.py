"""调度数据结构 — Task, TaskStatus。"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..gateway.models import UnifiedMessage


class TaskStatus(str, Enum):
    PENDING = "pending"          # 排队中
    ASSIGNED = "assigned"        # 已分配分身
    RUNNING = "running"          # 执行中
    WAITING_USER = "waiting"     # 等待用户输入
    COMPLETED = "completed"      # 完成
    FAILED = "failed"            # 失败
    CANCELLED = "cancelled"      # 取消


@dataclass
class Task:
    """一个待执行的任务。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source_message: Optional[UnifiedMessage] = None
    status: TaskStatus = TaskStatus.PENDING
    assigned_avatar: Optional[str] = None   # 分身 ID
    priority: int = 5                       # 0=紧急 9=低
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[str] = None            # 执行结果
    progress: Optional[str] = None          # 当前进度描述
    error: Optional[str] = None             # 错误信息

    @property
    def is_terminal(self) -> bool:
        """任务是否已结束。"""
        return self.status in (
            TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED
        )

    def update_status(self, status: TaskStatus) -> None:
        self.status = status
        self.updated_at = time.time()
