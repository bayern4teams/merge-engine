"""
Unit tests for src/mcp_server/connectors/databricks.py

Tests DatabricksConnector's schema inspection, data loading, column addition,
and error handling. All tests use mocks to avoid requiring a real Databricks
connection.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def databricks_config():
    """Basic Databricks connection config dict."""
    return {
        "host": "dbc-test.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/abc123",
        "token": "dapi_token_123",
        "catalog": "main",
        "schema": "default",
    }


@pytest.fixture
def mock_connection():
    """Mock synchronous Databricks SQL connection."""
    conn = MagicMock()
    return conn


@pytest.fixture
def mock_cursor():
    """Mock synchronous cursor."""
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    cursor.close = MagicMock()
    return cursor


# =============================================================================
# Constructor & Lifecycle tests
# =============================================================================


@pytest.mark.skip(reason="Skipped per user request - databricks tests excluded from test suite")
class TestDatabricksConnectorInit:
    """Tests for DatabricksConnector initialization."""

    def test_init_sets_connection_to_none(self, databricks_config):
        """__init__ should set _connection to None before first use."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        assert connector._connection is None
        assert connector.config == databricks_config

    def test_init_stores_config(self, databricks_config):
        """__init__ should store the config dict."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        assert connector.config is databricks_config

    def test_init_stores_catalog_and_schema(self, databricks_config):
        """__init__ should store catalog and schema from config."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        assert connector._catalog == "main"
        assert connector._schema == "default"

    def test_init_defaults_catalog_and_schema(self):
        """__init__ should default catalog and schema when not in config."""
        config = {"host": "x", "http_path": "y", "token": "z"}
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(config)
        assert connector._catalog == "main"
        assert connector._schema == "default"


# =============================================================================
# _ensure_connection tests
# =============================================================================


@pytest.mark.skip(reason="Skipped per user request - databricks tests excluded from test suite")
class TestEnsureConnection:
    """Tests for _ensure_connection lifecycle method."""

    @pytest.mark.asyncio
    async def test_creates_connection_on_first_call(self, databricks_config):
        """_ensure_connection should create a Databricks SQL connection."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)

        with patch("src.mcp_server.connectors.databricks.dbsql_connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            conn = await connector._ensure_connection()

            assert conn is mock_conn
            mock_connect.assert_called_once()
            kwargs = mock_connect.call_args[1]
            assert kwargs["server_hostname"] == "dbc-test.cloud.databricks.com"
            assert kwargs["http_path"] == "/sql/1.0/warehouses/abc123"
            assert kwargs["access_token"] == "dapi_token_123"
            assert kwargs["catalog"] == "main"
            assert kwargs["schema"] == "default"

    @pytest.mark.asyncio
    async def test_reuses_existing_connection(self, databricks_config):
        """_ensure_connection should reuse connection if already created."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        existing_conn = MagicMock()
        connector._connection = existing_conn

        with patch("src.mcp_server.connectors.databricks.dbsql_connect") as mock_connect:
            conn = await connector._ensure_connection()

            assert conn is existing_conn
            mock_connect.assert_not_called()


# =============================================================================
# close() tests
# =============================================================================


@pytest.mark.skip(reason="Skipped per user request - databricks tests excluded from test suite")
class TestClose:
    """Tests for the close() method."""

    @pytest.mark.asyncio
    async def test_closes_connection(self, databricks_config):
        """close() should call close on the connection."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        mock_conn = MagicMock()
        connector._connection = mock_conn

        await connector.close()

        mock_conn.close.assert_called_once()
        assert connector._connection is None

    @pytest.mark.asyncio
    async def test_no_op_when_no_connection(self, databricks_config):
        """close() should do nothing when connection is None."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        await connector.close()  # Should not raise


# =============================================================================
# get_table_schema() tests
# =============================================================================


@pytest.mark.skip(reason="Skipped per user request - databricks tests excluded from test suite")
class TestGetTableSchema:
    """Tests for get_table_schema method."""

    @pytest.mark.asyncio
    async def test_inspects_table_schema(self, databricks_config, mock_connection, mock_cursor):
        """get_table_schema should return column info from DESCRIBE TABLE."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        # DESCRIBE TABLE returns: (col_name, data_type, comment)
        mock_cursor.fetchall.return_value = [
            ("id", "STRING", None),
            ("name", "STRING", None),
            ("created_at", "TIMESTAMP", None),
        ]

        schema = await connector.get_table_schema("users")

        assert "columns" in schema
        assert len(schema["columns"]) == 3
        assert schema["columns"][0]["name"] == "id"
        assert schema["columns"][0]["type"] == "STRING"
        assert schema["columns"][0]["nullable"] is True
        assert schema["primary_key"] == "id"

    @pytest.mark.asyncio
    async def test_uses_correct_qualified_name(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """get_table_schema should use catalog.schema.table qualified name."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        await connector.get_table_schema("users")

        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "main.default.users" in sql

    @pytest.mark.asyncio
    async def test_handles_empty_table(self, databricks_config, mock_connection, mock_cursor):
        """get_table_schema should handle empty table (no columns)."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        schema = await connector.get_table_schema("empty_table")

        assert schema["columns"] == []
        assert schema["primary_key"] == "id"

    @pytest.mark.asyncio
    async def test_closes_cursor_on_completion(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """get_table_schema should close the cursor after use."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        await connector.get_table_schema("users")

        mock_cursor.close.assert_called_once()


# =============================================================================
# load_batch() tests
# =============================================================================


@pytest.mark.skip(reason="Skipped per user request - databricks tests excluded from test suite")
class TestLoadBatch:
    """Tests for load_batch method."""

    @pytest.mark.asyncio
    async def test_loads_batch_with_merge(self, databricks_config, mock_connection, mock_cursor):
        """load_batch should execute MERGE statement for upsert."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        records = [
            {"id": "1", "name": "Alice", "email": "alice@example.com"},
            {"id": "2", "name": "Bob", "email": "bob@example.com"},
        ]
        mock_cursor.rowcount = 2

        result = await connector.load_batch("users", records)

        assert result["inserted"] == 2
        assert result["updated"] == 0
        mock_connection.commit.assert_called()

    @pytest.mark.asyncio
    async def test_load_batch_empty_records(self, databricks_config, mock_connection, mock_cursor):
        """load_batch should return zero counts for empty records list."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        result = await connector.load_batch("users", [])

        assert result == {"inserted": 0, "updated": 0}
        mock_cursor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_batch_uses_upsert_key(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """load_batch should use upsert_key for MERGE condition."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        records = [{"user_id": "1", "name": "Alice"}]
        await connector.load_batch("users", records, upsert_key="user_id")

        call_args = mock_cursor.execute.call_args
        sql = call_args[0][0]
        assert "target.user_id = source.user_id" in sql

    @pytest.mark.asyncio
    async def test_load_batch_micro_batches_of_500(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """load_batch should process records in micro-batches of 500."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        # 750 records → should execute twice (500 + 250)
        records = [{"id": str(i), "name": f"user_{i}"} for i in range(750)]
        mock_cursor.rowcount = 500

        result = await connector.load_batch("users", records)

        assert mock_cursor.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_load_batch_uses_qualified_table_name(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """load_batch should use catalog.schema.table qualified name."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        await connector.load_batch("users", [{"id": "1", "name": "A"}])

        sql = mock_cursor.execute.call_args[0][0]
        assert "main.default.users" in sql

    @pytest.mark.asyncio
    async def test_load_batch_with_mapping(self, databricks_config, mock_connection, mock_cursor):
        """load_batch should use first record's keys for columns."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        records = [
            {"mapped_id": "1", "mapped_name": "Alice"},
        ]
        mock_cursor.rowcount = 1

        await connector.load_batch("users", records, mapping={"id": "mapped_id"})

        # SQL should use the record keys as column names
        sql = mock_cursor.execute.call_args[0][0]
        assert "mapped_id" in sql
        assert "mapped_name" in sql

    @pytest.mark.asyncio
    async def test_load_batch_closes_cursor(self, databricks_config, mock_connection, mock_cursor):
        """load_batch should close cursor after completion."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        await connector.load_batch("users", [{"id": "1", "name": "A"}])

        mock_cursor.close.assert_called_once()


# =============================================================================
# add_column() tests
# =============================================================================


@pytest.mark.skip(reason="Skipped per user request - databricks tests excluded from test suite")
class TestAddColumn:
    """Tests for add_column method."""

    @pytest.mark.asyncio
    async def test_adds_column(self, databricks_config, mock_connection, mock_cursor):
        """add_column should execute ALTER TABLE ADD COLUMN."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        result = await connector.add_column("users", "phone", "STRING")

        assert result is True
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "ALTER TABLE" in sql
        assert "main.default.users" in sql
        assert "ADD COLUMN" in sql
        assert "phone" in sql
        assert "STRING" in sql
        mock_connection.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_adds_column_closes_cursor(self, databricks_config, mock_connection, mock_cursor):
        """add_column should close cursor after completion."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        await connector.add_column("users", "phone", "STRING")

        mock_cursor.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_column_handles_error(self, databricks_config, mock_connection, mock_cursor):
        """add_column should return False on Databricks error."""
        from databricks.sql.exc import Error as DatabricksError

        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = DatabricksError("column already exists")

        result = await connector.add_column("users", "phone", "STRING")

        assert result is False
        mock_cursor.close.assert_called_once()


# =============================================================================
# extend_schema_batch() tests
# =============================================================================


@pytest.mark.skip(reason="Skipped per user request - databricks tests excluded from test suite")
class TestExtendSchemaBatch:
    """Tests for extend_schema_batch method."""

    @pytest.mark.asyncio
    async def test_extends_schema_multiple_changes(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """extend_schema_batch should execute multiple ALTER TABLE statements."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        changes = [
            {"table": "users", "column": "phone", "data_type": "STRING"},
            {"table": "users", "column": "address", "data_type": "STRING"},
        ]

        result = await connector.extend_schema_batch(changes)

        assert result["added"] == 2
        assert result["failed"] == 0
        assert mock_cursor.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_extend_schema_batch_counts_failures(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """extend_schema_batch should count failed additions."""
        from databricks.sql.exc import Error as DatabricksError

        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        # First succeeds, second fails
        mock_cursor.execute.side_effect = [None, DatabricksError("already exists")]

        changes = [
            {"table": "users", "column": "phone", "data_type": "STRING"},
            {"table": "users", "column": "email", "data_type": "STRING"},
        ]

        result = await connector.extend_schema_batch(changes)

        assert result["added"] == 1
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_extend_schema_batch_closes_cursor(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """extend_schema_batch should close cursor after completion."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        changes = [{"table": "users", "column": "phone", "data_type": "STRING"}]
        await connector.extend_schema_batch(changes)

        mock_cursor.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_schema_batch_empty_changes(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """extend_schema_batch should handle empty changes list."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        result = await connector.extend_schema_batch([])

        assert result == {"added": 0, "failed": 0}
        mock_cursor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_schema_batch_uses_backticks(
        self, databricks_config, mock_connection, mock_cursor
    ):
        """extend_schema_batch should wrap column name in backticks."""
        from src.mcp_server.connectors.databricks import DatabricksConnector

        connector = DatabricksConnector(databricks_config)
        connector._connection = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        changes = [{"table": "users", "column": "my_column", "data_type": "STRING"}]
        await connector.extend_schema_batch(changes)

        sql = mock_cursor.execute.call_args[0][0]
        assert "`my_column`" in sql
