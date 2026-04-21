"""FileEditTool - Edit files using string replacement.

Reference: src/tools/FileEditTool/FileEditTool.ts
"""

import asyncio
import difflib
import os
from pathlib import Path
from typing import Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult


class FileEditTool(BaseTool):
    """Edit files by replacing exact string matches."""

    name = "Edit"
    description = (
        "Performs exact string replacement in files. The old_string must "
        "match exactly (including whitespace). Use replace_all to change "
        "every occurrence."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to find and replace",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default: false)",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    permission_category = PermissionCategory.DESTRUCTIVE

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        """Execute string replacement edit."""
        file_path = params["file_path"]
        old_string = params["old_string"]
        new_string = params["new_string"]
        replace_all = params.get("replace_all", False)

        path = Path(file_path)
        if not path.is_absolute():
            path = Path(os.getcwd()) / path

        if not path.exists():
            return ToolResult(
                content=f"File not found: {file_path}", is_error=True
            )

        if old_string == new_string:
            return ToolResult(
                content="old_string and new_string are identical",
                is_error=True,
            )

        try:
            content = await asyncio.to_thread(
                path.read_text, encoding="utf-8"
            )

            # Check that old_string exists
            count = content.count(old_string)
            if count == 0:
                return ToolResult(
                    content=f"old_string not found in {file_path}",
                    is_error=True,
                )

            # Check uniqueness when not replacing all
            if not replace_all and count > 1:
                return ToolResult(
                    content=f"old_string found {count} times in {file_path}. "
                    "Provide more context to make it unique, or use replace_all=true.",
                    is_error=True,
                )

            # Perform replacement
            if replace_all:
                new_content = content.replace(old_string, new_string)
                replaced = count
            else:
                new_content = content.replace(old_string, new_string, 1)
                replaced = 1

            # Generate unified diff before writing
            diff_lines = list(difflib.unified_diff(
                content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
                lineterm="",
            ))
            diff_text = "\n".join(diff_lines) if diff_lines else ""

            # Write back
            await asyncio.to_thread(
                path.write_text, new_content, encoding="utf-8"
            )

            result_text = f"Successfully replaced {replaced} occurrence(s) in {path}"
            if diff_text:
                result_text += f"\n\n```diff\n{diff_text}\n```"

            return ToolResult(
                content=result_text,
                is_error=False,
            )

        except PermissionError:
            return ToolResult(
                content=f"Permission denied: {file_path}", is_error=True
            )
        except Exception as e:
            return ToolResult(content=f"Error editing file: {e}", is_error=True)
