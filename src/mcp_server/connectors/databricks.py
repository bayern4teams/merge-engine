"""
Databricks (Delta Lake) connector for data loading and schema inspection.

Uses the official Databricks SQL connector for Python.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from databricks.sql import connect as dbsql_connect
from databricks.sql.exc import Error as DatabricksError

from src.mcp_server.connectors.base import BaseConnector
from src.orchestrator.utils.retry import TransientError, retry_on_transient

logger = logging.getLogger("merge-engine.connectors.databricks")


class DatabricksConnector(BaseConnector):
    """Databricks Delta Lake connector for destination writes."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._connection = None
        self._catalog: str = config.get("catalog", "main")
        self._schema: str = config.get("schema", "default")

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def _ensure_connection(self):
        """Create or return the Databricks SQL connection."""
        if self._connection is not None:
            return self._connection

        host = self.config.get("host", "")
        http_path = self.config.get("http_path", "")
        token = self.config.get("token", "")

        self._connection = dbsql_connect(
            server_hostname=host,
            http_path=http_path,
            access_token=token,
            catalog=self._catalog,
            schema=self._schema,
        )
        return self._connection

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # ── Schema Inspection ─────────────────────────────────────────────

    async def get_table_schema(self, table_name: str) -> dict[str, Any]:
        """Inspect the schema of an existing Databricks table.

        Returns
        -------
        dict
            {
                "columns": [{"name": ..., "type": ..., "nullable": ...}],
                "primary_key": str | None  # typically 'id' for UUID key
            }
        """
        conn = await self._ensure_connection()
        qualified = f"{self._catalog}.{self._schema}.{table_name}"

        cursor = conn.cursor()
        try:
            cursor.execute(f"DESCRIBE TABLE {qualified}")
            rows = cursor.fetchall()
            columns = []
            for row in rows:
                # DESCRIBE TABLE returns: col_name, data_type, comment (row format)
                name = row[0] if isinstance(row, (list, tuple)) else row.get("col_name")
                dtype = row[1] if len(row) > 1 else row.get("data_type")
                columns.append({
                    "name": str(name),
                    "type": str(dtype),
                    "nullable": True,
                })

            return {"columns": columns, "primary_key": "id"}
        finally:
            cursor.close()

    # ── Data Loading ──────────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    async def load_batch(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        mapping: dict[str, Any] | None = None,
        upsert_key: str = "id",
    ) -> dict[str, int]:
        """Upsert records into a Databricks Delta table using MERGE.

        Parameters
        ----------
        table_name : str
            Destination table name (entity type).
        records : list[dict]
            Unified records to upsert.
        mapping : dict, optional
            Field mapping used to transform record keys before writing.
        upsert_key : str
            Column to use as the merge key (default "id").

        Returns
        -------
        dict
            {"inserted": int, "updated": int}
        """
        conn = await self._ensure_connection()
        qualified = f"{self._catalog}.{self._schema}.{table_name}"

        if not records:
            return {"inserted": 0, "updated": 0}

        # Determine target columns from the first record
        target_columns = list(records[0].keys())
        col_list = ", ".join(f'"{c}"' for c in target_columns)
        placeholder_list = ", ".join(f":{c}" for c in target_columns)

        # Build MERGE statement
        merge_sql = f"""
            MERGE INTO {qualified} AS target
            USING (
                SELECT {col_list}
                FROM __records__
            ) AS source
            ON target.{upsert_key} = source.{upsert_key}
            WHEN MATCHED THEN UPDATE SET {', '.join(f'target."{c}" = source."{c}"' for c in target_columns if c != upsert_key)}
            WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({placeholder_list})
        """

        # Process records in micro-batches of 500
        batch_size = 500
        total_inserted = 0
        total_updated = 0

        cursor = conn.cursor()
        try:
            for i in range(0, len(records), batch_size):
                batch = records[i : i + batch_size]
                batch_params = [dict(r) for r in batch]

                # Use execute_batch if available, else individual executes
                cursor.execute(merge_sql, batch_params)
                conn.commit()

                total_inserted += cursor.rowcount
        finally:
            cursor.close()

        return {"inserted": total_inserted, "updated": 0}  # rowcount behavior varies

    # ── Schema Extension ──────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    async def add_column(
        self,
        table_name: str,
        column_name: str,
        data_type: str,
    ) -> bool:
        """Add a column to an existing Delta table.

        Parameters
        ----------
        table_name : str
            Destination table name.
        column_name : str
            New column name.
        data_type : str
            Databricks-compatible SQL data type (e.g. "STRING", "INTEGER").

        Returns
        -------
        bool
            True on success.
        """
        conn = await self._ensure_connection()
        qualified = f"{self._catalog}.{self._schema}.{table_name}"

        cursor = conn.cursor()
        try:
            # ALTER TABLE ... ADD COLUMN (idempotent: ignore if exists)
            sql = f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS `{column_name}` {data_type}"
            cursor.execute(sql)
            conn.commit()
            return True
        except DatabricksError as exc:
            # IF NOT EXISTS may not be supported; check error
            logger.warning("Failed to add column %s.%s: %s", table_name, column_name, exc)
            return False
        finally:
            cursor.close()

    @retry_on_transient(max_attempts=3, backoff="exponential")
    async def extend_schema_batch(
        self,
        changes: list[dict[str, str]],
    ) -> dict[str, int]:
        """Apply multiple ALTER TABLE ADD COLUMN statements in one session.

        Parameters
        ----------
        changes : list[dict]
            [{table_name, column_name, data_type}, ...]

        Returns
        -------
        dict
            {"added": int, "failed": int}
        """
        conn = await self._ensure_connection()
        cursor = conn.cursor()
        added = 0
        failed = 0

        try:
            for change in changes:
                table = change["table"]
                col = change["column"]
                dtype = change["data_type"]
                qualified = f"{self._catalog}.{self._schema}.{table}"

                try:
                    sql = f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS `{col}` {dtype}"
                    cursor.execute(sql)
                    added += 1
                except DatabricksError as exc:
                    logger.warning(
                        "Failed to add column %s.%s: %s", table, col, exc
                    )
                    failed += 1
        finally:
            cursor.close()

        return {"added": added, "failed": failed}