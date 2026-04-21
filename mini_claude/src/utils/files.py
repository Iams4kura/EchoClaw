"""File utility helpers."""

import os
from pathlib import Path
from typing import Optional, List
from datetime import datetime


def find_claude_md(working_dir: Optional[str] = None) -> Optional[str]:
    """Find and load CLAUDE.md content.

    Search order:
    1. CLAUDE.md in working directory
    2. .claude/CLAUDE.md in working directory
    3. Walk up parent directories
    """
    cwd = Path(working_dir or os.getcwd())
    search_names = ["CLAUDE.md", ".claude/CLAUDE.md"]

    # Search current and parent directories
    current = cwd
    while current != current.parent:
        for name in search_names:
            path = current / name
            if path.exists() and path.is_file():
                try:
                    return path.read_text(encoding="utf-8")
                except Exception:
                    pass
        current = current.parent

    return None


def format_file_size(size_bytes: int) -> str:
    """Format byte count to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def safe_read_file(path: str, encoding: str = "utf-8") -> Optional[str]:
    """Read file content, returning None on any error."""
    try:
        return Path(path).read_text(encoding=encoding, errors="replace")
    except Exception:
        return None
