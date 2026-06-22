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
    origin_id: str,
    table_name: str,
    ids: list[str],
) -> dict[str, Any]:
    """Mark rows as imported on the origin database.

    Parameters
    ----------
    origin_id : str
        The origin database ID from config.
    table_name : str
        Table or collection name.
    ids : list[str]
        Original row identifiers to mark.

    Returns
    -------
    dict
        {"success": True, "marked_count": int}
    """
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

    try:
        connector = create_connector(origin_cfg.type, origin_cfg.connection.model_dump())
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
    """MCP tool handler entry point."""
    return await mark_imported_impl(**arguments)