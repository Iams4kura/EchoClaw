"""分身数据结构 — AvatarConfig, Avatar, AvatarType, AvatarStatus。"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import yaml


class AvatarType(str, Enum):
    RESIDENT = "resident"      # 常驻分身 — 长期存在
    EPHEMERAL = "ephemeral"    # 临时分身 — 任务完成即回收


class AvatarStatus(str, Enum):
    IDLE = "idle"              # 空闲
    BUSY = "busy"              # 执行中
    SLEEPING = "sleeping"      # 休眠（长时间无任务，释放引擎资源）
    DEAD = "dead"              # 已销毁


@dataclass
class AvatarConfig:
    """分身配置（可从 YAML preset 加载）。"""
    id: str
    name: str                                # 显示名称（如"代码手"）
    type: AvatarType = AvatarType.RESIDENT
    system_prompt: str = ""                  # 角色专属 system prompt
    tools_whitelist: List[str] = field(default_factory=list)
    memory_namespace: str = ""               # 私有记忆命名空间（默认为 id）
    model: Optional[str] = None              # LLM 模型（None = 使用全局默认）
    max_concurrent_tasks: int = 1            # 最大并发任务数
    heartbeat_interval: int = 30             # 心跳间隔（秒）
    max_idle_time: int = 3600                # 最大空闲时间（秒）→ 休眠
    working_dir: str = "."                   # 工作目录
    permission_mode: str = "auto"            # 权限模式

    def __post_init__(self) -> None:
        if not self.memory_namespace:
            self.memory_namespace = self.id

    @classmethod
    def from_yaml(cls, path: str) -> "AvatarConfig":
        """从 YAML 文件加载配置。"""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AvatarConfig":
        """从字典加载配置。"""
        avatar_type = data.get("type", "resident")
        if isinstance(avatar_type, str):
            avatar_type = AvatarType(avatar_type)

        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            type=avatar_type,
            system_prompt=data.get("system_prompt", ""),
            tools_whitelist=data.get("tools_whitelist", []),
            memory_namespace=data.get("memory_namespace", ""),
            model=data.get("model"),
            max_concurrent_tasks=data.get("max_concurrent_tasks", 1),
            heartbeat_interval=data.get("heartbeat_interval", 30),
            max_idle_time=data.get("max_idle_time", 3600),
            working_dir=data.get("working_dir", "."),
            permission_mode=data.get("permission_mode", "auto"),
        )


@dataclass
class Avatar:
    """运行中的分身实例。"""
    config: AvatarConfig
    status: AvatarStatus = AvatarStatus.IDLE
    current_tasks: List[str] = field(default_factory=list)   # 正在执行的任务 ID
    engine: Any = None                                        # QueryEngine 实例
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)

    @property
    def is_available(self) -> bool:
        """是否可以接受新任务。"""
        if self.status not in (AvatarStatus.IDLE, AvatarStatus.BUSY):
            return False
        return len(self.current_tasks) < self.config.max_concurrent_tasks

    @property
    def idle_seconds(self) -> float:
        """空闲时长。"""
        return time.time() - self.last_active

    def touch(self) -> None:
        """更新活跃时间。"""
        self.last_active = time.time()
