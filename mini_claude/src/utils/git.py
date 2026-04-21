"""Git operations for context assembly.

Reference: src/context.ts getSystemContext()
"""

import asyncio
import os
from pathlib import Path
from typing import Optional


async def is_git_repo(path: Optional[str] = None) -> bool:
    """Check if path is inside a git repository."""
    cwd = path or os.getcwd()
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--is-inside-work-tree",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=cwd,
    )
    stdout, _ = await proc.communicate()
    return proc.returncode == 0 and stdout.strip() == b"true"


async def get_git_status(path: Optional[str] = None) -> Optional[str]:
    """Get formatted git status for system context.

    Returns a summary including:
    - Current branch
    - Main/master branch
    - Working tree status
    - Recent commits
    """
    cwd = path or os.getcwd()

    if not await is_git_repo(cwd):
        return None

    parts = []

    # Current branch
    branch = await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch:
        parts.append(f"Current branch: {branch}")

    # Detect main branch
    main_branch = await _detect_main_branch(cwd)
    if main_branch:
        parts.append(f"Main branch: {main_branch}")

    # Status (short format)
    status = await _run_git(["status", "--short"], cwd)
    if status:
        parts.append(f"\nStatus:\n{status}")
    else:
        parts.append("\nStatus: clean")

    # Recent commits (last 5)
    log = await _run_git(
        ["log", "--oneline", "-5", "--no-decorate"],
        cwd,
    )
    if log:
        parts.append(f"\nRecent commits:\n{log}")

    return "\n".join(parts)


async def _detect_main_branch(cwd: str) -> str:
    """Detect the main/master branch name."""
    for name in ["main", "master"]:
        result = await _run_git(
            ["rev-parse", "--verify", f"refs/heads/{name}"],
            cwd,
        )
        if result is not None:
            return name
    return "main"  # default assumption


async def _run_git(args: list, cwd: str) -> Optional[str]:
    """Run a git command and return stdout, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return None
