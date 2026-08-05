"""
discover_schema tool implementation.

Connects to an origin database and returns all tables/collections
with column names, types, one sample value, and foreign keys.
For PostgreSQL: uses information_schema.
For MongoDB: samples N documents to infer schema.

This function is called both by the MCP server dispatch and directly
by the orchestrator node (no protocol overhead).
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.connectors import create_connector
from src.orchestrator.utils.retry import PermanentError

logger = logging.getLogger("merge-engine.tools.discover_schema")


async def discover_schema_impl(origin_config: dict[str, Any]) -> dict[str, Any]:
    """Discover schema for a given origin.

    Parameters
    ----------
    origin_config : dict
        Origin configuration from the UI, with keys:
        - id (str): unique origin identifier
        - type (str): "postgres" or "mongodb"
        - connection (dict): host, port, user, password, database
        - priority (int, optional): priority for conflict resolution

    Returns
    -------
    dict
        Schema representation:
        {
            "origin_id": str,
            "origin_type": str,
            "tables": [
                {
                    "name": str,
                    "columns": [{"name": ..., "type": ..., "nullable": ...}],
                    "foreign_keys": [{"column": ..., "ref_table": ..., "ref_column": ...}],
                    "sample_row": {...}
                }
            ]
        }

    Raises
    ------
    ValueError
        If origin config is invalid.
    PermanentError
        If the connector fails during discovery.
    """
    logger.debug(
        "ENTER discover_schema_impl: origin_id=%s, origin_type=%s",
        origin_config.get("id"),
        origin_config.get("type"),
    )
    origin_id = origin_config.get("id", "unknown")
    origin_type = origin_config.get("type", "postgres")
    connection = origin_config.get("connection", {})

    if not origin_id:
        logger.debug("Validation failed: missing origin_id")
        raise ValueError("origin_config must have 'id' field")
    if not origin_type:
        logger.debug("Validation failed: missing origin_type")
        raise ValueError("origin_config must have 'type' field")
    if not connection:
        logger.debug("Validation failed: missing connection dict")
        raise ValueError("origin_config must have 'connection' dict")

    logger.debug("Creating connector for type: %s", origin_type)
    connector = None
    try:
        connector = create_connector(origin_type, connection)
        logger.debug("Connector created, calling discover_schema()")
        schema = await connector.discover_schema()
        logger.debug("Schema discovery completed: %d tables", len(schema.get("tables", [])))
        return {
            "origin_id": origin_id,
            "origin_type": origin_type,
            **schema,
        }
    except Exception as exc:
        logger.error("Schema discovery failed for origin %s: %s", origin_id, exc)
        raise PermanentError(f"Schema discovery failed for origin '{origin_id}': {exc}") from exc
    finally:
        if connector is not None:
            await connector.close()


async def discover_schema_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point.

    Parameters
    ----------
    arguments : dict
        Either {"origin_id": str} for legacy MCP calls,
        or {"origin_config": dict} for full config calls.

    Returns
    -------
    dict
        Schema representation.
    """
    logger.debug("ENTER discover_schema_handler: arguments_keys=%s", list(arguments.keys()))
    if "origin_config" in arguments:
        logger.debug("Using origin_config path")
        return await discover_schema_impl(arguments["origin_config"])

    # Legacy path: origin_id only
    origin_id = arguments.get("origin_id")
    logger.debug("Using legacy origin_id path: origin_id=%s", origin_id)
    if not origin_id:
        raise ValueError("Either 'origin_id' or 'origin_config' must be provided")

    # For backward compatibility, look up origin config
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

    return await discover_schema_impl(
        {
            "id": origin_cfg.id,
            "type": origin_cfg.type,
            "connection": origin_cfg.connection.model_dump(),
            "priority": origin_cfg.priority,
        }
    )
