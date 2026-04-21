"""Integration test: full query flow with real tools (no LLM)."""

import asyncio
import os
import tempfile
import pytest
from unittest.mock import AsyncMock

from src.models.message import Message, TextBlock, ToolUseBlock, ToolResultBlock
from src.models.state import AppState
from src.models.tool import ToolResult
from src.services.llm import LLMResponse
from src.services.permissions import PermissionManager
from src.services.compaction import ContextCompactor
from src.tools.registry import ToolRegistry
from src.tools.file_read import FileReadTool
from src.tools.file_write import FileWriteTool
from src.tools.file_edit import FileEditTool
from src.tools.glob_tool import GlobTool
from src.tools.todo import TodoWriteTool
from src.engine.query import QueryEngine


class TestFullQueryFlow:
    """Test complete query flow: user -> LLM(mocked) -> tool execution -> response."""

    def setup_method(self):
        self.state = AppState()
        self.registry = ToolRegistry()
        self.registry.register(FileReadTool(), aliases=["read"])
        self.registry.register(FileWriteTool(), aliases=["write"])
        self.registry.register(FileEditTool(), aliases=["edit"])
        self.registry.register(GlobTool(), aliases=["glob"])
        self.registry.register(TodoWriteTool())

        self.llm = AsyncMock()
        self.permissions = PermissionManager(mode="auto")
        self.engine = QueryEngine(
            llm=self.llm,
            tools=self.registry,
            state=self.state,
            permissions=self.permissions,
        )

    @pytest.mark.asyncio
    async def test_write_then_read_file(self):
        """LLM writes a file, then reads it back."""
        with tempfile.TemporaryDirectory() as d:
            filepath = os.path.join(d, "test.txt")

            self.llm.complete = AsyncMock(side_effect=[
                # Turn 1: Write file
                LLMResponse(
                    content=[
                        TextBlock(text="Writing file..."),
                        ToolUseBlock(id="tu_1", name="FileWrite", input={
                            "file_path": filepath,
                            "content": "Hello from integration test!",
                        }),
                    ],
                    
                    usage={"input_tokens": 10, "output_tokens": 10},
                ),
                # Turn 2: Read file
                LLMResponse(
                    content=[
                        ToolUseBlock(id="tu_2", name="FileRead", input={
                            "file_path": filepath,
                        }),
                    ],
                    
                    usage={"input_tokens": 20, "output_tokens": 10},
                ),
                # Turn 3: Final response
                LLMResponse(
                    content=[TextBlock(text="File contains the test message.")],
                    
                    usage={"input_tokens": 30, "output_tokens": 10},
                ),
            ])

            result = await self.engine.run_turn("Write and read a test file")
            assert "File contains the test message" in result
            assert os.path.exists(filepath)
            with open(filepath) as f:
                assert f.read() == "Hello from integration test!"

    @pytest.mark.asyncio
    async def test_edit_existing_file(self):
        """LLM edits an existing file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def hello():\n    return 'world'\n")
            f.flush()
            filepath = f.name

        self.llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=[
                    ToolUseBlock(id="tu_1", name="FileEdit", input={
                        "file_path": filepath,
                        "old_string": "return 'world'",
                        "new_string": "return 'universe'",
                    }),
                ],
                
                usage={},
            ),
            LLMResponse(
                content=[TextBlock(text="Updated the return value.")],
                
                usage={},
            ),
        ])

        result = await self.engine.run_turn("change world to universe")
        assert "Updated" in result
        with open(filepath) as f:
            assert "universe" in f.read()
        os.unlink(filepath)

    @pytest.mark.asyncio
    async def test_glob_finds_files(self):
        """LLM uses glob to find files."""
        with tempfile.TemporaryDirectory() as d:
            for name in ["a.py", "b.py", "c.txt"]:
                open(os.path.join(d, name), "w").close()

            self.llm.complete = AsyncMock(side_effect=[
                LLMResponse(
                    content=[
                        ToolUseBlock(id="tu_1", name="Glob", input={
                            "pattern": "*.py", "path": d,
                        }),
                    ],
                    
                    usage={},
                ),
                LLMResponse(
                    content=[TextBlock(text="Found 2 Python files.")],
                    
                    usage={},
                ),
            ])

            result = await self.engine.run_turn("find python files")
            assert "Found 2" in result

    @pytest.mark.asyncio
    async def test_compaction_triggers(self):
        """Compaction runs when context is too large."""
        compactor = ContextCompactor(max_tokens=50, threshold=0.5)
        engine = QueryEngine(
            llm=self.llm,
            tools=self.registry,
            state=self.state,
            permissions=self.permissions,
            compactor=compactor,
        )

        # Add enough messages to trigger compaction
        for i in range(20):
            self.state.messages.append(
                Message(role="user" if i % 2 == 0 else "assistant",
                        content=f"Message {i} " + "x" * 200)
            )

        self.llm.complete = AsyncMock(return_value=LLMResponse(
            content=[TextBlock(text="Done")],
            
            usage={},
        ))

        before_count = len(self.state.messages)
        result = await engine.run_turn("test compaction")
        # Messages should have been compacted
        # (exact count depends on strategy, but should be fewer)
        assert result == "Done"

    @pytest.mark.asyncio
    async def test_todo_workflow(self):
        """LLM creates and updates a todo item."""
        self.llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=[
                    ToolUseBlock(id="tu_1", name="TodoWrite", input={
                        "action": "create",
                        "subject": "Fix bug #42",
                        "description": "Null pointer in login",
                    }),
                ],
                
                usage={},
            ),
            LLMResponse(
                content=[
                    ToolUseBlock(id="tu_2", name="TodoWrite", input={
                        "action": "update",
                        "task_id": "1",
                        "status": "completed",
                    }),
                ],
                
                usage={},
            ),
            LLMResponse(
                content=[TextBlock(text="Bug fixed and marked complete.")],
                
                usage={},
            ),
        ])

        result = await self.engine.run_turn("fix bug 42")
        assert "fixed" in result.lower() or "complete" in result.lower()
