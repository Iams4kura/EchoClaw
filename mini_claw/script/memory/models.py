"""记忆数据结构 — MemoryEntry 和 MemoryType。"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MemoryType(str, Enum):
    """记忆类型，与 CLAUDE.md auto memory 规范一致。"""
    USER = "user"               # 用户画像：角色、偏好、知识背景
    FEEDBACK = "feedback"       # 行为反馈：该做/不该做
    PROJECT = "project"         # 项目信息：进展、决策、目标
    REFERENCE = "reference"     # 外部引用：文档、链接、系统位置
    REFLECTION = "reflection"   # Brain 自我反思：什么方法有效/无效


@dataclass
class MemoryEntry:
    """一条结构化记忆。

    对应磁盘上一个 markdown 文件，包含 YAML frontmatter + 正文。
    """
    name: str                             # 记忆名称（也用作文件名前缀）
    type: MemoryType                      # 记忆类型
    description: str                      # 一行描述（用于索引和相关性判断）
    content: str                          # 正文内容（markdown 格式）
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source_avatar: Optional[str] = None   # 产生此记忆的分身 ID
    source_user: Optional[str] = None     # 关联的用户 ID

    def to_frontmatter(self) -> str:
        """序列化为 markdown 文件内容（frontmatter + body）。"""
        lines = [
            "---",
            f"name: {self.name}",
            f"description: {self.description}",
            f"type: {self.type.value}",
            f"id: {self.id}",
            f"created_at: {self.created_at:.0f}",
            f"updated_at: {self.updated_at:.0f}",
        ]
        if self.source_avatar:
            lines.append(f"source_avatar: {self.source_avatar}")
        if self.source_user:
            lines.append(f"source_user: {self.source_user}")
        lines.append("---")
        lines.append("")
        lines.append(self.content)
        return "\n".join(lines)

    @classmethod
    def from_frontmatter(cls, text: str) -> "MemoryEntry":
        """从 markdown 文件内容解析。"""
        if not text.startswith("---"):
            raise ValueError("Missing frontmatter delimiter")

        # 分离 frontmatter 和 body
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Invalid frontmatter format")

        meta_text = parts[1].strip()
        body = parts[2].strip()

        # 简单解析 YAML frontmatter（避免引入额外依赖）
        meta = {}
        for line in meta_text.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()

        return cls(
            name=meta.get("name", ""),
            type=MemoryType(meta.get("type", "project")),
            description=meta.get("description", ""),
            content=body,
            id=meta.get("id", uuid.uuid4().hex[:12]),
            created_at=float(meta.get("created_at", time.time())),
            updated_at=float(meta.get("updated_at", time.time())),
            source_avatar=meta.get("source_avatar"),
            source_user=meta.get("source_user"),
        )

    def to_index_line(self) -> str:
        """生成 MEMORY.md 索引行。"""
        filename = self.filename()
        return f"- [{self.name}]({filename}) — {self.description}"

    def filename(self) -> str:
        """生成文件名。"""
        # 清理名称，只保留安全字符
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.name)
        return f"{self.type.value}_{safe_name}.md"
