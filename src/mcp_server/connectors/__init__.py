"""Database connectors for merge-engine.

Provides a unified interface for schema discovery, data extraction,
and import flagging across PostgreSQL, MongoDB and ClickHouse.
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.connectors.base import BaseConnector
from src.mcp_server.connectors.clickhouse import ClickHouseConnector
from src.mcp_server.connectors.mongodb import MongoDBConnector
from src.mcp_server.connectors.postgres import PostgresConnector

# Mapping of connector type strings to their classes
_CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    "postgres": PostgresConnector,
    "postgresql": PostgresConnector,
    "mongodb": MongoDBConnector,
    "clickhouse": ClickHouseConnector,
}


def create_connector(origin_type: str, config: dict[str, Any]) -> BaseConnector:
    """Factory function to create the appropriate connector.

    Parameters
    ----------
    origin_type : str
        One of "postgres", "mongodb", "clickhouse".
    config : dict
        Connection configuration for the target origin/destination.

    Returns
    -------
    BaseConnector
        An initialized connector instance.

    Raises
    ------
    ValueError
        If *origin_type* is not a registered connector type.
    """
    connector_cls = _CONNECTOR_REGISTRY.get(origin_type)
    if connector_cls is None:
        raise ValueError(
            f"Unknown connector type '{origin_type}'. Available: {list(_CONNECTOR_REGISTRY.keys())}"
        )
    return connector_cls(config)


__all__ = [
    "_CONNECTOR_REGISTRY",
    "BaseConnector",
    "ClickHouseConnector",
    "MongoDBConnector",
    "PostgresConnector",
    "create_connector",
]
