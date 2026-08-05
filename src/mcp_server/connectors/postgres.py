"""
PostgreSQL connector for schema discovery, data extraction, and import flagging.

Uses SQLAlchemy for connection management and psycopg2 for the driver.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp_server.connectors.base import BaseConnector
from src.mcp_server.connectors.filters import normalize_filter
from src.mcp_server.connectors.keyset import (
    advance_boundary,
    assert_key_unique,
    choose_keyset,
    decode_cursor,
    drop_consumed,
    encode_cursor,
    keyset_is_unique,
)
from src.mcp_server.connectors.watermark import detect_watermark_column
from src.orchestrator.utils.retry import retry_on_transient

logger = logging.getLogger("merge-engine.connectors.postgres")

# Text that looks like a timestamp, with and without a UTC offset. Cursor
# values arrive as strings (JSON has no date type) and asyncpg binds by
# declared type, so the placeholder has to name the type it is comparing to.
_TIMESTAMPTZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)$"
)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?$")

# Alias the extracted table so the whole row can be hashed for a deterministic
# tiebreak when the ordering key is not provably unique.
_ROW_ALIAS = "_src"


def _keyset_placeholder(index: int, value: Any) -> str:
    """The bind expression for one keyset column, cast if it needs it.

    The cast is chosen from the value's own shape rather than assumed: a
    naive timestamp compared against a ``timestamptz`` placeholder would be
    shifted by the session timezone, which is a silently wrong batch
    boundary rather than an error.
    """
    name = f":ks_{index}"
    if isinstance(value, str):
        if _TIMESTAMPTZ_RE.match(value):
            return _text_cast(name, "TIMESTAMPTZ")
        if _TIMESTAMP_RE.match(value):
            return _text_cast(name, "TIMESTAMP")
    return name


def _text_cast(placeholder: str, sql_type: str) -> str:
    """Cast a bind parameter, via text, to `sql_type`.

    The inner ``AS TEXT`` is load-bearing. asyncpg reads each parameter's
    type from how the statement uses it, so a bare ``CAST($1 AS TIMESTAMPTZ)``
    makes $1 a timestamptz and asyncpg then rejects the string we actually
    have. Pinning $1 to text first lets PostgreSQL do the parsing instead.
    """
    return f"CAST(CAST({placeholder} AS TEXT) AS {sql_type})"


class PostgresConnector(BaseConnector):
    """PostgreSQL database connector."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._engine: AsyncEngine | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def _ensure_engine(self) -> AsyncEngine:
        """Create or return the async SQLAlchemy engine."""
        logger.debug("ENTER _ensure_engine")
        if self._engine is not None:
            logger.debug("Engine already exists, returning")
            return self._engine

        conn = self.config
        # Support both flat connection dict and nested origin config shape
        if "connection" in conn and isinstance(conn["connection"], dict):
            conn = conn["connection"]
        drivername = "postgresql+asyncpg"
        host = conn.get("host") or ""
        port = conn.get("port", 5432)
        user = conn.get("user") or ""
        password = conn.get("password") or ""
        database = conn.get("database") or ""

        url = f"{drivername}://{user}:{password}@{host}:{port}/{database}"
        logger.debug(
            "Creating PostgreSQL async engine: host=%s, port=%d, database=%s, user=%s",
            host,
            port,
            database,
            user,
        )
        self._engine = create_async_engine(
            url,
            echo=False,
            pool_size=5,
            pool_recycle=3600,
            pool_pre_ping=True,
        )
        logger.debug("PostgreSQL async engine created successfully")
        return self._engine

    async def close(self) -> None:
        logger.debug("ENTER close")
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            logger.debug("PostgreSQL engine disposed")

    # ── Schema Discovery ──────────────────────────────────────────────

    async def discover_schema(self) -> dict[str, Any]:
        """Discover tables, columns, types, FKs, and one sample row per table."""
        logger.debug("ENTER discover_schema")
        engine = await self._ensure_engine()

        # ── Fetch all user tables ──────────────────────────────────────
        query = text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        )

        tables: list[dict[str, Any]] = []
        logger.debug("Querying information_schema.tables for user tables")
        async with engine.connect() as conn:  # type: ignore[attr-defined]
            result = await conn.execute(query)
            rows = result.mappings().fetchall()
            await conn.commit()
        logger.debug("Found %d user tables", len(rows))

        for tbl_row in rows:
            schema_name = tbl_row["table_schema"]
            table_name = tbl_row["table_name"]
            full_name = f"{schema_name}.{table_name}"
            logger.debug("Discovering schema for table: %s", full_name)

            try:
                # Fetch columns
                cols_query = text(
                    """
                    SELECT column_name, data_type, is_nullable, character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = :schema AND table_name = :table
                    ORDER BY ordinal_position
                    """
                )
                async with engine.connect() as conn:
                    r = await conn.execute(cols_query, {"schema": schema_name, "table": table_name})
                    col_rows = r.mappings().fetchall()
                    await conn.commit()

                columns: list[dict[str, Any]] = []
                for c in col_rows:
                    columns.append(
                        {
                            "name": c["column_name"],
                            "type": self._map_sql_type(
                                c["data_type"], c.get("character_maximum_length")
                            ),
                            "nullable": c["is_nullable"] == "YES",
                        }
                    )
                logger.debug("  Table %s: %d columns", full_name, len(columns))

                # Fetch foreign keys
                fk_query = text(
                    """
                    SELECT kcu.column_name,
                           kcu.constraint_name,
                           ccu.table_name AS ref_table,
                           ccu.column_name AS ref_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                        ON ccu.constraint_name = tc.constraint_name
                        AND ccu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema = :schema
                      AND tc.table_name = :table
                    """
                )
                async with engine.connect() as conn:
                    fr = await conn.execute(fk_query, {"schema": schema_name, "table": table_name})
                    fk_rows = fr.mappings().fetchall()
                    await conn.commit()

                foreign_keys: list[dict[str, Any]] = [
                    {
                        "column": fk["column_name"],
                        "ref_table": fk["ref_table"],
                        "ref_column": fk["ref_column"],
                    }
                    for fk in fk_rows
                ]
                logger.debug("  Table %s: %d foreign keys", full_name, len(foreign_keys))

                # Fetch primary key columns
                pk_query = text(
                    """
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = :schema
                      AND tc.table_name = :table
                    ORDER BY kcu.ordinal_position
                    """
                )
                async with engine.connect() as conn:
                    pr = await conn.execute(pk_query, {"schema": schema_name, "table": table_name})
                    pk_rows = pr.mappings().fetchall()
                    await conn.commit()
                primary_key = [r["column_name"] for r in pk_rows]
                logger.debug("  Table %s: primary_key=%s", full_name, primary_key)

                # Fetch sample row (limit 1)
                sample_query = text(f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT 1')
                async with engine.connect() as conn:
                    sr = await conn.execute(sample_query)
                    sample = sr.mappings().fetchone()
                    await conn.commit()

                watermark_column, watermark_kind = detect_watermark_column(columns, primary_key)
                logger.debug(
                    "  Table %s: watermark_column=%s, watermark_kind=%s",
                    full_name,
                    watermark_column,
                    watermark_kind,
                )

                tables.append(
                    {
                        "schema": schema_name,
                        "name": table_name,
                        "full_name": full_name,
                        "columns": columns,
                        "foreign_keys": foreign_keys,
                        "primary_key": primary_key,
                        "watermark_column": watermark_column,
                        "watermark_kind": watermark_kind,
                        "sample_row": dict(sample) if sample else {},
                    }
                )
            except Exception as exc:
                logger.warning("Failed to discover schema for %s: %s", full_name, exc)
                logger.debug("Continuing to next table after error for %s", full_name)

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

        Pagination is keyset ("seek") whenever the table offers a unique
        ordering — the watermark column plus the primary key. Each batch asks
        for the rows *after* the last one it saw, which an index satisfies in
        constant time, instead of ``OFFSET n`` which walks past every earlier
        row again on every batch. Tables with neither a watermark nor a
        primary key still fall back to offset pagination.

        Parameters
        ----------
        watermark : dict, optional
            ``{"column": str, "since": Any, "kind": "numeric"|"timestamp"}``.
            When given, pushes ``WHERE <column> > :wm_since`` down into the
            query (server-side, index-friendly) and orders by that column
            ascending so the caller can read the new high-water mark off the
            last extracted row.
        pk_columns : list[str], optional
            Primary key columns, used to break ties in a non-unique watermark
            so a batch boundary cannot skip or repeat rows.
        """
        logger.debug(
            "ENTER extract: table=%s, columns=%s, filter=%s, batch_size=%d, cursor=%s, watermark=%s",
            table_name,
            columns,
            filter,
            batch_size,
            cursor,
            watermark,
        )
        engine = await self._ensure_engine()

        # Resolve schema-qualified table name (default to public)
        if "." not in table_name:
            table_name = f"public.{table_name}"
        logger.debug("Resolved table name: %s", table_name)

        col_list = "* " if columns == ["*"] else ", ".join(f'"{c}"' for c in columns)

        conditions: list[str] = []
        params: dict[str, Any] = {}

        # Custom filters from caller. Supports equals (back-compat), ILIKE
        # (case-insensitive substring, for a subject-name match) and IN (for a
        # foreign key restricted to a set of subject ids). Indexed param names
        # (:f0, :f1…) avoid colliding with a column literally named "limit".
        for i, cond in enumerate(normalize_filter(filter)):
            col, op, val = cond["column"], cond["op"], cond["value"]
            if op == "ilike":
                conditions.append(f'"{col}"::text ILIKE :f{i}')
                params[f"f{i}"] = f"%{val}%"
            elif op == "in":
                names = [f"f{i}_{j}" for j in range(len(val))]
                conditions.append(f'"{col}" IN (' + ", ".join(f":{n}" for n in names) + ")")
                for n, v in zip(names, val, strict=True):
                    params[n] = v
            else:
                conditions.append(f'"{col}" = :f{i}')
                params[f"f{i}"] = val
            logger.debug("Adding custom filter: %s %s %s", col, op, val)

        watermark_col = None
        if watermark and watermark.get("since") is not None:
            watermark_col = watermark["column"]
            if watermark.get("kind") == "timestamp":
                # Same asyncpg typed-bind trap as the keyset predicate: the
                # stored watermark is TEXT, so the parameter must be pinned
                # to text before PostgreSQL parses it as a timestamp.
                since_text = str(watermark.get("since") or "")
                sql_type = "TIMESTAMPTZ" if _TIMESTAMPTZ_RE.match(since_text) else "TIMESTAMP"
                conditions.append(f'"{watermark_col}" > {_text_cast(":wm_since", sql_type)}')
                params["wm_since"] = since_text
            else:
                conditions.append(f'"{watermark_col}" > :wm_since')
                params["wm_since"] = watermark["since"]
            logger.debug("Adding watermark filter: %s > %s", watermark_col, watermark["since"])

        logger.debug("Query WHERE clause: %s", conditions)

        # ── Pagination ──────────────────────────────────────────────
        position = decode_cursor(cursor)
        offset = position["offset"]
        keyset = choose_keyset(watermark, pk_columns)

        # The count query must be built before the keyset predicate is added,
        # or it would only count the rows still ahead of us.
        count_conditions = list(conditions)

        unique_key = keyset_is_unique(keyset, pk_columns)
        boundary_seen = 0 if unique_key else position["boundary_seen"]
        if keyset:
            quoted_keyset = ", ".join(f'"{c}"' for c in keyset)
            if position["keyset"]:
                # Row-value comparison: (a, b) >= (:k0, :k1) is exactly
                # "at or after this row in this ordering", and PostgreSQL can
                # answer it from a composite index.
                #
                # ">=" not ">" because the key is not guaranteed unique. A
                # strict ">" drops every row sharing the boundary key; the
                # already-returned ones are removed in Python instead.
                #
                # Cursors round-trip through JSON, so a timestamp comes back
                # as text and asyncpg — which binds by declared type — refuses
                # to compare it. Casting in SQL is the same fix the watermark
                # filter above already uses.
                placeholders = ", ".join(
                    _keyset_placeholder(i, value) for i, value in enumerate(position["keyset"])
                )
                comparison = ">" if unique_key else ">="
                conditions.append(f"({quoted_keyset}) {comparison} ({placeholders})")
                for i, value in enumerate(position["keyset"]):
                    params[f"ks_{i}"] = value
            order_by = quoted_keyset
            if not unique_key:
                # Equal-key rows come back in whatever order the plan happens
                # to produce, and that order is not stable between queries —
                # measured: 3 duplicated rows in 16,044 on public.rental.
                # Hashing the whole row gives a deterministic tiebreak, so
                # "skip the ones already returned" means the same rows twice.
                order_by += f", md5({_ROW_ALIAS}::text)"
            logger.debug("Keyset pagination on (%s), after=%s", quoted_keyset, position["keyset"])
        else:
            # No unique ordering available: fall back to offset paging and
            # say so, because this is the case that will not scale.
            order_by = f'"{watermark["column"]}"' if watermark and watermark.get("column") else "1"
            logger.warning(
                "Table %s has no watermark or primary key to seek on — using "
                "OFFSET pagination, which slows down as the table is read.",
                table_name,
            )

        sql = f"SELECT {col_list} FROM {table_name} AS {_ROW_ALIAS}"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += f" ORDER BY {order_by} LIMIT :limit"
        # Widened by boundary_seen so a full batch survives dropping the
        # boundary rows an earlier batch already returned.
        fetch_limit = int(batch_size) + int(boundary_seen)
        params["limit"] = fetch_limit if keyset else batch_size
        if not keyset:
            sql += " OFFSET :offset"
            params["offset"] = offset

        # ── Row count: once per table, not once per batch ───────────
        total_count = position["total_count"]
        if total_count is None:
            count_sql = f"SELECT COUNT(*) AS cnt FROM {table_name}"
            if count_conditions:
                count_sql += " WHERE " + " AND ".join(count_conditions)
            logger.debug("Executing count query (first batch only)")
            async with engine.connect() as conn:  # type: ignore[attr-defined]
                cr = await conn.execute(text(count_sql), params)
                total_count = cr.scalar()
                await conn.commit()
            logger.debug("Total count: %s", total_count)

        logger.debug("Executing data query")
        async with engine.connect() as conn:  # type: ignore[attr-defined]
            dr = await conn.execute(text(sql), params)
            rows = [dict(r) for r in dr.mappings().fetchall()]
            await conn.commit()
        fetched = len(rows)
        if keyset and unique_key:
            assert_key_unique(rows, keyset, table_name)
        elif keyset:
            rows = drop_consumed(rows, keyset, position["keyset"], boundary_seen)[:batch_size]
        logger.debug("Extracted %d rows", len(rows))

        next_cursor = None
        if keyset:
            # A short window means the table is exhausted. Judging by
            # len(rows) would stop early whenever dropped boundary rows left
            # the batch under-full.
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
            next_cursor = encode_cursor(offset=offset + len(rows), total_count=total_count)

        origin_ids = [str(i) for i in range(offset, offset + len(rows))]

        logger.debug("EXIT extract: rows=%d, next_cursor=%s", len(rows), next_cursor)
        return {
            "rows": rows,
            "column_names": list(rows[0].keys()) if rows else [],
            "total_count": total_count,
            "next_cursor": next_cursor,
            "origin_ids": origin_ids,
        }

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _map_sql_type(data_type: str, char_max: int | None = None) -> str:
        """Map PostgreSQL data_type to a cross-database type string."""
        type_map = {
            "character varying": "VARCHAR",
            "character": "CHAR",
            "text": "TEXT",
            "integer": "INTEGER",
            "bigint": "BIGINT",
            "smallint": "SMALLINT",
            "numeric": "DECIMAL",
            "real": "REAL",
            "double precision": "DOUBLE",
            "boolean": "BOOLEAN",
            "timestamp without time zone": "TIMESTAMP",
            "timestamp with time zone": "TIMESTAMPTZ",
            "date": "DATE",
            "time without time zone": "TIME",
            "json": "JSON",
            "jsonb": "JSONB",
            "uuid": "UUID",
            "bytea": "BYTEA",
            "inet": "INET",
        }
        mapped = type_map.get(data_type, data_type.upper())
        if char_max and data_type in ("character varying", "character"):
            return f"VARCHAR({char_max})"
        return mapped
