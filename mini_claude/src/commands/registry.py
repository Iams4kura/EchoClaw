"""Command registry — register and dispatch slash commands.

Reference: src/commands.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional, List, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui.app import App


@dataclass
class Command:
    """A registered slash command."""

    name: str
    handler: Callable[["App", str], Awaitable[bool]]
    description: str = ""
    aliases: List[str] = field(default_factory=list)


class CommandRegistry:
    """Registry for slash commands with alias support."""

    def __init__(self) -> None:
        self._commands: Dict[str, Command] = {}
        self._aliases: Dict[str, str] = {}  # alias -> canonical name

    def register(self, cmd: Command) -> None:
        """Register a command and its aliases."""
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._aliases[alias] = cmd.name

    def get(self, name: str) -> Optional[Command]:
        """Look up command by name or alias."""
        canonical = self._aliases.get(name, name)
        return self._commands.get(canonical)

    def list_all(self) -> List[Command]:
        """Return all registered commands (no duplicates from aliases)."""
        return list(self._commands.values())

    def all_names(self) -> List[str]:
        """Return all command names and aliases (for tab completion)."""
        names = list(self._commands.keys())
        names.extend(self._aliases.keys())
        return sorted(set(names))
