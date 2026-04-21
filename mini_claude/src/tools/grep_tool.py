"""GrepTool - Search file contents with regex.

Reference: src/tools/GrepTool/GrepTool.ts
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Optional, List

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult


class GrepTool(BaseTool):
    """Search file contents using regex patterns."""

    name = "Grep"
    description = (
        "Search for patterns in file contents using regex. "
        "Returns matching file paths or matching lines with context."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in (default: cwd)",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g., '*.py')",
            },
            "output_mode": {
                "type": "string",
                "enum": ["files_with_matches", "content", "count"],
                "description": "Output mode (default: files_with_matches)",
            },
            "context": {
                "type": "integer",
                "description": "Lines of context around matches",
            },
        },
        "required": ["pattern"],
    }
    permission_category = PermissionCategory.READ

    IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", "venv", ".venv",
        ".tox", "dist", "build", ".eggs",
    }

    async def execute(
        self, params: dict, abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        pattern = params["pattern"]
        base_path = Path(params.get("path", os.getcwd()))
        file_glob = params.get("glob")
        output_mode = params.get("output_mode", "files_with_matches")
        context_lines = params.get("context", 0)

        if not base_path.is_absolute():
            base_path = Path(os.getcwd()) / base_path

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(content=f"Invalid regex: {e}", is_error=True)

        # Try ripgrep first, fall back to Python
        rg_result = await self._try_ripgrep(pattern, base_path, file_glob, output_mode, context_lines)
        if rg_result is not None:
            return rg_result

        # Python fallback
        return await asyncio.to_thread(
            self._python_grep, regex, base_path, file_glob, output_mode, context_lines
        )

    async def _try_ripgrep(
        self, pattern: str, path: Path, file_glob: Optional[str],
        output_mode: str, context: int,
    ) -> Optional[ToolResult]:
        """Try using ripgrep (rg) if available."""
        args = ["rg", "--no-heading"]

        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        else:
            args.append("-n")
            if context > 0:
                args.extend(["-C", str(context)])

        if file_glob:
            args.extend(["--glob", file_glob])

        args.extend(["--max-count", "250", pattern, str(path)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode in (0, 1):  # 0=found, 1=not found
                output = stdout.decode("utf-8", errors="replace").strip()
                if not output:
                    return ToolResult(content=f"No matches for: {pattern}", is_error=False)
                return ToolResult(content=output, is_error=False)
        except (FileNotFoundError, asyncio.TimeoutError):
            pass  # rg not installed or timeout, fall through

        return None

    def _python_grep(
        self, regex: re.Pattern, base_path: Path, file_glob: Optional[str],
        output_mode: str, context: int,
    ) -> ToolResult:
        """Pure Python grep fallback."""
        results = []
        files_searched = 0
        max_results = 250

        if base_path.is_file():
            files = [base_path]
        else:
            pattern = file_glob or "**/*"
            files = [
                f for f in base_path.glob(pattern)
                if f.is_file() and not any(d in f.parts for d in self.IGNORE_DIRS)
            ]

        for file_path in files:
            if len(results) >= max_results:
                break
            files_searched += 1

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.splitlines()
            file_matches = []

            for i, line in enumerate(lines):
                if regex.search(line):
                    if output_mode == "files_with_matches":
                        results.append(str(file_path))
                        break
                    elif output_mode == "count":
                        file_matches.append(line)
                    else:
                        # Content mode with context
                        start = max(0, i - context)
                        end = min(len(lines), i + context + 1)
                        for j in range(start, end):
                            prefix = ">" if j == i else " "
                            file_matches.append(f"{file_path}:{j + 1}:{prefix}{lines[j]}")

            if output_mode == "count" and file_matches:
                results.append(f"{file_path}:{len(file_matches)}")
            elif file_matches:
                results.extend(file_matches)

        if not results:
            return ToolResult(content=f"No matches found ({files_searched} files searched)", is_error=False)

        output = "\n".join(results)
        if len(results) >= max_results:
            output += f"\n... (truncated at {max_results} results)"

        return ToolResult(content=output, is_error=False)
