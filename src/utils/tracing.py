"""Generic tracing helper — Part 1.5 C.4.

An ``async with span(...)`` context manager any layer from
``src.mcp_server.connectors`` upward may use to add a named span nested
under whatever LangGraph node is currently executing, without owning the
Langfuse callback-handler wiring (that stays in ``src.llm.tracing``, which
this module never imports — see Layering below).

**Not raw OpenTelemetry, and not a direct Langfuse client call — both were
tried and verified NOT to work.**

A span opened via the plain ``opentelemetry`` API
(``trace.get_tracer(...).start_as_current_span(...)``) nests correctly under
the ambient OTel context and gets a real SDK span object, but Langfuse's own
``LangfuseSpanProcessor`` silently drops it at export time: it filters every
span by ``instrumentation_scope.name`` against an allow-list of Langfuse's
own tracer name plus known LLM-library instrumentations
(``langfuse/_client/span_filter.py``), and an arbitrary third-party scope
name like ``"merge-engine"`` is not on that list. Verified live: 0 of 2
raw-OTel spans reached the Langfuse UI.

Calling Langfuse's own ``get_client().start_as_current_observation(...)``
instead avoids the filter, but still failed to nest in the real pipeline —
verified live, twice: every ``resolve.<entity>.<phase>`` span landed as its
own **orphan root trace** (``session_id=None``, no parent), even though a
standalone script with no LangGraph run in progress showed correct nesting.
Root cause, reproduced directly: the Langfuse LangChain ``CallbackHandler``
attaches the node's span via raw OTel ``context.attach(...)`` inside
``on_chain_start`` — but that attached contextvar value is **not visible
inside the node function's own coroutine body**
(``opentelemetry.trace.get_current_span()`` returns an invalid span context
there, confirmed with a minimal LangGraph repro). LangGraph's node execution
does not run in the same context capture as the callback event that
attached it, so anything relying on ambient *OTel* context to find its
parent gets none.

What **does** correctly nest — proven by the ``ChatOpenAI`` GENERATION spans
already landing under their node, and confirmed directly for a custom span
— is LangChain's own callback-manager parent tracking, reached via
``ensure_config(None)["callbacks"]`` (the same ambient lookup
``src.llm.tracing.trace_config`` already relies on). That ambient object is
an ``AsyncCallbackManager`` whose ``parent_run_id`` is the current node's own
run — so driving the *same* ``on_chain_start``/``on_chain_end`` lifecycle a
LangChain ``Runnable`` would (via ``AsyncCallbackManager.configure(
inheritable_callbacks=ambient_callbacks)``) makes the Langfuse handler emit a
properly nested child ``CHAIN`` span, verified live: parent observation id
matches the node's own id exactly.

Tracing is a no-op when ``LANGFUSE_PUBLIC_KEY`` is unset, mirroring
``src.llm.tracing.get_trace_handler``'s own gate — checked independently
here (not imported from there) specifically so this module never reaches
upward into ``src.llm``.

Layering (``scripts/lint_arch.py``): lives in ``src/utils``, the lowest
layer, so connectors can import it without reaching into ``src/llm``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

logger = logging.getLogger("merge-engine.utils.tracing")


class _NullObservation:
    """Yielded in place of a real span when tracing is off or failed.

    Callers do ``async with span(...) as obs: ...; obs.update(output=stats)``
    unconditionally — this makes that always safe, so a call site never
    needs an ``if obs is not None`` guard around attaching its output.
    """

    def update(self, **_kwargs: Any) -> None:
        return None


_NULL_OBSERVATION = _NullObservation()


class _RunManagerObservation:
    """Adapts a LangChain child-run manager to span()'s ``.update(output=...)``
    contract — the run manager itself only accepts the full output dict at
    ``on_chain_end`` time, so calls to ``.update()`` accumulate into it."""

    def __init__(self, run_manager: Any) -> None:
        self._run_manager = run_manager
        self.output: dict[str, Any] = {}

    def update(self, **kwargs: Any) -> None:
        self.output.update(kwargs)


@asynccontextmanager
async def span(name: str, **attributes: Any) -> AsyncIterator[Any]:
    """Open a named span nested under the current LangGraph node, if any.

    Yields the observation stand-in (with an ``.update(output=...)`` method
    for attaching results computed only after the wrapped code finishes)
    when tracing is active, or a no-op stand-in otherwise — so every call
    site can call ``.update(...)`` unconditionally.

    A true no-op when ``LANGFUSE_PUBLIC_KEY`` is unset. Any failure while
    tracing (no ambient graph run, callback setup error) is swallowed and
    logged at DEBUG, never allowed to break the caller — matching
    ``src.llm.tracing``'s "tracing never blocks or breaks a merge run" rule.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        yield _NULL_OBSERVATION
        return

    # Only span SETUP is guarded — once the `async with` block below is
    # entered, any exception raised by the caller's own code must propagate
    # normally through it, not be swallowed here as if it were a tracing
    # failure.
    run_manager = None
    try:
        from langchain_core.callbacks.manager import AsyncCallbackManager
        from langchain_core.runnables.config import ensure_config

        ambient_callbacks = ensure_config(None).get("callbacks")
        manager = AsyncCallbackManager.configure(inheritable_callbacks=ambient_callbacks)
        run_manager = await manager.on_chain_start({"name": name}, dict(attributes))
    except Exception as exc:
        logger.debug("Custom span '%s' setup failed (%s) — continuing untraced.", name, exc)
        run_manager = None

    if run_manager is None:
        yield _NULL_OBSERVATION
        return

    observation = _RunManagerObservation(run_manager)
    try:
        yield observation
    except BaseException as exc_value:
        # Record the caller's real failure on the span (so it shows as an
        # error in the Langfuse UI, not a silent success) before it
        # continues propagating — then re-raise unchanged.
        with suppress(Exception):
            await run_manager.on_chain_error(exc_value)
        raise
    else:
        with suppress(Exception):
            await run_manager.on_chain_end(observation.output)
