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
    logger.debug("ENTER map_fields")
    logger.debug("State keys: %s", list(state.keys()))
    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {})
    destination = state.get("destination")
    target_entities: list[str] = state.get("target_entities", [])
    errors: list[str] = list(state.get("errors", []))
    field_mappings: dict[str, dict] = dict(state.get("field_mappings", {}))
    pending_schema_changes: list[dict] = list(state.get("pending_schema_changes", []))

    logger.debug("target_entities: %s", target_entities)
    logger.debug("unified_entities keys: %s", list(unified_entities.keys()))
    for ue, recs in unified_entities.items():
        logger.debug("  Entity '%s': %d unified records", ue, len(recs))

    # Get destination type (handles both dict and Config object)
    if isinstance(destination, dict):
        dest_type = destination.get("type", "postgresql")
        dest_tables = destination.get("tables", {})
    else:
        dest_type = getattr(destination, "type", "postgresql")
        dest_tables = getattr(destination, "tables", {})
    logger.debug("destination type: %s", dest_type)

    for entity_type in target_entities:
        records = unified_entities.get(entity_type, [])
        logger.debug("Processing entity '%s': %d unified records", entity_type, len(records))
        if not records:
            logger.info("No unified records for entity '%s', skipping field mapping.", entity_type)
            field_mappings[entity_type] = {}
            continue

        # Get destination table schema
        dest_table = dest_tables.get(entity_type, entity_type) if isinstance(dest_tables, dict) else entity_type
        logger.debug("Destination table: %s", dest_table)

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
        logger.debug("Unified schema fields: %d", len(unified_schema))
        for fname, finfo in unified_schema.items():
            logger.debug("  Field '%s': type=%s", fname, finfo.get("type"))

        try:
            logger.debug("Calling map_fields_impl for entity '%s'", entity_type)
            result = await map_fields_impl(
                entity_type=entity_type,
                unified_schema=unified_schema,
                destination_schema=dest_schema_info,
            )
            logger.debug("map_fields_impl result keys: %s", list(result.keys()))

            mapping = result.get("field_mapping", {})
            field_mappings[entity_type] = mapping
            logger.debug("Field mapping for '%s': %d mappings", entity_type, len(mapping))
            for mname, mval in mapping.items():
                logger.debug("  Mapping '%s': requires_schema_change=%s", mname, mval.get("requires_schema_change", False))

            schema_changes = result.get("schema_changes", [])
            pending_schema_changes.extend(schema_changes)
            logger.debug("Schema changes: %d", len(schema_changes))

            unmapped = result.get("unapped_fields", [])
            logger.debug("Unmapped fields: %d", len(unmapped))

            logger.info(
                "Field mapping for '%s': %d mapped, %d unmapped, %d schema changes",
                entity_type,
                len([m for m in mapping.values() if not m.get("requires_schema_change")]),
                len(unmapped),
                len(schema_changes),
            )

        except Exception as exc:
            msg = f"Field mapping failed for '{entity_type}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)
            field_mappings[entity_type] = {}

    logger.debug("Total field_mappings: %d", len(field_mappings))
    logger.debug("Total pending_schema_changes: %d", len(pending_schema_changes))
    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT map_fields")

    return {
        "field_mappings": field_mappings,
        "pending_schema_changes": pending_schema_changes,
        "errors": errors,
        "origins": state.get("origins", []),
        "destination": state.get("destination", {}),
        "llm_config": state.get("llm_config", {}),
        "extraction_config": state.get("extraction_config", {}),
        "target_entities": target_entities,
        "schemas": state.get("schemas", {}),
        "dependency_tree": state.get("dependency_tree", {}),
        "extraction_order": state.get("extraction_order", []),
        "extracted_data": state.get("extracted_data", {}),
        "entity_clusters": state.get("entity_clusters", {}),
        "unified_entities": unified_entities,
        "human_feedback": state.get("human_feedback"),
        "load_approval": state.get("load_approval"),
        "retry_counts": state.get("retry_counts", {}),
        "interrupt_data": None,
    }


def has_unmapped_fields(state: dict[str, Any]) -> bool:
    """Conditional edge: True if any entity has unmapped fields."""
    field_mappings: dict[str, dict] = state.get("field_mappings", {})
    pending_schema_changes: list[dict] = state.get("pending_schema_changes", [])

    # If there are schema changes, human review is needed
    if pending_schema_changes:
        return True

    return False