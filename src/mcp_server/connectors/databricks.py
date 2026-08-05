"""
Databricks (Delta Lake) connector for data loading and schema inspection.

Uses the official Databricks SQL connector for Python.
"""

from __future__ import annotations

import logging
from typing import Any

from databricks.sql import connect as dbsql_connect
from databricks.sql.exc import Error as DatabricksError

from src.mcp_server.connectors.base import BaseConnector
from src.orchestrator.utils.retry import retry_on_transient

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
        logger.debug("ENTER _ensure_connection")
        if self._connection is not None:
            logger.debug("Connection already exists, returning")
            return self._connection

        host = self.config.get("host", "")
        http_path = self.config.get("http_path", "")
        token = self.config.get("token", "")

        logger.debug(
            "Creating Databricks connection: host=%s, http_path=%s, catalog=%s, schema=%s",
            host,
            http_path,
            self._catalog,
            self._schema,
        )
        self._connection = dbsql_connect(
            server_hostname=host,
            http_path=http_path,
            access_token=token,
            catalog=self._catalog,
            schema=self._schema,
        )
        logger.debug("Databricks connection created successfully")
        return self._connection

    async def close(self) -> None:
        logger.debug("ENTER close")
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            logger.debug("Databricks connection closed")

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
        logger.debug("ENTER get_table_schema: table=%s", table_name)
        conn = await self._ensure_connection()
        qualified = f"{self._catalog}.{self._schema}.{table_name}"
        logger.debug("Describing table: %s", qualified)

        cursor = conn.cursor()
        try:
            cursor.execute(f"DESCRIBE TABLE {qualified}")
            rows = cursor.fetchall()
            logger.debug("DESCRIBE TABLE returned %d rows", len(rows))
            columns = []
            for row in rows:
                # DESCRIBE TABLE returns: col_name, data_type, comment (row format)
                name = row[0] if isinstance(row, (list, tuple)) else row.get("col_name")
                dtype = row[1] if len(row) > 1 else row.get("data_type")
                columns.append(
                    {
                        "name": str(name),
                        "type": str(dtype),
                        "nullable": True,
                    }
                )
            logger.debug("Table %s has %d columns", table_name, len(columns))

            result = {"columns": columns, "primary_key": "id"}
            logger.debug("EXIT get_table_schema: columns=%d", len(columns))
            return result
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
        logger.debug(
            "ENTER load_batch: table=%s, records_count=%d, upsert_key=%s",
            table_name,
            len(records),
            upsert_key,
        )
        conn = await self._ensure_connection()
        qualified = f"{self._catalog}.{self._schema}.{table_name}"
        logger.debug("Qualified table name: %s", qualified)

        if not records:
            logger.debug("No records to load, returning zeros")
            return {"inserted": 0, "updated": 0}

        # Determine target columns from the first record
        target_columns = list(records[0].keys())
        logger.debug("Target columns: %s", target_columns)
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
            WHEN MATCHED THEN UPDATE SET {", ".join(f'target."{c}" = source."{c}"' for c in target_columns if c != upsert_key)}
            WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({placeholder_list})
        """
        logger.debug("MERGE SQL built for table %s", table_name)

        # Process records in micro-batches of 500
        batch_size = 500
        total_batches = (len(records) + batch_size - 1) // batch_size
        total_inserted = 0
        logger.debug(
            "Processing %d records in %d batches of %d", len(records), total_batches, batch_size
        )

        cursor = conn.cursor()
        try:
            for batch_idx in range(0, len(records), batch_size):
                batch = records[batch_idx : batch_idx + batch_size]
                batch_params = [dict(r) for r in batch]
                logger.debug(
                    "Executing MERGE batch %d/%d: %d records",
                    batch_idx // batch_size + 1,
                    total_batches,
                    len(batch),
                )

                # Use execute_batch if available, else individual executes
                cursor.execute(merge_sql, batch_params)
                conn.commit()

                total_inserted += cursor.rowcount
                logger.debug(
                    "Batch %d/%d completed: rowcount=%d",
                    batch_idx // batch_size + 1,
                    total_batches,
                    cursor.rowcount,
                )
        finally:
            cursor.close()

        logger.debug(
            "EXIT load_batch: table=%s, total_inserted=%d, total_batches=%d",
            table_name,
            total_inserted,
            total_batches,
        )
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
        logger.debug(
            "ENTER add_column: table=%s, column=%s, type=%s", table_name, column_name, data_type
        )
        conn = await self._ensure_connection()
        qualified = f"{self._catalog}.{self._schema}.{table_name}"

        cursor = conn.cursor()
        try:
            # ALTER TABLE ... ADD COLUMN (idempotent: ignore if exists)
            sql = f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS `{column_name}` {data_type}"
            logger.debug("Executing: %s", sql)
            cursor.execute(sql)
            conn.commit()
            logger.debug("add_column successful: %s.%s", table_name, column_name)
            return True
        except DatabricksError as exc:
            # IF NOT EXISTS may not be supported; check error
            logger.warning("Failed to add column %s.%s: %s", table_name, column_name, exc)
            logger.debug("add_column failed for %s.%s", table_name, column_name)
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
        logger.debug("ENTER extend_schema_batch: changes_count=%d", len(changes))
        conn = await self._ensure_connection()
        cursor = conn.cursor()
        added = 0
        failed = 0

        try:
            for idx, change in enumerate(changes):
                table = change["table"]
                col = change["column"]
                dtype = change["data_type"]
                qualified = f"{self._catalog}.{self._schema}.{table}"
                logger.debug(
                    "Processing schema change %d/%d: ALTER TABLE %s ADD COLUMN %s (%s)",
                    idx + 1,
                    len(changes),
                    qualified,
                    col,
                    dtype,
                )

                try:
                    sql = f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS `{col}` {dtype}"
                    cursor.execute(sql)
                    added += 1
                    logger.debug("Successfully added column %s.%s", table, col)
                except DatabricksError as exc:
                    logger.warning("Failed to add column %s.%s: %s", table, col, exc)
                    failed += 1
        finally:
            cursor.close()

        logger.debug(
            "EXIT extend_schema_batch: added=%d, failed=%d, total=%d",
            added,
            failed,
            len(changes),
        )
        return {"added": added, "failed": failed}
