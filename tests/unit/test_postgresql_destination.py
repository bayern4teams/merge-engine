"""
Unit tests for src/mcp_server/connectors/postgresql_destination.py

Tests PostgreSQLDestinationConnector's schema inspection, data loading (upsert),
schema extension, and error handling. All tests use mocks to avoid requiring
a real PostgreSQL server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def dest_config():
    """Basic PostgreSQL destination config dict."""
    return {
        "host": "localhost",
        "port": 5432,
        "user": "admin",
        "password": "secret",
        "database": "mergedb",
        "schema": "public",
    }


@pytest.fixture
def mock_psycopg2_connect():
    """Patch psycopg2.connect globally for tests."""
    with patch("src.mcp_server.connectors.postgresql_destination.psycopg2.connect") as mock:
        mock_conn = MagicMock()
        mock.return_value = mock_conn
        yield mock, mock_conn


@pytest.fixture
def mock_cursor():
    """Mock psycopg2 cursor."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (True,)
    cursor.fetchall.return_value = []
    cursor.rowcount = 1
    cursor.close = MagicMock()
    return cursor


# =============================================================================
# Constructor & Lifecycle tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestPostgreSQLDestinationInit:
    """Tests for PostgreSQLDestinationConnector initialization."""

    def test_init_stores_config(self, dest_config):
        """__init__ should store the config dict."""
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        assert connector._config is dest_config

    def test_init_sets_connection_to_none(self, dest_config):
        """__init__ should set _connection to None before first use."""
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        assert connector._connection is None

    def test_init_stores_schema(self, dest_config):
        """__init__ should store schema from config."""
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        assert connector._schema == "public"

    def test_init_defaults_schema(self):
        """__init__ should default schema to 'public' when not in config."""
        config = {"host": "localhost", "port": 5432, "user": "u", "password": "p", "database": "d"}
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(config)
        assert connector._schema == "public"


# =============================================================================
# _ensure_connection() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestEnsureConnection:
    """Tests for _ensure_connection method."""

    def test_creates_connection_on_first_call(self, dest_config, mock_psycopg2_connect):
        """_ensure_connection should create a psycopg2 connection."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        conn = connector._ensure_connection()

        assert conn is mock_conn
        mock_psycopg2.assert_called_once()
        kwargs = mock_psycopg2.call_args[1]
        assert kwargs["host"] == "localhost"
        assert kwargs["port"] == 5432
        assert kwargs["user"] == "admin"
        assert kwargs["password"] == "secret"
        assert kwargs["database"] == "mergedb"
        assert kwargs["connect_timeout"] == 30

    def test_reuses_existing_connection(self, dest_config, mock_psycopg2_connect):
        """_ensure_connection should reuse connection if open."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.closed = 0

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector._connection = mock_conn

        conn = connector._ensure_connection()

        assert conn is mock_conn
        assert mock_psycopg2.call_count == 1  # Only initial call

    def test_creates_new_when_closed(self, dest_config, mock_psycopg2_connect):
        """_ensure_connection should create new connection if previous is closed."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.closed = 1

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector._connection = mock_conn

        connector._ensure_connection()

        assert mock_psycopg2.call_count == 2


# =============================================================================
# close() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestClose:
    """Tests for the close() method."""

    def test_closes_connection(self, dest_config, mock_psycopg2_connect):
        """close() should close the psycopg2 connection."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.closed = 0

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector._connection = mock_conn

        connector.close()

        mock_conn.close.assert_called_once()
        assert connector._connection is None

    def test_no_op_when_closed(self, dest_config, mock_psycopg2_connect):
        """close() should do nothing when connection is already closed."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.closed = 1

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector._connection = mock_conn

        connector.close()  # Should not raise

    def test_no_op_when_none(self, dest_config):
        """close() should do nothing when _connection is None."""
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.close()  # Should not raise


# =============================================================================
# Context manager tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestContextManager:
    """Tests for __enter__ and __exit__ methods."""

    def test_enter_ensures_connection(self, dest_config, mock_psycopg2_connect):
        """__enter__ should ensure connection is created."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        with PostgreSQLDestinationConnector(dest_config) as connector:
            assert connector._connection is not None

    def test_exit_closes_connection(self, dest_config, mock_psycopg2_connect):
        """__exit__ should close the connection."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.closed = 0

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector._ensure_connection()

        connector.__exit__(None, None, None)

        mock_conn.close.assert_called_once()


# =============================================================================
# get_table_schema() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestGetTableSchema:
    """Tests for get_table_schema method."""

    def test_returns_empty_for_nonexistent_table(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """get_table_schema should return empty for non-existent table."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        # EXISTS check returns False
        mock_cursor.fetchone.return_value = (False,)

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        schema = connector.get_table_schema("nonexistent")

        assert schema["columns"] == []
        assert schema["primary_key"] is None

    def test_returns_column_info(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """get_table_schema should return column info for existing table."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        # Table exists
        mock_cursor.fetchone.return_value = (True,)
        mock_cursor.fetchall.return_value = [
            ("id", "integer", "NO"),
            ("name", "character varying", "NO"),
            ("email", "character varying", "YES"),
        ]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        schema = connector.get_table_schema("users")

        assert len(schema["columns"]) == 3
        assert schema["columns"][0]["name"] == "id"
        assert schema["columns"][0]["type"] == "INTEGER"
        assert schema["columns"][0]["nullable"] is False
        assert schema["columns"][1]["type"] == "VARCHAR"
        assert schema["columns"][2]["nullable"] is True

    def test_returns_primary_key(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """get_table_schema should return primary key column name."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            (True,),   # table exists
            ("id",),   # primary key
        ]
        mock_cursor.fetchall.return_value = [
            ("id", "integer", "NO"),
        ]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        schema = connector.get_table_schema("users")

        assert schema["primary_key"] == "id"

    def test_closes_cursor(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """get_table_schema should close the cursor after use."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.get_table_schema("users")

        mock_cursor.close.assert_called_once()


# =============================================================================
# list_tables() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestListTables:
    """Tests for list_tables method."""

    def test_lists_tables_in_schema(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """list_tables should return table names in the target schema."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("users",),
            ("orders",),
            ("products",),
        ]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        tables = connector.list_tables()

        assert tables == ["users", "orders", "products"]

    def test_returns_empty_for_no_tables(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """list_tables should return empty list when no tables exist."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        tables = connector.list_tables()

        assert tables == []

    def test_closes_cursor(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """list_tables should close the cursor after use."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.list_tables()

        mock_cursor.close.assert_called_once()


# =============================================================================
# column_exists() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestColumnExists:
    """Tests for column_exists method."""

    def test_returns_true_for_existing_column(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """column_exists should return True when column exists."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (True,)

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.column_exists("users", "email")

        assert result is True

    def test_returns_false_for_nonexistent_column(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """column_exists should return False when column doesn't exist."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (False,)

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.column_exists("users", "nonexistent")

        assert result is False

    def test_closes_cursor(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """column_exists should close the cursor after use."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.column_exists("users", "email")

        mock_cursor.close.assert_called_once()


# =============================================================================
# load_batch() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestLoadBatch:
    """Tests for load_batch method."""

    def test_returns_zero_for_empty_records(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch should return zero counts for empty records."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.load_batch("users", [])

        assert result == {"inserted": 0, "updated": 0}

    def test_upserts_records(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch should execute INSERT ... ON CONFLICT DO UPDATE."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        records = [
            {"id": "1", "name": "Alice", "email": "alice@example.com"},
            {"id": "2", "name": "Bob", "email": "bob@example.com"},
        ]
        mock_cursor.rowcount = 2

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.load_batch("users", records)

        # Should have executed once per record
        assert mock_cursor.execute.call_count == 2
        mock_conn.commit.assert_called_once()

    def test_upserts_with_custom_key(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch should use custom upsert_key for conflict column."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        records = [{"user_id": "1", "name": "Alice"}]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.load_batch("users", records, upsert_key="user_id")

        sql = mock_cursor.execute.call_args[0][0]
        assert "ON CONFLICT (\"user_id\")" in sql

    def test_handles_json_values(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch should serialize dict/list values as JSON strings."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        records = [
            {"id": "1", "metadata": {"key": "value"}, "tags": ["a", "b"]},
        ]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.load_batch("users", records)

        # Check the processed record passed to execute
        call_args = mock_cursor.execute.call_args
        processed = call_args[0][1]
        import json
        assert processed["metadata"] == json.dumps({"key": "value"})
        assert processed["tags"] == json.dumps(["a", "b"])

    def test_rolls_back_on_error(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch should rollback on error."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("database error")

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        with pytest.raises(Exception, match="database error"):
            connector.load_batch("users", [{"id": "1", "name": "A"}])

        mock_conn.rollback.assert_called_once()

    def test_closes_cursor(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch should close the cursor after completion."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.load_batch("users", [{"id": "1", "name": "A"}])

        mock_cursor.close.assert_called_once()

    def test_uses_target_schema(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch should use configured schema in INSERT statement."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        config = {**dest_config, "schema": "analytics"}
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(config)
        connector.load_batch("users", [{"id": "1", "name": "A"}])

        sql = mock_cursor.execute.call_args[0][0]
        assert '"analytics"."users"' in sql


# =============================================================================
# load_batch_bulk() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestLoadBatchBulk:
    """Tests for load_batch_bulk method."""

    def test_returns_zero_for_empty_records(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch_bulk should return zero counts for empty records."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.load_batch_bulk("users", [])

        assert result == {"inserted": 0, "updated": 0}

    def test_bulk_loads_records(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch_bulk should use executemany-style bulk insert."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.rowcount = 100

        records = [{"id": str(i), "name": f"user_{i}"} for i in range(100)]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.load_batch_bulk("users", records)

        assert result["inserted"] == 100
        assert result["updated"] == 0

    def test_bulk_uses_micro_batches(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """load_batch_bulk should process in micro-batches of 1000."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.rowcount = 1000

        # 2500 records → should execute 3 times (1000 + 1000 + 500)
        records = [{"id": str(i), "name": f"user_{i}"} for i in range(2500)]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.load_batch_bulk("users", records)

        # Should call execute_values 3 times
        assert mock_cursor.execute.call_count == 3


# =============================================================================
# add_column() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestAddColumn:
    """Tests for add_column method."""

    def test_adds_column(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """add_column should execute ALTER TABLE ADD COLUMN."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.add_column("users", "phone", "TEXT")

        assert result is True
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "ALTER TABLE" in sql
        assert '"public"."users"' in sql
        assert "ADD COLUMN" in sql
        assert '"phone"' in sql
        assert "TEXT" in sql
        mock_conn.commit.assert_called_once()

    def test_returns_true_for_existing_column(self, dest_config, mock_psycopg2_connect):
        """add_column should return True when column already exists (idempotent)."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        from psycopg2 import OperationalError as Psycopg2OperationalError
        mock_cursor.execute.side_effect = Psycopg2OperationalError("column already exists")

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.add_column("users", "phone", "TEXT")

        assert result is True

    def test_returns_false_on_other_errors(self, dest_config, mock_psycopg2_connect):
        """add_column should return False on non-already-exists errors."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        from psycopg2 import OperationalError as Psycopg2OperationalError
        mock_cursor.execute.side_effect = Psycopg2OperationalError("permission denied")

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.add_column("users", "phone", "TEXT")

        assert result is False

    def test_closes_cursor(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """add_column should close the cursor after completion."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.add_column("users", "phone", "TEXT")

        mock_cursor.close.assert_called_once()


# =============================================================================
# extend_schema_batch() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestExtendSchemaBatch:
    """Tests for extend_schema_batch method."""

    def test_extends_multiple_columns(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """extend_schema_batch should execute multiple ALTER TABLE statements."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        changes = [
            {"table": "users", "column": "phone", "data_type": "TEXT"},
            {"table": "users", "column": "address", "data_type": "TEXT"},
        ]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.extend_schema_batch(changes)

        assert result["added"] == 2
        assert result["failed"] == 0
        assert mock_cursor.execute.call_count == 2
        mock_conn.commit.assert_called_once()

    def test_counts_failed_additions(self, dest_config, mock_psycopg2_connect):
        """extend_schema_batch should count failed additions."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        from psycopg2 import OperationalError as Psycopg2OperationalError
        # First succeeds, second fails
        mock_cursor.execute.side_effect = [
            None,
            Psycopg2OperationalError("permission denied"),
        ]

        changes = [
            {"table": "users", "column": "phone", "data_type": "TEXT"},
            {"table": "users", "column": "email", "data_type": "TEXT"},
        ]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.extend_schema_batch(changes)

        assert result["added"] == 1
        assert result["failed"] == 1

    def test_handles_existing_column_idempotently(self, dest_config, mock_psycopg2_connect):
        """extend_schema_batch should count existing columns as added."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        from psycopg2 import OperationalError as Psycopg2OperationalError
        mock_cursor.execute.side_effect = Psycopg2OperationalError("column already exists")

        changes = [{"table": "users", "column": "phone", "data_type": "TEXT"}]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.extend_schema_batch(changes)

        assert result["added"] == 1
        assert result["failed"] == 0

    def test_rolls_back_on_error(self, dest_config, mock_psycopg2_connect):
        """extend_schema_batch should rollback on error."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.execute.side_effect = Exception("database error")

        changes = [{"table": "users", "column": "phone", "data_type": "TEXT"}]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        with pytest.raises(Exception, match="database error"):
            connector.extend_schema_batch(changes)

        mock_conn.rollback.assert_called_once()

    def test_closes_cursor(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """extend_schema_batch should close the cursor after completion."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        changes = [{"table": "users", "column": "phone", "data_type": "TEXT"}]

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        connector.extend_schema_batch(changes)

        mock_cursor.close.assert_called_once()

    def test_empty_changes(self, dest_config, mock_psycopg2_connect, mock_cursor):
        """extend_schema_batch should handle empty changes list."""
        mock_psycopg2, mock_conn = mock_psycopg2_connect
        mock_conn.cursor.return_value = mock_cursor

        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

        connector = PostgreSQLDestinationConnector(dest_config)
        result = connector.extend_schema_batch([])

        assert result == {"added": 0, "failed": 0}
        mock_cursor.execute.assert_not_called()


# =============================================================================
# _normalize_type() tests
# =============================================================================

@pytest.mark.skip(reason="Skipped per user request - postgresql_destination tests excluded from test suite")
class TestNormalizeType:
    """Tests for the static _normalize_type helper."""

    def test_normalizes_varchar(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("character varying") == "VARCHAR"

    def test_normalizes_integer(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("integer") == "INTEGER"

    def test_normalizes_bigint(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("bigint") == "BIGINT"

    def test_normalizes_smallint(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("smallint") == "SMALLINT"

    def test_normalizes_numeric(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("numeric") == "NUMERIC"

    def test_normalizes_boolean(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("boolean") == "BOOLEAN"

    def test_normalizes_timestamp_types(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("timestamp without time zone") == "TIMESTAMP"
        assert PostgreSQLDestinationConnector._normalize_type("timestamp with time zone") == "TIMESTAMPTZ"

    def test_normalizes_json_types(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("json") == "JSON"
        assert PostgreSQLDestinationConnector._normalize_type("jsonb") == "JSONB"

    def test_normalizes_uuid(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("uuid") == "UUID"

    def test_normalizes_bytea(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("bytea") == "BYTEA"

    def test_normalizes_date(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("date") == "DATE"

    def test_normalizes_text(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("text") == "TEXT"

    def test_normalizes_double_precision(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("double precision") == "DOUBLE PRECISION"

    def test_uppercases_unknown_type(self):
        from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
        assert PostgreSQLDestinationConnector._normalize_type("my_custom_type") == "MY_CUSTOM_TYPE"