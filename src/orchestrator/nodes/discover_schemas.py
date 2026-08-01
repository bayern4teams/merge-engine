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
    logger.debug("ENTER discover_schemas")
    logger.debug("State keys: %s", list(state.keys()))
    origins = state.get("origins", [])
    schemas: dict[str, Any] = {}
    errors: list[str] = list(state.get("errors", []))
    failed_origins: list[str] = []

    logger.debug("Processing %d origins", len(origins))

    for origin in origins:
        origin_id = origin.get("id", "unknown") if isinstance(origin, dict) else getattr(origin, "id", "unknown")
        origin_type = origin.get("type", "unknown") if isinstance(origin, dict) else "unknown"
        logger.debug("Discovering schema for origin '%s' (type=%s)", origin_id, origin_type)
        try:
            # Pass full origin config, not just the id string
            logger.debug("Calling discover_schema_impl for origin '%s'", origin_id)
            schema = await discover_schema_impl(origin)
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
        except Exception as exc:
            msg = f"Schema discovery failed for origin '{origin_id}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)
            failed_origins.append(origin_id)

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

    return {
        "schemas": schemas,
        "errors": errors,
        "failure": failure,
        "origins": state.get("origins", []),
        "destination": state.get("destination", {}),
        "llm_config": state.get("llm_config", {}),
        "extraction_config": state.get("extraction_config", {}),
        "target_entities": state.get("target_entities", []),
        "dependency_tree": state.get("dependency_tree", {}),
        "extraction_order": state.get("extraction_order", []),
        "extracted_data": state.get("extracted_data", {}),
        "entity_clusters": state.get("entity_clusters", {}),
        "unified_entities": state.get("unified_entities", {}),
        "field_mappings": state.get("field_mappings", {}),
        "pending_schema_changes": state.get("pending_schema_changes", []),
        "human_feedback": state.get("human_feedback"),
        "load_approval": state.get("load_approval"),
        "retry_counts": state.get("retry_counts", {}),
        "interrupt_data": None,
    }