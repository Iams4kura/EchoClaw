"""Base tool interface. Reference: src/Tool.ts"""

from abc import ABC, abstractmethod
from typing import Literal, Optional, Any, AsyncIterator, Union, List
from pydantic import BaseModel
import asyncio

from ..models.tool import ToolResult, ToolChunk


class PermissionCategory:
    """Tool permission categories."""
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"


class BaseTool(ABC):
    """Abstract base class for all tools.

    Reference: src/Tool.ts

    Each tool must define:
    - name: Tool identifier used by LLM
    - description: What the tool does
    - input_schema: JSON Schema for parameters
    - permission_category: Security classification

    And implement:
    - execute(): Main execution logic
    - Optional: execute_streaming() for live output
    """

    # Tool metadata (must be overridden)
    name: str = ""
    description: str = ""
    input_schema: dict = {}

    # Execution options
    supports_streaming: bool = False

    # Security classification
    permission_category: str = PermissionCategory.READ

    def __init__(self):
        """Validate tool definition."""
        if not self.name:
            raise ValueError(f"{self.__class__.__name__} must define 'name'")
        if not self.description:
            raise ValueError(f"{self.__class__.__name__} must define 'description'")

    @abstractmethod
    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None
    ) -> ToolResult:
        """Execute the tool with given parameters.

        Args:
            params: Tool parameters validated against input_schema
            abort_event: Cancellation signal

        Returns:
            ToolResult with content and error flag
        """
        pass

    async def execute_streaming(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None
    ) -> AsyncIterator[ToolChunk]:
        """Execute with streaming output.

        Override this if the tool can produce output incrementally
        (e.g., shell commands, long-running processes).

        Yields:
            ToolChunk chunks with output fragments
        """
        raise NotImplementedError(
            f"{self.name} does not support streaming execution"
        )

    def is_destructive(self) -> bool:
        """Check if this tool performs destructive operations."""
        return self.permission_category == PermissionCategory.DESTRUCTIVE

    def is_external(self) -> bool:
        """Check if this tool interacts with external systems."""
        return self.permission_category == PermissionCategory.EXTERNAL

    def get_schema_for_prompt(self) -> dict:
        """Get tool schema for LLM system prompt."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema
        }
