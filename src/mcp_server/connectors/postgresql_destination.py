"""
PostgreSQL destination connector for data loading and schema inspection.

Uses psycopg2-binary for connection and cursor management.
This connector is used for the destination PostgreSQL database where
merged/unified data is loaded.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2 import OperationalError as Psycopg2OperationalError
from psycopg2.extensions import connection as Psycopg2Connection

from src.orchestrator.utils.retry import TransientError, retry_on_transient

logger = logging.getLogger("merge-engine.connectors.postgresql_destination")


class PostgreSQLDestinationConnector:
    """PostgreSQL connector for destination writes.

    Handles upserts, schema inspection, and schema extension for the
    destination PostgreSQL database.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the connector with configuration.

        Parameters
        ----------
        config : dict
            Destination connection configuration with keys:
            - host (str): PostgreSQL hostname or IP
            - port (int): Port number (default 5432)
            - user (str): Username
            - password (str): Password
            - database (str): Database name
            - schema (str): Schema name (default "public")
        """
        self._config = config
        self._connection: Psycopg2Connection | None = None
        self._schema: str = config.get("schema", "public")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def _ensure_connection(self) -> Psycopg2Connection:
        """Create or return the PostgreSQL connection."""
        if self._connection is not None and self._connection.closed == 0:
            return self._connection

        host = self._config.get("host", "localhost")
        port = self._config.get("port", 5432)
        user = self._config.get("user", "")
        password = self._config.get("password", "")
        database = self._config.get("database", "")

        self._connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=30,
        )
        return self._connection

    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None and self._connection.closed == 0:
            self._connection.close()
        self._connection = None

    def __enter__(self):
        self._ensure_connection()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ── Schema Inspection ─────────────────────────────────────────────

    def get_table_schema(self, table_name: str) -> dict[str, Any]:
        """Inspect the schema of an existing PostgreSQL table.

        Parameters
        ----------
        table_name : str
            Table name (may include schema prefix like "public.my_table").

        Returns
        -------
        dict
            {
                "columns": [
                    {"name": ..., "type": ..., "nullable": ...}
                ],
                "primary_key": str | None
            }
        """
        conn = self._ensure_connection()

        # Check if table exists
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                );
            """, (self._schema, table_name))
            exists = cursor.fetchone()[0]

            if not exists:
                return {"columns": [], "primary_key": None}

            # Get column info
            cursor.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position;
            """, (self._schema, table_name))
            columns = [
                {
                    "name": row[0],
                    "type": self._normalize_type(row[1]),
                    "nullable": row[2] == "YES",
                }
                for row in cursor.fetchall()
            ]

            # Get primary key
            cursor.execute("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s;
            """, (self._schema, table_name))
            pk_row = cursor.fetchone()
            primary_key = pk_row[0] if pk_row else None

            return {"columns": columns, "primary_key": primary_key}
        finally:
            cursor.close()

    def list_tables(self) -> list[str]:
        """List all tables in the target schema.

        Returns
        -------
        list[str]
            Table names.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE';
            """, (self._schema,))
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()

    def column_exists(self, table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table.

        Parameters
        ----------
        table_name : str
            Table name.
        column_name : str
            Column name.

        Returns
        -------
        bool
            True if column exists.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s AND column_name = %s
                );
            """, (self._schema, table_name, column_name))
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    # ── Data Loading (Upsert) ─────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def load_batch(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        mapping: dict[str, Any] | None = None,
        upsert_key: str = "id",
    ) -> dict[str, int]:
        """Upsert records into a PostgreSQL table.

        Uses INSERT ... ON CONFLICT DO UPDATE for upsert behavior.

        Parameters
        ----------
        table_name : str
            Destination table name.
        records : list[dict]
            Unified records to upsert.
        mapping : dict, optional
            Field mapping used to transform record keys before writing.
        upsert_key : str
            Column to use as the conflict key (default "id").

        Returns
        -------
        dict
            {"inserted": int, "updated": int}
        """
        conn = self._ensure_connection()

        if not records:
            return {"inserted": 0, "updated": 0}

        # Determine target columns from the first record
        target_columns = list(records[0].keys())
        quoted_columns = ", ".join(f'"{c}"' for c in target_columns)
        placeholders = ", ".join(f":{c}" for c in target_columns)
        updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in target_columns if c != upsert_key)

        sql = f"""
            INSERT INTO "{self._schema}"."{table_name}" ({quoted_columns})
            VALUES ({placeholders})
            ON CONFLICT ("{upsert_key}") DO UPDATE SET {updates}
        """

        total_inserted = 0
        total_updated = 0

        cursor = conn.cursor()
        try:
            # Convert JSON-serializable values to strings for psycopg2
            processed_records = []
            for record in records:
                processed = {}
                for key, value in record.items():
                    if isinstance(value, (dict, list)):
                        processed[key] = json.dumps(value)
                    else:
                        processed[key] = value
                processed_records.append(processed)

                # Execute per-row for reliable rowcount tracking
                cursor.execute(sql, processed)

                if cursor.rowcount == 1:
                    # rowcount is 1 for both insert and update in psycopg2
                    # We need to check if it was an insert or update
                    pass

            conn.commit()

            # Count inserts vs updates by checking the table
            cursor.execute(f"""
                SELECT COUNT(*) FROM "{self._schema}"."{table_name}"
                WHERE "{upsert_key}" IN (
                    SELECT "{upsert_key}" FROM unnest(%s::text[], %s::text[]) AS t
                );
            """, ([r[upsert_key] for r in processed_records],
                  [str(r[upsert_key]) for r in processed_records]))

            total_inserted = cursor.rowcount  # This is approximate
            total_updated = len(records) - total_inserted

        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

        return {
            "inserted": total_inserted,
            "updated": total_updated,
        }

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def load_batch_bulk(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        upsert_key: str = "id",
    ) -> dict[str, int]:
        """Bulk load records using INSERT ... ON CONFLICT.

        Uses executemany for better performance with large batches.

        Parameters
        ----------
        table_name : str
            Destination table name.
        records : list[dict]
            Unified records to insert.
        upsert_key : str
            Column to use as the conflict key.

        Returns
        -------
        dict
            {"inserted": int, "updated": int}
        """
        conn = self._ensure_connection()

        if not records:
            return {"inserted": 0, "updated": 0}

        target_columns = list(records[0].keys())
        quoted_columns = ", ".join(f'"{c}"' for c in target_columns)
        placeholders = ", ".join(f":{c}" for c in target_columns)
        updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in target_columns if c != upsert_key)

        sql = f"""
            INSERT INTO "{self._schema}"."{table_name}" ({quoted_columns})
            VALUES ({placeholders})
            ON CONFLICT ("{upsert_key}") DO UPDATE SET {updates}
        """

        # Process records in micro-batches of 1000
        batch_size = 1000
        total_rows = 0

        cursor = conn.cursor()
        try:
            for i in range(0, len(records), batch_size):
                batch = records[i : i + batch_size]
                processed_batch = []
                for record in batch:
                    processed = {}
                    for key, value in record.items():
                        if isinstance(value, (dict, list)):
                            processed[key] = json.dumps(value)
                        else:
                            processed[key] = value
                    processed_batch.append(processed)

                # Use execute_values for bulk insert
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    sql.replace("VALUES (%s)", "VALUES %(col1)s").replace("%(col1)s", ""),
                    processed_batch,
                    template=f"({', '.join(['%s'] * len(target_columns))})",
                )
                total_rows += cursor.rowcount

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

        return {"inserted": total_rows, "updated": 0}

    # ── Schema Extension ──────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def add_column(
        self,
        table_name: str,
        column_name: str,
        data_type: str,
    ) -> bool:
        """Add a column to an existing table.

        Parameters
        ----------
        table_name : str
            Destination table name.
        column_name : str
            New column name.
        data_type : str
            PostgreSQL-compatible data type (e.g. "TEXT", "INTEGER", "JSONB").

        Returns
        -------
        bool
            True on success, False if column already exists.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            sql = f'ALTER TABLE "{self._schema}"."{table_name}" ADD COLUMN IF NOT EXISTS "{column_name}" {data_type}'
            cursor.execute(sql)
            conn.commit()
            return True
        except Psycopg2OperationalError as exc:
            if "already exists" in str(exc).lower():
                return True  # Idempotent
            logger.warning("Failed to add column %s.%s: %s", table_name, column_name, exc)
            return False
        finally:
            cursor.close()

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def extend_schema_batch(
        self,
        changes: list[dict[str, str]],
    ) -> dict[str, int]:
        """Apply multiple ALTER TABLE ADD COLUMN statements.

        Parameters
        ----------
        changes : list[dict]
            [{table_name, column_name, data_type}, ...]

        Returns
        -------
        dict
            {"added": int, "failed": int}
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        added = 0
        failed = 0

        try:
            for change in changes:
                table = change["table"]
                col = change["column"]
                dtype = change["data_type"]

                try:
                    sql = f'ALTER TABLE "{self._schema}"."{table}" ADD COLUMN IF NOT EXISTS "{col}" {dtype}'
                    cursor.execute(sql)
                    added += 1
                except Psycopg2OperationalError as exc:
                    if "already exists" in str(exc).lower():
                        added += 1
                    else:
                        logger.warning(
                            "Failed to add column %s.%s: %s", table, col, exc
                        )
                        failed += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

        return {"added": added, "failed": failed}

    # ── Helper ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_type(data_type: str) -> str:
        """Normalize PostgreSQL data types to canonical form."""
        mapping = {
            "character varying": "VARCHAR",
            "uuid": "UUID",
            "timestamp without time zone": "TIMESTAMP",
            "timestamp with time zone": "TIMESTAMPTZ",
            "boolean": "BOOLEAN",
            "integer": "INTEGER",
            "bigint": "BIGINT",
            "smallint": "SMALLINT",
            "numeric": "NUMERIC",
            "real": "REAL",
            "double precision": "DOUBLE PRECISION",
            "text": "TEXT",
            "json": "JSON",
            "jsonb": "JSONB",
            "bytea": "BYTEA",
            "date": "DATE",
            "time without time zone": "TIME",
        }
        return mapping.get(data_type.lower(), data_type.upper())