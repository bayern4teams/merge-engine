"""
discover_schemas node: Calls discover_schema tool for all origins.

Gathers schema information from every configured origin database
before dependency analysis.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.mcp_server.tools.discover_schema import discover_schema_impl

logger = logging.getLogger("merge-engine.orchestrator.discover_schemas")


async def discover_schemas(state: dict[str, Any]) -> dict[str, Any]:
    """Discover schemas from all origin databases.

    Parameters
    ----------
    state : dict
        MergeState with ``origins`` populated.

    Returns
    -------
    dict
        Update dict with ``schemas`` key.
    """
    logger.debug("ENTER discover_schemas")
    logger.debug("State keys: %s", list(state.keys()))
    origins = state.get("origins", [])
    schemas: dict[str, Any] = {}
    # Only THIS call's own errors (Phase 0 Sec 0.8) - see load_data_node's
    # identical fix for why seeding with the existing history double-counted
    # it through operator.add's reducer.
    errors: list[str] = []
    failed_origins: list[str] = []

    logger.debug("Processing %d origins", len(origins))

    # BUG D.4.1: origins are independent — discover_schema_impl opens and
    # closes its own connector per call with no shared state — so there is
    # no reason to discover them one at a time. Run all origins concurrently;
    # asyncio.gather preserves input order in its results regardless of
    # completion order, so `schemas` still populates deterministically and
    # `failed_origins` is sorted below rather than relying on completion
    # order (which parallel execution no longer guarantees matches origin
    # order for any incidental reason).
    async def _discover_one(origin: Any) -> tuple[str, Any, Exception | None]:
        origin_id = (
            origin.get("id", "unknown")
            if isinstance(origin, dict)
            else getattr(origin, "id", "unknown")
        )
        origin_type = origin.get("type", "unknown") if isinstance(origin, dict) else "unknown"
        logger.debug("Discovering schema for origin '%s' (type=%s)", origin_id, origin_type)
        try:
            schema = await discover_schema_impl(origin)
            return origin_id, schema, None
        except Exception as exc:
            return origin_id, None, exc

    results = await asyncio.gather(*(_discover_one(o) for o in origins))

    for origin_id, schema, exc in results:
        if exc is not None:
            msg = f"Schema discovery failed for origin '{origin_id}': {exc}"
            logger.error(msg, exc_info=exc)
            errors.append(msg)
            failed_origins.append(origin_id)
            continue
        table_count = len(schema.get("tables", []))
        schemas[origin_id] = schema
        logger.debug("Schema discovered for origin '%s': %d tables", origin_id, table_count)
        table_names = [t.get("name", "?") for t in schema.get("tables", [])]
        logger.debug("Table names: %s", table_names)
        logger.info(
            "Schema discovered for origin '%s': %d tables",
            origin_id,
            table_count,
        )

    failed_origins.sort()

    logger.debug("Total schemas collected: %d", len(schemas))
    logger.debug("Total errors: %d", len(errors))

    # An origin whose schema never loaded contributes nothing to the merge.
    # Continuing silently is how a two-origin request quietly becomes a
    # one-origin one and still reports success.
    failure: dict[str, Any] = {}
    if failed_origins:
        failure = {
            "node": "discover_schemas",
            "error": (
                f"Could not read the schema of {len(failed_origins)} of "
                f"{len(origins)} origin(s): {', '.join(failed_origins)}. "
                "Merging now would silently leave their data out."
            ),
            "error_class": "PermanentError",
            "disposition": "escalate",
            "context": {
                "failed_origins": failed_origins,
                "succeeded_origins": sorted(schemas.keys()),
            },
        }
        logger.error("discover_schemas failing loudly: %s", failure["error"])

    logger.debug("EXIT discover_schemas")

    # Only the keys this node actually computes (Phase 0 Sec 0.8) - a key it
    # omits is left untouched by LangGraph's per-key TypedDict state update,
    # not cleared.
    return {
        "schemas": schemas,
        "errors": errors,
        "failure": failure,
        "interrupt_data": None,
    }
