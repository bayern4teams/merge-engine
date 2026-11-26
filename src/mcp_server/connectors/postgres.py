"""
PostgreSQL connector for schema discovery, data extraction, and import flagging.

Uses SQLAlchemy for connection management and psycopg2 for the driver.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import bindparam, text
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
        # Cache for _resolve_schema_qualified -- resolved once per bare table
        # name, not once per call (extract/count/sample_distinct_values/
        # explain_cost each ask this identically, often many times per run).
        self._table_schema_cache: dict[str, str] = {}

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

    async def _resolve_schema_qualified(self, table_name: str) -> str:
        """A bare table name resolved to its REAL schema, not a hardcoded
        "public" guess.

        ``discover_schema`` scans every non-system schema and exposes each
        table by its bare name (``"name"``) plus a separately-carried
        ``"full_name"`` -- but every other query-building method here
        (``extract``, ``sample_distinct_values``, ``profile_column``,
        ``estimate_query_cost``) previously received only the bare name and
        defaulted straight to ``public.<table>`` whenever the caller didn't
        already pass a dotted name. Live-reproduced: testdiffbot's entire
        ``diffbot`` schema (13 tables, zero of them in ``public``) made
        every one of these queries fail with "relation public.<table> does
        not exist" -- which silently zeroed out scope-planning's join-graph
        edges (caught there) and then hard-failed live extraction outright
        (not caught -- this is what a real Boeing run hit once the join
        graph correctly found a path to ``companyexperience``).

        Cached per connector instance (each table asked identically many
        times across one run) via ``self._table_schema_cache``; a table
        found in more than one schema prefers a non-``public`` one, since a
        bulk-loaded origin's real tables are the common case this is fixing
        and ``public`` empty-by-convention is the far more common shape for
        those. Falls back to ``public`` only when the table genuinely isn't
        found anywhere else -- unchanged behavior for a real public-schema
        origin.
        """
        if "." in table_name:
            return table_name
        cached = self._table_schema_cache.get(table_name)
        if cached:
            return f"{cached}.{table_name}"
        schema = "public"
        try:
            engine = await self._ensure_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT table_schema FROM information_schema.tables
                        WHERE table_name = :table
                          AND table_schema NOT IN ('pg_catalog', 'information_schema')
                        ORDER BY (table_schema = 'public') ASC
                        LIMIT 1
                        """
                    ),
                    {"table": table_name},
                )
                row = result.fetchone()
                await conn.commit()
            if row:
                schema = row[0]
        except Exception as exc:
            logger.debug(
                "Could not resolve real schema for bare table name %r; defaulting to 'public': %s",
                table_name,
                exc,
            )
        self._table_schema_cache[table_name] = schema
        return f"{schema}.{table_name}"

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
        async with engine.connect() as conn:
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

        table_name = await self._resolve_schema_qualified(table_name)
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
            async with engine.connect() as conn:
                cr = await conn.execute(text(count_sql), params)
                total_count = cr.scalar()
                await conn.commit()
            logger.debug("Total count: %s", total_count)

        logger.debug("Executing data query")
        async with engine.connect() as conn:
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

    # ── Join-graph support (Phase 4.1) ──────────────────────────────────

    async def sample_distinct_values(
        self,
        table_name: str,
        column: str,
        limit: int = 500,
        filter_values: list[Any] | None = None,
    ) -> list[Any]:
        """Bounded DISTINCT sample, pushed down — never a full-table read.

        Same table-name resolution as extract() (bare name defaults to the
        public schema; a "schema.table" string is used as-is).

        ``filter_values``, when given, restricts to rows whose ``column``
        is one of these values instead of sampling independently — this is
        what makes join-graph inclusion-dependency verification (Phase
        4.1) actually work: two independent arbitrary samples of a large
        parent table and a small child table have a near-zero chance of
        coincidentally overlapping even when the FK relationship is 100%
        intact, since the parent sample almost never happens to contain
        the exact rows the child references. Querying the parent FILTERED
        to the child's own sampled values instead answers the real
        question directly. Found live 2026-08-10: Company (thousands of
        rows) vs. CompanyFinancials (29 rows) scored an 0.069 overlap
        under independent sampling despite every CompanyFinancials row
        genuinely referencing a real company.
        """
        logger.debug(
            "ENTER sample_distinct_values: table=%s, column=%s, limit=%d, filtered=%s",
            table_name,
            column,
            limit,
            bool(filter_values),
        )
        engine = await self._ensure_engine()
        resolved = await self._resolve_schema_qualified(table_name)
        if filter_values:
            sql = text(
                f'SELECT DISTINCT "{column}" FROM {resolved} WHERE "{column}" IN :values LIMIT :limit'
            ).bindparams(bindparam("values", expanding=True))
            params: dict[str, Any] = {"values": filter_values, "limit": limit}
        else:
            # ORDER BY random() is required here, not cosmetic -- see
            # clickhouse.py's identical fix (BP-B2, live-caught 2026-08-19):
            # a plain LIMIT with no ordering is the planner's arbitrary scan
            # order, not a random sample, and silently biases inclusion-
            # coverage estimates. PostgreSQL rejects `SELECT DISTINCT col
            # ... ORDER BY random()` directly ("ORDER BY expressions must
            # appear in select list" -- live-caught against the real
            # global_warehouse destination DB), so the DISTINCT has to be
            # computed in a subquery before randomizing its order.
            sql = text(
                f'SELECT "{column}" FROM ('
                f'SELECT DISTINCT "{column}" FROM {resolved} WHERE "{column}" IS NOT NULL'
                f") AS distinct_vals "
                "ORDER BY random() LIMIT :limit"
            )
            params = {"limit": limit}
        async with engine.connect() as conn:
            result = await conn.execute(sql, params)
            values = [row[0] for row in result.fetchall()]
            await conn.commit()
        logger.debug("EXIT sample_distinct_values: %d value(s)", len(values))
        return values

    # ── Capability matrix (Part B / P-2) ────────────────────────────────

    def capabilities(self) -> dict[str, bool]:
        return {
            "supports_declared_pk": True,
            "supports_declared_fk": True,
            "supports_declared_unique_constraints": True,
            "supports_statistics": True,
            "supports_sampling": True,
            "supports_explain": True,
            "supports_cte": True,
            "supports_temp_tables": True,
            "supports_window_functions": True,
            "supports_pushdown": True,
            "supports_parallel_queries": True,
            "supports_column_comments": True,
            "supports_table_comments": True,
        }

    async def discover_indexes(self, table_name: str) -> list[dict[str, Any]]:
        """Real PostgreSQL indexes from ``pg_index``/``pg_class`` — never
        parsed out of ``pg_indexes``' pre-formatted DDL text, so multi-
        column indexes come back with their columns in the actual index
        key order, not guessed from a string."""
        engine = await self._ensure_engine()
        schema, table = self._split_table_name(table_name)
        sql = text("""
            SELECT
                ix.relname AS index_name,
                i.indisunique AS is_unique,
                i.indisprimary AS is_primary,
                array_agg(a.attname ORDER BY k.ord) AS columns
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_class ix ON ix.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
            WHERE n.nspname = :schema AND t.relname = :table
            GROUP BY ix.relname, i.indisunique, i.indisprimary
        """)
        async with engine.connect() as conn:
            result = await conn.execute(sql, {"schema": schema, "table": table})
            rows = result.mappings().fetchall()
            await conn.commit()
        return [
            {
                "name": row["index_name"],
                "columns": list(row["columns"]),
                "is_unique": bool(row["is_unique"]),
                "is_primary": bool(row["is_primary"]),
                "kind": "btree",
            }
            for row in rows
        ]

    async def discover_statistics(self, table_name: str) -> dict[str, Any]:
        """Row-count estimate and size straight from PostgreSQL's own
        catalog (``pg_class``/``pg_stat_user_tables``) — the planner's own
        numbers, current as of the last ``ANALYZE``, never a live
        ``COUNT(*)`` scan.

        ``pg_class.reltuples`` is ``-1`` for a table that has never been
        analyzed (live-caught against a freshly-loaded table with this
        exact method: a real 2-row table reported -1, not 0 or 2). Falls
        back to ``pg_stat_user_tables.n_live_tup`` in that case, which
        autovacuum's stats collector tracks independently of ``ANALYZE`` --
        still an estimate, but a live one, rather than the "nobody has
        looked yet" sentinel.
        """
        engine = await self._ensure_engine()
        schema, table = self._split_table_name(table_name)
        sql = text("""
            SELECT
                c.reltuples::BIGINT AS row_count_estimate,
                s.n_live_tup,
                pg_total_relation_size(c.oid) AS total_bytes,
                s.last_analyze,
                s.last_autoanalyze
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s
                ON s.schemaname = n.nspname AND s.relname = c.relname
            WHERE n.nspname = :schema AND c.relname = :table
        """)
        async with engine.connect() as conn:
            result = await conn.execute(sql, {"schema": schema, "table": table})
            row = result.mappings().fetchone()
            await conn.commit()
        if row is None:
            return {"row_count_estimate": 0, "total_bytes": 0}
        last_analyzed = row["last_analyze"] or row["last_autoanalyze"]
        row_count_estimate = row["row_count_estimate"]
        if row_count_estimate < 0:
            row_count_estimate = row["n_live_tup"] or 0
        return {
            "row_count_estimate": row_count_estimate,
            "total_bytes": row["total_bytes"],
            "last_analyzed": last_analyzed.isoformat() if last_analyzed else None,
        }

    async def profile_column(
        self, table_name: str, column: str, mode: str = "SAMPLED", sample_size: int = 10_000
    ) -> dict[str, Any]:
        """Null/distinct/min/max, computed database-side.

        SAMPLED wraps the source in a ``TABLESAMPLE SYSTEM`` (or a plain
        LIMIT for a small table) so a 40M-row table is never fully scanned
        by default; FULL removes that bound explicitly.
        """
        engine = await self._ensure_engine()
        resolved = await self._resolve_schema_qualified(table_name)
        source = (
            f"(SELECT * FROM {resolved} LIMIT :sample_size) sub" if mode == "SAMPLED" else resolved
        )
        sql = text(f"""
            SELECT
                COUNT(*) AS total_count,
                COUNT(*) - COUNT("{column}") AS null_count,
                COUNT(DISTINCT "{column}") AS distinct_count,
                MIN("{column}"::TEXT) AS min_value,
                MAX("{column}"::TEXT) AS max_value
            FROM {source}
        """)
        params = {"sample_size": sample_size} if mode == "SAMPLED" else {}
        async with engine.connect() as conn:
            result = await conn.execute(sql, params)
            row = result.mappings().fetchone()
            await conn.commit()
        total = row["total_count"] if row else 0
        null_count = row["null_count"] if row else 0
        return {
            "mode": mode,
            "total_count": total,
            "null_count": null_count,
            "null_ratio": (null_count / total) if total else 0.0,
            "distinct_count": row["distinct_count"] if row else 0,
            "min_value": row["min_value"] if row else None,
            "max_value": row["max_value"] if row else None,
        }

    async def estimate_query_cost(
        self, table_name: str, filter: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """The planner's own cost estimate (``EXPLAIN (FORMAT JSON)``) for
        reading ``table_name`` under ``filter`` — a real number from the
        query planner, not a row-count guess."""
        engine = await self._ensure_engine()
        resolved = await self._resolve_schema_qualified(table_name)
        where_sql = ""
        params: dict[str, Any] = {}
        if filter:
            clauses = []
            for i, (col, val) in enumerate(filter.items()):
                key = f"p{i}"
                clauses.append(f'"{col}" = :{key}')
                params[key] = val
            where_sql = " WHERE " + " AND ".join(clauses)
        sql = text(f"EXPLAIN (FORMAT JSON) SELECT * FROM {resolved}{where_sql}")
        async with engine.connect() as conn:
            result = await conn.execute(sql, params)
            plan = result.scalar()
            await conn.commit()
        root = plan[0]["Plan"] if plan else {}
        return {
            "estimated_rows": root.get("Plan Rows", 0),
            "estimated_cost": root.get("Total Cost", 0.0),
            "node_type": root.get("Node Type", ""),
        }

    @staticmethod
    def _split_table_name(table_name: str) -> tuple[str, str]:
        """Split a possibly-schema-qualified table name; bare names
        default to the ``public`` schema, matching this connector's other
        table-name resolution (extract/sample_distinct_values)."""
        if "." in table_name:
            schema, table = table_name.split(".", 1)
            return schema, table
        return "public", table_name

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
