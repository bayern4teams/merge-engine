"""
mark_imported tool implementation.

Flags extracted rows as imported by setting _imported=true or
inserting into a tracking table.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.connectors import create_connector
from src.orchestrator.utils.retry import TransientError, PermanentError

logger = logging.getLogger("merge-engine.tools.mark_imported")


async def mark_imported_impl(
    origin_config: dict[str, Any],
    table_name: str,
    ids: list[str],
) -> dict[str, Any]:
    """Mark rows as imported on the origin database.

    Parameters
    ----------
    origin_config : dict
        Origin configuration dict with keys: id, type, connection, priority.
    table_name : str
        Table or collection name.
    ids : list[str]
        Original row identifiers to mark.

    Returns
    -------
    dict
        {"success": True, "marked_count": int}
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
        success = await connector.mark_imported(table_name=table_name, ids=ids)
        return {
            "success": success,
            "marked_count": len(ids),
            "origin_id": origin_id,
            "table_name": table_name,
        }
    except Exception as exc:
        logger.error(
            "Mark imported failed for %s.%s: %s", origin_id, table_name, exc
        )
        raise PermanentError(
            f"Mark imported failed for origin '{origin_id}', table '{table_name}': {exc}"
        ) from exc


async def mark_imported_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point.

    Parameters
    ----------
    arguments : dict
        Either {"origin_config": dict, ...} for full config calls,
        or {"origin_id": str, ...} for legacy MCP calls.

    Returns
    -------
    dict
        Mark imported result.
    """
    if "origin_config" in arguments:
        return await mark_imported_impl(**arguments)

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

    return await mark_imported_impl({
        "id": origin_cfg.id,
        "type": origin_cfg.type,
        "connection": origin_cfg.connection.model_dump(),
        "priority": origin_cfg.priority,
    }, **{k: v for k, v in arguments.items() if k != "origin_id"})
