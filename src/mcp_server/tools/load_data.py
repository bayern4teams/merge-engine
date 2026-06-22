"""
load_data tool implementation.

Upserts unified records into destination Databricks table (Delta)
using MERGE. Handles conflict column and provenance tracking.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.mcp_server.connectors import create_connector
from src.orchestrator.utils.retry import TransientError, PermanentError
from src.utils.id_utils import generate_uuid

logger = logging.getLogger("merge-engine.tools.load_data")


async def load_data_impl(
    entity_type: str,
    records: list[dict[str, Any]],
    mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert unified records into the Databricks destination.

    Parameters
    ----------
    entity_type : str
        The entity type being loaded.
    records : list[dict]
        Unified records to upsert. Each should have ``_cluster_id``
        or ``id`` as the universal UUID.
    mapping : dict, optional
        Field mapping dict from map_fields output:
        {source_field: {"dest_column": str, "confidence": float, ...}, ...}

    Returns
    -------
    dict
        {
            "success": bool,
            "entity_type": str,
            "total_records": int,
            "inserted": int,
            "updated": int,
            "failed": int,
        }
    """
    from src.mcp_server.server import _get_config
    config = _get_config()
    dest_cfg = config.destination

    try:
        connector = create_connector(dest_cfg.type, dest_cfg.model_dump())
    except Exception as exc:
        raise PermanentError(f"Failed to create destination connector: {exc}") from exc

    # Get the destination table name for this entity type
    dest_table = dest_cfg.tables.get(entity_type, entity_type)

    # Transform records using mapping
    transformed_records = _transform_records(records, mapping, dest_table)

    # Build provenance tracking
    for rec in transformed_records:
        rec["_loaded_at"] = _get_timestamp()
        rec["_source_count"] = rec.get("_source_count", 1)

    try:
        result = await connector.load_batch(
            table_name=dest_table,
            records=transformed_records,
            mapping=mapping,
            upsert_key="id",
        )
        return {
            "success": True,
            "entity_type": entity_type,
            "total_records": len(records),
            "inserted": result.get("inserted", len(records)),
            "updated": result.get("updated", 0),
            "failed": 0,
        }
    except Exception as exc:
        logger.error("Load failed for entity '%s': %s", entity_type, exc)
        raise PermanentError(
            f"Load failed for entity '{entity_type}' into table '{dest_table}': {exc}"
        ) from exc


def _transform_records(
    records: list[dict[str, Any]],
    mapping: dict[str, Any] | None,
    dest_table: str,
) -> list[dict[str, Any]]:
    """Transform unified records according to field mapping.

    Parameters
    ----------
    records : list[dict]
        Unified records (input).
    mapping : dict | None
        Field mapping from map_fields output.
    dest_table : str
        Destination table name (for provenance).

    Returns
    -------
    list[dict]
        Transformed records ready for MERGE.
    """
    transformed: list[dict[str, Any]] = []

    for rec in records:
        t_rec: dict[str, Any] = {}

        if mapping:
            for src_field, field_info in mapping.items():
                dest_col = field_info.get("dest_column", src_field)
                value = rec.get(src_field)

                # Skip fields requiring schema changes (not yet added)
                if field_info.get("requires_schema_change"):
                    continue

                # Serialize conflict data to JSON string
                if isinstance(value, dict) and "conflicts" in value:
                    value = json.dumps(value["conflicts"], default=str)

                t_rec[dest_col] = value
        else:
            # No mapping — pass through non-internal fields
            for key, value in rec.items():
                if key.startswith("_"):
                    continue
                t_rec[key] = value

        # Ensure universal UUID key exists
        if "id" not in t_rec:
            t_rec["id"] = rec.get("_cluster_id", generate_uuid())

        # Add provenance metadata
        t_rec["_source_ids"] = json.dumps(
            [{"cluster_id": rec.get("_cluster_id", "")}],
            default=str,
        )

        transformed.append(t_rec)

    return transformed


def _get_timestamp() -> str:
    """Get current UTC timestamp as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def load_data_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    return await load_data_impl(**arguments)