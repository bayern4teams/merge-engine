"""
ClickHouse destination connector for data loading and schema management.

Uses clickhouse-connect for connection and bulk inserts.
This connector is used for the destination ClickHouse database where
merged/unified data is loaded.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect

from src.orchestrator.utils.retry import retry_on_transient

logger = logging.getLogger("merge-engine.connectors.clickhouse_destination")


class ClickHouseDestinationConnector:
    """ClickHouse connector for destination writes.

    Handles bulk inserts, schema inspection, and schema creation for the
    destination ClickHouse database.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the connector with configuration.

        Parameters
        ----------
        config : dict
            Destination connection configuration with keys:
            - host (str): ClickHouse hostname or IP
            - port (int): Port number (default 8123)
            - user (str): Username
            - password (str): Password
            - database (str): Database name
            - schema (str): Schema name (not used by ClickHouse, default "")
        """
        self._config = config
        self._client: clickhouse_connect.driver_client | None = None
        self._database: str = config.get("database", "default")
        self._schema: str = config.get("schema", "")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def _ensure_client(self) -> clickhouse_connect.driver_client:
        """Create or return the ClickHouse client."""
        if self._client is not None:
            return self._client

        conn = self._config
        host = conn.get("host", "localhost")
        port = conn.get("port", 8123)
        user = conn.get("user", "default")
        password = conn.get("password", "")
        database = conn.get("database", "default")

        self._database = database
        self._client = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=user,
            password=password,
            database=database,
            connect_timeout=10,
        )
        return self._client

    def close(self) -> None:
        """Close the database connection."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self):
        self._ensure_client()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @contextmanager
    def atomic(self, dry_run: bool = False):
        """Not a real transaction — ClickHouse has no cross-table rollback.

        Deliberately *not* silently pretending: the caller relies on this to
        undo a partial merge, and here it cannot. Saying so in the log is
        the difference between a known limitation and a data-loss surprise.
        A staging-table-plus-EXCHANGE implementation is the real fix and is
        not built yet (see TODO.md, Phase M.5).
        """
        if dry_run:
            raise NotImplementedError(
                "Dry-run loads are not supported for a ClickHouse "
                "destination: there is no transaction to roll back."
            )
        logger.warning(
            "ClickHouse destination: this load is NOT atomic. A failure "
            "part-way through will leave earlier entities written."
        )
        yield self

    # ── Schema Inspection ─────────────────────────────────────────────

    def get_table_schema(self, table_name: str) -> dict[str, Any]:
        """Inspect the schema of an existing ClickHouse table.

        Parameters
        ----------
        table_name : str
            Table name.

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
        client = self._ensure_client()

        try:
            result = client.query(
                """
                SELECT name, type, is_in_primary_key, nullable
                FROM system.columns
                WHERE database = :db AND table = :tbl
                ORDER BY position
                """,
                {"db": self._database, "tbl": table_name},
            )

            columns: list[dict[str, Any]] = []
            primary_keys: list[str] = []
            for row in result.result_rows:
                col_name, col_type, is_pk, is_nullable = row
                nullable = is_nullable or "Nullable" in str(col_type)
                base_type = str(col_type).replace("Nullable(", "").replace(")", "")
                columns.append(
                    {
                        "name": col_name,
                        "type": base_type,
                        "nullable": nullable,
                    }
                )
                if is_pk:
                    primary_keys.append(col_name)

            return {
                "columns": columns,
                "primary_key": primary_keys[0] if primary_keys else None,
            }
        except Exception as exc:
            logger.warning("Failed to get table schema for %s: %s", table_name, exc)
            return {"columns": [], "primary_key": None}

    def list_tables(self) -> list[str]:
        """List all tables in the target database.

        Returns
        -------
        list[str]
            Table names.
        """
        client = self._ensure_client()
        try:
            result = client.query(
                "SELECT name FROM system.tables WHERE database = :db",
                {"db": self._database},
            )
            return [row[0] for row in result.result_rows]
        except Exception as exc:
            logger.warning("Failed to list tables: %s", exc)
            return []

    def read_entity_rows(
        self,
        table_name: str,
        limit: int = 1000,
        name_columns: list[str] | None = None,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read existing destination rows, optionally narrowed to a subject.

        Destination-dedup counterpart of the PostgreSQL method — see its
        docstring. Best-effort and bounded; a failure yields an empty list.
        """
        client = self._ensure_client()
        try:
            where = ""
            params: dict[str, Any] = {}
            cols = [c for c in (name_columns or []) if c]
            if subject and cols:
                clauses = []
                for i, c in enumerate(cols):
                    clauses.append(f"toString(`{c}`) ILIKE {{s{i}:String}}")
                    params[f"s{i}"] = f"%{subject}%"
                where = " WHERE " + " OR ".join(clauses)
            result = client.query(
                f"SELECT * FROM `{self._database}`.`{table_name}`{where} LIMIT {int(limit)}",
                params,
            )
            names = result.column_names
            return [dict(zip(names, row, strict=True)) for row in result.result_rows]
        except Exception as exc:
            logger.warning("Could not read destination rows from %s: %s", table_name, exc)
            return []

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database.

        Parameters
        ----------
        table_name : str
            Table name.

        Returns
        -------
        bool
            True if table exists.
        """
        client = self._ensure_client()
        try:
            result = client.query(
                "SELECT 1 FROM system.tables WHERE database = :db AND name = :tbl LIMIT 1",
                {"db": self._database, "tbl": table_name},
            )
            return len(result.result_rows) > 0
        except Exception:
            return False

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
        client = self._ensure_client()
        try:
            result = client.query(
                "SELECT 1 FROM system.columns WHERE database = :db AND table = :tbl AND name = :col LIMIT 1",
                {"db": self._database, "tbl": table_name, "col": column_name},
            )
            return len(result.result_rows) > 0
        except Exception:
            return False

    # ── Extraction State Tracking (incremental watermark extraction) ──

    def ensure_extraction_state(self) -> None:
        """Create the extraction-state tracking table if it doesn't exist.

        One row per (origin_id, table_name) — O(number of tables), not
        O(number of rows). ReplacingMergeTree keyed on last_extracted_at so
        FINAL reads return the latest state per table even before a merge.
        """
        client = self._ensure_client()
        client.command(f"""
            CREATE TABLE IF NOT EXISTS `{self._database}`.`merge_engine_extraction_state` (
                origin_id String,
                table_name String,
                watermark_column Nullable(String),
                watermark_kind Nullable(String),
                last_value Nullable(String),
                last_run_id Nullable(String),
                last_extracted_at DateTime,
                rows_extracted_last_run Int64
            ) ENGINE = ReplacingMergeTree(last_extracted_at)
            ORDER BY (origin_id, table_name)
        """)

    def get_extraction_state(self, origin_id: str, table_name: str) -> dict[str, Any] | None:
        """Fetch the tracked extraction state for one (origin, table), or None."""
        self.ensure_extraction_state()
        client = self._ensure_client()
        result = client.query(
            f"""
            SELECT watermark_column, watermark_kind, last_value, last_run_id, rows_extracted_last_run
            FROM `{self._database}`.`merge_engine_extraction_state` FINAL
            WHERE origin_id = {{origin_id:String}} AND table_name = {{table_name:String}}
            """,
            parameters={"origin_id": origin_id, "table_name": table_name},
        )
        if not result.result_rows:
            return None
        row = result.result_rows[0]
        return {
            "watermark_column": row[0],
            "watermark_kind": row[1],
            "last_value": row[2],
            "last_run_id": row[3],
            "rows_extracted_last_run": row[4],
        }

    def update_extraction_state(
        self,
        origin_id: str,
        table_name: str,
        watermark_column: str | None,
        watermark_kind: str | None,
        last_value: str | None,
        run_id: str,
        rows_extracted: int,
    ) -> None:
        """Insert the new extraction state after a successful load for one table.

        ReplacingMergeTree deduplicates by (origin_id, table_name) on merge;
        get_extraction_state uses FINAL so the latest row always wins.
        """
        self.ensure_extraction_state()
        client = self._ensure_client()
        client.insert(
            "merge_engine_extraction_state",
            [
                [
                    origin_id,
                    table_name,
                    watermark_column,
                    watermark_kind,
                    last_value,
                    run_id,
                    datetime.now(UTC),
                    rows_extracted,
                ]
            ],
            column_names=[
                "origin_id",
                "table_name",
                "watermark_column",
                "watermark_kind",
                "last_value",
                "last_run_id",
                "last_extracted_at",
                "rows_extracted_last_run",
            ],
            database=self._database,
        )

    def clear_extraction_state(
        self, origin_id: str | None = None, table_name: str | None = None
    ) -> int:
        """Forget tracked watermarks so the next run re-extracts in full.

        See the PostgreSQL destination's docstring for the why. ClickHouse has
        no row count from a delete and deletes are mutations, so this issues an
        ``ALTER TABLE ... DELETE`` and returns -1 (unknown count) on success.
        """
        self.ensure_extraction_state()
        client = self._ensure_client()
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if origin_id is not None:
            clauses.append("origin_id = {origin_id:String}")
            params["origin_id"] = origin_id
        if table_name is not None:
            clauses.append("table_name = {table_name:String}")
            params["table_name"] = table_name
        where = " AND ".join(clauses) if clauses else "1=1"
        client.command(
            f"ALTER TABLE `{self._database}`.`merge_engine_extraction_state` DELETE WHERE {where}",
            parameters=params,
        )
        logger.info(
            "Cleared extraction-state for a full re-merge (origin=%s, table=%s)",
            origin_id,
            table_name,
        )
        return -1

    # ── Data Loading ──────────────────────────────────────────────────

    def _clickhouse_type_from_string(self, data_type: str) -> str:
        """Normalize a cross-database type string to ClickHouse-compatible type."""
        mapping = {
            "VARCHAR": "String",
            "TEXT": "String",
            "CHAR": "String",
            "INTEGER": "Int32",
            "INT": "Int32",
            "BIGINT": "Int64",
            "SMALLINT": "Int16",
            "TINYINT": "Int8",
            "DECIMAL": "Float64",
            "NUMERIC": "Float64",
            "REAL": "Float32",
            "DOUBLE": "Float64",
            "DOUBLE PRECISION": "Float64",
            "BOOLEAN": "Bool",
            "BOOL": "Bool",
            "TIMESTAMP": "DateTime64(6)",
            "TIMESTAMPTZ": "DateTime64(6, 'UTC')",
            "DATE": "Date",
            "TIME": "DateTime64(6)",
            "JSON": "String",
            "JSONB": "String",
            "UUID": "String",
            "BYTEA": "String",
            "INET": "String",
        }
        return mapping.get(data_type.upper(), data_type)

    def _create_table_sql(
        self,
        table_name: str,
        columns: list[dict[str, Any]],
        engine: str = "MergeTree()",
        order_by: str = "id",
    ) -> str:
        """Generate CREATE TABLE SQL for ClickHouse.

        Parameters
        ----------
        table_name : str
            Table name.
        columns : list[dict]
            Column definitions with name, type, nullable keys.
        engine : str
            Table engine (default MergeTree).
        order_by : str
            ORDER BY column for MergeTree.

        Returns
        -------
        str
            CREATE TABLE SQL statement.
        """
        col_defs = []
        for col in columns:
            ch_type = self._clickhouse_type_from_string(col["type"])
            if col.get("nullable"):
                ch_type = f"Nullable({ch_type})"
            col_defs.append(f"`{col['name']}` {ch_type}")

        col_list = ", ".join(col_defs)
        sql = f"CREATE TABLE IF NOT EXISTS `{self._database}`.`{table_name}` ({col_list}) ENGINE = {engine} ORDER BY ({order_by})"
        return sql

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def create_table(
        self,
        table_name: str,
        columns: list[dict[str, Any]],
        engine: str = "MergeTree()",
        order_by: str = "id",
    ) -> bool:
        """Create a destination table if it doesn't exist.

        Parameters
        ----------
        table_name : str
            Table name.
        columns : list[dict]
            Column definitions.
        engine : str
            ClickHouse table engine.
        order_by : str
            ORDER BY clause.

        Returns
        -------
        bool
            True on success.
        """
        client = self._ensure_client()
        try:
            sql = self._create_table_sql(table_name, columns, engine, order_by)
            client.command(sql)
            return True
        except Exception as exc:
            logger.warning("Failed to create table %s: %s", table_name, exc)
            return False

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def load_batch(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        mapping: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Bulk insert records into a ClickHouse table.

        Uses INSERT with column list for efficient batch loading.

        Parameters
        ----------
        table_name : str
            Destination table name.
        records : list[dict]
            Unified records to insert.
        mapping : dict, optional
            Field mapping (unused for ClickHouse, passed for API compatibility).

        Returns
        -------
        dict
            {"inserted": int}
        """
        client = self._ensure_client()

        if not records:
            return {"inserted": 0}

        target_columns = list(records[0].keys())

        # Convert complex types to strings
        processed_records = []
        for record in records:
            processed = {}
            for key, value in record.items():
                if isinstance(value, (dict, list)):
                    processed[key] = json.dumps(value)
                else:
                    processed[key] = value
            processed_records.append(processed)

        try:
            client.insert(
                table_name,
                processed_records,
                column_names=target_columns,
            )
            return {"inserted": len(processed_records)}
        except Exception as exc:
            logger.warning("Failed to load batch into %s: %s", table_name, exc)
            return {"inserted": 0}

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def load_batch_bulk(
        self,
        table_name: str,
        records: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Bulk load records using batch inserts.

        Parameters
        ----------
        table_name : str
            Destination table name.
        records : list[dict]
            Records to insert.

        Returns
        -------
        dict
            {"inserted": int}
        """
        return self.load_batch(table_name, records)

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
            ClickHouse-compatible data type.

        Returns
        -------
        bool
            True on success, False if column already exists.
        """
        client = self._ensure_client()
        try:
            ch_type = self._clickhouse_type_from_string(data_type)
            sql = f"ALTER TABLE `{self._database}`.`{table_name}` ADD COLUMN IF NOT EXISTS `{column_name}` {ch_type}"
            client.command(sql)
            return True
        except Exception as exc:
            error_str = str(exc).lower()
            if "already exists" in error_str or "noNewColumn" in error_str:
                return True
            logger.warning("Failed to add column %s.%s: %s", table_name, column_name, exc)
            return False

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
        client = self._ensure_client()
        added = 0
        failed = 0

        for change in changes:
            table = change["table"]
            col = change["column"]
            dtype = change["data_type"]

            try:
                ch_type = self._clickhouse_type_from_string(dtype)
                sql = f"ALTER TABLE `{self._database}`.`{table}` ADD COLUMN IF NOT EXISTS `{col}` {ch_type}"
                client.command(sql)
                added += 1
            except Exception as exc:
                error_str = str(exc).lower()
                if "already exists" in error_str or "noNewColumn" in error_str:
                    added += 1
                else:
                    logger.warning("Failed to add column %s.%s: %s", table, col, exc)
                    failed += 1

        return {"added": added, "failed": failed}
