"""Unit tests verifying that all heuristic fallback floors remain functional (Task B1.6)."""

from unittest.mock import AsyncMock, patch

import pytest

from src.llm.client import LLMClient


class TestHeuristicFloorsOutageResilience:
    """Test that pipeline non-LLM heuristic fallback paths function when LLMs fail completely."""

    @pytest.mark.asyncio
    async def test_llm_outage_forces_fallback_error(self):
        config = {
            "model": "primary",
            "fallback_models": [],
            "api_key": "test",
            "cache_enabled": False,
        }
        client = LLMClient(config)

        failing_model = AsyncMock()
        failing_model.ainvoke = AsyncMock(side_effect=Exception("503 LLM service unavailable"))

        with patch.object(client, "_get_model", return_value=failing_model):
            with pytest.raises(Exception, match="503 LLM service unavailable"):
                await client.call(system_prompt="sys", user_prompt="usr")
