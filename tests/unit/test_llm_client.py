"""
Unit tests for the LLM client module.

Tests LLMClient initialization, model creation, and JSON parsing.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.client import LLMClient, create_llm_client


class TestCreateLLMClient:
    """Tests for the create_llm_client factory function."""

    def test_create_openai_client(self) -> None:
        """Should create an OpenAI client by default."""
        config = {"provider": "openai", "model": "gpt-4o", "api_key": "test"}
        client = create_llm_client(config)
        assert isinstance(client, LLMClient)
        assert client.provider == "openai"
        assert client.model_name == "gpt-4o"

    def test_create_anthropic_client(self) -> None:
        """Should create an Anthropic client."""
        config = {"provider": "anthropic", "model": "claude-3", "api_key": "test"}
        client = create_llm_client(config)
        assert client.provider == "anthropic"
        assert client.model_name == "claude-3"

    def test_create_azure_client(self) -> None:
        """Should create an Azure client."""
        config = {
            "provider": "azure",
            "model": "gpt-4",
            "api_key": "test",
            "base_url": "https://example.openai.azure.com",
        }
        client = create_llm_client(config)
        assert client.provider == "azure"
        assert client.base_url == "https://example.openai.azure.com"

    def test_create_client_default_model(self) -> None:
        """Should use default model when not specified."""
        config = {"provider": "openai", "api_key": "test"}
        client = create_llm_client(config)
        assert client.model_name == "gpt-4o"

    def test_create_client_default_temperature(self) -> None:
        """Should use default temperature when not specified."""
        config = {"provider": "openai", "model": "gpt-4o", "api_key": "test"}
        client = create_llm_client(config)
        assert client.temperature == 0.0


class TestLLMClientInit:
    """Tests for LLMClient initialization."""

    def test_minimal_config(self) -> None:
        """Should handle minimal config."""
        client = LLMClient({"api_key": "key"})
        assert client.provider == "openai"
        assert client.model_name == "gpt-4o"
        assert client.api_key == "key"
        assert client.base_url is None
        assert client.temperature == 0.0

    def test_full_config(self) -> None:
        """Should handle full config."""
        config = {
            "provider": "anthropic",
            "model": "claude-3-opus",
            "api_key": "sk-abc123",
            "base_url": "https://api.example.com",
            "temperature": 0.7,
        }
        client = LLMClient(config)
        assert client.provider == "anthropic"
        assert client.model_name == "claude-3-opus"
        assert client.api_key == "sk-abc123"
        assert client.base_url == "https://api.example.com"
        assert client.temperature == 0.7


class TestLLMClientGetModel:
    """Tests for lazy model initialization."""

    @pytest.mark.asyncio
    async def test_openai_model_creation(self) -> None:
        """Should create OpenAI model on first access."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            model = client._get_model()
            assert model == mock_model
            mock_chat.assert_called_once()
            call_kwargs = mock_chat.call_args[1]
            assert call_kwargs["model"] == "gpt-4o"
            assert call_kwargs["api_key"] == "test"
            assert call_kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_anthropic_model_creation(self) -> None:
        """Should create Anthropic model on first access."""
        with patch("src.llm.client.ChatAnthropic") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            client = LLMClient({"provider": "anthropic", "model": "claude-3", "api_key": "test"})
            model = client._get_model()
            assert model == mock_model

    @pytest.mark.asyncio
    async def test_azure_model_creation(self) -> None:
        """Should create Azure model (via OpenAI) on first access."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            client = LLMClient({
                "provider": "azure",
                "model": "gpt-4",
                "api_key": "test",
                "base_url": "https://example.azure.com",
            })
            model = client._get_model()
            assert model == mock_model
            call_kwargs = mock_chat.call_args[1]
            assert "azure_endpoint" in call_kwargs

    @pytest.mark.asyncio
    async def test_invalid_provider_raises(self) -> None:
        """Should raise ValueError for unknown provider."""
        client = LLMClient({"provider": "unknown", "model": "gpt-4", "api_key": "test"})
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            client._get_model()

    @pytest.mark.asyncio
    async def test_model_caching(self) -> None:
        """Should cache the model instance."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            model1 = client._get_model()
            model2 = client._get_model()
            assert model1 is model2
            assert mock_chat.call_count == 1


class TestLLMClientCall:
    """Tests for the LLMClient.call method."""

    @pytest.mark.asyncio
    async def test_basic_call(self) -> None:
        """Should call the LLM with system and user messages."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            mock_response = MagicMock()
            mock_response.content = "test response"
            mock_model.ainvoke = AsyncMock(return_value=mock_response)

            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            result = await client.call("system prompt", "user prompt")
            assert result == "test response"

    @pytest.mark.asyncio
    async def test_call_with_structured_output(self) -> None:
        """Should use structured output for OpenAI."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            mock_with_structured = MagicMock()
            mock_model.with_structured_output.return_value = mock_with_structured
            mock_response = MagicMock()
            mock_response.content = '{"key": "value"}'
            mock_with_structured.ainvoke = AsyncMock(return_value=mock_response)

            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            schema = {"type": "object", "properties": {"key": {"type": "string"}}}
            result = await client.call("system", "user", response_format=schema)
            mock_model.with_structured_output.assert_called_once_with(schema)

    @pytest.mark.asyncio
    async def test_call_respects_temperature(self) -> None:
        """Should pass temperature to the model."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model
            mock_response = MagicMock()
            mock_response.content = "response"
            mock_model.ainvoke = AsyncMock(return_value=mock_response)

            client = LLMClient({
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test",
                "temperature": 0.7,
            })
            await client.call("system", "user")
            call_kwargs = mock_chat.call_args[1]
            assert call_kwargs["temperature"] == 0.7


class TestLLMClientCallJson:
    """Tests for the LLMClient.call_json method."""

    @pytest.mark.asyncio
    async def test_basic_json_response(self) -> None:
        """Should parse a basic JSON response."""
        with patch.object(LLMClient, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = '{"name": "test", "value": 42}'
            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            result = await client.call_json("system", "user")
            assert result == {"name": "test", "value": 42}

    @pytest.mark.asyncio
    async def test_json_from_code_block(self) -> None:
        """Should extract JSON from markdown code blocks."""
        with patch.object(LLMClient, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "```json\n{\"key\": \"value\"}\n```"
            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            result = await client.call_json("system", "user")
            assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_json_no_language_code(self) -> None:
        """Should extract JSON from code blocks without language specifier."""
        with patch.object(LLMClient, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "```\n{\"key\": \"value\"}\n```"
            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            result = await client.call_json("system", "user")
            assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self) -> None:
        """Should raise JSONDecodeError for invalid JSON."""
        with patch.object(LLMClient, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "This is not JSON"
            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            with pytest.raises(json.JSONDecodeError):
                await client.call_json("system", "user")


class TestLLMClientCallBatch:
    """Tests for the LLMClient.call_batch method."""

    @pytest.mark.asyncio
    async def test_batch_call(self) -> None:
        """Should process multiple prompts sequentially."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model

            call_counter = {"count": 0}

            async def ainvoke_side_effect(msgs):
                idx = call_counter["count"]
                call_counter["count"] += 1
                return MagicMock(content=f"response {idx + 1}")

            mock_model.ainvoke.side_effect = ainvoke_side_effect

            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            results = await client.call_batch(
                "system prompt",
                ["prompt 1", "prompt 2", "prompt 3"],
            )
            assert results == ["response 1", "response 2", "response 3"]
            assert call_counter["count"] == 3

    @pytest.mark.asyncio
    async def test_batch_call_empty(self) -> None:
        """Should handle empty prompts list."""
        with patch("src.llm.client.ChatOpenAI") as mock_chat:
            mock_model = MagicMock()
            mock_chat.return_value = mock_model

            client = LLMClient({"provider": "openai", "model": "gpt-4o", "api_key": "test"})
            results = await client.call_batch("system prompt", [])
            assert results == []
            assert mock_model.ainvoke.call_count == 0