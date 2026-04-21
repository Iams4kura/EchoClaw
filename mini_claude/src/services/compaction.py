"""5-layer context compression strategy.

Reference: src/services/compact/, src/utils/tokens.ts
"""

import json
import logging
from typing import List, Optional

from ..models.message import Message, TextBlock, ToolUseBlock, ToolResultBlock

logger = logging.getLogger(__name__)

# Lazy-loaded tiktoken encoder
_encoder = None


def _get_encoder():
    """Get or create cached tiktoken encoder."""
    global _encoder
    if _encoder is None:
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
        except (ImportError, Exception) as e:
            logger.warning("tiktoken unavailable, falling back to char estimation: %s", e)
    return _encoder


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken if available, else rough estimate."""
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return len(text) // 4 + 1


COMPACTION_STRATEGIES = [
    "truncate_tool_results",
    "remove_assistant_thinking",
    "summarize_early_messages",
    "remove_old_tool_calls",
    "hard_truncation",
]


class ContextCompactor:
    """Progressive context compression to stay within token limits.

    Applies strategies from least to most aggressive until
    the message history fits within the target token budget.
    """

    def __init__(self, max_tokens: int = 200000, threshold: float = 0.9):
        self.max_tokens = max_tokens
        self.threshold = threshold  # Compact at this % of max
        self._target = int(max_tokens * threshold)
        self._last_api_input_tokens: Optional[int] = None
        # Circuit breaker for LLM summarization
        self._cb_failures: int = 0
        self._cb_max_failures: int = 3

    def update_api_usage(self, input_tokens: int) -> None:
        """Update with actual token count from API response."""
        self._last_api_input_tokens = input_tokens

    def needs_compaction(self, messages: List[Message]) -> bool:
        """Check if messages need compaction.

        Prefers API-reported input_tokens if available (most accurate),
        otherwise falls back to local tiktoken estimation.
        """
        if self._last_api_input_tokens is not None:
            return self._last_api_input_tokens > self._target
        return self.estimate_tokens(messages) > self._target

    async def compact(
        self,
        messages: List[Message],
        target_tokens: Optional[int] = None,
    ) -> List[Message]:
        """Apply strategies progressively until under limit."""
        target = target_tokens or self._target
        current = list(messages)

        for strategy in COMPACTION_STRATEGIES:
            if self.estimate_tokens(current) <= target:
                break
            logger.info(f"Applying compaction strategy: {strategy}")
            current = await self._apply_strategy(current, strategy)

        return current

    async def _apply_strategy(
        self, messages: List[Message], strategy: str
    ) -> List[Message]:
        """Apply a single compaction strategy."""
        if strategy == "truncate_tool_results":
            return self._truncate_tool_results(messages)
        elif strategy == "remove_assistant_thinking":
            return self._remove_thinking(messages)
        elif strategy == "summarize_early_messages":
            return await self._summarize_early(messages)
        elif strategy == "remove_old_tool_calls":
            return self._remove_old_tools(messages)
        elif strategy == "hard_truncation":
            return self._hard_truncate(messages)
        return messages

    def _truncate_tool_results(
        self, messages: List[Message], max_result_len: int = 1000,
    ) -> List[Message]:
        """Layer 1: Truncate long tool result outputs."""
        result = []
        for msg in messages:
            if not isinstance(msg.content, list):
                result.append(msg)
                continue

            new_blocks = []
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    content = block.content
                    if isinstance(content, str) and len(content) > max_result_len:
                        content = content[:max_result_len] + f"\n... (truncated, was {len(block.content)} chars)"
                    new_blocks.append(ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=content,
                        is_error=block.is_error,
                    ))
                else:
                    new_blocks.append(block)

            result.append(Message(
                role=msg.role,
                content=new_blocks,
                timestamp=msg.timestamp,
                is_summarized=msg.is_summarized,
            ))
        return result

    def _remove_thinking(self, messages: List[Message]) -> List[Message]:
        """Layer 2: Remove verbose reasoning from assistant messages."""
        result = []
        for msg in messages:
            if msg.role != "assistant":
                result.append(msg)
                continue

            # Keep tool use blocks, trim long text
            if isinstance(msg.content, list):
                new_blocks = []
                for block in msg.content:
                    if isinstance(block, TextBlock) and len(block.text) > 500:
                        # Keep first and last 200 chars
                        trimmed = block.text[:200] + "\n...\n" + block.text[-200:]
                        new_blocks.append(TextBlock(text=trimmed))
                    else:
                        new_blocks.append(block)
                result.append(Message(
                    role=msg.role, content=new_blocks,
                    timestamp=msg.timestamp, is_summarized=True,
                ))
            else:
                result.append(msg)
        return result

    async def _summarize_early(self, messages: List[Message]) -> List[Message]:
        """Layer 3: Replace early messages with an LLM-generated summary."""
        if len(messages) <= 6:
            return messages

        # Keep first 2 (system + first user) and last 4 messages
        early = messages[2:-4]
        if not early:
            return messages

        summary_text = await self._llm_summarize(early)
        summary = Message(role="user", content=summary_text, is_summarized=True)

        return messages[:2] + [summary] + messages[-4:]

    async def _llm_summarize(self, messages: List[Message]) -> str:
        """Use LLM to generate a concise summary of messages.

        Falls back to placeholder if circuit breaker trips or LLM unavailable.
        """
        count = len(messages)
        placeholder = f"[{count} earlier messages summarized]"

        # Circuit breaker check
        if self._cb_failures >= self._cb_max_failures:
            logger.warning("LLM summarization circuit breaker open (failures=%d)", self._cb_failures)
            return placeholder

        # Serialize messages to text for summarization
        text_parts = []
        for msg in messages:
            role = msg.role
            if isinstance(msg.content, list):
                for block in msg.content:
                    if hasattr(block, 'text'):
                        text_parts.append(f"{role}: {block.text}")
                    elif hasattr(block, 'name'):
                        text_parts.append(f"{role}: [tool:{block.name}]")
                    elif hasattr(block, 'tool_use_id'):
                        content_str = block.content if isinstance(block.content, str) else str(block.content)
                        text_parts.append(f"{role}: [result: {content_str[:200]}]")
            elif isinstance(msg.content, str):
                text_parts.append(f"{role}: {msg.content}")

        conversation_text = "\n".join(text_parts)
        # Limit input to avoid excessive cost
        if len(conversation_text) > 8000:
            conversation_text = conversation_text[:8000] + "\n... (truncated)"

        prompt = (
            "Summarize the following conversation concisely. "
            "Preserve: file paths, decisions made, errors encountered, "
            "and key technical details. Output a single paragraph.\n\n"
            f"{conversation_text}"
        )

        try:
            from ..engine.headless import run_headless
            summary = await run_headless(
                input_text=prompt,
                system_prompt_extra="You are a concise summarizer. Output only the summary, no preamble.",
            )
            if summary and len(summary.strip()) > 10:
                self._cb_failures = 0  # Reset on success
                logger.info("LLM summarization succeeded (%d chars)", len(summary))
                return f"[Summary of {count} earlier messages]\n{summary.strip()}"
            else:
                raise ValueError("Empty summary returned")
        except Exception as e:
            self._cb_failures += 1
            logger.warning(
                "LLM summarization failed (attempt %d/%d): %s",
                self._cb_failures, self._cb_max_failures, e,
            )
            return placeholder

    def _remove_old_tools(self, messages: List[Message]) -> List[Message]:
        """Layer 4: Remove tool_use and tool_result blocks from old messages."""
        if len(messages) <= 4:
            return messages

        result = []
        cutoff = len(messages) - 4

        for i, msg in enumerate(messages):
            if i >= cutoff:
                result.append(msg)
                continue

            if isinstance(msg.content, list):
                # Remove tool blocks, keep text
                text_blocks = [b for b in msg.content if isinstance(b, TextBlock)]
                if text_blocks:
                    result.append(Message(
                        role=msg.role, content=text_blocks,
                        timestamp=msg.timestamp, is_summarized=True,
                    ))
                # Skip messages that were only tool results
            else:
                result.append(msg)

        return result

    def _hard_truncate(self, messages: List[Message]) -> List[Message]:
        """Layer 5: Keep only the most recent messages."""
        if len(messages) <= 4:
            return messages

        # Keep system message + last 3 messages
        system = [m for m in messages if m.role == "system"]
        recent = messages[-3:]
        return system + recent

    @staticmethod
    def estimate_tokens(messages: List[Message]) -> int:
        """Estimate tokens using tiktoken (precise) or char ratio (fallback).

        Per-message overhead of ~4 tokens accounts for role/formatting.
        """
        total = 0
        for msg in messages:
            total += 4  # per-message overhead
            if isinstance(msg.content, str):
                total += count_tokens(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        total += count_tokens(block.text)
                    elif isinstance(block, ToolUseBlock):
                        total += count_tokens(json.dumps(block.input)) + 10
                    elif isinstance(block, ToolResultBlock):
                        content = block.content if isinstance(block.content, str) else str(block.content)
                        total += count_tokens(content) + 5
        return total
