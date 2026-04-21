"""CognitiveLoop — Brain 的认知循环，mini_claw 的核心创新。

七步认知流程：
1. 构建思考上下文
2. 意图分类（LLM）
3. 主动记忆检索
4. 决策
5. 执行
6. 人格化响应包装（LLM）
7. 后处理（更新记忆、情绪）
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..gateway.models import BotResponse, UnifiedMessage
from ..hands.manager import HandsManager
from ..hands.models import ExecutionResult
from ..memory.loader import MemoryLoader
from ..memory.extractor import MemoryExtractor
from ..memory.store import MemoryStore
from ..recovery.self_healer import SelfHealer
from ..soul.manager import SoulManager
from .composer import ResponseComposer
from .conversation import ConversationStore
from .llm_client import BrainLLMClient
from .models import BrainDecision, Intent, IntentType, PlanStep, ThinkingContext
from .planner import TaskPlanner
from .user_state import UserProcessingState

logger = logging.getLogger(__name__)

# ── 意图分类 Prompt ──────────────────────────────────────────

CLASSIFY_SYSTEM_PROMPT = """你是一个意图分类器。分析用户消息，返回 JSON 格式的意图分类。

{soul_context}

当前状态：{mood_context}

## 意图类型
- chitchat: 闲聊、问候、感谢、告别（仅限不需要外部信息就能回答的纯社交对话）
- status: 查询任务状态、系统状态、工作进度
- coding: 编码任务（写代码、改 Bug、Review、重构、测试）
- file_ops: 文件操作（读/写/查找/编辑文件，但不涉及编码逻辑）
- knowledge: 知识问答（技术概念、最佳实践、工具使用、以及任何需要搜索/查询外部信息才能准确回答的问题）
- command: 系统命令（/reset、/status 等以 / 开头的命令）
- complex: 需要多步骤的复杂任务（同时涉及多个子任务）
- memory: 记忆操作（"记住xxx"、"你还记得xxx吗"、"忘记xxx"）

## 判断规则
1. 如果消息以 / 开头，类型为 command
2. 如果涉及代码修改/编写/调试，类型为 coding
3. 如果明确要求多步操作或涉及多个不同领域，类型为 complex
4. 如果只是简单读文件/找文件，类型为 file_ops
5. requires_engine: coding、file_ops、complex 中需要代码执行的步骤为 true
6. memory_keywords: 提取 2-5 个与消息核心内容相关的关键词（用于记忆检索）
7. emotional_tone: 如果用户消息中包含情绪表达（开心、难过、焦虑、抱怨等），用一个词概括情绪基调

## 关键：chitchat 与 knowledge 的区分
- "你好""谢谢""再见" → chitchat（纯社交，不需要外部信息）
- "推荐旅游地点""最近有什么新闻""天气怎么样""有什么好吃的" → knowledge + requires_engine=true（需要搜索实时信息）
- "xxx怎么用""什么是xxx" → knowledge（可能需要引擎，视问题复杂度判断 requires_engine）
- 判断标准：如果你不确定答案是否准确、是否过时、是否需要联网查询，就归为 knowledge 且 requires_engine=true

## 关键：询问系统功能/文件内容 ≠ 闲聊
- 用户问"心跳任务有哪些""定时任务列表""你的配置是什么" → status（查询系统状态）
- 用户问"xxx文件里写了什么""把文件内容发出来看看" → file_ops + requires_engine=true
- 用户问"你读到的内容对不对""文件有没有写进去" → file_ops + requires_engine=true
- 这些都不是 chitchat！凡是涉及查看/验证文件内容或系统状态的，绝不能归为 chitchat

## 关键：何时归为 complex
- 一句话中包含多个**独立**请求（"帮我搜一下A，同时看看B的情况"）→ complex
- 同一句话里表达了**情绪/态度 + 任务请求**，且情绪需要被回应（"不想聊xx了，帮我做yy"）→ complex
- 只有一个明确任务，不管多难，都不是 complex（用 coding/knowledge/file_ops）

## 输出格式（严格 JSON）
{{"type": "...", "confidence": 0.9, "summary": "一句话概括", "requires_engine": true, "memory_keywords": ["kw1", "kw2"], "emotional_tone": ""}}"""

CLASSIFY_USER_PROMPT = """## 最近对话
{recent_conversation}

## 当前消息
{message}

请分类。"""


class CognitiveLoop:
    """Brain 的认知循环 — 理解 → 记忆 → 决策 → 表达。

    这是 mini_claw 作为独立数字分身的核心：接收用户消息后，
    经过独立思考和判断，决定如何回应——直接对话、委派 mini_claude、
    还是分步规划。
    """

    _OWNER_KEY = "__owner__"

    def __init__(
        self,
        llm: BrainLLMClient,
        soul: SoulManager,
        hands: HandsManager,
        memory_store: MemoryStore,
        memory_loader: MemoryLoader,
        memory_extractor: MemoryExtractor,
        conversation: Optional[ConversationStore] = None,
        state_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        workspace: Any = None,
        agents_rules: str = "",
        bootstrap_prompt: str = "",
        diary_context: str = "",
        personal_mode: bool = False,
        self_healer: Optional[SelfHealer] = None,
    ) -> None:
        self._llm = llm
        self._soul = soul
        self._hands = hands
        self._memory_store = memory_store
        self._memory_loader = memory_loader
        self._memory_extractor = memory_extractor
        self._conversation = conversation or ConversationStore()
        self._state_provider = state_provider
        self._workspace = workspace
        self._agents_rules = agents_rules
        self._bootstrap_prompt = bootstrap_prompt
        self._bootstrapped = False
        self._diary_context = diary_context
        self._personal_mode = personal_mode
        self._self_healer = self_healer
        self._composer = ResponseComposer(llm, soul)
        self._planner = TaskPlanner(llm)

        # 外部回调：长任务时先发确认消息
        self._on_ack: Optional[Callable[[str, str], Awaitable[None]]] = None
        # 外部回调：主动推送消息 (platform, chat_id, text)
        self._on_push: Optional[Callable[[str, str, str], Awaitable[None]]] = None

        # per-user 处理状态：消息队列 + 思考追踪 + /btw 取消
        self._user_states: Dict[str, UserProcessingState] = {}

    # ── 消息队列 & /btw 打断 ──────────────────────────────────

    def _map_uid(self, user_id: str) -> str:
        """personal 模式下所有用户映射到同一个处理状态。"""
        return self._OWNER_KEY if self._personal_mode else user_id

    def _get_user_state(self, user_id: str) -> UserProcessingState:
        """获取或创建用户的处理状态。"""
        uid = self._map_uid(user_id)
        if uid not in self._user_states:
            self._user_states[uid] = UserProcessingState()
        return self._user_states[uid]

    def get_thinking_state(self, user_id: str) -> Optional[UserProcessingState]:
        """获取用户当前思考状态（供 /thinking 端点查询）。"""
        return self._user_states.get(self._map_uid(user_id))

    async def process(self, msg: UnifiedMessage) -> BotResponse:
        """处理一条消息。支持排队、/btw 打断。Brain 的主入口。"""
        state = self._get_user_state(msg.user_id)

        # /btw 打断：取消当前处理并重新提交组合消息
        if msg.content.strip().startswith("/btw "):
            return await self._handle_btw(msg, state)

        # 当前正在处理 → 排队等待
        if state.is_processing:
            logger.info("用户 %s 消息排队 (队列长度: %d)", msg.user_id, state.queue.qsize() + 1)
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[BotResponse] = loop.create_future()
            await state.queue.put((msg, fut))
            return await fut

        # 空闲 → 直接处理
        return await self._process_under_lock(msg, state)

    async def _process_under_lock(
        self, msg: UnifiedMessage, state: UserProcessingState
    ) -> BotResponse:
        """在 per-user lock 下执行认知循环，完成后自动排空队列。"""
        async with state.lock:
            state.reset_for_new_message(msg.content)
            state.current_message = msg
            try:
                response = await self._process_single(msg, state)
                return response
            except asyncio.CancelledError:
                logger.info("用户 %s 处理被 /btw 打断", msg.user_id)
                return BotResponse(text="[interrupted]", reply_to=msg.message_id)
            except Exception as e:
                logger.error("认知循环异常: %s", e, exc_info=True)
                self._soul.on_error()
                if self._self_healer:
                    return await self._self_healer.heal(
                        e, msg, state, verify_fn=self._process_single,
                    )
                return BotResponse(
                    text=self._soul.get_error_message(str(e)),
                    reply_to=msg.message_id,
                )
            finally:
                state.is_processing = False
                state.current_message = None
                # 排空队列：处理下一条排队消息
                asyncio.create_task(self._drain_queue(msg.user_id, state))

    async def _drain_queue(self, user_id: str, state: UserProcessingState) -> None:
        """从队列逐条取出消息并处理，结果写入对应 Future。

        直接管理 state 和调用 _process_single，不经过 _process_under_lock，
        避免递归触发 _drain_queue 导致竞争。
        """
        while not state.queue.empty():
            try:
                queued_msg, fut = state.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            # 调用方已取消（如 /btw 打断），跳过
            if fut.done():
                continue
            async with state.lock:
                state.reset_for_new_message(queued_msg.content)
                state.current_message = queued_msg
                try:
                    response = await self._process_single(queued_msg, state)
                    if not fut.done():
                        fut.set_result(response)
                except asyncio.CancelledError:
                    if not fut.done():
                        fut.set_result(BotResponse(
                            text="[interrupted]",
                            reply_to=queued_msg.message_id,
                        ))
                except Exception as e:
                    logger.error("队列消息处理异常: %s", e, exc_info=True)
                    self._soul.on_error()
                    if self._self_healer:
                        heal_resp = await self._self_healer.heal(
                            e, queued_msg, state, verify_fn=self._process_single,
                        )
                    else:
                        heal_resp = BotResponse(
                            text=self._soul.get_error_message(str(e)),
                            reply_to=queued_msg.message_id,
                        )
                    if not fut.done():
                        fut.set_result(heal_resp)
                finally:
                    state.is_processing = False
                    state.current_message = None

    async def _handle_btw(
        self, msg: UnifiedMessage, state: UserProcessingState
    ) -> BotResponse:
        """/btw 打断：取消当前处理，组合上下文重新提交。"""
        btw_content = msg.content.strip()[len("/btw "):].strip()

        if not state.is_processing:
            # 没在处理中，当普通消息处理
            new_msg = UnifiedMessage(
                platform=msg.platform,
                user_id=msg.user_id,
                chat_id=msg.chat_id,
                content=btw_content,
                message_id=msg.message_id,
            )
            return await self._process_under_lock(new_msg, state)

        # 1. 快照当前思考进度
        thinking_snapshot = state.format_thinking_snapshot()
        original = state.original_message or ""
        partial = state.partial_result

        # 2. 触发取消（_process_single 会在下一个检查点抛出 CancelledError）
        state.cancel_event.set()

        # 3. 等待锁释放（被取消的任务会释放 lock）
        # 4. 组合消息重新提交
        partial_section = f"\n[中间结果]:\n{partial[:1000]}" if partial else ""

        combined_content = (
            f"[原始问题]: {original}\n\n"
            f"[思考进度]:\n{thinking_snapshot}\n"
            f"{partial_section}\n\n"
            f"[用户补充(/btw)]: {btw_content}\n\n"
            f"请结合用户的补充信息，重新思考并回答。"
        )

        combined_msg = UnifiedMessage(
            platform=msg.platform,
            user_id=msg.user_id,
            chat_id=msg.chat_id,
            content=combined_content,
            message_id=msg.message_id,
            metadata={"is_btw": True},
        )

        return await self._process_under_lock(combined_msg, state)

    # ── 认知循环核心（原 process 逻辑）─────────────────────────

    async def _process_single(
        self, msg: UnifiedMessage, state: UserProcessingState
    ) -> BotResponse:
        """7 步认知循环的实际执行。"""
        start_time = time.time()

        # 情绪 tick：空闲恢复 + 跨天重置
        self._soul.soul.mood.tick()

        # 1. 构建思考上下文
        state.update_thinking(1, "build_context", "running", "构建上下文...")
        ctx = self._build_context(msg)
        state.update_thinking(1, "build_context", "done", "上下文就绪")
        state.check_cancelled()

        # 2. 意图分类
        state.update_thinking(2, "classify_intent", "running", "分析意图...")
        intent = await self._classify_intent(ctx)
        state.update_thinking(
            2, "classify_intent", "done",
            f"意图: {intent.type.value} ({intent.summary})",
        )
        logger.info(
            "意图分类: type=%s, confidence=%.2f, summary=%s",
            intent.type.value, intent.confidence, intent.summary,
        )
        state.check_cancelled()

        # 3. 主动记忆检索
        state.update_thinking(3, "recall_memory", "running", "检索记忆...")
        memories = self._recall_memories(intent, ctx)
        ctx.relevant_memories = memories
        state.update_thinking(
            3, "recall_memory", "done",
            f"找到 {len(memories)} 条相关记忆",
        )
        state.check_cancelled()

        # 4. 决策
        state.update_thinking(4, "decide", "running", "决策中...")
        decision = await self._decide(intent, ctx)
        state.update_thinking(
            4, "decide", "done", f"决策: {decision.action}",
        )
        state.check_cancelled()

        # 5. 执行
        state.update_thinking(5, "execute", "running", f"执行: {decision.action}...")
        raw_result = await self._execute_decision(decision, ctx, msg, state)
        state.partial_result = raw_result[:2000] if raw_result else ""
        state.update_thinking(5, "execute", "done", "执行完成")
        state.check_cancelled()

        # 6. 响应包装
        state.update_thinking(6, "compose", "running", "组织回复...")
        response = await self._compose_response(raw_result, intent, ctx)
        # 最终防线：清洗响应中的伪工具调用标签
        response = self._sanitize_response(response)
        state.update_thinking(6, "compose", "done", "回复就绪")

        # 7. 先同步记录对话历史（避免异步竞态导致下轮丢失上下文）
        self._conversation.add(msg.user_id, "user", msg.content, intent.type.value)
        self._conversation.add(msg.user_id, "assistant", response)

        # 后处理（异步，不阻塞响应：情绪更新、记忆提取等）
        state.update_thinking(7, "post_process", "running", "后处理中...")
        asyncio.create_task(self._post_process(msg, response, intent, decision))

        elapsed = time.time() - start_time
        logger.info("认知循环完成: %.1fms, intent=%s", elapsed * 1000, intent.type.value)

        return BotResponse(text=response, reply_to=msg.message_id)

    # ── Step 1: 构建上下文 ────────────────────────────────────

    # 工作区文件地图：让模型在所有对话中都了解自己的完整工作区
    _WORKSPACE_MAP = """
    ## 你的工作区
    
    以下是你工作区内的所有文件，你应该了解每个文件的用途，知道什么时候该查阅或更新哪个文件。
    
    | 文件 | 用途 | 何时查阅/更新 |
    |------|------|--------------|
    | SOUL.md | 你的内核：行为准则、价值观、边界 | 行事原则相关时；改动需告知用户 |
    | IDENTITY.md | 你的身份：名字、角色、风格、表达习惯、情绪基调 | 人设/风格相关时 |
    | USER.md | 用户画像：称呼、偏好、背景、工作习惯 | 了解用户时；用户提供新个人信息时更新 |
    | AGENTS.md | 工作规则：决策流程、记忆规则、心跳规则、自我改进 | 工作流/规则相关时 |
    | HEARTBEAT.md | 定时任务清单：你的自驱日程 | 用户问定时任务时；需要增删定时任务时 |
    | TOOLS.md | 工具配置：环境特有的技巧和配置 | 工具使用相关时 |
    | MEMORY.md | 长期记忆索引：沉淀过的认知 | 需要回忆重要事件/决策时 |
    | memory/YYYY-MM-DD.md | 每日日记：当天的工作记录 | 了解近期上下文时 |
    | memory/sessions/*.jsonl | 会话日志：完整对话记录 | 需要回溯具体对话时 |
    | memory/learnings/ | 经验教训：错误记录、最佳实践、功能请求 | 犯错/学到新东西时 |
    | BOOTSTRAP.md | 首次启动引导（完成后删除） | 仅首次启动时 |
    
    ## 人称规范
    
    - 工作区中的文件是**你自己的**，不是用户的。用户问"你的记忆/文件在哪里"时，回复"**我的**xx在xx"，不要说"你的xx在xx"。
    - SOUL.md 是"我的内核"，IDENTITY.md 是"我的身份"，USER.md 是"关于你（用户）的信息"。
    - 简言之：对自己的东西用"我的"，对用户的东西用"你的"。"""

    def _build_context(self, msg: UnifiedMessage) -> ThinkingContext:
        """组装 Brain 思考所需的全部上下文。"""
        soul_fragment = self._soul.get_system_prompt_fragment()

        # 注入工作区文件地图和人称规范
        soul_fragment += self._WORKSPACE_MAP

        # 首次启动：注入 BOOTSTRAP.md 引导指令
        if self._bootstrap_prompt and not self._bootstrapped:
            soul_fragment += "\n\n--- 首次启动引导 ---\n" + self._bootstrap_prompt

        # 动态加载最近日记（每次对话都获取最新）
        diary_context = ""
        if self._workspace:
            try:
                diary_context = self._workspace.list_recent_diaries(days=2)
            except Exception:
                diary_context = self._diary_context  # fallback 到启动时的缓存

        return ThinkingContext(
            user_message=msg.content,
            user_id=msg.user_id,
            chat_id=msg.chat_id,
            platform=msg.platform,
            soul_fragment=soul_fragment,
            mood_context=self._soul.get_mood_context(),
            recent_conversation=self._conversation.get_recent(msg.user_id),
            system_state=self._state_provider() if self._state_provider else {},
            agents_rules=self._agents_rules,
            diary_context=diary_context,
        )

    # ── Step 2: 意图分类 ──────────────────────────────────────

    async def _classify_intent(self, ctx: ThinkingContext) -> Intent:
        """使用 Brain LLM 进行意图分类。"""
        # 快速路径：以 / 开头的命令直接识别
        if ctx.user_message.strip().startswith("/"):
            return Intent(
                type=IntentType.COMMAND,
                confidence=1.0,
                summary=f"系统命令: {ctx.user_message.strip().split()[0]}",
                requires_engine=False,
            )

        rules_section = ""
        if ctx.agents_rules:
            rules_section = f"\n\n## 工作规则\n{ctx.agents_rules[:500]}"

        system_prompt = CLASSIFY_SYSTEM_PROMPT.format(
            soul_context=ctx.soul_fragment,
            mood_context=ctx.mood_context,
        ) + rules_section

        # 格式化最近对话
        recent = ""
        if ctx.recent_conversation:
            lines = []
            for turn in ctx.recent_conversation[-4:]:  # 最近 4 轮
                lines.append(f"{turn['role']}: {turn['content'][:200]}")
            recent = "\n".join(lines)

        user_prompt = CLASSIFY_USER_PROMPT.format(
            recent_conversation=recent or "（无历史对话）",
            message=ctx.user_message,
        )

        try:
            result = await self._llm.classify(system_prompt, user_prompt)
            return Intent(
                type=IntentType(result.get("type", "chitchat")),
                confidence=float(result.get("confidence", 0.5)),
                summary=result.get("summary", ctx.user_message[:50]),
                requires_engine=bool(result.get("requires_engine", False)),
                memory_keywords=result.get("memory_keywords", []),
                emotional_tone=result.get("emotional_tone", ""),
            )
        except Exception as e:
            logger.warning("意图分类失败，降级为 chitchat: %s", e)
            return Intent(
                type=IntentType.CHITCHAT,
                confidence=0.3,
                summary=ctx.user_message[:50],
                requires_engine=False,
            )

    # ── Step 3: 主动记忆检索 ──────────────────────────────────

    def _recall_memories(self, intent: Intent, ctx: ThinkingContext) -> List[Any]:
        """根据意图关键词主动检索记忆。"""
        keywords = intent.memory_keywords
        if not keywords:
            # 从消息本身提取简单关键词
            keywords = [w for w in ctx.user_message.split() if len(w) > 1][:5]

        if not keywords:
            return []

        return self._memory_loader.active_recall(
            keywords=keywords,
            intent_type=intent.type.value,
            user_id=ctx.user_id,
        )

    # ── Step 4: 决策 ──────────────────────────────────────────

    async def _decide(self, intent: Intent, ctx: ThinkingContext) -> BrainDecision:
        """根据意图类型选择行动路径。"""
        match intent.type:
            case IntentType.CHITCHAT:
                return await self._decide_chitchat(intent, ctx)
            case IntentType.STATUS:
                return await self._decide_status(ctx)
            case IntentType.CODING | IntentType.FILE_OPS:
                return self._decide_delegate(intent, ctx)
            case IntentType.KNOWLEDGE:
                return await self._decide_knowledge(intent, ctx)
            case IntentType.COMMAND:
                return await self._decide_command(ctx)
            case IntentType.COMPLEX:
                return await self._decide_complex(intent, ctx)
            case IntentType.MEMORY:
                return self._decide_memory(ctx)
            case _:
                return self._decide_delegate(intent, ctx)

    async def _decide_chitchat(self, intent: Intent, ctx: ThinkingContext) -> BrainDecision:
        """闲聊 → Brain LLM 直接回复。

        低置信度时防御性委派：避免对文件/系统相关问题编造答案。
        """
        # 低置信度 chitchat 可能是误分类，涉及文件/系统内容时委派引擎
        if intent.confidence < 0.7:
            msg_lower = ctx.user_message.lower()
            risky_keywords = [
                "文件", "内容", "读取", "配置", "任务", "心跳", "heartbeat",
                "定时", "日程", "有哪些", "列表", "说说", "展示", "显示",
            ]
            if any(kw in msg_lower for kw in risky_keywords):
                logger.info("低置信度 chitchat (%.2f) 涉及文件/系统查询，转委派", intent.confidence)
                return self._decide_delegate(intent, ctx)

        memory_hint = ""
        if ctx.relevant_memories:
            lines = [f"- {m.name}: {m.content[:100]}" for m in ctx.relevant_memories[:3]]
            memory_hint = "\n\n相关记忆（可参考但不必都提及）:\n" + "\n".join(lines)

        recent = ""
        if ctx.recent_conversation:
            lines = [f"{t['role']}: {t['content'][:150]}" for t in ctx.recent_conversation[-4:]]
            recent = "\n\n最近对话:\n" + "\n".join(lines)

        diary_hint = ""
        if ctx.diary_context:
            diary_hint = f"\n\n近期日记（你的工作记录，可参考）:\n{ctx.diary_context[:500]}"

        user_prompt = f"""用户说: {ctx.user_message}{recent}{memory_hint}{diary_hint}

请自然地回复。注意：你只能输出纯文本，不能调用工具、生成 XML 标签或 tool_code。如果用户的请求需要执行操作（如搜索、编码），请告诉用户你会帮他处理，但不要模拟工具调用。"""

        text = await self._llm.think(ctx.soul_fragment, user_prompt)

        # 检测幻觉：LLM 在闲聊中模拟工具调用 → 转为委派引擎执行
        if self._looks_like_hallucinated_action(text):
            logger.info("闲聊回复检测到幻觉工具调用，转委派引擎")
            return self._decide_delegate(intent, ctx)

        return BrainDecision(action="reply", response_text=text)

    async def _decide_status(self, ctx: ThinkingContext) -> BrainDecision:
        """状态查询 → LLM 根据完整状态信息回答用户问题。"""
        state = ctx.system_state

        # 组装完整的状态信息
        status_parts = [
            f"情绪状态：{self._soul.get_mood_context()}",
        ]

        if "active_executors" in state:
            status_parts.append(f"活跃引擎数: {state['active_executors']}")
        if "total_turns" in state:
            status_parts.append(f"对话轮次: {state['total_turns']}")

        # 定时任务列表
        routine_jobs = state.get("routine_jobs", [])
        if routine_jobs:
            job_lines = []
            for j in routine_jobs:
                if j.get("type") == "heartbeat":
                    last_str = j.get("last_executed", "从未执行")
                    job_lines.append(
                        f"  - {j['name']} [心跳]: {j.get('description', '')}, "
                        f"条件={j.get('condition', '无')}, 上次={last_str}"
                    )
                else:
                    last = j.get("last_run")
                    last_str = (
                        datetime.fromtimestamp(last).strftime("%H:%M:%S") if last else "未执行"
                    )
                    job_lines.append(
                        f"  - {j['name']} [系统]: {j.get('description', '')}, "
                        f"频率={j.get('frequency', '?')}, 启用={j.get('enabled', True)}, 上次={last_str}"
                    )
            status_parts.append("定时任务:\n" + "\n".join(job_lines))
        else:
            status_parts.append("定时任务: 无")

        status_text = "\n".join(status_parts)

        recent = ""
        if ctx.recent_conversation:
            lines = [f"{t['role']}: {t['content'][:150]}" for t in ctx.recent_conversation[-4:]]
            recent = "\n\n最近对话:\n" + "\n".join(lines)

        user_prompt = f"""用户说: {ctx.user_message}{recent}

## 当前系统完整状态
{status_text}

请根据用户的具体问题，从上述状态信息中选取相关内容回答。不要把所有信息都堆上去，只回答用户关心的部分。
注意：你只能输出纯文本回复，不能调用工具或生成 XML 标签。"""

        text = await self._llm.think(ctx.soul_fragment, user_prompt)
        return BrainDecision(action="reply", response_text=text)

    def _decide_delegate(self, intent: Intent, ctx: ThinkingContext) -> BrainDecision:
        """编码/文件任务 → 委派给 mini_claude 引擎。"""
        # 构造完整的引擎 prompt
        parts = [ctx.user_message]

        # 注入对话历史（让引擎理解指代："那个文件""刚才的内容"等）
        if ctx.recent_conversation:
            lines = [f"{t['role']}: {t['content'][:150]}" for t in ctx.recent_conversation[-4:]]
            parts.append("对话上下文（帮助理解指代）:\n" + "\n".join(lines))

        # 注入相关记忆上下文
        if ctx.relevant_memories:
            memory_lines = [f"- {m.name}: {m.content[:200]}" for m in ctx.relevant_memories[:3]]
            parts.append("相关背景信息:\n" + "\n".join(memory_lines))

        # 防幻觉指令
        parts.append(
            "重要：如果无法确认某个 URL 链接的真实性，不要编造链接。"
            "可以告诉用户搜索关键词或平台名称，让用户自行查找。"
        )

        engine_prompt = "\n\n".join(parts)
        return BrainDecision(action="delegate", engine_prompt=engine_prompt)

    async def _decide_knowledge(self, intent: Intent, ctx: ThinkingContext) -> BrainDecision:
        """知识问答 → 先尝试 Brain 直接回答，不确定则委派引擎。"""
        # 对话历史（让模型理解指代和上下文）
        recent_hint = ""
        if ctx.recent_conversation:
            lines = [f"{t['role']}: {t['content'][:150]}" for t in ctx.recent_conversation[-4:]]
            recent_hint = "\n\n最近对话:\n" + "\n".join(lines)

        memory_hint = ""
        if ctx.relevant_memories:
            lines = [f"- {m.name}: {m.content[:200]}" for m in ctx.relevant_memories[:3]]
            memory_hint = "\n\n参考记忆:\n" + "\n".join(lines)

        diary_hint = ""
        if ctx.diary_context:
            diary_hint = f"\n\n近期日记:\n{ctx.diary_context[:500]}"

        user_prompt = f"""用户问题: {ctx.user_message}{recent_hint}{memory_hint}{diary_hint}

## 重要规则
- 你是 Brain 决策层，**没有**直接执行命令、读文件、搜索互联网的能力。
- 如果问题需要查看代码、读取文件、执行命令、搜索网络、或查看系统状态，你**必须**回复 "NEED_ENGINE"，由底层引擎代为执行。
- 只有当你凭自身知识就能确信回答时，才直接回答（纯文本，不要生成 XML 标签或模拟命令执行）。
- 不要编造命令输出或假装你执行了某个操作。
- 注意结合最近对话理解用户的指代（如"那个文件""刚才的内容"等）。"""

        text = await self._llm.think(ctx.soul_fragment, user_prompt)

        # 检测是否需要引擎，或 LLM 虽没说 NEED_ENGINE 但在模拟执行
        if "NEED_ENGINE" in text or self._looks_like_hallucinated_action(text):
            return self._decide_delegate(intent, ctx)
        return BrainDecision(action="reply", response_text=text)

    @staticmethod
    def _looks_like_hallucinated_action(text: str) -> bool:
        """检测 LLM 是否在模拟执行命令/工具调用而非真正回答。"""
        import re
        indicators = [
            r"<tool_code>",         # 模拟工具调用
            r"\[TOOL_CALL\]",       # 模拟工具调用标签
            r"<tool\s+name=",       # 模拟 XML 工具调用
            r"<param\s+name=",      # 模拟参数标签
            r"```\s*(bash|shell|sh)\s*\n",  # 假装运行 shell
            r"我执行了\s*`",         # "我执行了 `ls`"
            r"让我.*?(执行|运行|查看|搜索)",  # "让我执行/搜索..."
            r"我来.*?(执行|运行|查看|搜索|检索)",  # "我来帮你搜索"
        ]
        for pattern in indicators:
            if re.search(pattern, text):
                return True
        return False

    @staticmethod
    def _sanitize_response(text: str) -> str:
        """清洗响应文本，移除幻觉产生的伪工具调用和可疑 URL。最终防线。"""
        import re
        # 移除 [TOOL_CALL]...[/TOOL_CALL] 块
        text = re.sub(r"\[TOOL_CALL\][\s\S]*?\[/TOOL_CALL\]", "", text)
        # 移除 <tool ...>...</tool> XML 标签块
        text = re.sub(r"<tool\s+[^>]*>[\s\S]*?</tool>", "", text)
        # 移除单独的 <tool_code>...</tool_code>
        text = re.sub(r"<tool_code>[\s\S]*?</tool_code>", "", text)
        # 移除孤立的 <param ...>...</param>
        text = re.sub(r"<param\s+[^>]*>[\s\S]*?</param>", "", text)

        # URL 幻觉检测：标记可疑链接
        def _flag_suspicious_url(match: re.Match) -> str:
            url = match.group(0)
            # 可疑特征：随机哈希路径、.html 结尾的 API 风格路径、域名与描述不符
            suspicious = (
                re.search(r"/[a-f0-9]{20,}\.html", url)  # 哈希+.html
                or re.search(r"job_detail/[a-zA-Z0-9_-]{15,}", url)  # 伪造 job 链接
                or re.search(r"/[a-f0-9]{32}", url)  # 长哈希路径
            )
            if suspicious:
                return f"{url}（⚠️ 此链接可能由AI生成，请自行验证）"
            return url

        text = re.sub(r"https?://[^\s\)）\]]+", _flag_suspicious_url, text)

        # 清理多余空行（连续 3+ 个换行 → 2 个）
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    async def _decide_command(self, ctx: ThinkingContext) -> BrainDecision:
        """系统命令 → 直接处理。"""
        parts = ctx.user_message.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        match cmd:
            case "/reset":
                return BrainDecision(
                    action="reply",
                    response_text="会话已重置。",
                    memory_ops=[{"op": "reset_session", "user_id": ctx.user_id}],
                )
            case "/status":
                return await self._decide_status(ctx)
            case "/help":
                help_text = (
                    f"我是{self._soul.name}，你的数字分身。\n\n"
                    "可用命令:\n"
                    "/reset - 重置会话\n"
                    "/status - 查看状态\n"
                    "/help - 显示帮助\n"
                    "/mood - 查看/调整情绪\n"
                    "/diary [内容] - 写日记/查看今日日记\n"
                    "/memo <内容> - 记住一件事\n"
                    "/recall [关键词] - 回忆相关记忆\n"
                    "/forget <名称> - 忘记一条记忆\n"
                    "/todo [内容] - 查看/添加待办\n"
                    "/summary - 总结最近对话\n"
                    "/heartbeat - 查看定时任务状态\n\n"
                    "直接发消息给我就行，编码任务、问答、闲聊都可以。"
                )
                return BrainDecision(action="reply", response_text=help_text)
            case "/mood":
                return await self._skill_mood(arg, ctx)
            case "/diary":
                return await self._skill_diary(arg, ctx)
            case "/memo":
                return await self._skill_memo(arg, ctx)
            case "/recall":
                return await self._skill_recall(arg, ctx)
            case "/forget":
                return await self._skill_forget(arg, ctx)
            case "/todo":
                return await self._skill_todo(arg, ctx)
            case "/summary":
                return await self._skill_summary(ctx)
            case "/heartbeat":
                return await self._skill_heartbeat(ctx)
            case _:
                # 未知命令 → 尝试当作自然语言发送给引擎
                return BrainDecision(
                    action="reply",
                    response_text=f"未知命令: {cmd}。试试 /help 查看可用命令。",
                )

    # ── 内置技能实现 ─────────────────────────────────────────────

    async def _skill_mood(self, arg: str, ctx: ThinkingContext) -> BrainDecision:
        """查看或调整情绪状态。"""
        mood = self._soul.soul.mood
        if not arg:
            text = (
                f"当前状态：\n"
                f"- 情绪：{mood.mood}\n"
                f"- 精力：{mood.energy:.0%}\n"
                f"- 今日完成任务：{mood.tasks_completed_today}\n"
                f"- 连续错误：{mood.consecutive_errors}"
            )
            return BrainDecision(action="reply", response_text=text)
        # 设置情绪
        valid_moods = ["positive", "neutral", "tired", "frustrated"]
        if arg in valid_moods:
            mood.mood = arg
            return BrainDecision(action="reply", response_text=f"情绪已调整为：{arg}")
        return BrainDecision(
            action="reply",
            response_text=f"可选情绪：{', '.join(valid_moods)}",
        )

    async def _skill_diary(self, arg: str, ctx: ThinkingContext) -> BrainDecision:
        """写日记或查看今日日记。"""
        from datetime import date
        today = date.today().isoformat()
        diary_dir = self._memory_store.root
        diary_file = diary_dir / f"{today}.md"

        if not arg:
            # 查看今日日记
            if diary_file.exists():
                content = diary_file.read_text(encoding="utf-8")
                return BrainDecision(action="reply", response_text=f"**{today} 日记：**\n\n{content}")
            return BrainDecision(action="reply", response_text=f"今天（{today}）还没有日记。用 `/diary 内容` 来写一篇。")
        # 追加日记
        diary_dir.mkdir(parents=True, exist_ok=True)
        entry = f"\n- {datetime.now().strftime('%H:%M')} {arg}\n"
        with open(diary_file, "a", encoding="utf-8") as f:
            f.write(entry)
        return BrainDecision(action="reply", response_text=f"已记录到今日日记。")

    async def _skill_memo(self, arg: str, ctx: ThinkingContext) -> BrainDecision:
        """记住一件事。"""
        if not arg:
            return BrainDecision(action="reply", response_text="用法：`/memo 要记住的内容`")
        from ..memory.models import MemoryEntry, MemoryType
        import time as _time
        entry = MemoryEntry(
            name=arg[:30],
            description=arg,
            type=MemoryType.PROJECT,
            content=arg,
            created_at=_time.time(),
        )
        self._memory_store.save(entry)
        return BrainDecision(action="reply", response_text=f"已记住：{arg}")

    async def _skill_recall(self, arg: str, ctx: ThinkingContext) -> BrainDecision:
        """回忆相关记忆。"""
        entries = self._memory_store.list_all()
        if not entries:
            return BrainDecision(action="reply", response_text="记忆库为空。")
        if arg:
            # 简单关键词过滤
            kw = arg.lower()
            entries = [e for e in entries if kw in e.name.lower() or kw in (e.content or "").lower()]
            if not entries:
                return BrainDecision(action="reply", response_text=f"没有找到与「{arg}」相关的记忆。")
        lines = [f"- **{e.name}**：{e.description or e.content or ''}" for e in entries[:20]]
        return BrainDecision(
            action="reply",
            response_text=f"记忆（共 {len(entries)} 条）：\n" + "\n".join(lines),
        )

    async def _skill_forget(self, arg: str, ctx: ThinkingContext) -> BrainDecision:
        """删除一条记忆。"""
        if not arg:
            return BrainDecision(action="reply", response_text="用法：`/forget 记忆名称`")
        entry = self._memory_store.find_by_name(arg)
        if not entry:
            # 尝试模糊匹配
            all_entries = self._memory_store.list_all()
            matches = [e for e in all_entries if arg.lower() in e.name.lower()]
            if not matches:
                return BrainDecision(action="reply", response_text=f"未找到名为「{arg}」的记忆。")
            if len(matches) == 1:
                self._memory_store.delete(matches[0].filename())
                return BrainDecision(action="reply", response_text=f"已删除记忆：{matches[0].name}")
            names = ", ".join(e.name for e in matches[:10])
            return BrainDecision(action="reply", response_text=f"找到多条匹配，请指定具体名称：{names}")
        self._memory_store.delete(entry.filename())
        return BrainDecision(action="reply", response_text=f"已删除记忆：{entry.name}")

    async def _skill_todo(self, arg: str, ctx: ThinkingContext) -> BrainDecision:
        """查看或添加待办事项。"""
        todo_file = self._memory_store.root / "TODO.md"
        if not arg:
            if not todo_file.exists():
                return BrainDecision(action="reply", response_text="待办列表为空。用 `/todo 内容` 添加。")
            content = todo_file.read_text(encoding="utf-8")
            return BrainDecision(action="reply", response_text=f"**待办事项：**\n\n{content}")
        # 添加待办
        self._memory_store.root.mkdir(parents=True, exist_ok=True)
        entry = f"- [ ] {arg}\n"
        with open(todo_file, "a", encoding="utf-8") as f:
            f.write(entry)
        return BrainDecision(action="reply", response_text=f"已添加待办：{arg}")

    async def _skill_summary(self, ctx: ThinkingContext) -> BrainDecision:
        """总结最近对话。"""
        history = self._conversation.get_recent(ctx.user_id, n=20)
        if not history:
            return BrainDecision(action="reply", response_text="还没有对话记录可以总结。")
        # 用 LLM 生成总结
        msgs_text = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in history)
        summary = await self._llm.chat(
            system="请用 3-5 个要点总结以下对话内容，简洁清晰。",
            user=msgs_text[:3000],
        )
        return BrainDecision(action="reply", response_text=f"**对话总结：**\n\n{summary}")

    async def _skill_heartbeat(self, ctx: ThinkingContext) -> BrainDecision:
        """查看定时任务状态。"""
        import pathlib
        ws_root = pathlib.Path(self._workspace.root) if self._workspace and hasattr(self._workspace, "root") else None
        if not ws_root:
            return BrainDecision(action="reply", response_text="无法获取工作区路径。")
        hb_file = ws_root / "HEARTBEAT.md"
        if not hb_file.exists():
            return BrainDecision(action="reply", response_text="未找到 HEARTBEAT.md 配置文件。")
        content = hb_file.read_text(encoding="utf-8")
        return BrainDecision(action="reply", response_text=f"**定时任务配置：**\n\n{content}")

    async def _decide_complex(self, intent: Intent, ctx: ThinkingContext) -> BrainDecision:
        """复杂任务 → 分解为多步计划。"""
        steps = await self._planner.plan(ctx.user_message, ctx)
        if not steps:
            # 降级为单步委派
            return self._decide_delegate(intent, ctx)
        return BrainDecision(action="plan", plan=steps)

    def _decide_memory(self, ctx: ThinkingContext) -> BrainDecision:
        """记忆操作 → 操作记忆存储。"""
        msg = ctx.user_message.lower()
        if "记住" in msg or "remember" in msg:
            return BrainDecision(
                action="memory_op",
                memory_ops=[{"op": "save", "content": ctx.user_message, "user_id": ctx.user_id}],
                response_text="好的，我记住了。",
            )
        if "忘记" in msg or "forget" in msg:
            return BrainDecision(
                action="memory_op",
                memory_ops=[{"op": "forget", "content": ctx.user_message, "user_id": ctx.user_id}],
                response_text="好的，我会忘记这个。",
            )
        # 默认：列出记忆
        memories = self._memory_store.list_all()
        if memories:
            lines = [f"- {m.name}: {m.description}" for m in memories[:10]]
            text = "我记得这些:\n" + "\n".join(lines)
        else:
            text = "目前没有存储任何记忆。"
        return BrainDecision(action="reply", response_text=text)

    # ── Step 5: 执行 ──────────────────────────────────────────

    async def _execute_decision(
        self,
        decision: BrainDecision,
        ctx: ThinkingContext,
        msg: UnifiedMessage,
        state: Optional[UserProcessingState] = None,
    ) -> str:
        """执行 Brain 的决策。"""
        match decision.action:
            case "reply":
                return decision.response_text or ""

            case "delegate":
                # 长任务先发确认
                if self._on_ack:
                    ack_msg = self._soul.get_thinking_message()
                    await self._on_ack(msg.chat_id, ack_msg)

                cancel_event = state.cancel_event if state else None
                result: ExecutionResult = await self._hands.execute(
                    ctx.user_id,
                    decision.engine_prompt or ctx.user_message,
                    cancel_event=cancel_event,
                )

                if not result.success:
                    self._soul.on_error()
                    # 自动记录错误到 ERRORS.md 和日记
                    if self._workspace:
                        try:
                            import uuid as _uuid
                            eid = _uuid.uuid4().hex[:8]
                            error_detail = (
                                f"引擎执行失败\n任务: {ctx.user_message[:200]}\n"
                                f"错误: {result.error or '未知错误'}"
                            )
                            self._workspace.append_error(eid, error_detail)
                            self._workspace.append_diary(
                                f"[error] 引擎执行失败: {result.error or '未知'}",
                            )
                        except Exception:
                            pass
                    return self._soul.get_error_message(result.error or "执行失败")
                return result.output

            case "plan":
                return await self._execute_plan(decision.plan or [], ctx, msg)

            case "memory_op":
                # 记忆操作本身的结果已在 response_text 中
                # 实际的记忆写入在 post_process 中异步完成
                return decision.response_text or "记忆操作完成。"

            case "workspace_op":
                results = []
                for op in decision.workspace_ops or []:
                    result = self._execute_workspace_op(op)
                    results.append(result)
                text = "\n".join(results)
                if decision.response_text:
                    return f"{decision.response_text}\n\n{text}"
                return text

            case _:
                return decision.response_text or ""

    async def _execute_plan(
        self,
        steps: List[PlanStep],
        ctx: ThinkingContext,
        msg: UnifiedMessage,
    ) -> str:
        """按依赖关系执行多步计划，无依赖的步骤并行执行。"""
        if self._on_ack:
            desc = "\n".join(f"{i+1}. {s.description}" for i, s in enumerate(steps))
            await self._on_ack(msg.chat_id, f"开始执行计划:\n{desc}")

        n = len(steps)
        results: List[Optional[str]] = [None] * n
        completed: set[int] = set()

        while len(completed) < n:
            # 找出所有依赖已满足、尚未完成的步骤
            ready = [
                i for i in range(n)
                if i not in completed
                and all(dep in completed for dep in steps[i].depends_on)
            ]
            if not ready:
                logger.error("计划存在循环依赖，终止执行")
                break

            logger.info(
                "并行执行步骤: %s",
                ", ".join(f"{i+1}.{steps[i].description}" for i in ready),
            )

            async def _run_step(idx: int) -> str:
                step = steps[idx]
                prompt = step.prompt
                for dep_idx in step.depends_on:
                    if results[dep_idx]:
                        prompt += f"\n\n步骤 {dep_idx + 1} 的结果:\n{results[dep_idx]}"
                if step.executor == "engine":
                    r = await self._hands.execute(ctx.user_id, prompt)
                    return r.output if r.success else f"错误: {r.error}"
                else:
                    return await self._llm.think(ctx.soul_fragment, prompt)

            # 并行执行当前批次
            coros = [_run_step(i) for i in ready]
            batch_results = await asyncio.gather(*coros, return_exceptions=True)

            for idx, r in zip(ready, batch_results):
                if isinstance(r, Exception):
                    results[idx] = f"错误: {r}"
                    logger.error("步骤 %d 执行异常: %s", idx + 1, r)
                else:
                    results[idx] = r
                steps[idx].result = results[idx]
                steps[idx].completed = True
                completed.add(idx)

        # 汇总所有步骤结果
        summary_parts = []
        for i, step in enumerate(steps):
            summary_parts.append(f"### 步骤 {i+1}: {step.description}\n{step.result}")
        return "\n\n".join(summary_parts)

    # ── workspace_op 路由 ──────────────────────────────────────

    def _execute_workspace_op(self, op: Dict[str, Any]) -> str:
        """执行单个 workspace 文件操作。"""
        if not self._workspace:
            return "错误: workspace 未初始化"

        op_type = op.get("op", "")
        try:
            match op_type:
                case "read":
                    content = self._workspace.read_file(op["file"])
                    return content or f"（文件 {op['file']} 为空或不存在）"
                case "write":
                    self._workspace.write_file(op["file"], op["content"])
                    return f"已写入 {op['file']}"
                case "append":
                    self._workspace.append_file(op["file"], op["content"])
                    return f"已追加到 {op['file']}"
                case "update_section":
                    self._workspace.update_section(
                        op["file"], op["section"], op["content"]
                    )
                    return f"已更新 {op['file']} ## {op['section']}"
                case "append_diary":
                    self._workspace.append_diary(op["content"], op.get("date", ""))
                    return "日记已更新"
                case "read_diary":
                    content = self._workspace.read_diary(op.get("date", ""))
                    return content or "（今天还没有日记）"
                case "append_learning":
                    self._workspace.append_learning(
                        op["id"], op["content"]
                    )
                    return "经验已记录"
                case "append_error":
                    self._workspace.append_error(op["id"], op["content"])
                    return "错误已记录"
                case "append_feature_request":
                    self._workspace.append_feature_request(
                        op["id"], op["content"]
                    )
                    return "功能请求已记录"
                case "get_skills":
                    skills = self._workspace.get_skills()
                    lines = [f"- **{s['name']}**: {s['desc']}" for s in skills]
                    return "我的能力：\n" + "\n".join(lines)
                case "complete_bootstrap":
                    self._workspace.complete_bootstrap()
                    self._bootstrapped = True
                    return "引导完成，BOOTSTRAP.md 已删除"
                case _:
                    return f"未知操作: {op_type}"
        except (ValueError, KeyError) as e:
            return f"操作失败 ({op_type}): {e}"

    # ── 主动推送 ──────────────────────────────────────────────

    async def push_message(self, platform: str, chat_id: str, text: str) -> None:
        """主动推送消息到指定平台/会话。"""
        if self._on_push:
            await self._on_push(platform, chat_id, text)
        else:
            logger.warning("推送失败: on_push 回调未注册")

    # ── 自我成长：从对话中沉淀信息到 workspace ─────────────────

    # 各 workspace 文件的定位，供 LLM 判断写入目标
    _WORKSPACE_FILE_GUIDE = """可更新的文件及其定位：
    - SOUL.md: 行为准则和价值观（内核）。只有真正影响行事方式的深层认知才写这里。
    - IDENTITY.md: 人设标签和外在表现（外壳）。表达习惯、语气偏好、称呼方式等。
    - USER.md: 用户画像。称呼、偏好、背景、工作习惯、禁忌等。
    - AGENTS.md "你的规则"章节: 在实践中总结的工作习惯、流程改进、决策规则。
    - HEARTBEAT.md: 定时任务清单。增删定时任务时更新。
    - TOOLS.md: 工具使用中发现的技巧、环境特有配置。
    - MEMORY.md "记忆"章节: 重要事件、关键决策、长期有效的背景信息。"""

    def _should_reflect(self, msg: UnifiedMessage, intent: Intent) -> bool:
        """事件驱动：判断这条消息是否值得触发自我成长反思。

        触发条件（满足任一即触发）：
        1. Bootstrap 阶段 + 真实用户消息（引导流程中的信息收集）
        2. 用户透露个人信息（意图为 chitchat 且内容较长，暗示有信息量）
        3. 用户给出反馈/纠正（内容含"不要"、"别"、"应该"、"记住"等信号词）
        4. 任务完成后的总结（coding/complex 意图，完成了有意义的工作）
        5. 用户主动要求记住（"记住"、"记一下"、"以后"等）

        不触发：
        - 系统消息 / routine 消息
        - 太短的消息（<15字）
        - 命令 / 状态查询
        - 普通闲聊问候
        """
        # 硬过滤：系统消息永远不触发
        if msg.user_id == "system" or msg.platform == "routine":
            return False

        # 硬过滤：命令、状态查询
        if intent.type in (IntentType.COMMAND, IntentType.STATUS):
            return False

        # 硬过滤：太短的消息
        if len(msg.content) < 15:
            return False

        # Bootstrap 阶段：真实用户的每条消息都可能含引导信息
        if self._bootstrap_prompt and not self._bootstrapped:
            return True

        # 信号词检测：用户在给反馈或要求记忆
        content = msg.content
        feedback_signals = ("不要", "别再", "应该", "记住", "记一下", "以后", "下次",
                           "我喜欢", "我不喜欢", "我习惯", "我的", "我是", "我在")
        if any(s in content for s in feedback_signals):
            return True

        # 任务完成：编码/复杂任务可能产生流程改进
        if intent.type in (IntentType.CODING, IntentType.COMPLEX):
            return True

        # 有信息量的闲聊（>50字，可能含个人信息）
        if intent.type == IntentType.CHITCHAT and len(content) > 50:
            return True

        return False

    async def _reflect_and_grow(
        self, msg: UnifiedMessage, response: str, intent: Intent,
    ) -> None:
        """事件驱动的自我成长：只在有价值的时刻才触发反思。

        不再每条消息都跑 LLM 判断，而是先用规则过滤，只有通过的才调用模型。
        """
        if not self._workspace:
            return

        if not self._should_reflect(msg, intent):
            return

        recent = self._conversation.get_recent(msg.user_id, n=6)
        if not recent:
            return

        convo_text = "\n".join(
            f"{t['role']}: {t['content'][:300]}" for t in recent
        )

        judge_prompt = f"""你是一个自我成长引擎。阅读以下对话，判断是否有值得长期保留的信息。

        {self._WORKSPACE_FILE_GUIDE}

        最近对话：
        {convo_text}
        """
        # Bootstrap 阶段：追加明确的写入指引
        if self._bootstrap_prompt and not self._bootstrapped:
            judge_prompt += """
            **⚠️ 当前处于首次启动引导阶段！** 请特别注意从对话中提取以下信息：
            - 用户的称呼/名字 → 必须写入 USER.md（如 SECTION: 基本信息）
            - 用户选择的角色定位（管家/搭档/军师等） → 写入 SOUL.md（如 SECTION: 角色定位）
            - 用户选择的相处风格（严谨/随性/温厚等） → 写入 IDENTITY.md（如 SECTION: 语气风格）

            引导阶段的信息非常重要，**不要**因为"严格标准"而跳过这些内容。只要对话中出现了上述任何信息，就必须提取并写入对应文件。
            """

        judge_prompt += """**严格标准**：这些文件会伴随你一生，只有真正有价值的内容才值得写入。
        以下情况**不要**更新：
        - 日常寒暄、闲聊、测试消息
        - 已经在文件中记录过的信息
        - 临时性的、一次性的内容
        - 不确定是否有长期价值的信息

        以下情况**值得**更新：
        - 用户透露了个人信息、偏好、工作背景 → USER.md
        - 用户纠正了你的行为或给出了明确反馈 → SOUL.md 或 AGENTS.md
        - 发现了更好的工作方式或流程改进 → AGENTS.md
        - 工具使用中发现了技巧或配置 → TOOLS.md
        - 用户的表达习惯暗示了你应调整的语气风格 → IDENTITY.md
        - 重要的事件、决策、需要长期记住的背景 → MEMORY.md

        如果没有值得更新的内容，只输出一行：
        无需更新

        如果有值得更新的内容，按以下格式输出（可以有多条）：
        ---
        FILE: 文件名.md
        SECTION: 要更新的章节标题（如果是追加到特定章节）
        CONTENT: 要追加的内容（简洁的一两行，像人写笔记一样自然）
        ---

        注意：CONTENT 是要追加的新内容，不是整个文件。保持简洁，一两行即可。"""

        try:
            result = await self._llm.think(
                "你是一个严格的信息筛选器。宁可漏掉，不可滥写。只输出结构化结果。",
                judge_prompt,
            )

            if "无需更新" in result:
                return

            # 解析更新指令
            updates = self._parse_growth_updates(result)
            if not updates:
                return

            for update in updates:
                try:
                    filename = update["file"]
                    content = update["content"]
                    section = update.get("section", "")

                    if section:
                        self._workspace.update_section(
                            filename, section, content, append=True,
                        )
                    else:
                        self._workspace.append_file(filename, "\n" + content + "\n")

                    logger.info("自我成长: %s 已更新 (%s)", filename, content[:50])
                except Exception as write_err:
                    logger.warning("自我成长写入失败 %s: %s", update.get("file"), write_err)

            # Bootstrap 完成检测：必须有真实用户参与
            if self._bootstrap_prompt and not self._bootstrapped:
                updated_files = {u["file"] for u in updates}
                bootstrap_files = {"USER.md", "IDENTITY.md", "SOUL.md"}
                if updated_files & bootstrap_files:
                    self._bootstrapped = True
                    self._workspace.complete_bootstrap()
                    logger.info("首次启动引导完成")

        except Exception as e:
            logger.warning("自我成长反思失败: %s", e)

    @staticmethod
    def _parse_growth_updates(text: str) -> list:
        """解析 LLM 返回的更新指令。"""
        updates = []
        current: dict = {}

        for line in text.splitlines():
            line = line.strip()
            if line == "---":
                if current.get("file") and current.get("content"):
                    updates.append(current)
                current = {}
                continue
            if line.startswith("FILE:"):
                current["file"] = line[5:].strip()
            elif line.startswith("SECTION:"):
                current["section"] = line[8:].strip()
            elif line.startswith("CONTENT:"):
                current["content"] = line[8:].strip()
            elif current.get("content") is not None and line:
                # 多行 content
                current["content"] += "\n" + line

        # 最后一条
        if current.get("file") and current.get("content"):
            updates.append(current)

        return updates

    # ── Step 6: 响应包装 ──────────────────────────────────────

    async def _compose_response(
        self, raw_result: str, intent: Intent, ctx: ThinkingContext
    ) -> str:
        """对执行结果进行人格化包装。"""
        return await self._composer.compose(raw_result, intent, ctx)

    # ── Step 7: 后处理 ────────────────────────────────────────

    async def _post_process(
        self,
        msg: UnifiedMessage,
        response: str,
        intent: Intent,
        decision: BrainDecision,
    ) -> None:
        """异步后处理：情绪更新、记忆提取等。

        注意：对话历史已在 _process_single 中同步记录，此处不再重复。
        """
        try:
            # 更新情绪
            if decision.action == "delegate":
                self._soul.on_task_complete(success=True)
            elif decision.action == "plan":
                self._soul.on_task_complete(success=True)

            # 编码/复杂任务完成后触发深度反思
            if intent.type in (IntentType.CODING, IntentType.COMPLEX):
                existing = self._memory_store.list_all()
                new_memories = await self._memory_extractor.reflect(
                    task_summary=intent.summary,
                    outcome=response[:500],
                    existing_memories=existing,
                    source_user=msg.user_id,
                )
                for mem in new_memories:
                    self._memory_store.save(mem)
                    # 同步写入 LEARNINGS.md
                    if self._workspace and mem.content:
                        try:
                            import uuid as _uuid
                            lid = _uuid.uuid4().hex[:8]
                            self._workspace.append_learning(
                                lid, f"[{mem.name}] {mem.content[:300]}"
                            )
                        except Exception:
                            pass

            # 闲聊/知识问答中提取用户反馈和偏好（轻量提取）
            elif intent.type in (IntentType.CHITCHAT, IntentType.KNOWLEDGE):
                # 只在用户消息足够长（>20字）时触发，避免对"你好"也跑提取
                if len(msg.content) > 20:
                    try:
                        existing = self._memory_store.list_all()
                        conversation = [
                            {"role": "user", "content": msg.content},
                            {"role": "assistant", "content": response[:500]},
                        ]
                        new_memories = await self._memory_extractor.extract(
                            conversation=conversation,
                            existing_memories=existing,
                            source_user=msg.user_id,
                        )
                        for mem in new_memories:
                            self._memory_store.save(mem)
                    except Exception as extract_err:
                        logger.debug("闲聊记忆提取跳过: %s", extract_err)

            # 自动日记：记录每次有意义的交互（仅用户消息，排除系统消息）
            if self._workspace and msg.user_id != "system" and intent.type not in (
                IntentType.CHITCHAT, IntentType.COMMAND
            ):
                try:
                    diary_entry = f"[{intent.type.value}] {intent.summary}"
                    if decision.action == "delegate":
                        diary_entry += " → 委派引擎执行"
                    self._workspace.append_diary(diary_entry)
                except Exception as diary_err:
                    logger.warning("日记写入失败: %s", diary_err)

            # 持久化情绪到 workspace
            if self._workspace:
                try:
                    self._workspace.save_mood(self._soul.soul.mood)
                except Exception as mood_err:
                    logger.warning("情绪持久化失败: %s", mood_err)

            # 会话日志：记录完整的用户-模型交互
            if self._workspace:
                try:
                    from datetime import datetime as _dt

                    self._workspace.append_session_log({
                        "timestamp": _dt.now().isoformat(),
                        "user_id": msg.user_id,
                        "platform": msg.platform,
                        "user_message": msg.content,
                        "intent": {
                            "type": intent.type.value,
                            "confidence": intent.confidence,
                            "summary": intent.summary,
                        },
                        "decision": {
                            "action": decision.action,
                        },
                        "response": response,
                    })
                except Exception as session_err:
                    logger.warning("会话日志写入失败: %s", session_err)

            # 自我成长：从对话中提取有价值的信息写入 workspace 文件
            await self._reflect_and_grow(msg, response, intent)

            # 处理记忆操作
            if decision.memory_ops:
                for op in decision.memory_ops:
                    if op.get("op") == "reset_session":
                        self._conversation.clear(op["user_id"])
                        await self._hands.reset_user(op["user_id"])

        except Exception as e:
            logger.warning("后处理异常: %s", e)
