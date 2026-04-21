"""Agent color manager - assigns unique colors to agents.

Reference: src/tools/AgentTool/agentColorManager.ts
"""

from typing import Dict

AGENT_COLORS = [
    "blue", "green", "yellow", "purple",
    "cyan", "magenta", "red", "white",
]


class AgentColorManager:
    """Assigns unique colors to agents for UI distinction."""

    def __init__(self) -> None:
        self._assigned: Dict[str, str] = {}
        self._available: list = AGENT_COLORS.copy()

    def assign(self, agent_id: str, name: str = "") -> str:
        """Assign a color to an agent."""
        if agent_id in self._assigned:
            return self._assigned[agent_id]

        color = self._available.pop(0) if self._available else "gray"
        self._assigned[agent_id] = color
        return color

    def release(self, agent_id: str) -> None:
        """Release a color back to the pool."""
        if agent_id in self._assigned:
            color = self._assigned.pop(agent_id)
            if color in AGENT_COLORS:
                self._available.append(color)

    def reset(self) -> None:
        """Reset all color assignments."""
        self._assigned.clear()
        self._available = AGENT_COLORS.copy()
