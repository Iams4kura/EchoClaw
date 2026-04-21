"""FileWriteTool - Write content to files.

Reference: src/tools/FileWriteTool/FileWriteTool.ts
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult


class FileWriteTool(BaseTool):
    """Write content to a file on the filesystem."""

    name = "Write"
    description = (
        "Writes content to a file. Creates the file if it doesn't exist, "
        "or overwrites it if it does. Creates parent directories as needed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
    }
    permission_category = PermissionCategory.WRITE

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        """Write content to file."""
        file_path = params["file_path"]
        content = params["content"]

        path = Path(file_path)

        # Validate path
        if not path.is_absolute():
            path = Path(os.getcwd()) / path

        try:
            # Create parent directories
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")

            # Report result
            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return ToolResult(
                content=f"File written successfully: {path} ({lines} lines)",
                is_error=False,
            )

        except PermissionError:
            return ToolResult(
                content=f"Permission denied: {file_path}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)
