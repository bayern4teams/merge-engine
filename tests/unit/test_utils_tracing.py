"""Unit tests for src/utils/tracing.py's span() helper (Part 1.5 C.4).

Neither raw OpenTelemetry nor a direct Langfuse client call actually nests a
custom span under its LangGraph node in this app — both were tried and
verified live to fail (see the module docstring for the full story: OTel
spans get filtered out by Langfuse's own instrumentation-scope allow-list,
and `context.attach()`-based nesting isn't visible inside a node's own
coroutine body). What does work, verified live, is driving the same
`on_chain_start`/`on_chain_end` callback-manager lifecycle a LangChain
Runnable would, via the ambient `AsyncCallbackManager` reached through
`ensure_config(None)["callbacks"]`. These tests cover the no-op path (no
LANGFUSE_PUBLIC_KEY) and the exception-propagation contract, since a tracing
helper that ever swallows the caller's own exception is a correctness bug,
not just an observability gap.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.tracing import span


class TestSpanDisabled:
    async def test_noop_when_no_public_key(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        entered = False
        async with span("some.span", attr=1):
            entered = True
        assert entered

    async def test_yields_a_safe_stand_in_callers_can_update_unconditionally(
        self, monkeypatch: Any
    ) -> None:
        """Every call site does `async with span(...) as obs: ...;
        obs.update(output=stats)` without an `if obs is not None` guard -
        the disabled path must yield something .update()-able, not None."""
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        async with span("some.span") as obs:
            obs.update(output={"whatever": 1})  # must not raise

    async def test_noop_does_not_configure_a_callback_manager(self, monkeypatch: Any) -> None:
        """No LANGFUSE_PUBLIC_KEY means span() must return before ever
        touching LangChain's callback machinery — cheap enough to call
        when tracing is off."""
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        with patch("langchain_core.callbacks.manager.AsyncCallbackManager.configure") as mock_cfg:
            async with span("some.span"):
                pass
        mock_cfg.assert_not_called()


class TestSpanEnabled:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-fake")

    async def test_calls_on_chain_start_then_on_chain_end(self) -> None:
        run_manager = MagicMock()
        run_manager.on_chain_end = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.on_chain_start = AsyncMock(return_value=run_manager)
        ambient_callbacks = MagicMock(name="ambient-callback-manager")

        with (
            patch(
                "langchain_core.runnables.config.ensure_config",
                return_value={"callbacks": ambient_callbacks},
            ),
            patch(
                "langchain_core.callbacks.manager.AsyncCallbackManager.configure",
                return_value=mock_manager,
            ) as mock_configure,
        ):
            async with span("resolve.company.blocking", candidate_pairs=411):
                pass

        mock_configure.assert_called_once_with(inheritable_callbacks=ambient_callbacks)
        mock_manager.on_chain_start.assert_called_once_with(
            {"name": "resolve.company.blocking"}, {"candidate_pairs": 411}
        )
        run_manager.on_chain_end.assert_called_once_with({})

    async def test_update_output_is_passed_to_on_chain_end(self) -> None:
        """`obs.update(output=blocking_stats)` inside the block must reach
        `on_chain_end`, so the span records the phase's real results."""
        run_manager = MagicMock()
        run_manager.on_chain_end = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.on_chain_start = AsyncMock(return_value=run_manager)

        with (
            patch("langchain_core.runnables.config.ensure_config", return_value={}),
            patch(
                "langchain_core.callbacks.manager.AsyncCallbackManager.configure",
                return_value=mock_manager,
            ),
        ):
            async with span("resolve.company.blocking") as obs:
                obs.update(output={"candidate_pairs": 411})

        run_manager.on_chain_end.assert_called_once_with({"output": {"candidate_pairs": 411}})

    async def test_caller_exception_is_not_swallowed(self) -> None:
        """The defining correctness test: a real failure inside `async with
        span(...):` must propagate to the caller unchanged, never be
        caught and replaced by tracing's own no-op fallback."""
        run_manager = MagicMock()
        run_manager.on_chain_error = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.on_chain_start = AsyncMock(return_value=run_manager)

        with (
            patch("langchain_core.runnables.config.ensure_config", return_value={}),
            patch(
                "langchain_core.callbacks.manager.AsyncCallbackManager.configure",
                return_value=mock_manager,
            ),
        ):
            with pytest.raises(ValueError, match="boom"):
                async with span("resolve.company.blocking"):
                    raise ValueError("boom")

        # on_chain_error must have been called with the real exception, not
        # silently skipped - that's what lets the span show as failed in
        # the Langfuse UI instead of silently looking successful.
        run_manager.on_chain_error.assert_called_once()
        (exc_arg,) = run_manager.on_chain_error.call_args.args
        assert isinstance(exc_arg, ValueError)
        assert str(exc_arg) == "boom"
        run_manager.on_chain_end.assert_not_called()

    async def test_on_chain_start_failure_degrades_to_untraced(self) -> None:
        """If building the callback manager or starting the run raises
        (e.g. no ambient graph run to nest under), the caller's code must
        still run — tracing setup failures never break a merge run — and
        still yield something .update()-able."""
        mock_manager = MagicMock()
        mock_manager.on_chain_start = AsyncMock(side_effect=RuntimeError("no active run"))

        with (
            patch("langchain_core.runnables.config.ensure_config", return_value={}),
            patch(
                "langchain_core.callbacks.manager.AsyncCallbackManager.configure",
                return_value=mock_manager,
            ),
        ):
            entered = False
            async with span("resolve.company.blocking") as obs:
                entered = True
                obs.update(output={"whatever": 1})  # must not raise
        assert entered

    async def test_configure_failure_degrades_to_untraced(self) -> None:
        with (
            patch("langchain_core.runnables.config.ensure_config", return_value={}),
            patch(
                "langchain_core.callbacks.manager.AsyncCallbackManager.configure",
                side_effect=RuntimeError("boom"),
            ),
        ):
            entered = False
            async with span("resolve.company.blocking") as obs:
                entered = True
                obs.update(output={"whatever": 1})  # must not raise
        assert entered

    async def test_attributes_pass_through_as_chain_start_inputs(self) -> None:
        run_manager = MagicMock()
        run_manager.on_chain_end = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.on_chain_start = AsyncMock(return_value=run_manager)

        with (
            patch("langchain_core.runnables.config.ensure_config", return_value={}),
            patch(
                "langchain_core.callbacks.manager.AsyncCallbackManager.configure",
                return_value=mock_manager,
            ),
        ):
            async with span("resolve.company.blocking", stats={"a": 1, "b": [1, 2]}):
                pass

        _, inputs = mock_manager.on_chain_start.call_args.args
        assert inputs == {"stats": {"a": 1, "b": [1, 2]}}
