"""Langfuse tracing setup (self-hosted, fully local).

One shared LangChain ``CallbackHandler`` is bound to every LLM call (through
``LLMClient``) and to the LangGraph run config, so a single pipeline run
produces one Langfuse trace with a span per orchestrator node and nested LLM
generation spans underneath.

Tracing is a no-op when ``LANGFUSE_PUBLIC_KEY`` is unset (e.g. CI, or when the
local Langfuse stack isn't running) — callers always get a plain dict back
from :func:`trace_config`, so nothing breaks without the stack.

The Langfuse stack is started separately (``docker/langfuse-compose.yml``);
credentials come from ``.env`` (``LANGFUSE_PUBLIC_KEY``/``LANGFUSE_SECRET_KEY``/
``LANGFUSE_HOST``). Langfuse must be imported *after* those env vars are loaded,
hence the ``load_dotenv()`` below runs at import time.

Part 1.5 C.0/C.2: ``trace_config`` used to unconditionally build a fresh
``{"callbacks": [handler], "metadata": {...}}`` dict. LangChain's
``ensure_config`` REPLACES (not merges) the ``callbacks`` and ``metadata``
keys of the ambient config with whatever an explicit config supplies —
verified directly against the installed ``langchain-core`` — so every LLM
call was silently discarding the parent node's callback manager (breaking
generation-under-node nesting) and the ``langgraph_node``/``langgraph_step``
metadata that would associate a generation with its node at all. Fixed by
inheriting the ambient callback manager whenever one with real handlers is
already active (inside a traced graph run) and merging langfuse_* keys into
the ambient metadata instead of replacing it; a handler is attached here
only when there is no ambient tracing to inherit (standalone scripts,
``tests/prompts/harness.py``, or an untraced graph config).

Part 1.5 C.3: a HITL-gated run is many separate top-level ``astream()``
calls (one per resume — see ``docs/architecture/orchestration_and_ui.md``'s
HITL resume trap), and LangChain always roots a fresh top-level call as a
NEW trace unless told otherwise. ``langfuse-langchain``'s own automatic
resume-trace-linkage (``CallbackHandler._is_langgraph_resume``) only
triggers for an explicit ``Command(resume=...)`` invocation; this app
resumes via ``graph.update_state(..., as_node=...)`` instead, so that
linkage never engaged — verified live: a full Boeing run produced 5
separate traces sharing one ``session_id``, not one trace. Fixed by binding
every top-level run's handler to a trace id *deterministically derived from
the thread id* (``Langfuse.create_trace_id(seed=thread_id)``, a pure hash —
same thread id always yields the same trace id, no cross-process state to
persist) via ``CallbackHandler(trace_context={"trace_id": ...})``, so the
initial run and every resume attach to the same trace as sibling root spans.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from langchain_core.runnables.config import ensure_config

logger = logging.getLogger("merge-engine.llm.tracing")

# Load .env before Langfuse reads its credentials from the environment.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


_handler: Any = None
_initialized = False
_last_attempt_monotonic: float = 0.0
# Sticky, separate from _initialized: determined once, on whichever call
# first runs. If LANGFUSE_PUBLIC_KEY was absent then, it stays a permanent
# "tracing disabled" answer even if the env var is set later mid-process —
# real deployments load it once via .env at startup and don't change it, and
# this keeps that case's behaviour exactly as it was before C.6 (still
# covered by test_one_shot_latch_does_not_recheck). Only a key that WAS
# present but whose stack was unreachable gets the bounded retry below.
_no_key_at_startup = False

# Part 1.5 C.6: `_initialized` used to be a permanent one-shot latch — if
# the app started before the Langfuse stack (docker compose up not yet
# finished, or not yet started at all), the FIRST auth_check failure
# disabled tracing for the rest of the process's lifetime, even once the
# stack came up seconds later. This is exactly the failure mode behind the
# Phase 0 baseline's `WinError 10061` (BASELINE_V1.md), and on a demo it
# looks like tracing is simply broken. Retrying on a bounded interval — not
# every call, which would mean every traced LLM call/node pays a network
# auth_check when the stack is genuinely down — fixes it without adding
# unbounded retry cost. Only the "key present but stack unreachable" case
# retries; no public key at all is a deliberate, stable "tracing disabled"
# state that re-checking cannot change.
_RETRY_INTERVAL_SECONDS = 60.0


def get_trace_handler() -> Any:
    """Return the shared Langfuse callback handler, or ``None`` if disabled.

    Lazily initialized and cached once a handler is successfully created.
    Returns ``None`` when no public key is configured (stable — never
    retried) or when the local Langfuse stack can't be authenticated
    against (retried at most once per ``_RETRY_INTERVAL_SECONDS``) —
    tracing never blocks or breaks a merge run either way.
    """
    global _handler, _initialized, _last_attempt_monotonic, _no_key_at_startup
    if _initialized and _handler is not None:
        return _handler
    if _no_key_at_startup:
        return None

    if not _initialized and not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        logger.info("Langfuse tracing disabled (LANGFUSE_PUBLIC_KEY not set).")
        _initialized = True
        _no_key_at_startup = True
        return None

    if _initialized:
        now = time.monotonic()
        if now - _last_attempt_monotonic < _RETRY_INTERVAL_SECONDS:
            return None
        logger.info("Retrying Langfuse connection (previous attempt failed).")
    _initialized = True
    _last_attempt_monotonic = time.monotonic()

    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        client = get_client()  # reads LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST
        if not client.auth_check():
            logger.warning(
                "Langfuse auth_check failed (host=%s) — tracing disabled for now "
                "(retrying every %.0fs). Is the stack up? `docker compose -f "
                "docker/langfuse-compose.yml --env-file docker/langfuse.env up -d`",
                os.environ.get("LANGFUSE_HOST"),
                _RETRY_INTERVAL_SECONDS,
            )
            _handler = None
            return None

        _handler = CallbackHandler()
        logger.info(
            "Langfuse tracing enabled (host=%s, project via public key).",
            os.environ.get("LANGFUSE_HOST"),
        )
    except Exception as exc:  # never let tracing setup crash the app
        logger.warning(
            "Langfuse tracing setup failed (%s) — continuing untraced for now "
            "(retrying every %.0fs).",
            exc,
            _RETRY_INTERVAL_SECONDS,
        )
        _handler = None

    return _handler


def trace_id_for_session(session_id: str) -> str:
    """Deterministic Langfuse trace id for a given session (workflow thread id).

    A pure hash of the seed (``Langfuse.create_trace_id`` is a ``@staticmethod``,
    no network call) — the same thread id always yields the same trace id, which
    is what lets every HITL resume's separate top-level ``astream()`` call
    rejoin the same trace with no persisted state (see module docstring, C.3).
    """
    from langfuse import Langfuse

    return Langfuse.create_trace_id(seed=session_id)


def get_trace_handler_for_trace(trace_id: str) -> Any:
    """Like :func:`get_trace_handler`, but returns a fresh handler bound to
    ``trace_id`` instead of the shared no-context singleton.

    Reuses ``get_trace_handler()`` purely for its enablement gate (public key
    present, auth check passes, logged once) — constructing a
    ``CallbackHandler`` is cheap (no I/O; verified against the installed SDK),
    so a new one per top-level run is not a real cost.
    """
    if get_trace_handler() is None:
        return None

    from langfuse.langchain import CallbackHandler

    return CallbackHandler(trace_context={"trace_id": trace_id})


def _has_active_callbacks(callbacks: Any) -> bool:
    """True when ``callbacks`` (a ``RunnableConfig["callbacks"]`` value)
    already carries at least one real handler.

    LangGraph injects an ``AsyncCallbackManager`` into every node's ambient
    config even when the graph run supplied no callbacks at all — verified
    directly: ``handlers == []`` but the manager object itself is still
    truthy (no ``__bool__``/``__len__``), so a bare ``bool(callbacks)`` check
    would wrongly treat every graph run as already traced.
    """
    if callbacks is None:
        return False
    handlers = getattr(callbacks, "handlers", None)
    if handlers is not None:
        return bool(handlers)
    if isinstance(callbacks, list):
        return bool(callbacks)
    return False


def trace_config(
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build a LangChain ``RunnableConfig`` fragment carrying the trace handler.

    Merge the result into the config passed to ``ainvoke``/``astream``. Returns
    an empty dict when tracing is disabled, so it's always safe to spread.

    Part 1.5 C.2: when called from inside an already-traced LangGraph node
    (the ambient config already carries a real callback manager — bound once,
    at the graph level, by ``WorkflowManager``), this INHERITS that manager
    rather than replacing it, and merges the langfuse_* keys into the
    ambient metadata rather than overwriting it. That is what makes an LLM
    call's GENERATION span nest under its node's CHAIN span, and keeps the
    node's own ``langgraph_node``/``langgraph_step`` metadata attached to the
    generation. A handler is attached here (the pre-Part-1.5 behaviour)
    only when there is nothing to inherit — a standalone script or test
    (``tests/prompts/harness.py``) with no surrounding graph run.

    Parameters
    ----------
    session_id : str, optional
        Groups a run's initial execution and all its HITL-resume continuations
        under one Langfuse session (pass the workflow ``thread_id``). Only
        used when attaching a fresh handler — inside an active graph run the
        session id was already set once, at the graph level.
    tags : list[str], optional
        Langfuse trace tags, merged into whatever langfuse_tags the ambient
        metadata may already carry.
    """
    ambient = ensure_config(None)
    ambient_metadata: dict[str, Any] = dict(ambient.get("metadata") or {})

    if _has_active_callbacks(ambient.get("callbacks")):
        # Copy — ambient_metadata must stay untouched so it can still be
        # compared against below to decide whether returning it at all is
        # even necessary.
        metadata = dict(ambient_metadata)
        if tags:
            existing_tags = metadata.get("langfuse_tags") or []
            metadata["langfuse_tags"] = sorted(set(existing_tags) | set(tags))
        if session_id:
            metadata.setdefault("langfuse_session_id", session_id)
        if metadata == ambient_metadata:
            # Nothing to add — an empty dict here is equivalent (ensure_config
            # would just merge in identical content) and cheaper to build.
            return {}
        # No "callbacks" key at all: ensure_config's merge then falls back
        # to the ambient manager instead of replacing it (verified: an
        # explicit config that omits "callbacks" entirely preserves the
        # parent's callback manager).
        return {"metadata": metadata}

    handler = (
        get_trace_handler_for_trace(trace_id_for_session(session_id))
        if session_id
        else get_trace_handler()
    )
    if handler is None:
        return {}

    cfg: dict[str, Any] = {"callbacks": [handler]}
    metadata = dict(ambient_metadata)
    if session_id:
        metadata["langfuse_session_id"] = session_id
    if tags:
        metadata["langfuse_tags"] = tags
    if metadata:
        cfg["metadata"] = metadata
    return cfg


def flush() -> None:
    """Flush any buffered spans to Langfuse (call before a short process exits)."""
    if not _initialized or _handler is None:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:
        logger.debug("Langfuse flush failed: %s", exc)
