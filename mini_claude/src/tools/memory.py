"""MemoryWriteTool — LLM can persist project memory to .claude/memory.md.

Reference: src/memdir/ (file-based persistent memory)
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult


class MemoryWriteTool(BaseTool):
    """Write, update, or delete project memory entries."""

    name = "MemoryWrite"
    description = (
        "Write project memory that persists across sessions. "
        "Each entry has a key and content. Stored in .claude/memory.md."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory entry key (used as heading)",
            },
            "content": {
                "type": "string",
                "description": "Memory content (markdown text)",
            },
            "action": {
                "type": "string",
                "enum": ["add", "update", "delete"],
                "description": "Action: add, update, or delete an entry",
                "default": "add",
            },
        },
        "required": ["key", "action"],
    }
    permission_category = PermissionCategory.WRITE

    def __init__(self, working_dir: Optional[str] = None):
        super().__init__()
        self._working_dir = working_dir or os.getcwd()

    @property
    def _memory_path(self) -> Path:
        return Path(self._working_dir) / ".claude" / "memory.md"

    def _read_memory(self) -> str:
        if self._memory_path.exists():
            return self._memory_path.read_text(encoding="utf-8")
        return ""

    def _write_memory(self, text: str) -> None:
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_path.write_text(text, encoding="utf-8")

    def _parse_entries(self, text: str) -> dict:
        """Parse memory.md into {key: content} dict."""
        entries = {}
        current_key = None
        current_lines = []

        for line in text.split("\n"):
            m = re.match(r"^## (.+)$", line)
            if m:
                if current_key is not None:
                    entries[current_key] = "\n".join(current_lines).strip()
                current_key = m.group(1).strip()
                current_lines = []
            elif current_key is not None:
                current_lines.append(line)

        if current_key is not None:
            entries[current_key] = "\n".join(current_lines).strip()

        return entries

    def _serialize_entries(self, entries: dict) -> str:
        """Serialize entries dict back to markdown."""
        parts = []
        for key, content in entries.items():
            parts.append(f"## {key}\n\n{content}")
        return "\n\n".join(parts) + "\n" if parts else ""

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        key = params["key"]
        action = params.get("action", "add")
        content = params.get("content", "")

        try:
            raw = await asyncio.to_thread(self._read_memory)
            entries = self._parse_entries(raw)

            if action == "delete":
                if key not in entries:
                    return ToolResult(
                        content=f"Memory entry not found: {key}", is_error=True
                    )
                del entries[key]
                await asyncio.to_thread(self._write_memory, self._serialize_entries(entries))
                return ToolResult(content=f"Deleted memory entry: {key}", is_error=False)

            if action == "update":
                if key not in entries:
                    return ToolResult(
                        content=f"Memory entry not found: {key}. Use action='add' to create.",
                        is_error=True,
                    )

            if not content:
                return ToolResult(
                    content="content is required for add/update actions",
                    is_error=True,
                )

            entries[key] = content
            await asyncio.to_thread(self._write_memory, self._serialize_entries(entries))
            verb = "Updated" if action == "update" else "Added"
            return ToolResult(content=f"{verb} memory entry: {key}", is_error=False)

        except Exception as e:
            return ToolResult(content=f"Memory write error: {e}", is_error=True)

    @classmethod
    def list_entries(cls, working_dir: str) -> dict:
        """Read and return all memory entries (for /memory command)."""
        tool = cls(working_dir=working_dir)
        raw = tool._read_memory()
        return tool._parse_entries(raw)
