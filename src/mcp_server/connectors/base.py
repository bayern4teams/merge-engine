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
        watermark: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract rows from a table/collection with pagination.

        Parameters
        ----------
        table_name : str
            Name of the table or collection to query.
        columns : list[str]
            Columns to retrieve. Use ``["*"]`` for all.
        filter : dict, optional
            Additional equality filter conditions.
        batch_size : int
            Maximum rows per batch.
        cursor : str, optional
            Pagination cursor (OFFSET for SQL, ``_id`` for MongoDB).
        watermark : dict, optional
            ``{"column": str, "since": Any, "kind": "numeric"|"timestamp"}``.
            When given, pushes an incremental ``> since`` filter down into
            the query and orders by that column so the caller can read the
            new high-water mark off the last extracted row. Origins are
            never written to — incremental state lives in the destination's
            ``merge_engine_extraction_state`` tracking table, not on origin
            rows (see connectors/watermark.py).

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

    # ── Join-graph support (Phase 4.1) ──────────────────────────────────

    async def sample_distinct_values(
        self,
        table_name: str,
        column: str,
        limit: int = 500,
        filter_values: list[Any] | None = None,
    ) -> list[Any]:
        """Sample up to `limit` distinct non-null values of one column.

        Used by join_graph.py to test inclusion dependencies between a
        candidate child column and a candidate parent key — the whole point
        is to never pull a full table (some origin tables here run to tens
        of millions of rows) just to check whether one column's values are
        contained in another's. Every subclass that has a real origin
        (ClickHouse, PostgreSQL, MongoDB) overrides this with a query-side
        DISTINCT/aggregation bounded by `limit`; the default here exists so
        a connector that hasn't been given one yet (or can't reasonably
        support it) degrades to "no inclusion-dependency evidence available"
        rather than crashing the whole join-graph build.

        ``filter_values``, when given, restricts to rows whose `column` is
        one of these values instead of sampling independently — this is
        what makes verification actually detect a real FK relationship
        between a large parent table and a small child table (see
        join_graph.py's `_verify_inclusion`): two arbitrary independent
        samples essentially never coincidentally overlap even when the
        relationship is 100% intact.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support sample_distinct_values")

    # ── Capability matrix (Part B / P-2, PreSplink §12-14) ──────────────

    def capabilities(self) -> dict[str, bool]:
        """What this backend can and cannot do — a routing input, not a
        surprise discovered mid-query.

        The most direct example: ClickHouse and MongoDB report
        ``supports_declared_fk: False`` because neither engine has a
        first-class foreign-key constraint. Before this existed, code that
        assumed "no FK found" meant "no relationship exists" would silently
        under-discover on those two backends — the honest answer is "this
        backend cannot declare one," which routes P-6's relationship
        discovery to inference instead of `discover_schema`'s FK list,
        rather than routing it to "there is nothing here."

        The conservative default here (everything False except the ones
        that are safe to assume for any SQL-shaped connector) is
        deliberate: a connector that hasn't overridden this yet reports
        "I can't do any of this" rather than a guessed True that a caller
        might act on incorrectly.
        """
        return {
            "supports_declared_pk": False,
            "supports_declared_fk": False,
            "supports_declared_unique_constraints": False,
            "supports_statistics": False,
            "supports_sampling": True,
            "supports_explain": False,
            "supports_cte": False,
            "supports_temp_tables": False,
            "supports_window_functions": False,
            "supports_pushdown": True,
            "supports_parallel_queries": False,
            "supports_column_comments": False,
            "supports_table_comments": False,
        }

    async def discover_indexes(self, table_name: str) -> list[dict[str, Any]]:
        """Every index on ``table_name``, however this backend represents
        an index (a real secondary index, or a sort/order key that plays
        the same role — e.g. ClickHouse's `ORDER BY`).

        Returns
        -------
        list[dict]
            ``{"name": str, "columns": list[str], "is_unique": bool,
            "is_primary": bool, "kind": str}`` per index. ``kind`` names the
            backend-specific concept (``"btree"``, ``"order_by"``, ...) so a
            caller comparing indexes across backends doesn't mistake one
            kind of structure for another.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support discover_indexes")

    async def discover_statistics(self, table_name: str) -> dict[str, Any]:
        """Table-level statistics this backend already tracks (row count
        estimate, size on disk, last-analyzed time) — read from the
        backend's own catalog, never computed by scanning the table.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support discover_statistics")

    async def profile_column(
        self, table_name: str, column: str, mode: str = "SAMPLED", sample_size: int = 10_000
    ) -> dict[str, Any]:
        """Null/distinct/min/max and a bounded top-values list for one
        column, computed database-side.

        ``mode`` is one of ``FULL`` (scan the whole table),
        ``SAMPLED`` (bounded to ``sample_size`` rows), or ``APPROXIMATE``
        (a backend-native approximate aggregate, e.g. ClickHouse's
        ``uniq`` instead of ``uniqExact``) — the caller always knows which
        one produced a given number (PreSplink §46), because the returned
        dict echoes ``mode`` back rather than leaving it implicit.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support profile_column")

    async def estimate_query_cost(
        self, table_name: str, filter: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """This backend's own query planner's cost estimate for reading
        ``table_name`` under ``filter`` — the preview PreSplink §55/§56
        require before any expensive discovery run, not a guess.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support estimate_query_cost")

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the connection pool. Override in subclasses."""
        if self._pool is not None:
            self._pool = None

    async def __aenter__(self) -> BaseConnector:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
