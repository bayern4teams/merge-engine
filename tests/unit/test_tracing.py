"""Unit tests for src/llm/tracing.py (Part 1.5 C.2).

No coverage existed for this module before Part 1.5 — these specifically
target the nesting bug: trace_config() must INHERIT an ambient callback
manager that already carries real handlers (a LangGraph node's own config)
rather than replacing it, and must MERGE langfuse_* metadata into the
ambient metadata rather than overwriting langgraph_node/langgraph_step.
Only when there is nothing to inherit (standalone script/test) should it
attach its own handler, matching the pre-Part-1.5 behaviour.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.callbacks import AsyncCallbackManager, CallbackManager
from langchain_core.runnables.config import var_child_runnable_config

import src.llm.tracing as tracing_mod
from src.llm.tracing import _has_active_callbacks, trace_config, trace_id_for_session


class TestHasActiveCallbacks:
    """_has_active_callbacks must not be fooled by LangGraph's empty
    AsyncCallbackManager, which is injected into every node's ambient
    config even when the graph run supplied no callbacks at all."""

    def test_none_is_inactive(self) -> None:
        assert _has_active_callbacks(None) is False

    def test_empty_list_is_inactive(self) -> None:
        assert _has_active_callbacks([]) is False

    def test_nonempty_list_is_active(self) -> None:
        assert _has_active_callbacks([MagicMock()]) is True

    def test_manager_with_no_handlers_is_inactive(self) -> None:
        # This is the exact shape LangGraph injects when no callbacks were
        # ever supplied to the graph run — verified live against langgraph:
        # the manager object itself is truthy (no __bool__/__len__), so a
        # bare `bool(callbacks)` check would get this wrong.
        mgr = AsyncCallbackManager(handlers=[])
        assert bool(mgr) is True  # sanity: confirms the trap this guards against
        assert _has_active_callbacks(mgr) is False

    def test_manager_with_handlers_is_active(self) -> None:
        mgr = CallbackManager(handlers=[MagicMock()])
        assert _has_active_callbacks(mgr) is True


@pytest.fixture(autouse=True)
def _reset_tracing_singleton():
    """_handler/_initialized/_last_attempt_monotonic/_no_key_at_startup are
    module-level state (Part 1.5 C.6 added the latter two) — reset between
    tests so one test's mocked handler or retry timer doesn't leak into the
    next."""
    tracing_mod._handler = None
    tracing_mod._initialized = False
    tracing_mod._last_attempt_monotonic = 0.0
    tracing_mod._no_key_at_startup = False
    yield
    tracing_mod._handler = None
    tracing_mod._initialized = False
    tracing_mod._last_attempt_monotonic = 0.0
    tracing_mod._no_key_at_startup = False


class TestTraceConfigInheritance:
    """The core Part 1.5 C.2 fix: inherit ambient tracing, don't replace it."""

    def test_inside_active_graph_run_does_not_attach_own_handler(self) -> None:
        """The defining regression test: LLMClient.call()'s trace_config()
        must not carry a 'callbacks' key when a node's own callback manager
        (with a real handler already in it) is already ambient — an
        explicit 'callbacks' key REPLACES the parent's in LangChain's
        config merge, which is exactly what broke GENERATION-under-CHAIN
        nesting before this fix."""
        parent_handler = MagicMock()
        ambient = {
            "callbacks": [parent_handler],
            "metadata": {"langgraph_node": "resolve_entities", "langgraph_step": 7},
        }
        token = var_child_runnable_config.set(ambient)
        try:
            cfg = trace_config(tags=["merge-engine"])
        finally:
            var_child_runnable_config.reset(token)

        assert "callbacks" not in cfg
        assert cfg["metadata"]["langgraph_node"] == "resolve_entities"
        assert cfg["metadata"]["langgraph_step"] == 7
        assert cfg["metadata"]["langfuse_tags"] == ["merge-engine"]

    def test_inside_active_graph_run_merges_tags_not_replaces(self) -> None:
        """Ambient langfuse_tags (e.g. from an earlier trace_config call in
        the same node) accumulate rather than get clobbered."""
        ambient = {
            "callbacks": [MagicMock()],
            "metadata": {"langfuse_tags": ["existing-tag"]},
        }
        token = var_child_runnable_config.set(ambient)
        try:
            cfg = trace_config(tags=["merge-engine"])
        finally:
            var_child_runnable_config.reset(token)

        assert set(cfg["metadata"]["langfuse_tags"]) == {"existing-tag", "merge-engine"}

    def test_inside_active_graph_run_no_new_metadata_returns_empty(self) -> None:
        """No session_id, no tags, nothing to add on top of the ambient
        metadata: returning {} is equivalent and cheaper than returning an
        identical metadata dict."""
        ambient = {"callbacks": [MagicMock()], "metadata": {"langgraph_node": "x"}}
        token = var_child_runnable_config.set(ambient)
        try:
            cfg = trace_config()
        finally:
            var_child_runnable_config.reset(token)

        assert cfg == {}

    def test_standalone_no_ambient_callbacks_attaches_own_handler(self) -> None:
        """No graph run in progress (e.g. tests/prompts/harness.py): attach
        a handler and set session_id/tags fresh. Part 1.5 C.3: bound to a
        trace id derived from session_id (not the bare no-context
        singleton), so repeated top-level runs for the same session — the
        initial run plus every HITL resume — rejoin one Langfuse trace
        instead of each rooting a new one."""
        fake_handler = MagicMock()
        with patch(
            "src.llm.tracing.get_trace_handler_for_trace", return_value=fake_handler
        ) as mock_get:
            cfg = trace_config(session_id="wf_abc123", tags=["merge-engine"])

        mock_get.assert_called_once_with(trace_id_for_session("wf_abc123"))
        assert cfg["callbacks"] == [fake_handler]
        assert cfg["metadata"]["langfuse_session_id"] == "wf_abc123"
        assert cfg["metadata"]["langfuse_tags"] == ["merge-engine"]

    def test_standalone_without_session_id_uses_bare_singleton(self) -> None:
        """No session_id at all (no thread id to key a trace on): fall back
        to the plain no-context handler exactly as before C.3."""
        fake_handler = MagicMock()
        with patch("src.llm.tracing.get_trace_handler", return_value=fake_handler):
            cfg = trace_config(tags=["merge-engine"])

        assert cfg["callbacks"] == [fake_handler]

    def test_standalone_tracing_disabled_returns_empty(self) -> None:
        """LANGFUSE_PUBLIC_KEY unset (or auth failed): still a no-op,
        exactly as before this fix."""
        with patch("src.llm.tracing.get_trace_handler", return_value=None):
            cfg = trace_config(session_id="wf_abc123")
        assert cfg == {}

    def test_ambient_manager_with_no_handlers_falls_back_to_standalone_path(self) -> None:
        """A graph run with NO callbacks bound at all (e.g. rehydrate_workflow
        before its own Part 1.5 C.3 fix, or any bare {"configurable": ...}
        config) must still get its own handler attached — LangGraph's empty
        AsyncCallbackManager must not be mistaken for 'already traced'."""
        fake_handler = MagicMock()
        ambient = {"callbacks": AsyncCallbackManager(handlers=[]), "metadata": {}}
        token = var_child_runnable_config.set(ambient)
        try:
            with patch("src.llm.tracing.get_trace_handler_for_trace", return_value=fake_handler):
                cfg = trace_config(session_id="wf_xyz")
        finally:
            var_child_runnable_config.reset(token)

        assert cfg["callbacks"] == [fake_handler]
        assert cfg["metadata"]["langfuse_session_id"] == "wf_xyz"


class TestGetTraceHandler:
    """Unchanged behaviour (not touched by C.2), but previously untested."""

    def test_disabled_when_no_public_key(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        assert tracing_mod.get_trace_handler() is None

    def test_one_shot_latch_does_not_recheck(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        assert tracing_mod.get_trace_handler() is None
        # Setting the env var after the first call must not matter - this
        # documents the existing one-shot-latch behaviour (tracing.py:47-49),
        # not a Part 1.5 change.
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        assert tracing_mod.get_trace_handler() is None


class TestGetTraceHandlerRetry:
    """Part 1.5 C.6: unlike the no-key case (still a permanent latch, see
    TestGetTraceHandler above), a key that IS present but whose Langfuse
    stack is unreachable must retry on a bounded schedule — not stay dead
    for the rest of the process if the stack comes up moments later."""

    def test_auth_failure_does_not_retry_before_the_interval(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        mock_client = MagicMock()
        mock_client.auth_check.return_value = False
        with patch("langfuse.get_client", return_value=mock_client):
            assert tracing_mod.get_trace_handler() is None
            assert tracing_mod.get_trace_handler() is None  # immediately again
        assert mock_client.auth_check.call_count == 1  # second call was suppressed

    def test_auth_failure_retries_after_the_interval_elapses(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        mock_client = MagicMock()
        mock_client.auth_check.return_value = False
        with patch("langfuse.get_client", return_value=mock_client):
            assert tracing_mod.get_trace_handler() is None
            # Simulate the retry window having elapsed.
            tracing_mod._last_attempt_monotonic -= tracing_mod._RETRY_INTERVAL_SECONDS + 1
            assert tracing_mod.get_trace_handler() is None
        assert mock_client.auth_check.call_count == 2

    def test_retry_succeeds_once_the_stack_comes_up(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        failing_client = MagicMock()
        failing_client.auth_check.return_value = False
        with patch("langfuse.get_client", return_value=failing_client):
            assert tracing_mod.get_trace_handler() is None

        tracing_mod._last_attempt_monotonic -= tracing_mod._RETRY_INTERVAL_SECONDS + 1
        working_client = MagicMock()
        working_client.auth_check.return_value = True
        fake_handler = MagicMock()
        with (
            patch("langfuse.get_client", return_value=working_client),
            patch("langfuse.langchain.CallbackHandler", return_value=fake_handler),
        ):
            assert tracing_mod.get_trace_handler() is fake_handler
        # And is now cached -- no further auth_check calls needed.
        assert tracing_mod.get_trace_handler() is fake_handler
        assert working_client.auth_check.call_count == 1

    def test_setup_exception_also_retries_after_the_interval(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        with patch("langfuse.get_client", side_effect=RuntimeError("connection refused")):
            assert tracing_mod.get_trace_handler() is None
            assert tracing_mod.get_trace_handler() is None  # suppressed, too soon

        tracing_mod._last_attempt_monotonic -= tracing_mod._RETRY_INTERVAL_SECONDS + 1
        mock_client = MagicMock()
        mock_client.auth_check.return_value = True
        fake_handler = MagicMock()
        with (
            patch("langfuse.get_client", return_value=mock_client),
            patch("langfuse.langchain.CallbackHandler", return_value=fake_handler),
        ):
            assert tracing_mod.get_trace_handler() is fake_handler


class TestTraceIdForSession:
    """Part 1.5 C.3: the deterministic trace id every top-level run for a
    thread must agree on, with no persisted state, so HITL resumes rejoin
    the initial run's trace."""

    def test_deterministic_for_same_seed(self) -> None:
        assert trace_id_for_session("wf_abc123") == trace_id_for_session("wf_abc123")

    def test_different_for_different_seeds(self) -> None:
        assert trace_id_for_session("wf_abc123") != trace_id_for_session("wf_xyz789")

    def test_is_a_valid_langfuse_trace_id(self) -> None:
        trace_id = trace_id_for_session("wf_abc123")
        assert len(trace_id) == 32
        int(trace_id, 16)  # must not raise - 32 lowercase hex chars


class TestGetTraceHandlerForTrace:
    """get_trace_handler_for_trace must share get_trace_handler's enablement
    gate (public key / auth check) but bind a fresh handler to the given
    trace id rather than reusing the no-context singleton."""

    def test_disabled_returns_none(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        assert tracing_mod.get_trace_handler_for_trace("some-trace-id") is None

    def test_enabled_binds_the_given_trace_id(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-fake")
        with (
            patch("src.llm.tracing.get_trace_handler", return_value=MagicMock()),
            patch("langfuse.langchain.CallbackHandler") as mock_handler_cls,
        ):
            tracing_mod.get_trace_handler_for_trace("deadbeef" * 4)

        mock_handler_cls.assert_called_once_with(trace_context={"trace_id": "deadbeef" * 4})
