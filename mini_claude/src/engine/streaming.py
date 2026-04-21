"""Streaming response handling.

Reference: src/query.ts streaming logic
"""

import asyncio
from typing import Optional, Callable, Awaitable, List, Union

from ..models.message import Message, TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock
from ..models.state import AppState
from ..models.tool import ToolResult
from ..services.llm import LLMClient
from ..services.permissions import PermissionManager, PermissionDecision
from ..services.compaction import ContextCompactor
from ..tools.registry import ToolRegistry
from ..tools.orchestration import ToolOrchestrator

MAX_TOOL_TURNS = 25


class StreamHandler:
    """Handles streaming conversation turns.

    Unlike QueryEngine which uses non-streaming complete(),
    StreamHandler uses complete_streaming() for real-time text display.
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        state: AppState,
        permissions: Optional[PermissionManager] = None,
        compactor: Optional[ContextCompactor] = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        on_tool_start: Optional[Callable[[ToolUseBlock], Awaitable[None]]] = None,
        on_tool_end: Optional[Callable[[str, ToolResult], Awaitable[None]]] = None,
        on_permission_ask: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
        on_thinking: Optional[Callable[[str], Awaitable[None]]] = None,
        on_thinking_content: Optional[Callable[[str], Awaitable[None]]] = None,
        on_turn_end: Optional[Callable[[dict], Awaitable[None]]] = None,
    ):
        self.llm = llm
        self.tools = tools
        self.state = state
        self.permissions = permissions or PermissionManager()
        self.compactor = compactor or ContextCompactor()
        self._orchestrator = ToolOrchestrator(registry=tools, state=state)
        self._on_token = on_token
        self._on_tool_start = on_tool_start
        self._on_tool_end = on_tool_end
        self._on_permission_ask = on_permission_ask
        self._on_thinking = on_thinking
        self._on_thinking_content = on_thinking_content
        self._on_turn_end = on_turn_end

    async def run_turn(self, user_input: str) -> str:
        """Execute a streaming conversation turn."""
        user_msg = Message(role="user", content=user_input)
        self.state.messages.append(user_msg)

        # Compact if needed
        if self.compactor.needs_compaction(self.state.messages):
            self.state.messages = await self.compactor.compact(self.state.messages)

        tool_turn_count = 0
        final_text = ""

        while tool_turn_count < MAX_TOOL_TURNS:
            if self.state.is_aborted():
                return "(aborted)"

            api_messages = self._build_api_messages()
            tool_defs = self.tools.get_tools_for_prompt() if len(self.tools) > 0 else None

            if self._on_thinking:
                await self._on_thinking("Thinking")
            self.state.is_streaming = True
            collected_text = []
            collected_thinking = []
            collected_tool_uses: List[ToolUseBlock] = []

            try:
                async for block in self.llm.complete_streaming(
                    messages=api_messages,
                    tools=tool_defs,
                ):
                    if self.state.is_aborted():
                        break

                    if isinstance(block, TextBlock):
                        collected_text.append(block.text)
                        if self._on_token:
                            await self._on_token(block.text)
                    elif isinstance(block, ThinkingBlock):
                        collected_thinking.append(block.thinking)
                        if self._on_thinking_content:
                            await self._on_thinking_content(block.thinking)
                    elif isinstance(block, ToolUseBlock):
                        collected_tool_uses.append(block)

            except Exception as e:
                self.state.is_streaming = False
                if self._on_thinking:
                    await self._on_thinking(None)
                return f"Streaming error: {e}"
            finally:
                self.state.is_streaming = False
                if self._on_thinking:
                    await self._on_thinking(None)

            # Build response content
            content_blocks: List[Union[TextBlock, ThinkingBlock, ToolUseBlock]] = []
            full_thinking = "".join(collected_thinking)
            if full_thinking:
                content_blocks.append(ThinkingBlock(thinking=full_thinking))
            full_text = "".join(collected_text)
            if full_text:
                content_blocks.append(TextBlock(text=full_text))
            content_blocks.extend(collected_tool_uses)

            assistant_msg = Message(role="assistant", content=content_blocks)
            self.state.messages.append(assistant_msg)

            final_text = full_text

            # No tool calls -> done
            if not collected_tool_uses:
                break

            # Execute tools with permissions and parallel orchestration
            tool_results = await self._execute_tools_with_permissions(collected_tool_uses)

            result_blocks = []
            for tool_use, result in zip(collected_tool_uses, tool_results):
                result_blocks.append(ToolResultBlock(
                    tool_use_id=tool_use.id,
                    content=result["content"],
                    is_error=result["is_error"],
                ))

            result_msg = Message(role="user", content=result_blocks)
            self.state.messages.append(result_msg)

            tool_turn_count += 1

        # Notify UI with usage stats
        if self._on_turn_end:
            await self._on_turn_end({
                "total_tokens": self.state.total_tokens,
                "tool_turns": tool_turn_count,
            })

        return final_text

    async def _execute_tools_with_permissions(
        self, tool_uses: List[ToolUseBlock]
    ) -> List[ToolResult]:
        """Check permissions then execute with orchestrator."""
        approved_uses = []
        results_map = {}

        for tool_use in tool_uses:
            tool = self.tools.get(tool_use.name)
            if not tool:
                results_map[tool_use.id] = ToolResult(
                    content=f"Unknown tool: {tool_use.name}", is_error=True,
                )
                continue

            decision = self.permissions.check(tool, tool_use.input)

            if decision == PermissionDecision.DENY:
                results_map[tool_use.id] = ToolResult(
                    content=f"Permission denied for {tool_use.name}",
                    is_error=True,
                )
            elif decision == PermissionDecision.ASK:
                allowed = True
                if self._on_permission_ask:
                    allowed = await self._on_permission_ask(tool_use.name, tool_use.input)
                if allowed:
                    approved_uses.append(tool_use)
                else:
                    results_map[tool_use.id] = ToolResult(
                        content=f"User denied {tool_use.name}", is_error=True,
                    )
            else:
                approved_uses.append(tool_use)

        if approved_uses:
            for tu in approved_uses:
                if self._on_tool_start:
                    await self._on_tool_start(tu)

            parallel_results = await self._orchestrator.execute_parallel(approved_uses)

            for tu, result in zip(approved_uses, parallel_results):
                results_map[tu.id] = result
                if self._on_tool_end:
                    await self._on_tool_end(tu.id, result)

        return [
            results_map.get(tu.id, ToolResult(content="Missing", is_error=True))
            for tu in tool_uses
        ]

    def _build_api_messages(self) -> List[Message]:
        """Build messages for API."""
        messages = []
        if self.state.system_prompt:
            messages.append(Message(role="system", content=self.state.system_prompt))
        messages.extend(self.state.messages)
        return messages
