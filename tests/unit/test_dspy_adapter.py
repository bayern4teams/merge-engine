"""Tests for src/llm/dspy_adapter.py - LLMClient DSPy BaseLM adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.client import LLMClient
from src.llm.cost import COST_LEDGER, SEMANTIC_CACHE
from src.llm.dspy_adapter import LLMClientDSPyAdapter


class TestDSPyAdapterDelegation:
    """Tests that LLMClientDSPyAdapter delegates directly to LLMClient."""

    def test_adapter_initialization(self):
        config = {"model": "gpt-4", "api_key": "test-key"}
        client = LLMClient(config)
        adapter = LLMClientDSPyAdapter(client)
        assert adapter.model == "gpt-4"
        assert adapter.client is client

    def test_forward_delegates_to_client_call(self):
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)
        adapter = LLMClientDSPyAdapter(client)

        with patch.object(client, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "Hello world"
            res = adapter("Hello user")
            mock_call.assert_called_once_with(system_prompt="", user_prompt="Hello user")
            assert res == ["Hello world"]

    def test_forward_messages_extraction(self):
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config)
        adapter = LLMClientDSPyAdapter(client)

        messages = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "What is 2+2?"},
        ]
        with patch.object(client, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "4"
            res = adapter(messages=messages)
            mock_call.assert_called_once_with(
                system_prompt="You are a helper.", user_prompt="What is 2+2?"
            )
            assert res == ["4"]


class TestDSPyAdapterQuotaFallback:
    """Tests that quota fallback chain is preserved through the adapter."""

    def test_quota_fallback_walks_chain(self):
        config = {
            "model": "primary",
            "fallback_models": ["second"],
            "api_key": "test",
            "cache_enabled": False,
        }
        client = LLMClient(config)
        adapter = LLMClientDSPyAdapter(client)

        failing_model = AsyncMock()
        failing_model.ainvoke = AsyncMock(
            side_effect=Exception("Error code: 403 - AllocationQuota.FreeTierOnly")
        )
        working_model = AsyncMock()
        working_model.ainvoke = AsyncMock(return_value=MagicMock(content="ok fallback"))

        with patch.object(client, "_get_model", side_effect=[failing_model, working_model]):
            res = adapter(prompt="test prompt")

        assert res == ["ok fallback"]
        assert client.model_name == "second"
        assert adapter.model == "second"


class TestDSPyAdapterCacheAndLedger:
    """Tests for SEMANTIC_CACHE and COST_LEDGER integration through adapter."""

    def test_cache_hit_on_second_call(self):
        SEMANTIC_CACHE.clear()
        config = {"model": "gpt-4", "api_key": "test", "cache_enabled": True}
        client = LLMClient(config)
        adapter = LLMClientDSPyAdapter(client)

        model = AsyncMock()
        model.ainvoke = AsyncMock(return_value=MagicMock(content="cached output"))

        with patch.object(client, "_get_model", return_value=model):
            res1 = adapter(prompt="same query")
            res2 = adapter(prompt="same query")

        assert res1 == ["cached output"]
        assert res2 == ["cached output"]
        assert model.ainvoke.call_count == 1  # Second call hit SEMANTIC_CACHE

    def test_cost_ledger_partitioned_by_run_id(self):
        run_id = "test-run-123"
        config = {
            "model": "gpt-4",
            "api_key": "test",
            "cache_enabled": False,
            "run_id": run_id,
        }
        client = LLMClient(config)
        adapter = LLMClientDSPyAdapter(client)

        mock_resp = MagicMock()
        mock_resp.content = "response"
        mock_resp.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        model = AsyncMock()
        model.ainvoke = AsyncMock(return_value=mock_resp)

        with patch.object(client, "_get_model", return_value=model):
            adapter(prompt="query for ledger")

        summary = COST_LEDGER.for_run(run_id)
        assert summary["calls"] >= 1
        assert summary["input_tokens"] >= 10
        assert summary["output_tokens"] >= 5


class TestDSPyAdapterTracing:
    """Tests that trace_config() callback injection is preserved."""

    def test_trace_config_injection(self):
        config = {"model": "gpt-4", "api_key": "test"}
        client = LLMClient(config, session_name="test-session")
        adapter = LLMClientDSPyAdapter(client)

        mock_resp = MagicMock()
        mock_resp.content = "traced response"
        model = AsyncMock()
        model.ainvoke = AsyncMock(return_value=mock_resp)

        with patch("src.llm.client.trace_config") as mock_trace_config:
            mock_trace_config.return_value = {"callbacks": []}
            with patch.object(client, "_get_model", return_value=model):
                adapter(prompt="trace test")
            mock_trace_config.assert_called_once_with(tags=["test-session"])

