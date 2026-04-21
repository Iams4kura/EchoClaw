"""Unit tests for tool system."""

import asyncio
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.tools.base import BaseTool, PermissionCategory
from src.tools.registry import ToolRegistry
from src.tools.file_read import FileReadTool
from src.tools.file_write import FileWriteTool
from src.tools.file_edit import FileEditTool
from src.tools.glob_tool import GlobTool
from src.tools.ask_user import AskUserTool
from src.tools.todo import TodoWriteTool
from src.models.tool import ToolResult


class TestToolRegistry:
    def setup_method(self):
        self.registry = ToolRegistry()

    def test_register_and_get(self):
        tool = FileReadTool()
        self.registry.register(tool)
        assert self.registry.get("FileRead") is tool

    def test_register_with_aliases(self):
        tool = FileReadTool()
        self.registry.register(tool, aliases=["read", "cat"])
        assert self.registry.get("read") is tool
        assert self.registry.get("cat") is tool

    def test_case_insensitive_lookup(self):
        tool = FileReadTool()
        self.registry.register(tool)
        assert self.registry.get("fileread") is tool
        assert self.registry.get("FILEREAD") is tool

    def test_unknown_tool(self):
        assert self.registry.get("nonexistent") is None

    def test_get_tools_for_prompt(self):
        self.registry.register(FileReadTool())
        self.registry.register(FileWriteTool())
        tools = self.registry.get_tools_for_prompt()
        assert len(tools) == 2
        assert all("name" in t for t in tools)
        assert all("input_schema" in t for t in tools)

    def test_len(self):
        self.registry.register(FileReadTool())
        self.registry.register(FileWriteTool())
        assert len(self.registry) == 2


class TestFileReadTool:
    def setup_method(self):
        self.tool = FileReadTool()

    @pytest.mark.asyncio
    async def test_read_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            f.flush()
            result = await self.tool.execute({"file_path": f.name})
            assert not result["is_error"]
            assert "line1" in result["content"]
            assert "line2" in result["content"]
            os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        result = await self.tool.execute({"file_path": "/nonexistent/file.txt"})
        assert result["is_error"]

    @pytest.mark.asyncio
    async def test_read_with_offset_limit(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for i in range(100):
                f.write(f"line {i}\n")
            f.flush()
            result = await self.tool.execute({"file_path": f.name, "offset": 10, "limit": 5})
            assert not result["is_error"]
            assert "line 10" in result["content"]
            os.unlink(f.name)


class TestFileWriteTool:
    def setup_method(self):
        self.tool = FileWriteTool()

    @pytest.mark.asyncio
    async def test_write_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test.txt")
            result = await self.tool.execute({"file_path": path, "content": "hello world"})
            assert not result["is_error"]
            assert os.path.exists(path)
            with open(path) as f:
                assert f.read() == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_parents(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "dir", "test.txt")
            result = await self.tool.execute({"file_path": path, "content": "nested"})
            assert not result["is_error"]
            assert os.path.exists(path)


class TestFileEditTool:
    def setup_method(self):
        self.tool = FileEditTool()

    @pytest.mark.asyncio
    async def test_edit_replace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("hello world\nfoo bar\n")
            f.flush()
            result = await self.tool.execute({
                "file_path": f.name,
                "old_string": "hello world",
                "new_string": "hello universe",
            })
            assert not result["is_error"]
            with open(f.name) as rf:
                assert "hello universe" in rf.read()
            os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_edit_nonunique(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("aaa\naaa\n")
            f.flush()
            result = await self.tool.execute({
                "file_path": f.name,
                "old_string": "aaa",
                "new_string": "bbb",
            })
            # Should fail because "aaa" appears twice
            assert result["is_error"]
            os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_edit_replace_all(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("aaa\naaa\n")
            f.flush()
            result = await self.tool.execute({
                "file_path": f.name,
                "old_string": "aaa",
                "new_string": "bbb",
                "replace_all": True,
            })
            assert not result["is_error"]
            with open(f.name) as rf:
                content = rf.read()
                assert "aaa" not in content
                assert content.count("bbb") == 2
            os.unlink(f.name)


class TestGlobTool:
    def setup_method(self):
        self.tool = GlobTool()

    @pytest.mark.asyncio
    async def test_glob_pattern(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["a.py", "b.py", "c.txt"]:
                open(os.path.join(d, name), "w").close()
            result = await self.tool.execute({"pattern": "*.py", "path": d})
            assert not result["is_error"]
            assert "a.py" in result["content"]
            assert "b.py" in result["content"]
            assert "c.txt" not in result["content"]


class TestTodoWriteTool:
    def setup_method(self):
        self.tool = TodoWriteTool()

    @pytest.mark.asyncio
    async def test_create_and_list(self):
        result = await self.tool.execute({
            "action": "create",
            "subject": "Test task",
            "description": "A test",
        })
        assert not result["is_error"]

        result = await self.tool.execute({"action": "list"})
        assert not result["is_error"]
        assert "Test task" in result["content"]

    @pytest.mark.asyncio
    async def test_update_status(self):
        await self.tool.execute({
            "action": "create",
            "subject": "To complete",
            "description": "Will be done",
        })
        result = await self.tool.execute({
            "action": "update",
            "task_id": "1",
            "status": "completed",
        })
        assert not result["is_error"]
