"""Unit tests for services layer."""

import asyncio
import json
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.models.message import Message, TextBlock, ToolUseBlock, ToolResultBlock
from src.models.state import AppState
from src.services.permissions import PermissionManager, PermissionDecision
from src.services.compaction import ContextCompactor
from src.services.persistence import SessionPersistence
from src.tools.base import PermissionCategory


# ---- Permission Tests ----

class TestPermissionManager:
    def setup_method(self):
        self.pm = PermissionManager(mode="ask")

    def test_read_tools_auto_allowed(self):
        tool = MagicMock()
        tool.name = "FileRead"
        tool.permission_category = PermissionCategory.READ
        assert self.pm.check(tool, {}) == PermissionDecision.ALLOW

    def test_write_tools_ask(self):
        tool = MagicMock()
        tool.name = "FileWrite"
        tool.permission_category = PermissionCategory.WRITE
        assert self.pm.check(tool, {}) == PermissionDecision.ASK

    def test_auto_mode_allows_all(self):
        pm = PermissionManager(mode="auto")
        tool = MagicMock()
        tool.name = "Bash"
        tool.permission_category = PermissionCategory.EXTERNAL
        assert pm.check(tool, {}) == PermissionDecision.ALLOW

    def test_restricted_mode_denies_destructive(self):
        pm = PermissionManager(mode="restricted")
        tool = MagicMock()
        tool.name = "FileEdit"
        tool.permission_category = PermissionCategory.DESTRUCTIVE
        assert pm.check(tool, {}) == PermissionDecision.DENY

    def test_session_override(self):
        tool = MagicMock()
        tool.name = "Bash"
        tool.permission_category = PermissionCategory.EXTERNAL
        self.pm.set_session_override("Bash", PermissionDecision.ALLOW)
        assert self.pm.check(tool, {}) == PermissionDecision.ALLOW


# ---- Compaction Tests ----

class TestContextCompactor:
    def setup_method(self):
        self.compactor = ContextCompactor(max_tokens=200000, threshold=0.9)

    def test_estimate_tokens(self):
        messages = [
            Message(role="user", content="Hello world"),
        ]
        tokens = self.compactor.estimate_tokens(messages)
        assert tokens > 0
        assert tokens < 100

    def test_needs_compaction_false(self):
        messages = [Message(role="user", content="short")]
        assert not self.compactor.needs_compaction(messages)

    def test_needs_compaction_true(self):
        # Create a huge message
        big_text = "x" * (200000 * 4)  # ~200k tokens
        messages = [Message(role="user", content=big_text)]
        assert self.compactor.needs_compaction(messages)

    @pytest.mark.asyncio
    async def test_truncate_tool_results(self):
        long_result = "x" * 5000
        messages = [
            Message(role="user", content=[
                ToolResultBlock(
                    tool_use_id="test_1",
                    content=long_result,
                    is_error=False,
                )
            ])
        ]
        result = self.compactor._truncate_tool_results(messages, max_result_len=100)
        block = result[0].content[0]
        assert len(block.content) < 5000
        assert "truncated" in block.content

    @pytest.mark.asyncio
    async def test_summarize_early(self):
        messages = [
            Message(role="system", content="system"),
            Message(role="user", content="first"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="second"),
            Message(role="assistant", content="a2"),
            Message(role="user", content="third"),
            Message(role="assistant", content="a3"),
            Message(role="user", content="fourth"),
        ]
        result = self.compactor._summarize_early(messages)
        assert len(result) < len(messages)
        # Should keep first 2 and last 4
        assert result[0].get_text() == "system"
        assert result[-1].get_text() == "fourth"

    @pytest.mark.asyncio
    async def test_hard_truncate(self):
        messages = [
            Message(role="system", content="sys"),
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="u2"),
            Message(role="assistant", content="a2"),
            Message(role="user", content="u3"),
            Message(role="assistant", content="a3"),
        ]
        result = self.compactor._hard_truncate(messages)
        # Should keep system + last 3
        assert len(result) == 4
        # System message has role "system" (it may get filtered by _hard_truncate logic)
        assert any(m.role == "system" for m in result)


# ---- Persistence Tests ----

class TestSessionPersistence:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.persistence = SessionPersistence(sessions_dir=self.tmpdir)

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load(self):
        state = AppState()
        state.messages.append(Message(role="user", content="hello"))

        path = self.persistence.save(state)
        assert os.path.exists(path)

        loaded = self.persistence.load(state.session_id)
        assert loaded is not None
        assert loaded.session_id == state.session_id

    def test_list_sessions(self):
        from src.utils.ids import generate_session_id
        for i in range(3):
            state = AppState(session_id=generate_session_id())
            state.messages.append(Message(role="user", content=f"test {i}"))
            self.persistence.save(state)

        sessions = self.persistence.list_sessions()
        assert len(sessions) == 3

    def test_delete_session(self):
        state = AppState()
        self.persistence.save(state)
        assert self.persistence.delete(state.session_id)
        assert self.persistence.load(state.session_id) is None

    def test_cleanup(self):
        import time
        from src.utils.ids import generate_session_id
        for i in range(5):
            state = AppState(session_id=generate_session_id())
            self.persistence.save(state)
            time.sleep(0.01)  # Ensure different mtime

        removed = self.persistence.cleanup(max_sessions=3)
        assert removed == 2
        assert len(self.persistence.list_sessions()) == 3

    def test_partial_match_load(self):
        state = AppState()
        self.persistence.save(state)
        # Use first 8 chars of session_id
        partial = state.session_id[:8]
        loaded = self.persistence.load(partial)
        assert loaded is not None
