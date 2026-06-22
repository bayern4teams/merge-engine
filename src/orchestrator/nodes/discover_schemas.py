"""
discover_schemas node: Calls discover_schema tool for all origins.

Gathers schema information from every configured origin database
before dependency analysis.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.discover_schema import discover_schema_impl
from src.orchestrator.utils.retry import PermanentError

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
    origins = state.get("origins", [])
    schemas: dict[str, Any] = {}
    errors: list[str] = list(state.get("errors", []))

    for origin in origins:
        origin_id = origin.id if hasattr(origin, "id") else origin.get("id", "")
        try:
            schema = await discover_schema_impl(origin_id)
            schemas[origin_id] = schema
            logger.info(
                "Schema discovered for origin '%s': %d tables",
                origin_id,
                len(schema.get("tables", [])),
            )
        except Exception as exc:
            msg = f"Schema discovery failed for origin '{origin_id}': {exc}"
            logger.error(msg)
            errors.append(msg)

    return {"schemas": schemas, "errors": errors}