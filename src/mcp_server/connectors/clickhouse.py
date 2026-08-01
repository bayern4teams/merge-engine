"""
ClickHouse connector for schema discovery, data extraction, and import flagging.

Uses clickhouse-connect for driver access.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import clickhouse_connect

from src.mcp_server.connectors.base import BaseConnector
from src.mcp_server.connectors.filters import normalize_filter
from src.mcp_server.connectors.watermark import detect_watermark_column
from src.mcp_server.connectors.keyset import (
    advance_boundary,
    drop_consumed,
    choose_keyset,
    decode_cursor,
    encode_cursor,
)
from src.orchestrator.utils.retry import TransientError, retry_on_transient

logger = logging.getLogger("merge-engine.connectors.clickhouse")


def _clickhouse_param_type(value: Any) -> str:
    """Name the bind type for a keyset value.

    ClickHouse's parameter syntax carries the type, and cursors round-trip
    through JSON, so a timestamp arrives as text and has to be named as such
    for the server to parse it back.
    """
    if isinstance(value, bool):
        return "UInt8"
    if isinstance(value, int):
        return "Int64"
    if isinstance(value, float):
        return "Float64"
    text = str(value)
    if _CH_DATETIME_RE.match(text):
        return "DateTime"
    return "String"


_CH_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"
)

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

        conn = self.config
        # Support both flat connection dict and nested origin config shape
        if "connection" in conn and isinstance(conn["connection"], dict):
            conn = conn["connection"]
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
                # Fetch columns (+ whether each is part of the primary key)
                cols_result = client.query(
                    f"""
                    SELECT name, type, default_kind, default_expression, is_in_primary_key
                    FROM system.columns
                    WHERE database = {{db:String}} AND table = {{tbl:String}}
                    ORDER BY position
                    """,
                    parameters={"db": self._database, "tbl": table_name},
                )

                columns: list[dict[str, Any]] = []
                primary_key: list[str] = []
                for col_row in cols_result.result_rows:
                    col_name, col_type, _, _, is_pk = col_row
                    nullable = "Nullable" in str(col_type)
                    base_type = col_type.replace("Nullable(", "").replace(")", "")
                    columns.append({
                        "name": col_name,
                        "type": base_type,
                        "nullable": nullable,
                    })
                    if is_pk:
                        primary_key.append(col_name)
                logger.debug("Table %s has %d columns, primary_key=%s", table_name, len(columns), primary_key)

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

                watermark_column, watermark_kind = detect_watermark_column(columns, primary_key)
                logger.debug("Table %s: watermark_column=%s, watermark_kind=%s", table_name, watermark_column, watermark_kind)

                tables.append({
                    "name": table_name,
                    "columns": columns,
                    "foreign_keys": [],  # ClickHouse doesn't have native FKs in the same sense
                    "primary_key": primary_key,
                    "watermark_column": watermark_column,
                    "watermark_kind": watermark_kind,
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
        watermark: dict[str, Any] | None = None,
        pk_columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Extract rows a batch at a time, seeking rather than counting past.

        ``LIMIT offset, n`` makes ClickHouse re-read and discard every earlier
        row on every batch — on the 43M-row cell_towers table that dominates
        the whole extraction. When the table offers a unique ordering
        (watermark plus sorting/primary key) each batch instead asks for the
        rows *after* the last one seen.

        Parameters
        ----------
        watermark : dict, optional
            ``{"column": str, "since": Any, "kind": "numeric"|"timestamp"}``.
            When given, pushes an incremental ``> since`` filter down into
            the query and orders by that column ascending.
        pk_columns : list[str], optional
            Key columns used to break ties in a non-unique watermark.
        """
        logger.debug("ENTER extract: table=%s, columns=%s, filter=%s, batch_size=%d, cursor=%s, watermark=%s",
                      table_name, columns, filter, batch_size, cursor, watermark)
        client = await self._ensure_client()

        col_list = "*" if columns == ["*"] else ", ".join(f"`{c}`" for c in columns)
        logger.debug("Building query: SELECT %s FROM %s.%s", col_list, self._database, table_name)

        conditions: list[str] = []
        params: dict[str, Any] = {}

        # Custom filters: equals (back-compat), ILIKE (subject-name substring)
        # and IN (foreign key in a set of subject ids). ClickHouse binds by
        # declared type, so each value's placeholder type is inferred from the
        # Python value — an int id compared as a String would never match.
        for i, cond in enumerate(normalize_filter(filter)):
            col, op, val = cond["column"], cond["op"], cond["value"]
            if op == "ilike":
                conditions.append(f"toString(`{col}`) ILIKE {{f{i}:String}}")
                params[f"f{i}"] = f"%{val}%"
            elif op == "in":
                names = [f"f{i}_{j}" for j in range(len(val))]
                conditions.append(
                    f"`{col}` IN ("
                    + ", ".join(
                        f"{{{n}:{_clickhouse_param_type(v)}}}" for n, v in zip(names, val)
                    )
                    + ")"
                )
                for n, v in zip(names, val):
                    params[n] = v
            else:
                conditions.append(f"`{col}` = {{f{i}:{_clickhouse_param_type(val)}}}")
                params[f"f{i}"] = val
            logger.debug("Adding custom filter: %s %s %s", col, op, val)

        if watermark and watermark.get("since") is not None:
            wm_col = watermark["column"]
            wm_type = "DateTime" if watermark.get("kind") == "timestamp" else "Int64"
            conditions.append(f"`{wm_col}` > {{wm_since:{wm_type}}}")
            params["wm_since"] = watermark["since"]
            logger.debug("Adding watermark filter: %s > %s", wm_col, watermark["since"])

        if conditions:
            logger.debug("Query WHERE clause: %s", conditions)

        # ── Pagination ──────────────────────────────────────────────
        position = decode_cursor(cursor)
        offset = position["offset"]
        keyset = choose_keyset(watermark, pk_columns)

        # Built before the keyset predicate is added, or it would only count
        # the rows still ahead of us.
        count_conditions = list(conditions)

        # ClickHouse has no uniqueness constraint. What `system.columns`
        # reports as the primary key is the sparse sorting key, and MergeTree
        # happily stores duplicate rows under it — cell_towers has 43,276,150
        # rows for ~42.97M distinct (radio, mcc, net, created, cell) tuples.
        # Trusting it the way a PostgreSQL PRIMARY KEY can be trusted would
        # take the strict-">" fast path and drop rows at every batch boundary,
        # so this connector always paginates defensively.
        boundary_seen = position["boundary_seen"]
        if keyset:
            quoted_keyset = ", ".join(f"`{c}`" for c in keyset)
            if position["keyset"]:
                # ClickHouse supports tuple comparison, which is exactly
                # "at or after this row in this ordering". It is ">=" and not
                # ">" because the key is not guaranteed unique — the rows at
                # the boundary key that were already returned get dropped in
                # Python below rather than excluded by SQL, which would also
                # discard the ones we have not seen.
                placeholders = []
                for i, value in enumerate(position["keyset"]):
                    ch_type = _clickhouse_param_type(value)
                    placeholders.append(f"{{ks_{i}:{ch_type}}}")
                    params[f"ks_{i}"] = value
                conditions.append(
                    f"({quoted_keyset}) >= ({', '.join(placeholders)})"
                )
            order_clause = quoted_keyset
            # Deterministic tiebreak within a run of equal keys, so
            # "skip the ones already returned" skips the same rows twice.
            order_clause += ", cityHash64(*)"
            logger.debug("Keyset pagination on (%s), after=%s",
                         quoted_keyset, position["keyset"])
        else:
            order_clause = (
                f"`{watermark['column']}`"
                if watermark and watermark.get("column") else "tuple()"
            )
            logger.warning(
                "Table %s has no watermark or key column to seek on — using "
                "OFFSET pagination, which slows down as the table is read.",
                table_name,
            )

        final_sql = f"SELECT {col_list} FROM `{self._database}`.`{table_name}`"
        if conditions:
            final_sql += " WHERE " + " AND ".join(conditions)
        final_sql += f" ORDER BY {order_clause}"
        # ClickHouse HTTP transport uses LIMIT offset, count syntax.
        # The window is widened by boundary_seen so that after dropping the
        # already-returned boundary rows a full batch still remains.
        fetch_limit = int(batch_size) + int(boundary_seen)
        if keyset:
            final_sql += f" LIMIT {fetch_limit}"
        else:
            final_sql += f" LIMIT {int(offset)}, {int(batch_size)}"

        # ── Row count: once per table, not once per batch ───────────
        total_count = position["total_count"]
        if total_count is None:
            count_sql = f"SELECT COUNT(*) AS cnt FROM `{self._database}`.`{table_name}`"
            if count_conditions:
                count_sql += " WHERE " + " AND ".join(count_conditions)
            logger.debug("Executing count query (first batch only)")
            try:
                total_result = client.query(count_sql, parameters=params)
                total_count = (
                    total_result.result_rows[0][0]
                    if total_result.result_rows else 0
                )
            except Exception:
                total_count = 0
        logger.debug("Total row count: %s", total_count)

        logger.debug("Executing data query")
        result = client.query(final_sql, parameters=params)
        rows = []
        for row_data in result.result_rows:
            row_dict = {}
            for i, col_name in enumerate(result.column_names):
                row_dict[col_name] = row_data[i]
            rows.append(row_dict)

        fetched = len(rows)
        if keyset:
            rows = drop_consumed(
                rows, keyset, position["keyset"], boundary_seen
            )[:batch_size]

        logger.debug("Extracted %d rows (total available: %s)", len(rows), total_count)
        next_cursor = None
        if keyset:
            # A short window means the table is exhausted; a full one means
            # there may be more. Judging by len(rows) instead would stop early
            # whenever dropped boundary rows left the batch under-full.
            if fetched == fetch_limit and rows:
                keyset_values, next_seen = advance_boundary(
                    rows, keyset, position["keyset"], boundary_seen
                )
                next_cursor = encode_cursor(
                    keyset_values=keyset_values,
                    total_count=total_count,
                    boundary_seen=next_seen,
                )
        elif len(rows) == batch_size:
            next_cursor = encode_cursor(
                offset=offset + len(rows), total_count=total_count
            )
        origin_ids = [str(i) for i in range(offset, offset + len(rows))]

        logger.debug("EXIT extract: rows=%d, next_cursor=%s", len(rows), next_cursor)
        return {
            "rows": rows,
            "column_names": list(result.column_names) if rows else [],
            "total_count": total_count,
            "next_cursor": next_cursor,
            "origin_ids": origin_ids,
        }

    # ── Helpers ───────────────────────────────────────────────────────