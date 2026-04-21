"""测试 Brain 认知循环。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from script.brain.cognitive import CognitiveLoop
from script.brain.conversation import ConversationStore, Turn
from script.brain.llm_client import BrainConfig
from script.brain.models import (
    BrainDecision,
    Intent,
    IntentType,
    PlanStep,
    ThinkingContext,
)
from script.brain.planner import TaskPlanner
from script.gateway.models import BotResponse, UnifiedMessage


# ── 测试数据 ─────────────────────────────────────────────────

def _make_msg(content: str = "你好", user_id: str = "test_user") -> UnifiedMessage:
    return UnifiedMessage(
        platform="test",
        user_id=user_id,
        chat_id=f"chat_{user_id}",
        content=content,
    )


def _make_cognitive() -> CognitiveLoop:
    """创建一个 mock 版 CognitiveLoop。"""
    llm = MagicMock()
    llm.classify = AsyncMock(return_value={
        "type": "chitchat",
        "confidence": 0.9,
        "summary": "用户打招呼",
        "requires_engine": False,
        "memory_keywords": ["你好"],
    })
    llm.think = AsyncMock(return_value="你好呀！很高兴见到你。")

    soul = MagicMock()
    soul.get_system_prompt_fragment.return_value = "你是小爪，一名数字员工。"
    soul.get_mood_context.return_value = "精力充沛，心情不错"
    soul.name = "小爪"
    soul.get_error_message.return_value = "出了点问题"
    soul.get_thinking_message.return_value = "让我想想..."

    hands = MagicMock()
    hands.execute = AsyncMock()
    hands.get_status.return_value = {"active_executors": 0}

    memory_store = MagicMock()
    memory_store.list_all.return_value = []

    memory_loader = MagicMock()
    memory_loader.active_recall.return_value = []

    memory_extractor = MagicMock()
    memory_extractor.reflect = AsyncMock(return_value=[])

    conversation = ConversationStore()

    return CognitiveLoop(
        llm=llm,
        soul=soul,
        hands=hands,
        memory_store=memory_store,
        memory_loader=memory_loader,
        memory_extractor=memory_extractor,
        conversation=conversation,
        state_provider=lambda: hands.get_status(),
    )


# ── Brain 模型测试 ───────────────────────────────────────────

class TestIntentType:
    def test_all_types_exist(self) -> None:
        expected = {"chitchat", "status", "coding", "file_ops", "knowledge", "command", "complex", "memory"}
        actual = {t.value for t in IntentType}
        assert expected == actual

    def test_from_string(self) -> None:
        assert IntentType("coding") == IntentType.CODING
        assert IntentType("chitchat") == IntentType.CHITCHAT


class TestIntent:
    def test_default_values(self) -> None:
        intent = Intent(type=IntentType.CHITCHAT, confidence=0.9, summary="test")
        assert intent.requires_engine is False
        assert intent.memory_keywords == []

    def test_coding_requires_engine(self) -> None:
        intent = Intent(
            type=IntentType.CODING,
            confidence=0.95,
            summary="写一个函数",
            requires_engine=True,
        )
        assert intent.requires_engine is True


class TestBrainDecision:
    def test_reply_action(self) -> None:
        d = BrainDecision(action="reply", response_text="hello")
        assert d.action == "reply"
        assert d.response_text == "hello"

    def test_delegate_action(self) -> None:
        d = BrainDecision(action="delegate", engine_prompt="写一个排序函数")
        assert d.action == "delegate"

    def test_plan_action(self) -> None:
        steps = [PlanStep(description="step1", executor="brain", prompt="do something")]
        d = BrainDecision(action="plan", plan=steps)
        assert len(d.plan) == 1


# ── 对话存储测试 ─────────────────────────────────────────────

class TestConversationStore:
    def test_add_and_get(self) -> None:
        store = ConversationStore()
        store.add("user1", "user", "hello")
        store.add("user1", "assistant", "hi there")

        recent = store.get_recent("user1")
        assert len(recent) == 2
        assert recent[0]["role"] == "user"
        assert recent[1]["content"] == "hi there"

    def test_max_history(self) -> None:
        store = ConversationStore(max_history=3)
        for i in range(5):
            store.add("user1", "user", f"msg{i}")

        recent = store.get_recent("user1")
        assert len(recent) == 3
        assert recent[0]["content"] == "msg2"

    def test_clear(self) -> None:
        store = ConversationStore()
        store.add("user1", "user", "hello")
        store.clear("user1")
        assert store.get_recent("user1") == []

    def test_separate_users(self) -> None:
        store = ConversationStore()
        store.add("user1", "user", "hello from user1")
        store.add("user2", "user", "hello from user2")
        assert len(store.get_recent("user1")) == 1
        assert len(store.get_recent("user2")) == 1


# ── 认知循环测试 ─────────────────────────────────────────────

class TestCognitiveLoop:
    async def test_chitchat_response(self) -> None:
        """闲聊消息应该由 Brain 直接回复。"""
        loop = _make_cognitive()
        msg = _make_msg("你好")

        response = await loop.process(msg)

        assert isinstance(response, BotResponse)
        assert response.text  # 非空回复

    async def test_command_fast_path(self) -> None:
        """/ 命令应该走快速路径，不调用 LLM 分类。"""
        loop = _make_cognitive()
        msg = _make_msg("/help")

        response = await loop.process(msg)

        assert isinstance(response, BotResponse)
        # /help 不应该调用 classify
        loop._llm.classify.assert_not_called()

    async def test_coding_delegates_to_engine(self) -> None:
        """编码任务应该委派给 Hands 执行。"""
        loop = _make_cognitive()

        # Mock: 分类为 coding
        loop._llm.classify = AsyncMock(return_value={
            "type": "coding",
            "confidence": 0.95,
            "summary": "写排序函数",
            "requires_engine": True,
            "memory_keywords": ["排序", "函数"],
        })

        # Mock: Hands 执行结果
        from script.hands.models import ExecutionResult
        loop._hands.execute = AsyncMock(return_value=ExecutionResult(
            success=True,
            output="def sort(arr): return sorted(arr)",
            duration_ms=500,
        ))

        msg = _make_msg("写一个排序函数")
        response = await loop.process(msg)

        assert isinstance(response, BotResponse)
        loop._hands.execute.assert_called_once()

    async def test_error_handling(self) -> None:
        """异常时应返回错误消息而非崩溃。"""
        loop = _make_cognitive()
        loop._llm.classify = AsyncMock(side_effect=Exception("LLM down"))

        msg = _make_msg("你好")
        response = await loop.process(msg)

        # 分类失败降级为 chitchat，仍应返回响应
        assert isinstance(response, BotResponse)

    async def test_memory_recall_called(self) -> None:
        """意图分类后应触发记忆检索。"""
        loop = _make_cognitive()
        msg = _make_msg("昨天那个项目怎么样了")

        await loop.process(msg)

        loop._memory_loader.active_recall.assert_called_once()


# ── TaskPlanner 测试 ─────────────────────────────────────────

class TestTaskPlanner:
    def test_parse_steps_valid_json(self) -> None:
        text = '''```json
[
  {"description": "读取文件", "executor": "engine", "prompt": "读 main.py", "depends_on": []},
  {"description": "分析代码", "executor": "brain", "prompt": "分析上一步结果", "depends_on": [0]}
]
```'''
        steps = TaskPlanner._parse_steps(text)
        assert len(steps) == 2
        assert steps[0].executor == "engine"
        assert steps[1].depends_on == [0]

    def test_parse_steps_empty(self) -> None:
        steps = TaskPlanner._parse_steps("no json here")
        assert steps == []

    def test_parse_steps_raw_json(self) -> None:
        text = '[{"description": "test", "executor": "brain", "prompt": "do it"}]'
        steps = TaskPlanner._parse_steps(text)
        assert len(steps) == 1
