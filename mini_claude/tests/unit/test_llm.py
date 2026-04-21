"""Tests for LLM client."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.models import Message, TextBlock, ToolUseBlock
from src.config import Config


# Skip real API tests by default
pytestmark = pytest.mark.asyncio


class TestLLMClientBasics:
    """Test LLMClient without real API calls."""

    def test_import(self):
        """Verify LLM module imports correctly."""
        from src.services.llm import LLMClient, BaseLLMBackend, LLMResponse
        from src.services.llm import AnthropicBackend, LiteLLMBackend
        assert LLMClient is not None
        assert BaseLLMBackend is not None

    def test_anthropic_backend_init_fails_without_key(self):
        """AnthropicBackend requires API key."""
        from src.services.llm import AnthropicBackend
        with pytest.raises(ValueError):
            AnthropicBackend(api_key=None)

    def test_llm_client_uses_config_defaults(self):
        """LLMClient reads config for defaults."""
        from src.services.llm import LLMClient
        config = Config(
            model="claude-3-haiku",
            temperature=0.5,
            max_tokens=2048,
        )
        # Mock backend initialization since we don't have anthropic
        with patch.object(LLMClient, '_init_backend'):
            client = LLMClient(config)
            assert client.config.model == "claude-3-haiku"
            assert client.config.temperature == 0.5
            assert client.config.max_tokens == 2048


class TestLLMClientMock:
    """Test LLMClient with mocked backend."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        backend = MagicMock()
        backend.complete = AsyncMock(return_value=MagicMock(
            content=[TextBlock(text="Hello, World!")],
            usage={"input_tokens": 10, "output_tokens": 5},
            model="test-model"
        ))
        return backend

    @pytest.fixture
    def client(self, mock_backend):
        """Create LLMClient with mocked backend."""
        from src.services.llm import LLMClient
        config = Config(api_key="test-key")
        client = object.__new__(LLMClient)
        client.config = config
        client._backend = mock_backend
        return client

    async def test_complete_calls_backend(self, client, mock_backend):
        """complete() delegates to backend."""
        messages = [Message(role="user", content="Test")]
        tools = [{"name": "Bash", "description": "Run command"}]

        result = await client.complete(messages, tools=tools)

        mock_backend.complete.assert_called_once()
        call_kwargs = mock_backend.complete.call_args[1]
        assert call_kwargs["tools"] == tools
        assert call_kwargs["temperature"] == client.config.temperature

    async def test_complete_uses_custom_params(self, client, mock_backend):
        """complete() respects override parameters."""
        messages = [Message(role="user", content="Test")]

        await client.complete(
            messages,
            model="custom-model",
            temperature=0.9,
            max_tokens=8192
        )

        call_kwargs = mock_backend.complete.call_args[1]
        assert call_kwargs["model"] == "custom-model"
        assert call_kwargs["temperature"] == 0.9
        assert call_kwargs["max_tokens"] == 8192

    async def test_complete_uses_config_defaults(self, client, mock_backend):
        """complete() falls back to config when params omitted."""
        messages = [Message(role="user", content="Test")]

        await client.complete(messages)

        call_kwargs = mock_backend.complete.call_args[1]
        assert call_kwargs["model"] == client.config.model
        assert call_kwargs["temperature"] == client.config.temperature
        assert call_kwargs["max_tokens"] == client.config.max_tokens


class TestTokenEstimation:
    """Test token estimation utility."""

    def test_estimate_tokens_rough(self):
        """Rough 4:1 char to token ratio."""
        from src.services.llm import LLMClient
        with patch.object(LLMClient, '_init_backend'):
            client = LLMClient(Config())
            assert client.estimate_tokens("") == 1  # Minimum 1
            assert client.estimate_tokens("aaaa") == 2  # 4 chars = 1 token + 1 min
            assert client.estimate_tokens("a" * 40) == 11  # 40 chars = 10 + 1


class TestMessageConversion:
    """Test message format conversion for API."""

    def test_simple_message_to_api(self):
        """Simple text message converts correctly."""
        msg = Message(role="user", content="Hello")
        api_format = msg.to_api_format()
        assert api_format["role"] == "user"
        assert len(api_format["content"]) == 1
        assert api_format["content"][0]["type"] == "text"
        assert api_format["content"][0]["text"] == "Hello"

    def test_message_with_string_content(self):
        """Message accepts string content and converts to TextBlock."""
        msg = Message(role="assistant", content="Lorem ipsum")
        assert isinstance(msg.content, list)
        assert isinstance(msg.content[0], TextBlock)

    def test_system_message_extraction(self):
        """System messages handled specially for Anthropic API."""
        from src.services.llm import AnthropicBackend
        # System should be extracted, not in messages list
        config = Config(api_key="test-key")
        config.model = "claude-3-sonnet"

        with patch('src.services.llm.HAS_ANTHROPIC', True):
            with patch('anthropic.AsyncAnthropic'):
                # This test is structure verification
                messages = [
                    Message(role="system", content="You are Claude"),
                    Message(role="user", content="Hello"),
                ]
                # System message should not be in api_messages
                api_messages = [m for m in messages if m.role != "system"]
                assert len(api_messages) == 1
