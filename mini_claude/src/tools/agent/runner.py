"""Agent runner - manages agent lifecycle and conversation.

Reference: src/tools/AgentTool/AgentTool.ts
"""

import asyncio
import logging
from typing import Optional

from ...models.state import AppState, AgentInfo
from ...models.message import Message
from ...services.llm import LLMClient
from ...tools.registry import ToolRegistry
from ...utils.ids import generate_agent_id
from .color import AgentColorManager

logger = logging.getLogger(__name__)


class AgentRunner:
    """Runs an Agent as a long-lived sub-conversation.

    Agents maintain their own message history and can
    execute tools independently from the main conversation.
    """

    def __init__(self, llm: LLMClient, tools: ToolRegistry):
        self.llm = llm
        self.tools = tools
        self.color_manager = AgentColorManager()

    async def spawn(
        self,
        name: str,
        prompt: str,
        parent_tool_use_id: str,
        state: AppState,
        model: Optional[str] = None,
    ) -> str:
        """Spawn a new agent and start it in the background.

        Returns: agent_id
        """
        agent_id = generate_agent_id()
        color = self.color_manager.assign(agent_id, name)

        agent_info = AgentInfo(
            agent_id=agent_id,
            name=name,
            color=color,
            model=model or self.llm.config.model,
            status="running",
            parent_tool_use_id=parent_tool_use_id,
        )

        state.active_agents[agent_id] = agent_info
        state.agent_mailbox[agent_id] = asyncio.Queue()

        # Start agent in background
        asyncio.create_task(self._run_agent(agent_info, prompt, state))

        return agent_id

    async def _run_agent(
        self, agent: AgentInfo, prompt: str, state: AppState,
    ) -> None:
        """Main agent execution loop."""
        try:
            # Build agent system prompt
            system = (
                f"You are agent '{agent.name}'. You are a sub-agent helping "
                f"with a specific task. Complete the task and return your results."
            )

            agent.messages.append(Message(role="system", content=system))
            agent.messages.append(Message(role="user", content=prompt))

            max_turns = 15
            for turn in range(max_turns):
                if state.is_aborted():
                    agent.status = "killed"
                    return

                try:
                    response = await self.llm.complete(
                        messages=agent.messages,
                        tools=self.tools.get_tools_for_prompt(),
                        model=agent.model,
                    )
                except Exception as e:
                    logger.error(f"Agent {agent.name} LLM error: {e}")
                    agent.status = "failed"
                    return

                # Add assistant response
                assistant_msg = Message(role="assistant", content=response.content)
                agent.messages.append(assistant_msg)

                # Check for tool uses
                tool_uses = assistant_msg.get_tool_uses()
                if not tool_uses:
                    break  # Agent done

                # Execute tools
                from ...models.message import ToolResultBlock
                result_blocks = []
                for tool_use in tool_uses:
                    tool = self.tools.get(tool_use.name)
                    if tool:
                        result = await tool.execute(tool_use.input, state.abort_event)
                        result_blocks.append(ToolResultBlock(
                            tool_use_id=tool_use.id,
                            content=result["content"],
                            is_error=result["is_error"],
                        ))
                    else:
                        result_blocks.append(ToolResultBlock(
                            tool_use_id=tool_use.id,
                            content=f"Unknown tool: {tool_use.name}",
                            is_error=True,
                        ))

                agent.messages.append(Message(role="user", content=result_blocks))

                # Check mailbox for inter-agent messages
                self._check_mailbox(agent, state)

            agent.status = "completed"

        except Exception as e:
            logger.error(f"Agent {agent.name} failed: {e}")
            agent.status = "failed"
        finally:
            self.color_manager.release(agent.agent_id)
            # Cleanup mailbox
            state.agent_mailbox.pop(agent.agent_id, None)

    @staticmethod
    def _check_mailbox(agent: AgentInfo, state: AppState) -> None:
        """Check and process any pending messages in the agent's mailbox."""
        mailbox = state.agent_mailbox.get(agent.agent_id)
        if mailbox is None:
            return

        messages_received = []
        while not mailbox.empty():
            try:
                msg = mailbox.get_nowait()
                sender = msg.get("from", "unknown")
                content = msg.get("message", "")
                messages_received.append(f"[Message from {sender}]: {content}")
            except Exception:
                break

        if messages_received:
            combined = "\n".join(messages_received)
            agent.messages.append(Message(role="user", content=combined))
            logger.info("Agent '%s' received %d message(s)", agent.name, len(messages_received))
