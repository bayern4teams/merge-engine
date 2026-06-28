"""
Tests for src/llm/client.py - LLM client setup using LangChain.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm import LLMClient


class TestLLMClientInit:
    """Tests for LLMClient initialization."""

    def test_default_config(self):
        """LLMClient should use defaults when config is minimal."""
        config = {"model": "gpt-4", "api_key": "test-key"}
        client = LLMClient(config)
        assert client.provider == "openai"
        assert client.model_name == "gpt-4"
        assert client.api_key == "test-key"
        assert client.base_url is None
        assert client.temperature == 0.0

    def test_custom_config(self):
        """LLMClient should accept all config options."""
        config = {
            "provider": "anthropic",
            "model": "claude-3",
            "api_key": "sk-test",
            "base_url": "https://api.custom.com",
            "temperature": 0.7,
        }
        client = LLMClient(config)
        assert client.provider == "anthropic"
        assert client.model_name == "claude-3"
        assert client.api_key == "sk-test"
        assert client.base_url == "https://api.custom.com"
        assert client.temperature == 0.7

    def test_azure_provider(self):
        """Azure provider should be accepted when explicitly specified."""
        config = {"provider": "azure", "model": "gpt-4", "api_key": "test", "base_url": "https://openai.azure.com/"}
        client = LLMClient(config)
        assert client.provider == "azure"

    def test_empty_api_key(self):
        """Empty API key should be accepted (may be set via env var)."""
        config = {"model": "gpt-4", "api_key": ""}
        client = LLMClient(config)
        assert client.api_key == ""


class TestLLMClientGetModel:
    """Tests for _get_model() method."""

    @pytest.mark.asyncio
    async def test_lazy_initialization_openai(self):
        """Should create ChatOpenAI model on first call."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)
        # Model should not be initialized yet
        assert client._model is None
        # Mock the ChatOpenAI class
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            model = client._get_model()
            assert model == mock_model
            assert client._model == mock_model

    @pytest.mark.asyncio
    async def test_cached_model(self):
        """Should return cached model on subsequent calls."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            # First call initializes
            model1 = client._get_model()
            # Second call returns cached
            model2 = client._get_model()
            assert model1 == model2
            mock_chat.assert_called_once()  # Only called once


class TestLLMClientCall:
    """Tests for call() method."""

    @pytest.mark.asyncio
    async def test_basic_call(self):
        """Basic call should invoke LLM with correct messages."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)

        with patch.object(client, "_get_model") as mock_get_model:
            mock_model = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = "Hello, how can I help?"
            mock_model.ainvoke = AsyncMock(return_value=mock_response)
            mock_get_model.return_value = mock_model

            result = await client.call(
                system_prompt="You are helpful.",
                user_prompt="Hello.",
            )
            assert result == "Hello, how can I help?"

            # Verify messages passed to model
            messages = mock_model.ainvoke.call_args[0][0]
            assert len(messages) == 2
            assert messages[0].content == "You are helpful."
            assert messages[1].content == "Hello."

    @pytest.mark.asyncio
    async def test_call_with_structured_output(self):
        """Call with response_format should use structured output."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)

        with patch.object(client, "_get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.with_structured_output = MagicMock(return_value=mock_model)
            mock_model.ainvoke = AsyncMock(return_value=MagicMock(content="{}"))
            mock_get_model.return_value = mock_model

            response_format = {"type": "json_object"}
            await client.call(
                system_prompt="Test",
                user_prompt="Test",
                response_format=response_format,
            )
            mock_model.with_structured_output.assert_called_once_with(response_format)

    @pytest.mark.asyncio
    async def test_unsupported_provider_raises(self):
        """Unsupported provider should raise ValueError."""
        config = {"provider": "gemini", "model": "gemini-pro", "api_key": "test"}
        client = LLMClient(config)

        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            client._get_model()


class TestLLMClientCallJson:
    """Tests for call_json() method."""

    @pytest.mark.asyncio
    async def test_json_response(self):
        """call_json should parse JSON response."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)

        with patch.object(client, "call") as mock_call:
            mock_call.return_value = '{"key": "value", "count": 42}'
            result = await client.call_json(
                system_prompt="Return JSON.",
                user_prompt="Parse this.",
            )
            assert result == {"key": "value", "count": 42}

    @pytest.mark.asyncio
    async def test_markdown_json_extraction(self):
        """Should extract JSON from markdown code blocks."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)

        with patch.object(client, "call") as mock_call:
            mock_call.return_value = '```json\n{"status": "ok"}\n```'
            result = await client.call_json(
                system_prompt="Return JSON.",
                user_prompt="Parse this.",
            )
            assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_markdown_without_json_tag(self):
        """Should extract JSON without json tag."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)

        with patch.object(client, "call") as mock_call:
            mock_call.return_value = '```\n{"data": [1, 2, 3]}\n```'
            result = await client.call_json(
                system_prompt="Return JSON.",
                user_prompt="Parse this.",
            )
            assert result == {"data": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        """Invalid JSON response should raise JSONDecodeError."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)

        with patch.object(client, "call") as mock_call:
            mock_call.return_value = "This is not JSON at all"
            with pytest.raises(json.JSONDecodeError):
                await client.call_json(
                    system_prompt="Return JSON.",
                    user_prompt="Parse this.",
                )


class TestLLMClientCallBatch:
    """Tests for call_batch() method."""

    @pytest.mark.asyncio
    async def test_batch_call(self):
        """Batch call should process multiple prompts."""
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)

        with patch.object(client, "_get_model") as mock_get_model:
            mock_model = MagicMock()

            async def mock_ainvoke(msgs):
                return MagicMock(content=f"Response to {msgs[1].content}")

            mock_model.ainvoke = AsyncMock(side_effect=mock_ainvoke)
            mock_get_model.return_value = mock_model

            prompts = ["Hello", "How are you?", "Goodbye"]
            results = await client.call_batch(
                system_prompt="Greet the user.",
                prompts=prompts,
            )
            assert len(results) == 3
            assert "Response to Hello" in results[0]
            assert "Response to How are you?" in results[1]
            assert "Response to Goodbye" in results[2]


class TestCreateLLMClient:
    """Tests for create_llm_client() factory function."""

    def test_factory_creates_client(self):
        """Factory should create and return LLMClient."""
        from src.llm.client import create_llm_client
        config = {"provider": "openai", "model": "gpt-4", "api_key": "test"}
        client = create_llm_client(config)
        assert isinstance(client, LLMClient)

    def test_factory_with_different_providers(self):
        """Factory should work with different providers."""
        from src.llm.client import create_llm_client
        for provider in ["openai", "azure", "anthropic"]:
            config = {
                "provider": provider,
                "model": "test-model",
                "api_key": "test-key",
            }
            client = create_llm_client(config)
            assert isinstance(client, LLMClient)
            assert client.provider == provider