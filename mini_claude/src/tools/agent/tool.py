"""AgentTool - Spawn sub-agents for complex tasks.

Reference: src/tools/AgentTool/AgentTool.ts
"""

import asyncio
from typing import Optional

from ..base import BaseTool, PermissionCategory
from ...models.tool import ToolResult
from ...models.state import AppState
from ...services.llm import LLMClient
from ...tools.registry import ToolRegistry
from .runner import AgentRunner


class AgentTool(BaseTool):
    """Spawn a sub-agent to handle complex tasks autonomously."""

    name = "Agent"
    description = (
        "Launch a sub-agent to handle complex, multi-step tasks. "
        "The agent runs autonomously with its own conversation and "
        "returns a result when complete."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task for the agent to perform",
            },
            "description": {
                "type": "string",
                "description": "Short description of the agent's purpose",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for this agent",
            },
        },
        "required": ["prompt", "description"],
    }
    supports_streaming = True
    permission_category = PermissionCategory.EXTERNAL

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        tools: Optional[ToolRegistry] = None,
        state: Optional[AppState] = None,
    ):
        super().__init__()
        self._llm = llm
        self._tools = tools
        self._state = state
        self._runner: Optional[AgentRunner] = None

    def configure(self, llm: LLMClient, tools: ToolRegistry, state: AppState) -> None:
        """Configure with runtime dependencies."""
        self._llm = llm
        self._tools = tools
        self._state = state
        self._runner = AgentRunner(llm, tools)

    async def execute(
        self, params: dict, abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        if not self._runner or not self._state:
            return ToolResult(
                content="Agent tool not configured. Call configure() first.",
                is_error=True,
            )

        prompt = params["prompt"]
        description = params.get("description", "sub-agent")
        model = params.get("model")

        # Generate a tool_use_id for tracking
        from ...utils.ids import generate_id
        parent_id = f"agent_{generate_id()}"

        agent_id = await self._runner.spawn(
            name=description,
            prompt=prompt,
            parent_tool_use_id=parent_id,
            state=self._state,
            model=model,
        )

        # Wait for agent to complete
        agent_info = self._state.active_agents.get(agent_id)
        if not agent_info:
            return ToolResult(content="Failed to spawn agent", is_error=True)

        # Poll for completion
        while agent_info.status == "running":
            await asyncio.sleep(0.5)
            if abort_event and abort_event.is_set():
                agent_info.status = "killed"
                return ToolResult(content="Agent aborted", is_error=True)

        # Collect agent's final response
        final_text = ""
        if agent_info.messages:
            last_msg = agent_info.messages[-1]
            if last_msg.role == "assistant":
                final_text = last_msg.get_text()

        if agent_info.status == "failed":
            return ToolResult(
                content=f"Agent failed: {final_text or 'unknown error'}",
                is_error=True,
            )

        return ToolResult(
            content=final_text or "(agent completed with no output)",
            is_error=False,
        )
