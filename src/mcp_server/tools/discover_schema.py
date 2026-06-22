"""
discover_schema tool implementation.

Connects to an origin database and returns all tables/collections
with column names, types, one sample value, and foreign keys.

This function is called both by the MCP server dispatch and directly
by the orchestrator node (no protocol overhead).
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.connectors import create_connector
from src.orchestrator.utils.retry import TransientError, PermanentError

logger = logging.getLogger("merge-engine.tools.discover_schema")


async def discover_schema_impl(origin_id: str) -> dict[str, Any]:
    """Discover schema for a given origin.

    Parameters
    ----------
    origin_id : str
        The origin database ID from config.

    Returns
    -------
    dict
        Schema representation matching the BaseConnector interface.

    Raises
    ------
    ValueError
        If *origin_id* is not found in config.
    PermanentError
        If the connector fails during discovery.
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
        schema = await connector.discover_schema()
        return {
            "origin_id": origin_id,
            "origin_type": origin_cfg.type,
            **schema,
        }
    except Exception as exc:
        logger.error("Schema discovery failed for origin %s: %s", origin_id, exc)
        raise PermanentError(
            f"Schema discovery failed for origin '{origin_id}': {exc}"
        ) from exc


async def discover_schema_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    origin_id = arguments["origin_id"]
    return await discover_schema_impl(origin_id)