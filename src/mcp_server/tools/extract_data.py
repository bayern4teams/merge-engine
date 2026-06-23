"""
extract_data tool implementation.

Extracts rows from a specific origin table/entity with batch-oriented
cursor-based pagination (SQL: OFFSET/LIMIT, MongoDB: _id-based skip).

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.connectors import create_connector
from src.orchestrator.utils.retry import TransientError, PermanentError

logger = logging.getLogger("merge-engine.tools.extract_data")


async def extract_data_impl(
    origin_config: dict[str, Any],
    table_name: str,
    columns: list[str],
    batch_size: int = 1000,
    cursor: str | None = None,
    filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract rows from an origin with batch pagination.

    Parameters
    ----------
    origin_config : dict
        Origin configuration dict with keys: id, type, connection, priority.
    table_name : str
        Table or collection name.
    columns : list[str]
        Columns to extract. Use ``["*"]`` for all.
    batch_size : int
        Maximum rows per batch.
    cursor : str, optional
        Pagination cursor for next batch.
    filter : dict, optional
        Additional filter conditions.

    Returns
    -------
    dict
        {
            "rows": [...],
            "column_names": [...],
            "total_count": int,
            "next_cursor": str | None,
            "origin_ids": [...],
        }
    """
    origin_id = origin_config.get("id", "unknown")
    origin_type = origin_config.get("type", "postgres")
    connection = origin_config.get("connection", {})

    if not origin_id:
        raise ValueError("origin_config must have 'id' field")
    if not origin_type:
        raise ValueError("origin_config must have 'type' field")
    if not connection:
        raise ValueError("origin_config must have 'connection' dict")

    try:
        connector = create_connector(origin_type, connection)
        result = await connector.extract(
            table_name=table_name,
            columns=columns,
            filter=filter,
            batch_size=batch_size,
            cursor=cursor,
        )
        result["origin_id"] = origin_id
        result["table_name"] = table_name
        return result
    except Exception as exc:
        logger.error(
            "Extraction failed for %s.%s: %s", origin_id, table_name, exc
        )
        raise PermanentError(
            f"Extraction failed for origin '{origin_id}', table '{table_name}': {exc}"
        ) from exc


async def extract_data_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point.

    Parameters
    ----------
    arguments : dict
        Either {"origin_config": dict, ...} for full config calls,
        or {"origin_id": str, ...} for legacy MCP calls.

    Returns
    -------
    dict
        Extraction result.
    """
    if "origin_config" in arguments:
        return await extract_data_impl(**arguments)

    # Legacy path: origin_id only — look up from config
    origin_id = arguments.get("origin_id")
    if not origin_id:
        raise ValueError("Either 'origin_id' or 'origin_config' must be provided")

    from src.mcp_server.server import _get_config
    config = _get_config()
    origin_cfg = None
    for o in config.origins:
        if o.id == origin_id:
            origin_cfg = o
            break

    if origin_cfg is None:
        available = [o.id for o in config.origins]
        raise ValueError(f"Unknown origin '{origin_id}'. Available: {available}")

    return await extract_data_impl({
        "id": origin_cfg.id,
        "type": origin_cfg.type,
        "connection": origin_cfg.connection.model_dump(),
        "priority": origin_cfg.priority,
    }, **{k: v for k, v in arguments.items() if k != "origin_id"})
