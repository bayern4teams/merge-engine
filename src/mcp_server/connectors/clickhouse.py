"""
ClickHouse connector for schema discovery, data extraction, and import flagging.

Uses clickhouse-connect for driver access.
"""

from __future__ import annotations

import logging
from typing import Any

import clickhouse_connect

from src.mcp_server.connectors.base import BaseConnector
from src.orchestrator.utils.retry import TransientError, retry_on_transient

logger = logging.getLogger("merge-engine.connectors.clickhouse")

# ClickHouse substitution format for HTTP transport
_CH_PARAM = " {name:String} "


class ClickHouseConnector(BaseConnector):
    """ClickHouse database connector."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._client: clickhouse_connect.driver_client | None = None
        self._database: str = "default"

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def _ensure_client(self) -> clickhouse_connect.driver_client:
        """Create or return the clickhouse-connect client."""
        logger.debug("ENTER _ensure_client")
        if self._client is not None:
            logger.debug("Client already exists, returning")
            return self._client

        conn = self.config.get("connection", {})
        host = conn.get("host", "localhost")
        port = conn.get("port", 8123)
        user = conn.get("user", "default")
        password = conn.get("password", "")
        database = conn.get("database", "default")

        self._database = database
        logger.debug(
            "Creating ClickHouse client: host=%s, port=%d, user=%s, database=%s",
            host, port, user, database,
        )
        self._client = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=user,
            password=password,
            database=database,
            connect_timeout=10,
        )
        logger.debug("ClickHouse client created successfully")
        return self._client

    async def close(self) -> None:
        logger.debug("ENTER close")
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.debug("ClickHouse client closed")

    # ── Schema Discovery ──────────────────────────────────────────────

    async def discover_schema(self) -> dict[str, Any]:
        """Discover tables, columns, types, and sample rows."""
        logger.debug("ENTER discover_schema")
        client = await self._ensure_client()

        tables: list[dict[str, Any]] = []
        logger.debug("Querying system.tables for database=%s", self._database)

        try:
            result = client.query(
                f"""
                SELECT name FROM system.tables
                WHERE database = {{db:String}}
                  AND name NOT LIKE '%.inner%.'
                  AND name NOT LIKE '%.tmp%'
                ORDER BY name
                """,
                parameters={"db": self._database},
            )
            table_names = [row[0] for row in result.result_rows]
            logger.debug("Found %d tables: %s", len(table_names), table_names)
        except Exception as exc:
            logger.warning("Failed to list ClickHouse tables: %s", exc)
            logger.debug("EXIT discover_schema - returning empty tables")
            return {"tables": []}

        for table_name in table_names:
            logger.debug("Discovering schema for table: %s", table_name)
            try:
                # Fetch columns
                cols_result = client.query(
                    f"""
                    SELECT name, type, default_kind, default_expression
                    FROM system.columns
                    WHERE database = {{db:String}} AND table = {{tbl:String}}
                    ORDER BY position
                    """,
                    parameters={"db": self._database, "tbl": table_name},
                )

                columns: list[dict[str, Any]] = []
                for col_row in cols_result.result_rows:
                    col_name, col_type, _, _ = col_row
                    nullable = "Nullable" in str(col_type)
                    base_type = col_type.replace("Nullable(", "").replace(")", "")
                    columns.append({
                        "name": col_name,
                        "type": base_type,
                        "nullable": nullable,
                    })
                logger.debug("Table %s has %d columns", table_name, len(columns))

                # Fetch sample row
                sample = client.query(
                    f"SELECT * FROM `{self._database}`.`{table_name}` LIMIT 1",
                )
                sample_row: dict[str, Any] = {}
                if sample.result_rows:
                    row_data = sample.result_rows[0]
                    for i, col_name in enumerate(sample.column_names):
                        sample_row[col_name] = row_data[i]
                logger.debug("Table %s sample_row: %s", table_name, sample_row)

                tables.append({
                    "name": table_name,
                    "columns": columns,
                    "foreign_keys": [],  # ClickHouse doesn't have native FKs in the same sense
                    "sample_row": sample_row,
                })
            except Exception as exc:
                logger.warning("Failed to discover schema for table %s: %s", table_name, exc)
                logger.debug("Continuing to next table after error")

        logger.debug("EXIT discover_schema - total tables: %d", len(tables))
        return {"tables": tables}

    # ── Data Extraction ───────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    async def extract(
        self,
        table_name: str,
        columns: list[str],
        filter: dict[str, Any] | None = None,
        batch_size: int = 1000,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Extract rows with OFFSET/LIMIT pagination."""
        logger.debug("ENTER extract: table=%s, columns=%s, filter=%s, batch_size=%d, cursor=%s",
                      table_name, columns, filter, batch_size, cursor)
        client = await self._ensure_client()

        col_list = "*" if columns == ["*"] else ", ".join(f"`{c}`" for c in columns)
        logger.debug("Building query: SELECT %s FROM %s.%s", col_list, self._database, table_name)

        conditions: list[str] = []
        params: dict[str, Any] = {}

        # Default filter: exclude imported rows
        has_imported = False
        if filter and filter.get("_imported") is not None:
            conditions.append(f"`_imported` = {{imported:Int}}")
            params["imported"] = filter["_imported"]
            has_imported = True
            logger.debug("Adding _imported filter: %s", filter["_imported"])

        # Custom filters
        if filter and not has_imported:
            for k, v in filter.items():
                conditions.append(f"`{k}` = {{{k}:String}}")
                params[k] = v
                logger.debug("Adding custom filter: %s = %s", k, v)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
            logger.debug("Query WHERE clause: %s", conditions)

        # Pagination
        offset = 0
        if cursor is not None:
            try:
                offset = int(cursor)
            except ValueError:
                offset = 0
        logger.debug("Pagination: offset=%d, batch_size=%d", offset, batch_size)

        # Build the final SQL with inline integers (offset/limit are always safe ints here)
        # ClickHouse HTTP transport uses LIMIT offset, count syntax (not OFFSET ... LIMIT)
        final_sql = f"SELECT {col_list} FROM `{self._database}`.`{table_name}`"
        if conditions:
            final_sql += " WHERE " + " AND ".join(conditions)
        final_sql += f" ORDER BY tuple() LIMIT {int(offset)}, {int(batch_size)}"

        # Total count
        count_sql = f"SELECT COUNT(*) AS cnt FROM `{self._database}`.`{table_name}`"
        if conditions:
            count_sql += " WHERE " + " AND ".join(conditions)
        logger.debug("Executing count query")

        try:
            total_result = client.query(count_sql, parameters=params)
            total_count = total_result.result_rows[0][0] if total_result.result_rows else 0
        except Exception:
            total_count = 0
        logger.debug("Total row count: %d", total_count)

        logger.debug("Executing data query")
        result = client.query(final_sql, parameters=params)
        rows = []
        for row_data in result.result_rows:
            row_dict = {}
            for i, col_name in enumerate(result.column_names):
                row_dict[col_name] = row_data[i]
            rows.append(row_dict)

        logger.debug("Extracted %d rows (total available: %d)", len(rows), total_count)
        next_cursor = str(offset + len(rows)) if len(rows) == batch_size else None
        origin_ids = [str(i) for i in range(offset, offset + len(rows))]

        logger.debug("EXIT extract: rows=%d, next_cursor=%s", len(rows), next_cursor)
        return {
            "rows": rows,
            "column_names": list(result.column_names) if rows else [],
            "total_count": total_count,
            "next_cursor": next_cursor,
            "origin_ids": origin_ids,
        }

    # ── Import Flagging ───────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    async def mark_imported(
        self,
        table_name: str,
        ids: list[str],
    ) -> bool:
        """Set _imported = 1 for rows matching the given id values."""
        logger.debug("ENTER mark_imported: table=%s, ids_count=%d", table_name, len(ids))
        client = await self._ensure_client()

        if not ids:
            logger.debug("No IDs provided, returning True early")
            return True

        # Build ALTER TABLE UPDATE for ClickHouse
        placeholders = ", ".join(f"{{{i}:String}}" for i in range(len(ids)))
        sql = (
            f"ALTER TABLE `{self._database}`.`{table_name}` "
            f"UPDATE `_imported` = 1 WHERE `id` IN ({placeholders})"
        )
        logger.debug("Executing ALTER TABLE UPDATE: %d IDs", len(ids))

        param_values = {str(i): iid for i, iid in enumerate(ids)}
        client.execute_query(sql, param_values, server_database=self._database)
        logger.debug("mark_imported completed successfully for %d IDs", len(ids))
        return True

    # ── Helpers ───────────────────────────────────────────────────────