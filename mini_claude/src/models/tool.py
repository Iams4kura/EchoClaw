"""Tool-related type definitions."""

from dataclasses import dataclass
from typing import TypedDict, Union, List, Any


@dataclass
class ToolResult:
    """Result of tool execution.

    Supports both attribute access (result.is_error) and
    dict-style access (result["is_error"]) for compatibility.
    """
    content: Union[str, List[Any]]
    is_error: bool

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class ToolChunk(TypedDict):
    """Streaming chunk from tool execution."""
    type: str  # "text", "error", "end"
    content: str
