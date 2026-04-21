"""Slash command registration and dispatch."""

from .registry import Command, CommandRegistry
from .builtins import register_builtins

__all__ = ["Command", "CommandRegistry", "register_builtins"]
