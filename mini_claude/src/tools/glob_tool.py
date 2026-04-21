"""GlobTool - Find files by pattern matching.

Reference: src/tools/GlobTool/GlobTool.ts
"""

import asyncio
import os
from pathlib import Path
from typing import Optional, List

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult


# Common patterns to always ignore
DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs", "*.egg-info",
}


class GlobTool(BaseTool):
    """Find files matching glob patterns."""

    name = "Glob"
    description = (
        "Fast file pattern matching tool. Supports glob patterns like "
        "'**/*.py' or 'src/**/*.ts'. Returns matching file paths sorted "
        "by modification time."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match files (e.g., '**/*.py')",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: current directory)",
            },
        },
        "required": ["pattern"],
    }
    permission_category = PermissionCategory.READ

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        """Find files matching the glob pattern."""
        pattern = params["pattern"]
        base_path = Path(params.get("path", os.getcwd()))

        if not base_path.is_absolute():
            base_path = Path(os.getcwd()) / base_path

        if not base_path.exists():
            return ToolResult(
                content=f"Directory not found: {base_path}", is_error=True
            )

        try:
            matches = await asyncio.to_thread(
                self._find_matches, base_path, pattern
            )

            if not matches:
                return ToolResult(
                    content=f"No files matched pattern: {pattern}",
                    is_error=False,
                )

            # Sort by modification time (newest first)
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            # Format output
            result_lines = [str(m) for m in matches]

            # Truncate if too many results
            total = len(result_lines)
            if total > 500:
                result_lines = result_lines[:500]
                result_lines.append(f"\n... and {total - 500} more files")

            return ToolResult(
                content="\n".join(result_lines),
                is_error=False,
            )

        except Exception as e:
            return ToolResult(content=f"Error searching files: {e}", is_error=True)

    def _find_matches(self, base_path: Path, pattern: str) -> List[Path]:
        """Find matching files, respecting ignore patterns."""
        matches = []
        for path in base_path.glob(pattern):
            # Skip ignored directories
            parts = path.parts
            if any(ignored in parts for ignored in DEFAULT_IGNORE_DIRS):
                continue
            if path.is_file():
                matches.append(path)
        return matches
