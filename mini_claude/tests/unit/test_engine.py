"""Unit tests for engine layer."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.message import Message, TextBlock, ToolUseBlock, ToolResultBlock
from src.models.state import AppState
from src.models.tool import ToolResult
from src.services.llm import LLMResponse
from src.services.permissions import PermissionManager, PermissionDecision
from src.tools.registry import ToolRegistry
from src.engine.query import QueryEngine


class TestQueryEngine:
    def setup_method(self):
        self.llm = MagicMock()
        self.registry = ToolRegistry()
        self.state = AppState()
        self.permissions = PermissionManager(mode="auto")
        self.engine = QueryEngine(
            llm=self.llm,
            tools=self.registry,
            state=self.state,
            permissions=self.permissions,
        )

    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        """LLM returns only text -> single turn."""
        self.llm.complete = AsyncMock(return_value=LLMResponse(
            content=[TextBlock(text="Hello!")],
            
            usage={"input_tokens": 10, "output_tokens": 5},
        ))

        result = await self.engine.run_turn("Hi")
        assert result == "Hello!"
        assert len(self.state.messages) == 2  # user + assistant
        assert self.state.total_tokens == 15

    @pytest.mark.asyncio
    async def test_tool_use_then_text(self):
        """LLM calls a tool, then responds with text."""
        mock_tool = MagicMock()
        mock_tool.name = "TestTool"
        mock_tool.permission_category = "read"
        mock_tool.execute = AsyncMock(return_value=ToolResult(
            content="tool output", is_error=False,
        ))
        self.registry.register(mock_tool)

        # First call: tool use
        # Second call: text response
        self.llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=[
                    TextBlock(text="Let me check..."),
                    ToolUseBlock(id="tu_1", name="TestTool", input={"key": "val"}),
                ],
                
                usage={"input_tokens": 20, "output_tokens": 30},
            ),
            LLMResponse(
                content=[TextBlock(text="Done!")],
                
                usage={"input_tokens": 40, "output_tokens": 10},
            ),
        ])

        result = await self.engine.run_turn("do something")
        assert result == "Done!"
        assert mock_tool.execute.called
        # user + assistant(tool_use) + user(tool_result) + assistant(text) = 4
        assert len(self.state.messages) == 4

    @pytest.mark.asyncio
    async def test_abort_during_turn(self):
        """Abort event stops the loop."""
        self.state.abort_event.set()
        result = await self.engine.run_turn("test")
        assert "aborted" in result.lower()

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """Unknown tool returns error result."""
        self.llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=[
                    ToolUseBlock(id="tu_1", name="FakeTool", input={}),
                ],
                
                usage={},
            ),
            LLMResponse(
                content=[TextBlock(text="ok")],
                
                usage={},
            ),
        ])

        result = await self.engine.run_turn("use fake tool")
        assert result == "ok"
        # Check that the tool result message has error
        tool_result_msg = self.state.messages[2]  # user(tool_result)
        assert tool_result_msg.content[0].is_error

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        """Tool with denied permission returns error."""
        permissions = PermissionManager(mode="restricted")
        engine = QueryEngine(
            llm=self.llm,
            tools=self.registry,
            state=self.state,
            permissions=permissions,
        )

        mock_tool = MagicMock()
        mock_tool.name = "DangerTool"
        mock_tool.permission_category = "destructive"
        self.registry.register(mock_tool)

        self.llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=[ToolUseBlock(id="tu_1", name="DangerTool", input={})],
                
                usage={},
            ),
            LLMResponse(
                content=[TextBlock(text="denied")],
                
                usage={},
            ),
        ])

        result = await engine.run_turn("danger")
        assert result == "denied"
        # Tool should NOT have been called
        assert not mock_tool.execute.called

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """LLM API error returns error message."""
        self.llm.complete = AsyncMock(side_effect=Exception("API down"))
        result = await self.engine.run_turn("test")
        assert "API error" in result

    @pytest.mark.asyncio
    async def test_max_tool_turns(self):
        """Engine stops after MAX_TOOL_TURNS."""
        mock_tool = MagicMock()
        mock_tool.name = "Loop"
        mock_tool.permission_category = "read"
        mock_tool.execute = AsyncMock(return_value=ToolResult(
            content="ok", is_error=False,
        ))
        self.registry.register(mock_tool)

        # Always return tool use
        self.llm.complete = AsyncMock(return_value=LLMResponse(
            content=[ToolUseBlock(id="tu_1", name="Loop", input={})],
            
            usage={},
        ))

        result = await self.engine.run_turn("loop forever")
        assert "maximum tool execution limit" in result.lower()
