"""
map_fields node: Maps unified fields to destination columns.

Calls map_fields_impl for each entity type, collects schema changes
and unmapped fields for human review.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.map_fields import map_fields_impl
from src.mcp_server.connectors import create_connector
from src.orchestrator.utils.retry import TransientError

logger = logging.getLogger("merge-engine.orchestrator.map_fields")


async def map_fields(state: dict[str, Any]) -> dict[str, Any]:
    """Map unified entity fields to destination columns.

    Parameters
    ----------
    state : dict
        MergeState with ``unified_entities``, ``destination``, ``target_entities``.

    Returns
    -------
    dict
        Update dict with ``field_mappings``, ``pending_schema_changes``, ``errors``.
    """
    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {})
    destination = state.get("destination")
    target_entities: list[str] = state.get("target_entities", [])
    errors: list[str] = list(state.get("errors", []))
    field_mappings: dict[str, dict] = dict(state.get("field_mappings", {}))
    pending_schema_changes: list[dict] = list(state.get("pending_schema_changes", []))

    # Get destination type (handles both dict and Config object)
    if isinstance(destination, dict):
        dest_type = destination.get("type", "postgresql")
        # Check if destination has nested tables mapping
        dest_tables = destination.get("tables", {})
    else:
        dest_type = getattr(destination, "type", "postgresql")
        dest_tables = getattr(destination, "tables", {})

    for entity_type in target_entities:
        records = unified_entities.get(entity_type, [])
        if not records:
            logger.info("No unified records for entity '%s', skipping field mapping.", entity_type)
            field_mappings[entity_type] = {}
            continue

        # Get destination table schema
        dest_table = dest_tables.get(entity_type, entity_type) if isinstance(dest_tables, dict) else entity_type

        # Build destination schema info
        dest_schema_info = {
            "table": dest_table,
            "columns": [
                {"name": "id", "type": "STRING"},
                {"name": "_source_ids", "type": "STRING"},
            ],
        }

        # Build unified schema from sample records
        unified_schema: dict[str, Any] = {}
        for rec in records[:5]:  # Sample first 5 records
            for key, value in rec.items():
                if key.startswith("_"):
                    continue
                if key not in unified_schema:
                    unified_schema[key] = {
                        "type": type(value).__name__,
                        "sample_value": value,
                    }

        try:
            result = await map_fields_impl(
                entity_type=entity_type,
                unified_schema=unified_schema,
                destination_schema=dest_schema_info,
            )

            mapping = result.get("field_mapping", {})
            field_mappings[entity_type] = mapping

            schema_changes = result.get("schema_changes", [])
            pending_schema_changes.extend(schema_changes)

            unmapped = result.get("unmapped_fields", [])

            logger.info(
                "Field mapping for '%s': %d mapped, %d unmapped, %d schema changes",
                entity_type,
                len([m for m in mapping.values() if not m.get("requires_schema_change")]),
                len(unmapped),
                len(schema_changes),
            )

        except Exception as exc:
            msg = f"Field mapping failed for '{entity_type}': {exc}"
            logger.error(msg)
            errors.append(msg)
            field_mappings[entity_type] = {}

    return {
        "field_mappings": field_mappings,
        "pending_schema_changes": pending_schema_changes,
        "errors": errors,
    }


def has_unmapped_fields(state: dict[str, Any]) -> bool:
    """Conditional edge: True if any entity has unmapped fields."""
    field_mappings: dict[str, dict] = state.get("field_mappings", {})
    pending_schema_changes: list[dict] = state.get("pending_schema_changes", [])

    # If there are schema changes, human review is needed
    if pending_schema_changes:
        return True

    return False