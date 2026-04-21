"""SendMessageTool — enables inter-agent communication.

Reference: src/coordinator/ (multi-agent orchestration)
"""

import asyncio
from typing import Optional

from ..base import BaseTool, PermissionCategory
from ...models.tool import ToolResult
from ...models.state import AppState


class SendMessageTool(BaseTool):
    """Send a message to another active agent."""

    name = "SendMessage"
    description = (
        "Send a message to another running agent by name or ID. "
        "The message will be delivered to the agent's mailbox."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Target agent name or ID",
            },
            "message": {
                "type": "string",
                "description": "Message content to send",
            },
        },
        "required": ["to", "message"],
    }
    permission_category = PermissionCategory.WRITE

    def __init__(self, state: Optional[AppState] = None):
        super().__init__()
        self._state = state

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        target = params["to"]
        message = params["message"]

        if not self._state:
            return ToolResult(content="No state available", is_error=True)

        # Find target agent by name or ID
        target_id = None
        for agent_id, agent_info in self._state.active_agents.items():
            if agent_info.name == target or agent_id == target:
                target_id = agent_id
                break

        if target_id is None:
            available = [a.name for a in self._state.active_agents.values() if a.status == "running"]
            return ToolResult(
                content=f"Agent not found: {target}. Running agents: {', '.join(available) or '(none)'}",
                is_error=True,
            )

        agent_info = self._state.active_agents[target_id]
        if agent_info.status != "running":
            return ToolResult(
                content=f"Agent '{agent_info.name}' is not running (status: {agent_info.status})",
                is_error=True,
            )

        # Deliver to mailbox
        mailbox = self._state.agent_mailbox.get(target_id)
        if mailbox is None:
            return ToolResult(
                content=f"No mailbox for agent '{agent_info.name}'",
                is_error=True,
            )

        await mailbox.put({"from": "main", "message": message})
        return ToolResult(
            content=f"Message delivered to agent '{agent_info.name}'",
            is_error=False,
        )
