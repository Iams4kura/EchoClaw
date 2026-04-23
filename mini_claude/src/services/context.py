"""Context assembly - system prompt construction and CLAUDE.md loading.

Reference: src/context.ts
"""

import os
from datetime import datetime
from typing import Optional

from ..utils.git import get_git_status, is_git_repo
from ..utils.files import find_claude_md


class ContextAssembler:
    """Builds the system prompt from multiple context sources.

    Context sources:
    - Git status (branch, changes, recent commits)
    - CLAUDE.md project instructions
    - Current date/time
    - Working directory info
    - Model identity
    """

    def __init__(self, working_dir: Optional[str] = None, model: str = ""):
        self.working_dir = working_dir or os.getcwd()
        self._model = model
        self._cached_system_prompt: Optional[str] = None

    async def build_system_prompt(self) -> str:
        """Assemble the full system prompt."""
        if self._cached_system_prompt:
            return self._cached_system_prompt

        sections = []

        sections.append(
            "You are Mini Claude, an AI coding assistant developed by Iams4kura, "
            "built with Python. You help users with software engineering tasks "
            "using the tools available to you. "
            "When asked who you are, always say you are Mini Claude, "
            "developed by Iams4kura using Python. Never claim to be Claude Code or any other product."
        )

        system_ctx = await self._get_system_context()
        if system_ctx:
            sections.append(f"# Environment\n{system_ctx}")

        user_ctx = self._get_user_context()
        if user_ctx:
            sections.append(f"# Project Instructions\n{user_ctx}")

        sections.append(f"# Current Date\nToday is {datetime.now().strftime('%Y-%m-%d')}.")

        memory_ctx = self._load_memory()
        if memory_ctx:
            sections.append(f"# Project Memory\n{memory_ctx}")

        self._cached_system_prompt = "\n\n".join(sections)
        return self._cached_system_prompt

    async def _get_system_context(self) -> Optional[str]:
        """Gather system-level context."""
        parts = [f"Working directory: {self.working_dir}"]

        if await is_git_repo(self.working_dir):
            git_status = await get_git_status(self.working_dir)
            if git_status:
                parts.append(f"Git:\n{git_status}")

        return "\n".join(parts)

    def _get_user_context(self) -> Optional[str]:
        """Load CLAUDE.md project instructions."""
        return find_claude_md(self.working_dir)

    def _load_memory(self) -> Optional[str]:
        """Load .claude/memory.md if it exists."""
        memory_path = os.path.join(self.working_dir, ".claude", "memory.md")
        try:
            if os.path.exists(memory_path):
                with open(memory_path, encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
        return None

    def invalidate_cache(self) -> None:
        """Force rebuild of system prompt on next call."""
        self._cached_system_prompt = None
