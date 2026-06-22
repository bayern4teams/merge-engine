"""
load_data node: Upserts unified records into Databricks destination.

Applies field mappings, transforms records, and calls load_data_impl
for each entity type in dependency order.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.mcp_server.tools.load_data import load_data_impl
from src.mcp_server.connectors import create_connector

logger = logging.getLogger("merge-engine.orchestrator.load_data")


async def load_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Load unified data into the Databricks destination.

    Parameters
    ----------
    state : dict
        MergeState with ``unified_entities``, ``field_mappings``, ``destination``.

    Returns
    -------
    dict
        Update dict with ``errors`` key.
    """
    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {})
    field_mappings: dict[str, dict] = state.get("field_mappings", {})
    destination = state.get("destination")
    extraction_order = state.get("extraction_order", [])
    errors: list[str] = list(state.get("errors", []))

    # Apply schema changes first if any
    pending_schema_changes = state.get("pending_schema_changes", [])
    if pending_schema_changes:
        logger.info("Applying %d pending schema changes before loading...", len(pending_schema_changes))
        await _apply_schema_changes(destination, pending_schema_changes, errors)

    # Process in dependency order
    for entity_type in extraction_order:
        records = unified_entities.get(entity_type, [])
        if not records:
            logger.info("No records to load for entity '%s', skipping.", entity_type)
            continue

        mapping = field_mappings.get(entity_type, {})
        dest_table = destination.tables.get(entity_type, entity_type) if hasattr(destination, 'tables') else entity_type

        # Prepare records for load_data_impl
        load_records = []
        for rec in records:
            # Transform using field mapping
            transformed = {}
            for src_field, field_info in mapping.items():
                dest_col = field_info.get("dest_column", src_field)
                if not field_info.get("requires_schema_change"):
                    transformed[dest_col] = rec.get(src_field)

            # Ensure UUID key exists
            if "id" not in transformed:
                transformed["id"] = rec.get("id", rec.get("_cluster_id", ""))

            load_records.append(transformed)

        try:
            result = await load_data_impl(
                entity_type=entity_type,
                records=load_records,
                mapping=mapping,
            )

            logger.info(
                "Loaded entity '%s': %d total, %d inserted, %d updated",
                entity_type,
                result.get("total_records", 0),
                result.get("inserted", 0),
                result.get("updated", 0),
            )

        except Exception as exc:
            msg = f"Load failed for entity '{entity_type}': {exc}"
            logger.error(msg)
            errors.append(msg)

    return {"errors": errors}


async def _apply_schema_changes(
    destination: Any,
    changes: list[dict],
    errors: list[str],
) -> None:
    """Apply pending ALTER TABLE ADD COLUMN statements to Databricks."""
    dest_type = destination.type if hasattr(destination, "type") else destination.get("type", "databricks")
    dest_config = destination.model_dump() if hasattr(destination, "model_dump") else destination

    try:
        connector = create_connector(dest_type, dest_config)
        result = await connector.extend_schema_batch(changes)
        logger.info(
            "Schema extension: %d added, %d failed",
            result.get("added", 0),
            result.get("failed", 0),
        )
        for err in range(result.get("failed", 0)):
            errors.append("Schema change failed")
    except Exception as exc:
        msg = f"Schema extension failed: {exc}"
        logger.error(msg)
        errors.append(msg)


async def flag_origins_node(state: dict[str, Any]) -> dict[str, Any]:
    """Flag extracted rows as imported on origin databases.

    Parameters
    ----------
    state : dict
        MergeState with ``extracted_data``, ``origins``.

    Returns
    -------
    dict
        Update dict with ``errors`` key.
    """
    extracted_data: dict[str, list[dict]] = state.get("extracted_data", {})
    origins = state.get("origins", [])
    errors: list[str] = list(state.get("errors", []))

    origin_map: dict[str, Any] = {}
    for o in origins:
        oid = o.id if hasattr(o, "id") else o.get("id", "")
        origin_map[oid] = o

    # Collect unique (origin_id, table_name, id) per origin
    origin_ids_map: dict[str, dict[str, set[str]]] = {}  # origin_id -> {table: set(ids)}

    for entity_type, records in extracted_data.items():
        for rec in records:
            origin_id = rec.get("_origin_id", "")
            table_name = rec.get("_origin_table", "")
            row_id = rec.get("_origin_id_row", rec.get("id", ""))
            if origin_id and table_name:
                origin_ids_map.setdefault(origin_id, {}).setdefault(table_name, set()).add(row_id)

    for origin_id, tables in origin_ids_map.items():
        origin_cfg = origin_map.get(origin_id)
        if origin_cfg is None:
            errors.append(f"Unknown origin '{origin_id}' for flagging")
            continue

        origin_type = origin_cfg.type if hasattr(origin_cfg, "type") else origin_cfg.get("type", "postgres")
        origin_conn = origin_cfg.connection.model_dump() if hasattr(origin_cfg, "connection") else origin_cfg

        try:
            connector = create_connector(origin_type, origin_conn)
            for table_name, ids in tables.items():
                await connector.mark_imported(table_name, list(ids))
                logger.info(
                    "Flagged %d rows as imported on origin '%s', table '%s'",
                    len(ids), origin_id, table_name,
                )
        except Exception as exc:
            msg = f"Mark imported failed for origin '{origin_id}': {exc}"
            logger.error(msg)
            errors.append(msg)

    return {"errors": errors}