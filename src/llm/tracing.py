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
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("merge-engine.llm.tracing")

# Load .env before Langfuse reads its credentials from the environment.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


_handler: Any = None
_initialized = False


def get_trace_handler() -> Any:
    """Return the shared Langfuse callback handler, or ``None`` if disabled.

    Initialized once, lazily. Disabled (returns ``None``) when no public key is
    configured or when the local Langfuse stack can't be authenticated against —
    tracing never blocks or breaks a merge run.
    """
    global _handler, _initialized
    if _initialized:
        return _handler
    _initialized = True

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        logger.info("Langfuse tracing disabled (LANGFUSE_PUBLIC_KEY not set).")
        _handler = None
        return None

    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        client = get_client()  # reads LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST
        if not client.auth_check():
            logger.warning(
                "Langfuse auth_check failed (host=%s) — tracing disabled. Is the "
                "stack up? `docker compose -f docker/langfuse-compose.yml "
                "--env-file docker/langfuse.env up -d`",
                os.environ.get("LANGFUSE_HOST"),
            )
            _handler = None
            return None

        _handler = CallbackHandler()
        logger.info(
            "Langfuse tracing enabled (host=%s, project via public key).",
            os.environ.get("LANGFUSE_HOST"),
        )
    except Exception as exc:  # never let tracing setup crash the app
        logger.warning("Langfuse tracing setup failed (%s) — continuing untraced.", exc)
        _handler = None

    return _handler


def trace_config(
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build a LangChain ``RunnableConfig`` fragment carrying the trace handler.

    Merge the result into the config passed to ``ainvoke``/``astream``. Returns
    an empty dict when tracing is disabled, so it's always safe to spread.

    Parameters
    ----------
    session_id : str, optional
        Groups a run's initial execution and all its HITL-resume continuations
        under one Langfuse session (pass the workflow ``thread_id``).
    tags : list[str], optional
        Langfuse trace tags.
    """
    handler = get_trace_handler()
    if handler is None:
        return {}

    cfg: dict[str, Any] = {"callbacks": [handler]}
    metadata: dict[str, Any] = {}
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
