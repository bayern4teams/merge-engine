"""
PostgreSQL connector for schema discovery, data extraction, and import flagging.

Uses SQLAlchemy for connection management and psycopg2 for the driver.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import MetaData, Table, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp_server.connectors.base import BaseConnector
from src.orchestrator.utils.retry import TransientError, retry_on_transient

logger = logging.getLogger("merge-engine.connectors.postgres")


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

        conn = self.config.get("connection", {})
        drivername = "postgresql+psycopg2"
        host = conn.get("host") or ""
        port = conn.get("port", 5432)
        user = conn.get("user") or ""
        password = conn.get("password") or ""
        database = conn.get("database") or ""

        url = f"{drivername}://{user}:{password}@{host}:{port}/{database}"
        logger.debug(
            "Creating PostgreSQL async engine: host=%s, port=%d, database=%s, user=%s",
            host, port, database, user,
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
        meta = MetaData()

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
                    columns.append({
                        "name": c["column_name"],
                        "type": self._map_sql_type(c["data_type"], c.get("character_maximum_length")),
                        "nullable": c["is_nullable"] == "YES",
                    })
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

                # Fetch sample row (limit 1)
                sample_query = text(f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT 1')
                async with engine.connect() as conn:
                    sr = await conn.execute(sample_query)
                    sample = sr.mappings().fetchone()
                    await conn.commit()

                tables.append({
                    "schema": schema_name,
                    "name": table_name,
                    "full_name": full_name,
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                    "sample_row": dict(sample) if sample else {},
                })
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
    ) -> dict[str, Any]:
        """Extract rows with OFFSET/LIMIT pagination."""
        logger.debug("ENTER extract: table=%s, columns=%s, filter=%s, batch_size=%d, cursor=%s",
                      table_name, columns, filter, batch_size, cursor)
        engine = await self._ensure_engine()

        # Resolve schema-qualified table name (default to public)
        original_table_name = table_name
        if "." not in table_name:
            table_name = f"public.{table_name}"
        logger.debug("Resolved table name: %s", table_name)

        col_list = "* " if columns == ["*"] else ", ".join(f'"{c}"' for c in columns)
        sql = f"SELECT {col_list} FROM {table_name}"

        conditions: list[str] = []
        params: dict[str, Any] = {}

        # Default filter: exclude imported rows
        has_imported = False
        if filter and filter.get("_imported") is not None:
            conditions.append('"_imported" = :imported')
            params["imported"] = filter["_imported"]
            has_imported = True
            logger.debug("Adding _imported filter: %s", filter["_imported"])

        # Custom filters from caller
        if filter and not has_imported:
            for k, v in filter.items():
                conditions.append(f'"{k}" = :{k}')
                params[k] = v
                logger.debug("Adding custom filter: %s = %s", k, v)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
            logger.debug("Query WHERE clause: %s", conditions)

        # Pagination
        if cursor is not None:
            try:
                offset = int(cursor)
            except ValueError:
                offset = 0
        else:
            offset = 0
        logger.debug("Pagination: offset=%d, batch_size=%d", offset, batch_size)

        sql += f" ORDER BY 1 OFFSET :offset LIMIT :limit"
        params["offset"] = offset
        params["limit"] = batch_size

        # Total count query
        count_sql = f"SELECT COUNT(*) AS cnt FROM {table_name}"
        if conditions:
            count_sql += " WHERE " + " AND ".join(conditions)

        logger.debug("Executing count query")
        async with engine.connect() as conn:  # type: ignore[attr-defined]
            # Total count
            cr = await conn.execute(text(count_sql), params)
            total_count = cr.scalar()
            await conn.commit()
        logger.debug("Total count: %d", total_count)

        logger.debug("Executing data query")
        async with engine.connect() as conn:  # type: ignore[attr-defined]
            dr = await conn.execute(text(sql), params)
            rows = [dict(r) for r in dr.mappings().fetchall()]
            await conn.commit()
        logger.debug("Extracted %d rows", len(rows))

        next_cursor = str(offset + len(rows)) if len(rows) == batch_size else None
        origin_ids = [str(i) for i in range(offset, offset + len(rows))]

        logger.debug("EXIT extract: rows=%d, next_cursor=%s", len(rows), next_cursor)
        return {
            "rows": rows,
            "column_names": list(rows[0].keys()) if rows else [],
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
        """Set _imported = TRUE for rows whose id column matches the given IDs."""
        logger.debug("ENTER mark_imported: table=%s, ids_count=%d", table_name, len(ids))
        engine = await self._ensure_engine()

        if "." not in table_name:
            table_name = f"public.{table_name}"

        if not ids:
            logger.debug("No IDs provided, returning True early")
            return True

        placeholders = ", ".join(f":id_{i}" for i in range(len(ids)))
        sql = text(f'UPDATE "{table_name}" SET "_imported" = TRUE WHERE "id" IN ({placeholders})')
        params = {f"id_{i}": iid for i, iid in enumerate(ids)}
        logger.debug("Executing UPDATE: %d IDs for table %s", len(ids), table_name)

        async with engine.connect() as conn:  # type: ignore[attr-defined]
            await conn.execute(sql, params)
            await conn.commit()

        logger.debug("mark_imported completed successfully for %d IDs", len(ids))
        return True

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