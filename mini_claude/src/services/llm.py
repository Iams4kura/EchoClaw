"""LLM client with retry and streaming.

Reference: src/services/api/withRetry.ts, src/query.ts streaming logic
"""

import os
import json
import asyncio
import logging
from typing import Optional, List, AsyncIterator, Dict, Any, Union
from dataclasses import dataclass
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Try to import optional dependencies
try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    HAS_TENACITY = True
except ImportError:
    HAS_TENACITY = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import litellm
    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from ..config import Config
from ..models import Message, TextBlock, ToolUseBlock

# SSE (Server-Sent Events) data line prefix
_SSE_DATA_PREFIX = "data: "


@dataclass
class LLMResponse:
    """Structured LLM response."""
    content: List[Union[TextBlock, ToolUseBlock]]
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None


class BaseLLMBackend(ABC):
    """Abstract LLM backend interface."""

    @abstractmethod
    async def complete(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Non-streaming completion."""
        pass

    @abstractmethod
    async def complete_streaming(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[Union[TextBlock, ToolUseBlock]]:
        """Streaming completion - yields content blocks as received."""
        pass


class AnthropicBackend(BaseLLMBackend):
    """Direct Anthropic API backend."""

    def __init__(self, api_key: Optional[str] = None):
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package required. Install with: pip install anthropic")
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not provided")
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

    async def complete(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Complete with Anthropic API."""
        api_messages = [msg.to_api_format() for msg in messages if msg.role != "system"]
        system_prompt = next(
            (msg.content[0].text for msg in messages if msg.role == "system" and msg.content),
            None
        )

        kwargs = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if top_p is not None:
            kwargs["top_p"] = top_p
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        response = await self.client.messages.create(**kwargs)

        # Parse response content
        content_blocks = []
        for block in response.content:
            if block.type == "text":
                content_blocks.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content_blocks.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.input
                ))

        return LLMResponse(
            content=content_blocks,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            model=response.model
        )

    async def complete_streaming(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[Union[TextBlock, ToolUseBlock]]:
        """Stream completion from Anthropic."""
        api_messages = [msg.to_api_format() for msg in messages if msg.role != "system"]
        system_prompt = next(
            (msg.content[0].text for msg in messages if msg.role == "system" and msg.content),
            None
        )

        kwargs = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        current_tool_use = None
        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event.delta, 'text'):
                        yield TextBlock(text=event.delta.text)
                    elif hasattr(event.delta, 'partial_json'):
                        # Tool use streaming (partial JSON)
                        if current_tool_use:
                            current_tool_use['input'] += event.delta.partial_json
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        current_tool_use = {
                            'id': event.content_block.id,
                            'name': event.content_block.name,
                            'input': '',
                        }
                elif event.type == "content_block_stop":
                    if current_tool_use:
                        yield ToolUseBlock(
                            id=current_tool_use['id'],
                            name=current_tool_use['name'],
                            input=json.loads(current_tool_use['input']) if current_tool_use['input'] else {}
                        )
                        current_tool_use = None


class LiteLLMBackend(BaseLLMBackend):
    """LiteLLM proxy backend for multi-provider support."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        if not HAS_LITELLM:
            raise ImportError("litellm required. Install with: pip install litellm")
        self.api_key = api_key
        self.base_url = base_url

    async def complete(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = "anthropic/claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Complete via LiteLLM."""
        api_messages = [msg.to_api_format() for msg in messages if msg.role != "system"]
        system_prompt = next(
            (msg.content[0].text for msg in messages if msg.role == "system" and msg.content),
            None
        )

        kwargs = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if top_p is not None:
            kwargs["top_p"] = top_p
        if system_prompt:
            # LiteLLM accepts system as first message or separate param
            api_messages.insert(0, {"role": "system", "content": system_prompt})
        if tools:
            kwargs["tools"] = tools

        response = await litellm.acompletion(**kwargs)

        content_blocks = []
        for choice in response.choices:
            if choice.message.content:
                content_blocks.append(TextBlock(text=choice.message.content))
            if choice.message.tool_calls:
                for tool_call in choice.message.tool_calls:
                    content_blocks.append(ToolUseBlock(
                        id=tool_call.id or f"tu_{os.urandom(4).hex()}",
                        name=tool_call.function.name,
                        input=json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                    ))

        usage = None
        if hasattr(response, 'usage'):
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }

        return LLMResponse(
            content=content_blocks,
            usage=usage,
            model=response.model
        )

    async def complete_streaming(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = "anthropic/claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[Union[TextBlock, ToolUseBlock]]:
        """Stream via LiteLLM with real streaming support."""
        api_messages = [msg.to_api_format() for msg in messages if msg.role != "system"]
        system_prompt = next(
            (msg.content[0].text for msg in messages if msg.role == "system" and msg.content),
            None
        )

        kwargs = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system_prompt:
            api_messages.insert(0, {"role": "system", "content": system_prompt})
        if tools:
            kwargs["tools"] = tools

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception:
            # Fallback to non-streaming if streaming fails
            logger.warning("LiteLLM streaming failed, falling back to non-streaming")
            full_response = await self.complete(messages, tools, model, temperature, max_tokens)
            for block in full_response.content:
                yield block
            return

        tool_calls_acc: Dict[int, Dict[str, str]] = {}

        async for chunk in response:
            choices = chunk.get("choices", []) if isinstance(chunk, dict) else getattr(chunk, 'choices', [])
            for choice in choices:
                delta = choice.get("delta", {}) if isinstance(choice, dict) else getattr(choice, 'delta', None)
                if delta is None:
                    continue

                # Text content
                content = delta.get("content") if isinstance(delta, dict) else getattr(delta, 'content', None)
                if content:
                    yield TextBlock(text=content)

                # Tool call deltas
                tc_list = delta.get("tool_calls", []) if isinstance(delta, dict) else getattr(delta, 'tool_calls', None) or []
                for tc in tc_list:
                    idx = tc.get("index", 0) if isinstance(tc, dict) else getattr(tc, 'index', 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}

                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, 'id', None)
                    if tc_id:
                        tool_calls_acc[idx]["id"] = tc_id

                    func = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, 'function', None)
                    if func:
                        fname = func.get("name") if isinstance(func, dict) else getattr(func, 'name', None)
                        fargs = func.get("arguments") if isinstance(func, dict) else getattr(func, 'arguments', None)
                        if fname:
                            tool_calls_acc[idx]["name"] = fname
                        if fargs:
                            tool_calls_acc[idx]["arguments"] += fargs

        # Yield assembled tool calls
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            try:
                input_data = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                input_data = {}
            yield ToolUseBlock(
                id=tc["id"] or f"call_{os.urandom(4).hex()}",
                name=tc["name"],
                input=input_data,
            )


class OpenAICompatibleBackend(BaseLLMBackend):
    """OpenAI-compatible API backend (DashScope, DeepSeek, vLLM, etc.)."""

    def __init__(self, api_key: str, base_url: str):
        if not HAS_HTTPX:
            raise ImportError("httpx required. Install with: pip install httpx")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def complete(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = "gpt-3.5-turbo",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Complete via OpenAI-compatible endpoint."""
        api_messages = self._build_messages(messages)

        body: Dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if top_p is not None:
            body["top_p"] = top_p
        if tools:
            body["tools"] = self._convert_tools(tools)

        resp = await self._client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_response(data)

    async def complete_streaming(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: str = "gpt-3.5-turbo",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[Union[TextBlock, ToolUseBlock]]:
        """Stream via OpenAI-compatible endpoint."""
        api_messages = self._build_messages(messages)

        body: Dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            body["tools"] = self._convert_tools(tools)

        # Collect tool call deltas for assembly
        tool_calls_acc: Dict[int, Dict[str, str]] = {}

        async with self._client.stream("POST", "/chat/completions", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith(_SSE_DATA_PREFIX):
                    payload = line[len(_SSE_DATA_PREFIX):]
                else:
                    continue
                if payload == "[DONE]":
                    break

                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})

                    # Text content
                    if delta.get("content"):
                        yield TextBlock(text=delta["content"])

                    # Tool call deltas
                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.get("id", ""),
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": "",
                            }
                        if tc.get("id"):
                            tool_calls_acc[idx]["id"] = tc["id"]
                        if tc.get("function", {}).get("name"):
                            tool_calls_acc[idx]["name"] = tc["function"]["name"]
                        if tc.get("function", {}).get("arguments"):
                            tool_calls_acc[idx]["arguments"] += tc["function"]["arguments"]

        # Yield assembled tool calls
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            try:
                input_data = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                input_data = {}
            yield ToolUseBlock(
                id=tc["id"] or f"call_{os.urandom(4).hex()}",
                name=tc["name"],
                input=input_data,
            )

    def _build_messages(self, messages: List[Message]) -> List[dict]:
        """Convert messages to OpenAI format."""
        result = []
        for msg in messages:
            if msg.role == "system":
                text = msg.get_text()
                if text:
                    result.append({"role": "system", "content": text})
                continue

            api_fmt = msg.to_api_format()

            # Convert Anthropic-style content blocks to OpenAI format
            if msg.role == "assistant":
                text_parts = []
                tool_calls = []
                for block in api_fmt.get("content", []):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block["input"]),
                            },
                        })
                out = {"role": "assistant", "content": "".join(text_parts) or None}
                if tool_calls:
                    out["tool_calls"] = tool_calls
                result.append(out)

            elif msg.role == "user":
                # Check if content has tool_result blocks
                content = api_fmt.get("content", [])
                has_tool_results = any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
                if has_tool_results:
                    # Convert to OpenAI tool response messages
                    for block in content:
                        if block.get("type") == "tool_result":
                            result.append({
                                "role": "tool",
                                "tool_call_id": block["tool_use_id"],
                                "content": block["content"] if isinstance(block["content"], str)
                                           else json.dumps(block["content"]),
                            })
                        elif block.get("type") == "text":
                            result.append({"role": "user", "content": block["text"]})
                else:
                    # Plain text user message
                    text = msg.get_text()
                    result.append({"role": "user", "content": text})

        return result

    def _convert_tools(self, anthropic_tools: List[dict]) -> List[dict]:
        """Convert Anthropic tool format to OpenAI function calling format."""
        openai_tools = []
        for tool in anthropic_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        return openai_tools

    def _parse_response(self, data: dict) -> LLMResponse:
        """Parse OpenAI-format response into LLMResponse."""
        content_blocks = []
        for choice in data.get("choices", []):
            message = choice.get("message", {})
            if message.get("content"):
                content_blocks.append(TextBlock(text=message["content"]))
            for tc in message.get("tool_calls", []):
                func = tc.get("function", {})
                try:
                    input_data = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    input_data = {}
                content_blocks.append(ToolUseBlock(
                    id=tc.get("id", f"call_{os.urandom(4).hex()}"),
                    name=func.get("name", ""),
                    input=input_data,
                ))

        usage = None
        if data.get("usage"):
            usage = {
                "input_tokens": data["usage"].get("prompt_tokens", 0),
                "output_tokens": data["usage"].get("completion_tokens", 0),
            }

        return LLMResponse(
            content=content_blocks or [TextBlock(text="")],
            usage=usage,
            model=data.get("model"),
        )


# ── Error Classification ──────────────────────────────────────

class PromptTooLongError(Exception):
    """Raised when the prompt exceeds model context window."""
    pass


class AuthenticationError(Exception):
    """Raised on 401/403 or API key issues."""
    pass


def classify_error(e: Exception) -> str:
    """Classify an exception into a retry category.

    Returns one of: 'rate_limit', 'prompt_too_long', 'auth', 'network', 'unknown'.
    """
    err_str = str(e).lower()
    err_type = type(e).__name__.lower()

    # Check known SDK exception types
    if HAS_ANTHROPIC:
        if isinstance(e, getattr(anthropic, 'RateLimitError', type(None))):
            return 'rate_limit'
        if isinstance(e, getattr(anthropic, 'AuthenticationError', type(None))):
            return 'auth'

    # HTTP status codes in error messages
    if '429' in err_str or '529' in err_str or 'rate' in err_str:
        return 'rate_limit'
    if '401' in err_str or '403' in err_str or 'unauthorized' in err_str or 'authentication' in err_str:
        return 'auth'

    # Prompt too long
    if 'too long' in err_str or 'too many tokens' in err_str or 'context_length' in err_str or 'max_tokens' in err_str:
        return 'prompt_too_long'

    # Network errors
    if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return 'network'
    if 'timeout' in err_type or 'connection' in err_type:
        return 'network'
    if HAS_HTTPX and isinstance(e, (getattr(httpx, 'ConnectError', type(None)),
                                     getattr(httpx, 'TimeoutException', type(None)))):
        return 'network'

    return 'unknown'


# Retry config per error category
_RETRY_CONFIG = {
    'rate_limit': {'max_attempts': 5, 'initial_wait': 4.0, 'max_wait': 60.0},
    'network':    {'max_attempts': 3, 'initial_wait': 1.0, 'max_wait': 15.0},
    'unknown':    {'max_attempts': 2, 'initial_wait': 2.0, 'max_wait': 15.0},
    # auth and prompt_too_long: no retry
}


async def _retry_with_classification(func, *args, **kwargs):
    """Execute func with error-classified retry logic."""
    last_exc = None
    # Track cumulative attempts across categories
    total_attempts = 0
    max_total = 6

    while total_attempts < max_total:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            total_attempts += 1
            category = classify_error(e)
            last_exc = e

            if category == 'auth':
                raise AuthenticationError(
                    f"Authentication failed: {e}. Check your API key."
                ) from e

            if category == 'prompt_too_long':
                raise PromptTooLongError(str(e)) from e

            config = _RETRY_CONFIG.get(category)
            if config is None or total_attempts >= config['max_attempts']:
                raise

            wait = min(config['initial_wait'] * (2 ** (total_attempts - 1)), config['max_wait'])
            logger.warning(
                "LLM error (%s), retry %d/%d in %.1fs: %s",
                category, total_attempts, config['max_attempts'], wait, e,
            )
            await asyncio.sleep(wait)

    raise last_exc


class LLMClient:
    """Unified LLM client with error-classified retry and backend selection.

    Usage:
        client = LLMClient(Config())
        response = await client.complete(messages, tools=[...])

        # Streaming
        async for chunk in client.complete_streaming(messages):
            print(chunk.text if hasattr(chunk, 'text') else chunk.name)
    """

    def __init__(self, config: Config):
        self.config = config
        self._backend: Optional[BaseLLMBackend] = None
        self._init_backend()

    def _init_backend(self) -> None:
        """Initialize appropriate backend based on config.

        Priority:
        1. If base_url is set -> OpenAI-compatible backend
        2. If model starts with claude- -> Anthropic backend
        3. If LiteLLM available -> LiteLLM backend
        4. Fallback to Anthropic
        """
        model = self.config.model

        # If custom base_url is provided, use OpenAI-compatible backend
        if self.config.base_url:
            if HAS_HTTPX:
                self._backend = OpenAICompatibleBackend(
                    api_key=self.config.api_key or "",
                    base_url=self.config.base_url,
                )
                return
            else:
                raise ImportError("httpx required for OpenAI-compatible API. Install with: pip install httpx")

        # Detect backend type from model prefix
        if model.startswith("anthropic/") or model.startswith("claude-"):
            actual_model = model.replace("anthropic/", "")
            self.config.model = actual_model
            if HAS_ANTHROPIC:
                self._backend = AnthropicBackend(self.config.api_key)
            else:
                raise ImportError(f"Model {model} requires anthropic package")
        elif HAS_LITELLM:
            self._backend = LiteLLMBackend(self.config.api_key, self.config.base_url)
        elif HAS_ANTHROPIC:
            self._backend = AnthropicBackend(self.config.api_key)
        else:
            raise ImportError("No LLM backend available. Install anthropic or litellm.")

    async def complete(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Execute completion with error-classified retry."""
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature
        max_tokens = max_tokens or self.config.max_tokens

        return await _retry_with_classification(
            self._backend.complete,
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        )

    async def complete_streaming(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[Union[TextBlock, ToolUseBlock]]:
        """Execute streaming completion with retry on initial connection errors."""
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature
        max_tokens = max_tokens or self.config.max_tokens

        last_exc = None
        for attempt in range(3):
            try:
                async for chunk in self._backend.complete_streaming(
                    messages=messages,
                    tools=tools,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield chunk
                return  # success
            except Exception as e:
                category = classify_error(e)
                last_exc = e
                if category in ('auth', 'prompt_too_long'):
                    if category == 'auth':
                        raise AuthenticationError(str(e)) from e
                    raise PromptTooLongError(str(e)) from e
                config = _RETRY_CONFIG.get(category, _RETRY_CONFIG['unknown'])
                if attempt >= config['max_attempts'] - 1:
                    raise
                wait = min(config['initial_wait'] * (2 ** attempt), config['max_wait'])
                logger.warning("Streaming error (%s), retry %d in %.1fs: %s", category, attempt + 1, wait, e)
                await asyncio.sleep(wait)

        raise last_exc

    @staticmethod
    def estimate_tokens(text: str, model: Optional[str] = None) -> int:
        """Estimate tokens using tiktoken if available."""
        from .compaction import count_tokens
        return count_tokens(text)
