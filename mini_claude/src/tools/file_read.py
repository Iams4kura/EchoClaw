"""FileReadTool - Read file contents.

Reference: src/tools/FileReadTool/FileReadTool.ts
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult


class FileReadTool(BaseTool):
    """Read the contents of a file from the filesystem."""

    name = "Read"
    description = (
        "Reads a file from the local filesystem. Returns the file content "
        "with line numbers. Supports optional offset and limit for large files."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-based)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read",
            },
        },
        "required": ["file_path"],
    }
    permission_category = PermissionCategory.READ

    # Max lines to read by default to avoid loading huge files
    DEFAULT_LIMIT = 2000

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        """Read file contents with optional offset/limit."""
        file_path = params["file_path"]
        offset = params.get("offset", 1)
        limit = params.get("limit", self.DEFAULT_LIMIT)

        path = Path(file_path)

        # Validate path
        if not path.is_absolute():
            path = Path(os.getcwd()) / path

        if not path.exists():
            return ToolResult(
                content=f"File not found: {file_path}", is_error=True
            )

        if path.is_dir():
            return ToolResult(
                content=f"Path is a directory, not a file: {file_path}. "
                "Use Bash with 'ls' to list directory contents.",
                is_error=True,
            )

        try:
            # Check file size first
            file_size = path.stat().st_size
            if file_size > 10 * 1024 * 1024:  # 10MB
                return ToolResult(
                    content=f"File is too large ({file_size} bytes). "
                    "Use offset and limit parameters to read portions.",
                    is_error=True,
                )

            # Read file
            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            if not content:
                return ToolResult(content="(empty file)", is_error=False)

            # Apply offset and limit
            lines = content.splitlines(keepends=True)
            total_lines = len(lines)

            start = max(0, offset - 1)  # Convert 1-based to 0-based
            end = start + limit
            selected = lines[start:end]

            # Format with line numbers (cat -n style)
            result_lines = []
            for i, line in enumerate(selected, start=start + 1):
                result_lines.append(f"{i:>6}\t{line.rstrip()}")

            result = "\n".join(result_lines)

            # Add truncation notice if needed
            if end < total_lines:
                result += f"\n\n... ({total_lines - end} more lines, {total_lines} total)"

            return ToolResult(content=result, is_error=False)

        except UnicodeDecodeError:
            return ToolResult(
                content=f"Cannot read file as text (binary file?): {file_path}",
                is_error=True,
            )
        except PermissionError:
            return ToolResult(
                content=f"Permission denied: {file_path}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)
