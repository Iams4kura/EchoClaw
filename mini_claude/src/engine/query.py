"""Main conversation loop - orchestrates messages, tool execution, and context.

Reference: src/query.ts, src/QueryEngine.ts
"""

import asyncio
import logging
from typing import Optional, List, Callable, Awaitable

from ..models.message import Message, TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock
from ..models.state import AppState
from ..models.tool import ToolResult
from ..services.llm import LLMClient, LLMResponse, PromptTooLongError, AuthenticationError
from ..services.permissions import PermissionManager, PermissionDecision
from ..services.compaction import ContextCompactor
from ..services.hooks import HookRegistry, HookEvent
from ..tools.registry import ToolRegistry
from ..tools.orchestration import ToolOrchestrator

logger = logging.getLogger(__name__)

# Maximum consecutive tool-use turns before forcing a text response
MAX_TOOL_TURNS = 25

# Maximum calls to the same tool in one turn (prevents retry-storm)
MAX_SAME_TOOL_CALLS = 3


class QueryEngine:
    """Core conversation engine.

    Orchestrates the main loop:
    1. User sends message
    2. Build context (system prompt + history)
    3. Compact context if needed
    4. Call LLM with tools
    5. If LLM returns tool_use blocks, check permissions then execute
    6. Feed results back to LLM
    7. Repeat until LLM returns only text (end_turn)
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        state: AppState,
        permissions: Optional[PermissionManager] = None,
        compactor: Optional[ContextCompactor] = None,
        hooks: Optional[HookRegistry] = None,
        on_text: Optional[Callable[[str], Awaitable[None]]] = None,
        on_tool_start: Optional[Callable[[ToolUseBlock], Awaitable[None]]] = None,
        on_tool_end: Optional[Callable[[str, ToolResult], Awaitable[None]]] = None,
        on_permission_ask: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
        on_thinking: Optional[Callable[[str], Awaitable[None]]] = None,
        on_turn_end: Optional[Callable[[dict], Awaitable[None]]] = None,
    ):
        self.llm = llm
        self.tools = tools
        self.state = state
        self.permissions = permissions or PermissionManager()
        self.compactor = compactor or ContextCompactor()
        self.hooks = hooks or HookRegistry()
        self._orchestrator = ToolOrchestrator(registry=tools, state=state)
        # UI callbacks
        self._on_text = on_text
        self._on_tool_start = on_tool_start
        self._on_tool_end = on_tool_end
        self._on_permission_ask = on_permission_ask
        self._on_thinking = on_thinking      # called with status string
        self._on_turn_end = on_turn_end      # called with usage stats

    async def run_turn(self, user_input: str) -> str:
        """Execute a full conversation turn.

        Returns:
            The assistant's final text response.
        """
        # 1. Add user message
        user_msg = Message(role="user", content=user_input)
        self.state.messages.append(user_msg)

        # 2. Compact context if needed
        if self.compactor.needs_compaction(self.state.messages):
            logger.info("Context compaction triggered")
            self.state.messages = await self.compactor.compact(self.state.messages)

        tool_turn_count = 0
        tool_call_counts: dict[str, int] = {}  # 每个工具本轮调用次数
        final_text = ""

        while tool_turn_count < MAX_TOOL_TURNS:
            if self.state.is_aborted():
                return "(aborted)"

            # 3. Build messages for API
            api_messages = self._build_api_messages()

            # 4. Call LLM
            if self._on_thinking:
                await self._on_thinking("Thinking")
            self.state.is_streaming = True
            try:
                response = await self.llm.complete(
                    messages=api_messages,
                    tools=self.tools.get_tools_for_prompt() if len(self.tools) > 0 else None,
                )
            except PromptTooLongError:
                # Reactive compaction: compress and retry once
                self.state.is_streaming = False
                if self._on_thinking:
                    await self._on_thinking("Compacting context")
                logger.info("Prompt too long, triggering reactive compaction")
                self.state.messages = await self.compactor.compact(self.state.messages)
                api_messages = self._build_api_messages()
                if self._on_thinking:
                    await self._on_thinking("Thinking")
                self.state.is_streaming = True
                try:
                    response = await self.llm.complete(
                        messages=api_messages,
                        tools=self.tools.get_tools_for_prompt() if len(self.tools) > 0 else None,
                    )
                except Exception as e:
                    logger.error(f"LLM API error after compaction: {e}")
                    self.state.is_streaming = False
                    if self._on_thinking:
                        await self._on_thinking(None)
                    return f"API error (after compaction): {e}"
            except AuthenticationError as e:
                self.state.is_streaming = False
                if self._on_thinking:
                    await self._on_thinking(None)
                return f"Authentication failed: {e}\nPlease check your API key."
            except Exception as e:
                logger.error(f"LLM API error: {e}")
                self.state.is_streaming = False
                if self._on_thinking:
                    await self._on_thinking(None)
                return f"API error: {e}"
            finally:
                self.state.is_streaming = False
                if self._on_thinking:
                    await self._on_thinking(None)

            # 5. Process response
            assistant_msg = Message(role="assistant", content=response.content)
            self.state.messages.append(assistant_msg)

            # Update token tracking
            if response.usage:
                self.state.token_usage.add(response.usage)
                # Feed actual input tokens to compactor for precise threshold checks
                input_tokens = response.usage.get("input_tokens")
                if input_tokens is not None:
                    self.compactor.update_api_usage(input_tokens)

            # 6. Extract text and tool_use blocks
            text_parts = []
            tool_uses = []
            for block in response.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                    if self._on_text:
                        await self._on_text(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_uses.append(block)

            final_text = "".join(text_parts)

            # 7. If no tool calls, we're done
            if not tool_uses:
                break

            # 7.5 Enforce per-tool call limits & preprocess inputs
            capped_uses = []
            capped_results: dict[str, ToolResult] = {}
            for tu in tool_uses:
                count = tool_call_counts.get(tu.name, 0)
                if count >= MAX_SAME_TOOL_CALLS:
                    capped_results[tu.id] = ToolResult(
                        content=(
                            f"Tool '{tu.name}' already called {count} times this turn. "
                            f"Limit is {MAX_SAME_TOOL_CALLS}. "
                            "Summarize what you have and respond to the user."
                        ),
                        is_error=True,
                    )
                else:
                    tool_call_counts[tu.name] = count + 1
                    # 短查询自动扩展（仅 WebSearch）
                    self._maybe_expand_search_query(tu)
                    capped_uses.append(tu)

            # 8. Check permissions and execute tools (only non-capped ones)
            tool_results = await self._execute_tools_with_permissions(
                tool_uses, capped_uses, capped_results,
            )

            # 9. Add tool results as user message (Anthropic format)
            result_blocks = []
            for tool_use, result in zip(tool_uses, tool_results):
                result_blocks.append(ToolResultBlock(
                    tool_use_id=tool_use.id,
                    content=result["content"],
                    is_error=result["is_error"],
                ))

            result_msg = Message(role="user", content=result_blocks)
            self.state.messages.append(result_msg)

            tool_turn_count += 1

        if tool_turn_count >= MAX_TOOL_TURNS:
            final_text += "\n(Reached maximum tool execution limit)"

        # Notify UI with usage stats
        if self._on_turn_end:
            await self._on_turn_end({
                "total_tokens": self.state.total_tokens,
                "input_tokens": self.state.token_usage.input_tokens,
                "output_tokens": self.state.token_usage.output_tokens,
                "tool_turns": tool_turn_count,
            })

        return final_text

    async def _execute_tools_with_permissions(
        self,
        tool_uses: List[ToolUseBlock],
        eligible_uses: Optional[List[ToolUseBlock]] = None,
        pre_results: Optional[dict] = None,
    ) -> List[ToolResult]:
        """Check permissions then execute tools with parallel orchestration.

        Args:
            tool_uses: All tool_use blocks from the LLM response.
            eligible_uses: Subset that passed call-count limits.
                           If None, all tool_uses are eligible.
            pre_results: Pre-filled results for capped tools (keyed by id).
        """
        # First pass: check permissions for all tools
        approved_uses = []
        results_map: dict[str, ToolResult] = dict(pre_results or {})
        actual_uses = eligible_uses if eligible_uses is not None else tool_uses

        for tool_use in actual_uses:
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
                allowed = await self._ask_permission(tool_use)
                if allowed:
                    approved_uses.append(tool_use)
                else:
                    results_map[tool_use.id] = ToolResult(
                        content=f"User denied {tool_use.name} execution",
                        is_error=True,
                    )
            else:
                approved_uses.append(tool_use)

        # Execute approved tools with parallel orchestration
        if approved_uses:
            # PreToolUse hooks — may block execution
            still_approved = []
            for tu in approved_uses:
                hook_results = await self.hooks.fire(HookEvent.PRE_TOOL_USE, {
                    "tool_name": tu.name,
                    "tool_input": tu.input,
                    "file_path": tu.input.get("file_path", tu.input.get("path", "")),
                })
                blocked = [r for r in hook_results if r.blocked]
                if blocked:
                    reason = blocked[0].stderr or blocked[0].stdout or "Blocked by hook"
                    results_map[tu.id] = ToolResult(
                        content=f"Hook blocked {tu.name}: {reason}",
                        is_error=True,
                    )
                    logger.info("Hook blocked tool %s: %s", tu.name, reason)
                else:
                    still_approved.append(tu)

            # Notify UI for each tool
            for tu in still_approved:
                if self._on_tool_start:
                    await self._on_tool_start(tu)

            if still_approved:
                parallel_results = await self._orchestrator.execute_parallel(still_approved)

                for tu, result in zip(still_approved, parallel_results):
                    results_map[tu.id] = result
                    if self._on_tool_end:
                        await self._on_tool_end(tu.id, result)
                    # PostToolUse hooks
                    await self.hooks.fire(HookEvent.POST_TOOL_USE, {
                        "tool_name": tu.name,
                        "tool_input": tu.input,
                        "result_content": result.get("content", ""),
                        "is_error": str(result.get("is_error", False)),
                    })

        # Return in original order
        return [
            results_map.get(tu.id, ToolResult(content="Missing result", is_error=True))
            for tu in tool_uses
        ]

    def _maybe_expand_search_query(self, tool_use: ToolUseBlock) -> None:
        """对 WebSearch 的短查询自动补全上下文。

        当查询过短（< 6 字符）或看起来是追问（含"呢""的""吗"等语气词），
        从最近对话中提取上下文关键词拼接到查询里。

        例如：上一轮聊"北京天气"，用户追问"广州的呢"
        → query 从 "广州的呢" 扩展为 "广州 天气"
        """
        if tool_use.name != "WebSearch":
            return
        query = tool_use.input.get("query", "")
        if not query:
            return

        # 判断是否需要扩展：查询太短 或 以语气词结尾
        needs_expansion = (
            len(query) <= 8
            or query.rstrip("?？").endswith(("呢", "的呢", "吗", "啊", "的"))
        )
        if not needs_expansion:
            return

        # 提取新实体（去掉语气词）
        import re
        clean_query = re.sub(r"[的呢吗啊]+$", "", query.rstrip("?？")).strip()
        if not clean_query:
            return

        # 从最近对话中提取话题（不含实体），如 "天气"、"房价"
        topic = self._extract_topic_from_context(clean_query)
        if not topic:
            return

        expanded = f"{clean_query}{topic}"
        logger.info("Query expanded: '%s' → '%s'", query, expanded)
        tool_use.input["query"] = expanded

    def _extract_topic_from_context(
        self, new_entity: str, lookback: int = 6,
    ) -> str:
        """从最近对话中提取话题词（去掉实体部分）。

        策略：找到上一次 WebSearch 的 query，剥掉里面的实体前缀，
        剩下的就是话题。

        例如：prev_query="北京天气", new_entity="广州"
        → 去掉日期等噪音 → core="北京天气"
        → 剥掉实体长度的前缀(2字符) → topic="天气"
        """
        import re
        messages = self.state.messages[-lookback:]

        prev_query = ""
        # 1. 找最近一次 WebSearch 调用的 query
        for msg in reversed(messages):
            if msg.role != "assistant" or not isinstance(msg.content, list):
                continue
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name == "WebSearch":
                    prev_query = block.input.get("query", "")
                    if prev_query:
                        break
            if prev_query:
                break

        if prev_query:
            # 去掉日期、语气词等噪音
            core = re.sub(
                r"\d{4}年\d{1,2}月\d{1,2}日|今[天日]|最新|的|呢|吗|\s+",
                "", prev_query,
            ).strip()
            # 剥掉与 new_entity 等长的前缀 → 剩下话题
            # 例如 core="北京天气", new_entity="广州"(2字) → core[2:]="天气"
            entity_len = len(new_entity)
            if len(core) > entity_len:
                topic = core[entity_len:]
                if topic:
                    return topic

        # 2. fallback：从最近用户消息中提取话题
        for msg in reversed(messages):
            if msg.role != "user":
                continue
            text = msg.content if isinstance(msg.content, str) else ""
            if not text or len(text) < 4:
                continue
            core = re.sub(r"^(帮我|请|搜索?|查一下|看看)\s*", "", text).strip()
            core = re.sub(r"[的呢吗啊?？]+$", "", core).strip()
            # 同样剥掉实体长度的前缀
            entity_len = len(new_entity)
            if len(core) > entity_len:
                topic = core[entity_len:]
                if topic:
                    return topic

        return ""

    async def _ask_permission(self, tool_use: ToolUseBlock) -> bool:
        """Ask user for permission to execute a tool."""
        if self._on_permission_ask:
            return await self._on_permission_ask(tool_use.name, tool_use.input)
        # Default: allow (no UI callback registered)
        return True

    def _build_api_messages(self) -> List[Message]:
        """Build message list for API call."""
        messages = []
        if self.state.system_prompt:
            messages.append(Message(role="system", content=self.state.system_prompt))
        messages.extend(self.state.messages)
        return messages
