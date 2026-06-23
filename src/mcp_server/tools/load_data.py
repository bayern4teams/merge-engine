"""
load_data tool implementation.

Upserts unified records into the destination PostgreSQL table
using INSERT ... ON CONFLICT (MERGE-like behavior).
Handles conflict column and provenance tracking.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.mcp_server.connectors import create_connector
from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
from src.orchestrator.utils.retry import TransientError, PermanentError
from src.utils.id_utils import generate_uuid

logger = logging.getLogger("merge-engine.tools.load_data")


async def load_data_impl(
    entity_type: str,
    records: list[dict[str, Any]],
    mapping: dict[str, Any] | None = None,
    destination_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert unified records into the PostgreSQL destination.

    Parameters
    ----------
    entity_type : str
        The entity type being loaded.
    records : list[dict]
        Unified records to upsert. Each should have ``id`` as the universal UUID.
    mapping : dict, optional
        Field mapping dict from map_fields output:
        {source_field: {"dest_column": str, "confidence": float, ...}, ...}
    destination_config : dict, optional
        Destination connection config. If None, uses the global config.

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
    if not records:
        return {
            "success": True,
            "entity_type": entity_type,
            "total_records": 0,
            "inserted": 0,
            "updated": 0,
            "failed": 0,
        }

    try:
        connector = _get_destination_connector(destination_config)
    except Exception as exc:
        raise PermanentError(f"Failed to create destination connector: {exc}") from exc

    # Get the destination table name — use entity_type as default (dynamic)
    dest_table = entity_type

    # Transform records using mapping
    transformed_records = _transform_records(records, mapping, dest_table)

    # Build provenance tracking
    for rec in transformed_records:
        rec["_loaded_at"] = _get_timestamp()
        rec["_source_count"] = rec.get("_source_count", 1)

    try:
        result = connector.load_batch(
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


def _get_destination_connector(
    destination_config: dict[str, Any] | None,
) -> PostgreSQLDestinationConnector:
    """Get or create a PostgreSQL destination connector.

    Parameters
    ----------
    destination_config : dict | None
        Destination config from the UI, or None to use global config.

    Returns
    -------
    PostgreSQLDestinationConnector
    """
    if destination_config:
        config_dict = {
            "host": destination_config.get("host", "localhost"),
            "port": destination_config.get("port", 5432),
            "user": destination_config.get("user", ""),
            "password": destination_config.get("password", ""),
            "database": destination_config.get("database", ""),
            "schema": destination_config.get("schema", "public"),
        }
    else:
        from src.mcp_server.server import _get_config
        config = _get_config()
        dest = config.destination
        config_dict = {
            "host": dest.host or "localhost",
            "port": dest.port or 5432,
            "user": dest.user or "",
            "password": dest.password or "",
            "database": dest.database or "",
            "schema": dest.schema_name or "public",
        }

    return PostgreSQLDestinationConnector(config_dict)


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
        Transformed records ready for INSERT ... ON CONFLICT.
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
                elif isinstance(value, dict) and "conflict" in value:
                    value = json.dumps(value["conflict"], default=str)

                t_rec[dest_col] = value
        else:
            # No mapping — pass through non-internal fields
            for key, value in rec.items():
                if key.startswith("_") and key not in ("_cluster_id",):
                    continue
                t_rec[key] = value

        # Ensure universal UUID key exists
        if "id" not in t_rec:
            t_rec["id"] = rec.get("_cluster_id", rec.get("id", generate_uuid()))

        # Add provenance metadata
        source_ids = []
        if "_source_ids" in rec:
            try:
                source_ids = json.loads(rec["_source_ids"])
            except (json.JSONDecodeError, TypeError):
                source_ids = []
        if not source_ids:
            source_ids = [{"cluster_id": rec.get("_cluster_id", "")}]

        t_rec["_source_ids"] = json.dumps(source_ids, default=str)

        transformed.append(t_rec)

    return transformed


def _get_timestamp() -> str:
    """Get current UTC timestamp as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def load_data_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    return await load_data_impl(**arguments)