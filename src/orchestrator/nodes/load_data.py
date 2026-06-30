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
    logger.debug("ENTER load_data_node")
    logger.debug("State keys: %s", list(state.keys()))
    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {})
    field_mappings: dict[str, dict] = state.get("field_mappings", {})
    destination = state.get("destination")
    extraction_order = state.get("extraction_order", [])
    errors: list[str] = list(state.get("errors", []))

    logger.debug("extraction_order: %s", extraction_order)
    logger.debug("unified_entities keys: %s", list(unified_entities.keys()))
    for ue, recs in unified_entities.items():
        logger.debug("  Entity '%s': %d unified records", ue, len(recs))

    # Apply schema changes first if any
    pending_schema_changes = state.get("pending_schema_changes", [])
    if pending_schema_changes:
        logger.info("Applying %d pending schema changes before loading...", len(pending_schema_changes))
        logger.debug("Schema changes: %s", json.dumps(pending_schema_changes, default=str)[:500])
        await _apply_schema_changes(destination, pending_schema_changes, errors)

    # Process in dependency order
    for entity_type in extraction_order:
        records = unified_entities.get(entity_type, [])
        logger.debug("Processing entity '%s': %d records", entity_type, len(records))
        if not records:
            logger.info("No records to load for entity '%s', skipping.", entity_type)
            continue

        mapping = field_mappings.get(entity_type, {})
        logger.debug("Field mapping for '%s': %d mappings", entity_type, len(mapping))
        dest_tables = getattr(destination, 'tables', {}) if hasattr(destination, 'tables') else (destination.get('tables', {}) if isinstance(destination, dict) else {})
        dest_table = dest_tables.get(entity_type, entity_type) if isinstance(dest_tables, dict) else entity_type
        logger.debug("Destination table: %s", dest_table)

        # Prepare records for load_data_impl
        load_records = []
        for rec_idx, rec in enumerate(records):
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

        logger.debug("Prepared %d load records for entity '%s'", len(load_records), entity_type)

        try:
            logger.debug("Calling load_data_impl for entity '%s'", entity_type)
            result = await load_data_impl(
                entity_type=entity_type,
                records=load_records,
                mapping=mapping,
            )
            logger.debug("load_data_impl result: %s", json.dumps(result, default=str)[:300])

            logger.info(
                "Loaded entity '%s': %d total, %d inserted, %d updated",
                entity_type,
                result.get("total_records", 0),
                result.get("inserted", 0),
                result.get("updated", 0),
            )

        except Exception as exc:
            msg = f"Load failed for entity '{entity_type}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT load_data_node")

    return {"errors": errors}


async def _apply_schema_changes(
    destination: Any,
    changes: list[dict],
    errors: list[str],
) -> None:
    """Apply pending ALTER TABLE ADD COLUMN statements to Databricks."""
    dest_type = destination.type if hasattr(destination, "type") else (destination.get("type", "postgresql") if isinstance(destination, dict) else "postgresql")
    dest_config = destination.model_dump() if hasattr(destination, "model_dump") else (destination.connection.model_dump() if hasattr(destination, "connection") else destination)

    logger.debug("Applying schema changes to destination type '%s'", dest_type)
    logger.debug("Schema changes count: %d", len(changes))

    try:
        logger.debug("Creating connector for schema extension")
        connector = create_connector(dest_type, dest_config)
        logger.debug("Calling connector.extend_schema_batch with %d changes", len(changes))
        result = await connector.extend_schema_batch(changes)
        logger.debug("extend_schema_batch result: %s", json.dumps(result, default=str))
        logger.info(
            "Schema extension: %d added, %d failed",
            result.get("added", 0),
            result.get("failed", 0),
        )
        for err in range(result.get("failed", 0)):
            errors.append("Schema change failed")
    except Exception as exc:
        msg = f"Schema extension failed: {exc}"
        logger.error(msg, exc_info=True)
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
    logger.debug("ENTER flag_origins_node")
    logger.debug("State keys: %s", list(state.keys()))
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

    logger.debug("Origins to flag: %d", len(origin_ids_map))
    for origin_id, tables in origin_ids_map.items():
        logger.debug("  Origin '%s': %d tables", origin_id, len(tables))
        for tname, ids in tables.items():
            logger.debug("    Table '%s': %d rows", tname, len(ids))

    for origin_id, tables in origin_ids_map.items():
        origin_cfg = origin_map.get(origin_id)
        if origin_cfg is None:
            errors.append(f"Unknown origin '{origin_id}' for flagging")
            continue

        origin_type = origin_cfg.type if hasattr(origin_cfg, "type") else origin_cfg.get("type", "postgres")
        origin_conn = origin_cfg.connection.model_dump() if hasattr(origin_cfg, "connection") else origin_cfg

        try:
            logger.debug("Creating connector for origin '%s' (type=%s)", origin_id, origin_type)
            connector = create_connector(origin_type, origin_conn)
            for table_name, ids in tables.items():
                logger.debug("    Marking imported on '%s:%s' (%d rows)", origin_id, table_name, len(ids))
                await connector.mark_imported(table_name, list(ids))
                logger.info(
                    "Flagged %d rows as imported on origin '%s', table '%s'",
                    len(ids), origin_id, table_name,
                )
        except Exception as exc:
            msg = f"Mark imported failed for origin '{origin_id}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT flag_origins_node")
    return {"errors": errors}