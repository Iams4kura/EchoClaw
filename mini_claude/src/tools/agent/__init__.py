"""Agent subsystem - spawn and manage sub-agents."""

from .tool import AgentTool
from .runner import AgentRunner
from .color import AgentColorManager, AGENT_COLORS

__all__ = ["AgentTool", "AgentRunner", "AgentColorManager", "AGENT_COLORS"]
