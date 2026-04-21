"""测试记忆系统。"""

import os
import tempfile

import pytest

from script.memory.models import MemoryEntry, MemoryType
from script.memory.store import MemoryStore
from script.memory.loader import MemoryLoader
from script.memory.extractor import MemoryExtractor


class TestMemoryEntry:
    """MemoryEntry 数据结构测试。"""

    def test_to_frontmatter_and_back(self) -> None:
        entry = MemoryEntry(
            name="test_pref",
            type=MemoryType.USER,
            description="User prefers concise answers",
            content="The user is a senior engineer who prefers concise, direct answers.",
            id="abc123",
        )
        text = entry.to_frontmatter()
        restored = MemoryEntry.from_frontmatter(text)

        assert restored.name == entry.name
        assert restored.type == entry.type
        assert restored.description == entry.description
        assert restored.content == entry.content
        assert restored.id == entry.id

    def test_filename(self) -> None:
        entry = MemoryEntry(
            name="code-style",
            type=MemoryType.FEEDBACK,
            description="test",
            content="test",
        )
        assert entry.filename() == "feedback_code-style.md"

    def test_to_index_line(self) -> None:
        entry = MemoryEntry(
            name="my_pref",
            type=MemoryType.USER,
            description="A preference",
            content="content",
        )
        line = entry.to_index_line()
        assert "my_pref" in line
        assert "A preference" in line

    def test_from_frontmatter_invalid(self) -> None:
        with pytest.raises(ValueError):
            MemoryEntry.from_frontmatter("no frontmatter here")

    def test_frontmatter_with_optional_fields(self) -> None:
        entry = MemoryEntry(
            name="test",
            type=MemoryType.PROJECT,
            description="desc",
            content="body",
            source_avatar="coder",
            source_user="user1",
        )
        text = entry.to_frontmatter()
        assert "source_avatar: coder" in text
        assert "source_user: user1" in text

        restored = MemoryEntry.from_frontmatter(text)
        assert restored.source_avatar == "coder"
        assert restored.source_user == "user1"


class TestMemoryStore:
    """MemoryStore 文件存储测试。"""

    def _make_store(self, tmp_path: str) -> MemoryStore:
        return MemoryStore(root=tmp_path)

    def test_save_and_load(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        entry = MemoryEntry(
            name="test_entry",
            type=MemoryType.USER,
            description="Test description",
            content="Test content",
        )
        store.save(entry)

        loaded = store.load(entry.filename())
        assert loaded is not None
        assert loaded.name == "test_entry"
        assert loaded.content == "Test content"

    def test_list_all(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        for i in range(3):
            store.save(MemoryEntry(
                name=f"entry_{i}",
                type=MemoryType.PROJECT,
                description=f"Desc {i}",
                content=f"Content {i}",
            ))

        entries = store.list_all()
        assert len(entries) == 3

    def test_list_by_type(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        store.save(MemoryEntry(
            name="user_pref", type=MemoryType.USER,
            description="d", content="c",
        ))
        store.save(MemoryEntry(
            name="proj_info", type=MemoryType.PROJECT,
            description="d", content="c",
        ))

        users = store.list_by_type(MemoryType.USER)
        assert len(users) == 1
        assert users[0].name == "user_pref"

    def test_delete(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        entry = MemoryEntry(
            name="to_delete", type=MemoryType.FEEDBACK,
            description="d", content="c",
        )
        store.save(entry)
        assert len(store.list_all()) == 1

        store.delete(entry.filename())
        assert len(store.list_all()) == 0

    def test_find_by_name(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        store.save(MemoryEntry(
            name="target", type=MemoryType.USER,
            description="d", content="c",
        ))
        store.save(MemoryEntry(
            name="other", type=MemoryType.USER,
            description="d", content="c",
        ))

        found = store.find_by_name("target")
        assert found is not None
        assert found.name == "target"

        not_found = store.find_by_name("nonexistent")
        assert not_found is None

    def test_update_rebuilds_index(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        entry = MemoryEntry(
            name="evolving", type=MemoryType.PROJECT,
            description="Initial desc", content="Initial content",
        )
        store.save(entry)

        entry.content = "Updated content"
        entry.description = "Updated desc"
        store.update(entry)

        loaded = store.load(entry.filename())
        assert loaded is not None
        assert loaded.content == "Updated content"

        # 索引应包含更新后的描述
        index = store.get_index_content()
        assert "Updated desc" in index

    def test_avatar_namespace(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))

        # 保存到 coder 分身命名空间
        store.save(
            MemoryEntry(
                name="code_style", type=MemoryType.FEEDBACK,
                description="d", content="c",
            ),
            namespace="coder",
        )

        # global 命名空间为空
        assert len(store.list_all("global")) == 0
        # coder 命名空间有一条
        assert len(store.list_all("coder")) == 1

    def test_get_namespaces(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        store.save(MemoryEntry(
            name="g", type=MemoryType.USER, description="d", content="c",
        ))
        store.save(MemoryEntry(
            name="a", type=MemoryType.USER, description="d", content="c",
        ), namespace="coder")

        ns = store.get_namespaces()
        assert "global" in ns
        assert "coder" in ns


class TestMemoryLoader:
    """MemoryLoader 加载测试。"""

    def _populated_store(self, tmp_path: str) -> MemoryStore:
        store = MemoryStore(root=str(tmp_path))
        store.save(MemoryEntry(
            name="user_role", type=MemoryType.USER,
            description="User is a senior engineer",
            content="Senior Python engineer, prefers concise responses.",
        ))
        store.save(MemoryEntry(
            name="no_emoji", type=MemoryType.FEEDBACK,
            description="Do not use emojis",
            content="User explicitly asked to never use emojis.",
        ))
        store.save(MemoryEntry(
            name="auth_rewrite", type=MemoryType.PROJECT,
            description="Auth middleware rewrite in progress",
            content="Rewriting auth for compliance.\n\n**Why:** Legal flagged it.",
        ))
        store.save(MemoryEntry(
            name="grafana", type=MemoryType.REFERENCE,
            description="Grafana dashboard for API latency",
            content="grafana.internal/d/api-latency",
        ))
        return store

    def test_load_returns_text(self, tmp_path: str) -> None:
        store = self._populated_store(str(tmp_path))
        loader = MemoryLoader(store)

        text = loader.load_for_context(current_message="fix the auth bug")
        assert len(text) > 0
        assert "# Loaded Memories" in text

    def test_always_loads_user_and_feedback(self, tmp_path: str) -> None:
        store = self._populated_store(str(tmp_path))
        loader = MemoryLoader(store)

        text = loader.load_for_context(current_message="hello")
        assert "user_role" in text
        assert "no_emoji" in text

    def test_relevant_project_loaded(self, tmp_path: str) -> None:
        store = self._populated_store(str(tmp_path))
        loader = MemoryLoader(store)

        text = loader.load_for_context(current_message="update the auth middleware")
        assert "auth" in text.lower()

    def test_empty_store_returns_empty(self, tmp_path: str) -> None:
        store = MemoryStore(root=str(tmp_path))
        loader = MemoryLoader(store)

        text = loader.load_for_context(current_message="hello")
        assert text == ""

    def test_respects_token_budget(self, tmp_path: str) -> None:
        store = self._populated_store(str(tmp_path))
        loader = MemoryLoader(store)

        # 极小 token 预算
        text = loader.load_for_context(current_message="hello", max_tokens=10)
        # 应返回空或很短（预算不够加载任何 section）
        assert len(text) < 200


class TestMemoryExtractor:
    """MemoryExtractor 测试。"""

    def test_no_llm_returns_empty(self) -> None:
        extractor = MemoryExtractor(llm_client=None)
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract(
                conversation=[{"role": "user", "content": "hello"}]
            )
        )
        assert result == []

    def test_parse_valid_json(self) -> None:
        extractor = MemoryExtractor()
        json_text = """[
            {
                "type": "user",
                "name": "test_pref",
                "description": "User likes Python",
                "content": "The user prefers Python."
            }
        ]"""
        entries = extractor._parse_response(json_text)
        assert len(entries) == 1
        assert entries[0].name == "test_pref"
        assert entries[0].type == MemoryType.USER

    def test_parse_code_block_wrapped(self) -> None:
        extractor = MemoryExtractor()
        json_text = """```json
[{"type": "feedback", "name": "no_mock", "description": "d", "content": "c"}]
```"""
        entries = extractor._parse_response(json_text)
        assert len(entries) == 1
        assert entries[0].type == MemoryType.FEEDBACK

    def test_parse_invalid_json_returns_empty(self) -> None:
        extractor = MemoryExtractor()
        entries = extractor._parse_response("not json at all")
        assert entries == []

    def test_parse_empty_array(self) -> None:
        extractor = MemoryExtractor()
        entries = extractor._parse_response("[]")
        assert entries == []

    async def test_empty_conversation_returns_empty(self) -> None:
        extractor = MemoryExtractor(llm_client="fake")
        result = await extractor.extract(conversation=[])
        assert result == []
