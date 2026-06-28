"""Unit tests for parse_request node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.parse_request import parse_request


class TestParseRequest:
    """Tests for the parse_request node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all people and companies from our CRM",
            "target_entities": [],
            "llm_config": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test_key",
            },
            "errors": [],
        }

    @pytest.fixture
    def mock_llm_client(self) -> MagicMock:
        """Mock LLMClient for testing."""
        client = MagicMock()
        client.call_json = AsyncMock(return_value=["person", "company"])
        return client

    @pytest.mark.asyncio
    async def test_parse_request_success_list_response(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request with a list response from LLM."""
        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Extract entities: {user_request}"},
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == ["person", "company"]
        assert "errors" not in result or not result.get("errors")
        mock_llm_client.call_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_request_success_dict_response(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request with a dict response from LLM."""
        mock_llm_client.call_json = AsyncMock(
            return_value={"entities": ["person", "company"]}
        )

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Extract entities: {user_request}"},
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == ["person", "company"]

    @pytest.mark.asyncio
    async def test_parse_request_dict_response_with_target_entities_key(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request with target_entities key in dict response."""
        mock_llm_client.call_json = AsyncMock(
            return_value={"target_entities": ["person", "company", "order"]}
        )

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Extract entities: {user_request}"},
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == ["person", "company", "order"]

    @pytest.mark.asyncio
    async def test_parse_request_empty_dict_response(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request with empty dict response falls back to empty list."""
        mock_llm_client.call_json = AsyncMock(return_value={})

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Extract entities: {user_request}"},
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == []

    @pytest.mark.asyncio
    async def test_parse_request_missing_prompt_template_uses_fallback(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request falls back to default prompt when template missing."""
        mock_llm_client.call_json = AsyncMock(return_value=["person"])

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {},  # Empty - no request_parsing template
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == ["person"]
        mock_llm_client.call_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_request_llm_failure_returns_empty_with_error(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request handles LLM failure gracefully."""
        mock_llm_client.call_json = AsyncMock(
            side_effect=Exception("LLM service unavailable")
        )

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Extract entities: {user_request}"},
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == []
        assert len(result.get("errors", [])) == 1
        assert "Request parsing failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_parse_request_unexpected_result_type(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request handles unexpected LLM result type."""
        mock_llm_client.call_json = AsyncMock(return_value="string_not_list_or_dict")

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Extract entities: {user_request}"},
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == []

    @pytest.mark.asyncio
    async def test_parse_request_prompt_formatting(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test that the prompt is correctly formatted with user_request."""
        mock_llm_client.call_json = AsyncMock(return_value=["person"])

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Parse this: {user_request}"},
        ):
            await parse_request(base_state)

        call_args = mock_llm_client.call_json.call_args
        assert "Import all people and companies from our CRM" in call_args[1]["user_prompt"]

    @pytest.mark.asyncio
    async def test_parse_request_system_prompt_passed(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test that the system prompt is correctly passed to LLM."""
        mock_llm_client.call_json = AsyncMock(return_value=["person"])

        with patch(
            "src.orchestrator.nodes.parse_request.LLMClient",
            return_value=mock_llm_client,
        ), patch(
            "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
            {"request_parsing": "Extract entities: {user_request}"},
        ):
            await parse_request(base_state)

        call_args = mock_llm_client.call_json.call_args
        assert (
            "You extract entity types from user requests"
            in call_args[1]["system_prompt"]
        )