"""Tool registry for discovery and lookup. Reference: src/tools.ts"""

from typing import Dict, List, Optional, Type
from .base import BaseTool


class ToolRegistry:
    """Central registry for all tools.

    Usage:
        registry = ToolRegistry()
        registry.register(BashTool())

        bash = registry.get("Bash")
        all_tools = registry.get_all()
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._aliases: Dict[str, str] = {}  # alias -> canonical name

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalize tool name for lookup."""
        return name.lower().replace("_", "").replace("tool", "")

    def register(self, tool_instance: BaseTool, aliases: Optional[List[str]] = None) -> None:
        """Register a tool instance.

        Args:
            tool_instance: Configured tool instance
            aliases: Optional list of alternative names
        """
        name = tool_instance.name
        key = name.lower()
        self._tools[key] = tool_instance
        # Also register under aggressive normalization for fuzzy lookup
        norm_key = self._normalize(name)
        if norm_key != key:
            self._aliases[norm_key] = key

        # Register aliases
        if aliases:
            for alias in aliases:
                self._aliases[alias.lower()] = key
                norm_alias = self._normalize(alias)
                if norm_alias != alias.lower():
                    self._aliases[norm_alias] = key

    def get(self, name: str) -> Optional[BaseTool]:
        """Get tool by name (case-insensitive).

        Also resolves aliases and common variations:
        - Bash, bash, BashTool -> BashTool
        - FileRead, file_read, fileread -> FileReadTool
        """
        # Direct lookup by lowercase first (preserves underscores for MCP tools)
        lower = name.lower()
        if lower in self._tools:
            return self._tools[lower]

        # Then try aggressive normalization
        normalized = self._normalize(name)
        if normalized in self._tools:
            return self._tools[normalized]

        # Try aliases
        if normalized in self._aliases:
            canonical = self._aliases[normalized]
            return self._tools.get(canonical)

        # Fuzzy match: try to find by partial match
        for key, tool in self._tools.items():
            if normalized in key or key in normalized:
                return tool

        return None

    def get_all(self) -> List[BaseTool]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_tools_for_prompt(self) -> List[dict]:
        """Generate tool definitions for LLM system prompt."""
        return [tool.get_schema_for_prompt() for tool in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        """Check if tool is registered."""
        return self.get(name) is not None

    def __len__(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    async def register_mcp_tools(self, client: "MCPClient") -> int:
        """Register all tools from an MCP server.

        Returns the number of tools registered.
        """
        from .mcp_tool import MCPToolAdapter

        tools = await client.list_tools()
        count = 0
        for tool_spec in tools:
            adapter = MCPToolAdapter(mcp_client=client, tool_spec=tool_spec)
            self.register(adapter)
            count += 1
        return count

    def __iter__(self):
        """Iterate over registered tools."""
        return iter(self._tools.values())


# Global singleton for convenience
_global_registry: Optional[ToolRegistry] = None


def get_global_registry() -> ToolRegistry:
    """Get or create global tool registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry
