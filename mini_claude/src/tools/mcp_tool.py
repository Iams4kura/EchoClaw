"""MCP Tool Adapter — wraps MCP server tools as BaseTool instances.

Reference: src/services/mcp/ (MCP server management)
"""

import asyncio
from typing import Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult
from ..services.mcp import MCPClient


class MCPToolAdapter(BaseTool):
    """Adapts an MCP server tool to the BaseTool interface."""

    permission_category = PermissionCategory.EXTERNAL

    def __init__(self, mcp_client: MCPClient, tool_spec: dict):
        self._client = mcp_client
        self._tool_name = tool_spec["name"]
        # Set BaseTool attributes from MCP tool spec (before super().__init__ validation)
        self.name = f"mcp__{mcp_client.name}__{tool_spec['name']}"
        self.description = tool_spec.get("description", f"MCP tool from {mcp_client.name}")
        self.input_schema = tool_spec.get("inputSchema", {"type": "object", "properties": {}})
        super().__init__()

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        """Execute the MCP tool by delegating to the MCP server."""
        try:
            result = await self._client.call_tool(self._tool_name, params)
            return ToolResult(
                content=result.get("content", ""),
                is_error=result.get("isError", False),
            )
        except Exception as e:
            return ToolResult(
                content=f"MCP tool error ({self._client.name}/{self._tool_name}): {e}",
                is_error=True,
            )
