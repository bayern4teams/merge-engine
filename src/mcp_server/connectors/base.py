"""
Abstract base connector for all database connectors.

All connectors implement this interface so the MCP tools and orchestrator
nodes can interact with any data source through a unified API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    """Abstract base class for database connectors."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the connector with configuration.

        Parameters
        ----------
        config : dict
            Connection configuration from the resolved config (e.g. connection.host,
            connection.port, connection.uri, etc.).
        """
        self.config = config
        self._pool: Any = None

    # ── Schema Discovery ──────────────────────────────────────────────

    @abstractmethod
    async def discover_schema(self) -> dict[str, Any]:
        """Discover tables/collections, columns, types, and sample values.

        Returns
        -------
        dict
            Schema representation, e.g.:
            {
                "tables": [
                    {
                        "name": "customers",
                        "columns": [
                            {"name": "id", "type": "INTEGER", "nullable": False},
                            {"name": "email", "type": "VARCHAR", "nullable": False},
                        ],
                        "foreign_keys": [
                            {"column": "country_id", "ref_table": "countries", "ref_column": "id"}
                        ],
                        "sample_row": {"id": 1, "email": "a@b.com"}
                    }
                ]
            }
        """

    # ── Data Extraction ───────────────────────────────────────────────

    @abstractmethod
    async def extract(
        self,
        table_name: str,
        columns: list[str],
        filter: dict[str, Any] | None = None,
        batch_size: int = 1000,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Extract rows from a table/collection with pagination.

        Parameters
        ----------
        table_name : str
            Name of the table or collection to query.
        columns : list[str]
            Columns to retrieve. Use ``["*"]`` for all.
        filter : dict, optional
            Additional filter conditions, e.g. ``{"_imported": False}``.
        batch_size : int
            Maximum rows per batch.
        cursor : str, optional
            Pagination cursor (OFFSET for SQL, ``_id`` for MongoDB).

        Returns
        -------
        dict
            {
                "rows": [...],                # list of row dicts
                "column_names": [...],        # column order
                "total_count": int,           # total rows matching filter
                "next_cursor": str | None,    # cursor for next batch
                "origin_ids": [...],          # original row identifiers
            }
        """

    # ── Import Flagging ───────────────────────────────────────────────

    @abstractmethod
    async def mark_imported(
        self,
        table_name: str,
        ids: list[str],
    ) -> bool:
        """Flag extracted rows as imported.

        Parameters
        ----------
        table_name : str
            Table or collection name.
        ids : list[str]
            Original row identifiers to mark.

        Returns
        -------
        bool
            True on success.
        """

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the connection pool. Override in subclasses."""
        if self._pool is not None:
            self._pool = None

    async def __aenter__(self) -> BaseConnector:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()