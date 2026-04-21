"""记忆存储 — 文件系统读写 + MEMORY.md 索引管理。"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from .models import MemoryEntry, MemoryType

logger = logging.getLogger(__name__)

# 默认记忆根目录（workspace 模式下指向 workspace/memory/）
DEFAULT_MEMORY_ROOT = "data/memory"


class MemoryStore:
    """基于文件系统的记忆存储。

    v0.2 workspace 模式下目录结构：
        workspace/memory/     # 全局记忆（直接放在 memory/ 下）
        ├── *.md              # 记忆文件
        └── learnings/        # 错误教训和改进记录

    索引文件 MEMORY.md 放在 workspace/ 根目录（由 index_path 控制）。

    旧版兼容模式下仍支持 global/ 子目录。
    """

    def __init__(
        self,
        root: Optional[str] = None,
        index_path: Optional[str] = None,
    ) -> None:
        self.root = Path(root or DEFAULT_MEMORY_ROOT)
        # workspace 模式：索引文件上移到 workspace/ 根目录
        self._index_path = Path(index_path) if index_path else None

    def _namespace_dir(self, namespace: str = "global") -> Path:
        """获取记忆命名空间目录。

        workspace 模式下 "global" 直接使用 root（无 global/ 子目录）。
        """
        if namespace == "global":
            # workspace 模式：index_path 存在时说明 root 本身就是全局目录
            if self._index_path:
                return self.root
            return self.root / "global"
        return self.root / "avatars" / namespace

    def _ensure_dir(self, namespace: str = "global") -> Path:
        """确保命名空间目录存在。"""
        d = self._namespace_dir(namespace)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, entry: MemoryEntry, namespace: str = "global") -> Path:
        """保存记忆到文件，并更新索引。"""
        d = self._ensure_dir(namespace)
        filepath = d / entry.filename()

        # 写入记忆文件
        filepath.write_text(entry.to_frontmatter(), encoding="utf-8")
        logger.info("Memory saved: %s -> %s", entry.name, filepath)

        # 更新索引
        self._update_index(namespace)
        return filepath

    def load(self, filename: str, namespace: str = "global") -> Optional[MemoryEntry]:
        """从文件加载一条记忆。"""
        filepath = self._namespace_dir(namespace) / filename
        if not filepath.exists():
            return None
        try:
            text = filepath.read_text(encoding="utf-8")
            return MemoryEntry.from_frontmatter(text)
        except (ValueError, KeyError) as e:
            logger.warning("Failed to parse memory file %s: %s", filepath, e)
            return None

    def list_all(self, namespace: str = "global") -> List[MemoryEntry]:
        """列出命名空间下所有记忆。"""
        d = self._namespace_dir(namespace)
        if not d.exists():
            return []

        entries = []
        # 日期格式文件名（如 2026-04-14.md）是日记，不是记忆条目
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
        for f in sorted(d.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            if date_pattern.match(f.name):
                continue
            entry = self.load(f.name, namespace)
            if entry:
                entries.append(entry)
        return entries

    def list_by_type(
        self, memory_type: MemoryType, namespace: str = "global"
    ) -> List[MemoryEntry]:
        """按类型筛选记忆。"""
        return [e for e in self.list_all(namespace) if e.type == memory_type]

    def find_by_name(self, name: str, namespace: str = "global") -> Optional[MemoryEntry]:
        """按名称查找记忆。"""
        for entry in self.list_all(namespace):
            if entry.name == name:
                return entry
        return None

    def delete(self, filename: str, namespace: str = "global") -> bool:
        """删除记忆文件，更新索引。"""
        filepath = self._namespace_dir(namespace) / filename
        if not filepath.exists():
            return False
        filepath.unlink()
        logger.info("Memory deleted: %s", filepath)
        self._update_index(namespace)
        return True

    def update(self, entry: MemoryEntry, namespace: str = "global") -> Path:
        """更新已有记忆（覆盖写入）。"""
        import time
        entry.updated_at = time.time()
        return self.save(entry, namespace)

    _INDEX_SECTION_HEADER = "## 索引"
    _INDEX_AUTO_MARKER = "<!-- auto-index-start -->"
    _INDEX_AUTO_END = "<!-- auto-index-end -->"

    def _update_index(self, namespace: str = "global") -> None:
        """更新 MEMORY.md 中的自动索引区域，保留其他手写内容。

        在 ## 索引 section 中查找 <!-- auto-index-start/end --> 标记，
        只替换标记之间的内容。如果标记不存在则追加。
        如果文件不存在则创建完整的索引文件。
        """
        entries = self.list_all(namespace)
        # workspace 模式：全局索引放在 workspace/ 根目录
        if namespace == "global" and self._index_path:
            index_path = self._index_path
        else:
            index_path = self._namespace_dir(namespace) / "MEMORY.md"

        # 生成自动索引内容
        auto_lines = [self._INDEX_AUTO_MARKER, ""]
        if entries:
            by_type: Dict[MemoryType, List[MemoryEntry]] = {}
            for e in entries:
                by_type.setdefault(e.type, []).append(e)
            type_labels = {
                MemoryType.USER: "User",
                MemoryType.FEEDBACK: "Feedback",
                MemoryType.PROJECT: "Project",
                MemoryType.REFERENCE: "Reference",
            }
            for mt in MemoryType:
                group = by_type.get(mt, [])
                if group:
                    auto_lines.append(f"**{type_labels.get(mt, mt.value)}:**")
                    for e in group:
                        auto_lines.append(e.to_index_line())
                    auto_lines.append("")
        else:
            auto_lines.append("（暂无记忆条目）")
            auto_lines.append("")
        auto_lines.append(self._INDEX_AUTO_END)
        auto_block = "\n".join(auto_lines)

        # 读取现有内容
        if index_path.exists():
            content = index_path.read_text(encoding="utf-8")
        else:
            content = ""

        if self._INDEX_AUTO_MARKER in content and self._INDEX_AUTO_END in content:
            # 替换标记之间的内容
            before = content[:content.index(self._INDEX_AUTO_MARKER)]
            after = content[content.index(self._INDEX_AUTO_END) + len(self._INDEX_AUTO_END):]
            new_content = before + auto_block + after
        elif content:
            # 文件存在但无标记 → 在 ## 索引 section 末尾追加
            if self._INDEX_SECTION_HEADER in content:
                # 找到 ## 索引 后面的下一个 ## 或文件末尾
                idx = content.index(self._INDEX_SECTION_HEADER)
                after_header = idx + len(self._INDEX_SECTION_HEADER)
                # 找下一个 ## 标题
                next_section = content.find("\n## ", after_header)
                if next_section == -1:
                    # 没有下一个 section，追加到文件末尾
                    new_content = content.rstrip() + "\n\n" + auto_block + "\n"
                else:
                    # 插入到下一个 section 前
                    new_content = (
                        content[:next_section].rstrip()
                        + "\n\n" + auto_block + "\n"
                        + content[next_section:]
                    )
            else:
                # 没有 ## 索引 section，追加到文件末尾
                new_content = content.rstrip() + "\n\n" + auto_block + "\n"
        else:
            # 文件不存在 → 创建最小索引
            new_content = f"# MEMORY.md\n\n{self._INDEX_SECTION_HEADER}\n\n{auto_block}\n"

        index_path.write_text(new_content, encoding="utf-8")

    def get_index_content(self, namespace: str = "global") -> str:
        """读取 MEMORY.md 索引内容。"""
        if namespace == "global" and self._index_path:
            index_path = self._index_path
        else:
            index_path = self._namespace_dir(namespace) / "MEMORY.md"
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")
        return ""

    def get_namespaces(self) -> List[str]:
        """列出所有已有的记忆命名空间。"""
        namespaces = []
        global_dir = self.root / "global"
        if global_dir.exists():
            namespaces.append("global")
        avatars_dir = self.root / "avatars"
        if avatars_dir.exists():
            for d in sorted(avatars_dir.iterdir()):
                if d.is_dir():
                    namespaces.append(d.name)
        return namespaces
