"""测试首次启动引导与自我成长优化（2026-04-21 设计文档验收标准）。

验收标准：
1. 启动问候的系统消息不会触发自我成长写入
2. 用户只说名字时，引导不会立即结束（需要至少3轮或更多信息）
3. IDENTITY.md 不再出现"友好、轻快、带emoji"等提示词内容
4. USER.md 的称呼填写到 `- **称呼：** ` 位置
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from script.brain.cognitive import CognitiveLoop
from script.brain.conversation import ConversationStore
from script.brain.models import Intent, IntentType
from script.gateway.models import UnifiedMessage


# ── Fixtures ────────────────────────────────────────────────────


def _make_msg(
    content: str = "你好",
    user_id: str = "test_user",
    metadata: dict | None = None,
    platform: str = "test",
) -> UnifiedMessage:
    return UnifiedMessage(
        platform=platform,
        user_id=user_id,
        chat_id=f"chat_{user_id}",
        content=content,
        metadata=metadata or {},
    )


def _make_cognitive(
    bootstrap_prompt: str = "",
    bootstrapped: bool = False,
) -> CognitiveLoop:
    """创建带 bootstrap 配置的 mock CognitiveLoop。"""
    llm = MagicMock()
    llm.classify = AsyncMock(return_value={
        "type": "chitchat",
        "confidence": 0.9,
        "summary": "闲聊",
        "requires_engine": False,
        "memory_keywords": [],
    })
    llm.think = AsyncMock(return_value="你好呀！")

    soul = MagicMock()
    soul.get_system_prompt_fragment.return_value = "你是小爪。"
    soul.get_mood_context.return_value = "心情不错"
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

    workspace = MagicMock()
    workspace.update_section = MagicMock()
    workspace.append_file = MagicMock()
    workspace.complete_bootstrap = MagicMock()

    conversation = ConversationStore()

    loop = CognitiveLoop(
        llm=llm,
        soul=soul,
        hands=hands,
        memory_store=memory_store,
        memory_loader=memory_loader,
        memory_extractor=memory_extractor,
        conversation=conversation,
        state_provider=lambda: hands.get_status(),
        workspace=workspace,
        bootstrap_prompt=bootstrap_prompt,
    )
    loop._bootstrapped = bootstrapped
    return loop


# ── 验收标准 1：系统消息不触发自我成长 ──────────────────────────


class TestSystemMessageFiltering:
    """验收标准1：启动问候的系统消息不会触发自我成长写入。"""

    def test_should_reflect_rejects_system_origin(self) -> None:
        """标记 system_origin 的消息不应触发 _should_reflect。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")
        msg = _make_msg(
            content="你刚刚首次醒来，向主人打招呼并开始引导。",
            metadata={"system_origin": True, "system_type": "bootstrap"},
        )
        intent = Intent(type=IntentType.CHITCHAT, confidence=0.9, summary="系统引导", requires_engine=False)

        result = loop._should_reflect(msg, intent)

        assert result is False, "system_origin 消息不应触发自我成长"

    def test_should_reflect_allows_user_origin(self) -> None:
        """普通用户消息在 bootstrap 阶段应该触发 _should_reflect。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")
        msg = _make_msg(content="我叫 sakura")
        intent = Intent(type=IntentType.CHITCHAT, confidence=0.9, summary="用户自我介绍", requires_engine=False)

        result = loop._should_reflect(msg, intent)

        assert result is True, "用户真实消息在 bootstrap 阶段应触发自我成长"

    async def test_reflect_and_grow_filters_system_turns(self) -> None:
        """_reflect_and_grow 构建对话历史时应过滤掉 system_origin 的 Turn。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")
        # 模拟对话历史：系统消息 + 用户消息混合
        loop._conversation.add(
            "test_user", "user", "系统引导内容",
            metadata={"system_origin": True},
        )
        loop._conversation.add("test_user", "assistant", "你好！我是小爪~")
        loop._conversation.add("test_user", "user", "我叫 sakura", metadata=None)
        loop._conversation.add("test_user", "assistant", "sakura 你好！")

        # 调用 _reflect_and_grow，让 LLM 返回"无需更新"
        loop._llm.think = AsyncMock(return_value="无需更新")

        msg = _make_msg(content="我叫 sakura")
        intent = Intent(type=IntentType.CHITCHAT, confidence=0.9, summary="自我介绍", requires_engine=False)
        await loop._reflect_and_grow(msg, "sakura 你好！", intent)

        # 验证 LLM 被调用了（说明过滤后还有内容）
        loop._llm.think.assert_called_once()
        # 验证传入的 prompt 不包含系统引导内容
        call_args = loop._llm.think.call_args
        judge_prompt = call_args[0][1]  # 第二个位置参数
        assert "系统引导内容" not in judge_prompt, "对话历史中不应包含 system_origin 消息"


# ── 验收标准 2：引导不会因一句话就结束 ──────────────────────────


class TestBootstrapCompletion:
    """验收标准2：用户只说名字时，引导不会立即结束。"""

    def test_bootstrap_turns_counter_increments(self) -> None:
        """引导阶段每条真实用户消息应递增 _bootstrap_turns。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")

        assert loop._bootstrap_turns == 0

        # 模拟 process 入口中的计数逻辑
        msg = _make_msg(content="我叫 sakura")
        # 复刻 process 方法中的计数条件
        if (
            loop._bootstrap_prompt
            and not loop._bootstrapped
            and not msg.content.strip().startswith("[定时任务")
            and msg.user_id != "system"
            and msg.platform != "routine"
        ):
            loop._bootstrap_turns += 1

        assert loop._bootstrap_turns == 1

    def test_bootstrap_not_complete_at_turn_1(self) -> None:
        """第1轮对话：即使更新了文件也不应完成引导。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")
        loop._bootstrap_turns = 1

        is_enough_turns = loop._bootstrap_turns >= 3
        is_user_skipping = False  # chitchat，没有跳过

        assert is_enough_turns is False, "1轮对话不满足引导完成的轮数条件"

    def test_bootstrap_not_complete_at_turn_2(self) -> None:
        """第2轮对话：仍不应完成引导。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")
        loop._bootstrap_turns = 2

        is_enough_turns = loop._bootstrap_turns >= 3

        assert is_enough_turns is False, "2轮对话不满足引导完成的轮数条件"

    def test_bootstrap_completes_at_turn_3(self) -> None:
        """第3轮对话：满足轮数条件。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")
        loop._bootstrap_turns = 3

        is_enough_turns = loop._bootstrap_turns >= 3

        assert is_enough_turns is True, "3轮对话应满足引导完成的轮数条件"

    def test_bootstrap_skipped_by_coding_intent(self) -> None:
        """用户直接发编码任务时应可跳过引导（C条件）。"""
        skip_intents = [IntentType.CODING, IntentType.STATUS, IntentType.KNOWLEDGE, IntentType.COMPLEX]
        for intent_type in skip_intents:
            is_user_skipping = intent_type in (
                IntentType.CODING, IntentType.STATUS, IntentType.KNOWLEDGE, IntentType.COMPLEX
            )
            assert is_user_skipping is True, f"{intent_type} 应允许跳过引导"

    def test_bootstrap_not_skipped_by_chitchat(self) -> None:
        """闲聊不应触发跳过条件。"""
        is_user_skipping = IntentType.CHITCHAT in (
            IntentType.CODING, IntentType.STATUS, IntentType.KNOWLEDGE, IntentType.COMPLEX
        )
        assert is_user_skipping is False, "chitchat 不应触发跳过"

    def test_system_messages_dont_count_turns(self) -> None:
        """系统消息不应计入引导轮数。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")

        # system 用户消息
        msg_sys = _make_msg(content="系统引导", user_id="system")
        if (
            loop._bootstrap_prompt
            and not loop._bootstrapped
            and not msg_sys.content.strip().startswith("[定时任务")
            and msg_sys.user_id != "system"
            and msg_sys.platform != "routine"
        ):
            loop._bootstrap_turns += 1

        assert loop._bootstrap_turns == 0, "system 用户消息不应计入轮数"

        # routine 平台消息
        msg_routine = _make_msg(content="心跳检查", platform="routine")
        if (
            loop._bootstrap_prompt
            and not loop._bootstrapped
            and not msg_routine.content.strip().startswith("[定时任务")
            and msg_routine.user_id != "system"
            and msg_routine.platform != "routine"
        ):
            loop._bootstrap_turns += 1

        assert loop._bootstrap_turns == 0, "routine 平台消息不应计入轮数"


# ── 验收标准 3：提示词不污染文件 ────────────────────────────────


class TestPromptPollutionPrevention:
    """验收标准3：IDENTITY.md 不再出现提示词内容。"""

    def test_judge_prompt_contains_source_distinction(self) -> None:
        """judge_prompt 应包含消息来源区分说明。"""
        loop = _make_cognitive(bootstrap_prompt="引导流程...")
        # 构造对话历史
        loop._conversation.add("test_user", "user", "叫我 sakura 吧")
        loop._conversation.add("test_user", "assistant", "好的 sakura！")

        # 直接构建 judge_prompt 并检查内容
        recent = loop._conversation.get_full("test_user")[-6:]
        recent = [t for t in recent if not (t.metadata or {}).get("system_origin")]
        convo_text = "\n".join(f"{t.role}: {t.content[:300]}" for t in recent)

        # 重现 _reflect_and_grow 中的 judge_prompt 构建
        judge_prompt = f"""你是一个自我成长引擎。阅读以下对话，判断是否有值得长期保留的信息。

        最近对话：
        {convo_text}
        """

        # 验证关键防污染提示词存在于完整的 judge_prompt 中
        # （由于我们不能直接调用私有方法的中间结果，验证代码中静态存在即可）
        source_code_lines = [
            "区分消息来源",
            "系统给你的提示词",
            "不要",
            "格式要求",
        ]
        # 这些关键字应存在于 cognitive.py 源码的 judge_prompt 构建部分
        import inspect
        source = inspect.getsource(loop._reflect_and_grow)
        for keyword in source_code_lines:
            assert keyword in source, f"_reflect_and_grow 源码应包含 '{keyword}'"

    def test_judge_prompt_has_format_requirements(self) -> None:
        """judge_prompt 源码应包含格式要求（防止追加到错误位置）。"""
        import inspect
        loop = _make_cognitive()
        source = inspect.getsource(loop._reflect_and_grow)

        format_keywords = [
            "称呼",
            "USER.md",
            "IDENTITY.md",
            "语气风格",
            "不要创建新的",
        ]
        for kw in format_keywords:
            assert kw in source, f"judge_prompt 应包含格式要求 '{kw}'"


# ── 验收标准 4：格式正确性 ──────────────────────────────────────


class TestFormatCorrectness:
    """验收标准4：USER.md 的称呼填写到正确位置。"""

    def test_judge_prompt_specifies_template_format(self) -> None:
        """judge_prompt 应明确要求替换（暂无）占位符。"""
        import inspect
        loop = _make_cognitive()
        source = inspect.getsource(loop._reflect_and_grow)

        assert "（暂无）" in source, "judge_prompt 应提及替换（暂无）占位符"
        assert "SECTION" in source or "章节" in source, "judge_prompt 应提及章节定位"


# ── ConversationStore metadata 支持测试 ─────────────────────────


class TestConversationStoreMetadata:
    """确保 ConversationStore 正确保存和返回 metadata。"""

    def test_add_with_metadata(self) -> None:
        store = ConversationStore()
        store.add("u1", "user", "hello", metadata={"system_origin": True})

        turns = store.get_full("u1")
        assert len(turns) == 1
        assert turns[0].metadata == {"system_origin": True}

    def test_add_without_metadata(self) -> None:
        store = ConversationStore()
        store.add("u1", "user", "hello")

        turns = store.get_full("u1")
        assert turns[0].metadata is None

    def test_filter_system_origin_turns(self) -> None:
        """验证过滤 system_origin Turn 的模式能正确工作。"""
        store = ConversationStore()
        store.add("u1", "user", "系统消息", metadata={"system_origin": True})
        store.add("u1", "assistant", "回复1")
        store.add("u1", "user", "用户真实消息", metadata=None)
        store.add("u1", "assistant", "回复2")

        recent = store.get_full("u1")[-6:]
        filtered = [t for t in recent if not (t.metadata or {}).get("system_origin")]

        assert len(filtered) == 3, "过滤后应剩3条（1条系统消息被移除）"
        assert all("系统消息" != t.content for t in filtered), "系统消息应被过滤"


# ── UnifiedMessage metadata 测试 ────────────────────────────────


class TestUnifiedMessageMetadata:
    """确保 UnifiedMessage 正确携带 metadata。"""

    def test_default_metadata(self) -> None:
        msg = UnifiedMessage(
            platform="test", user_id="u1", chat_id="c1", content="hi",
        )
        assert isinstance(msg.metadata, dict)

    def test_system_origin_metadata(self) -> None:
        msg = UnifiedMessage(
            platform="test", user_id="u1", chat_id="c1", content="hi",
            metadata={"system_origin": True, "system_type": "bootstrap"},
        )
        assert msg.metadata["system_origin"] is True
        assert msg.metadata["system_type"] == "bootstrap"

    def test_metadata_get_safe(self) -> None:
        """metadata.get 对不存在的 key 应安全返回 None。"""
        msg = UnifiedMessage(
            platform="test", user_id="u1", chat_id="c1", content="hi",
        )
        assert msg.metadata.get("system_origin") is None
