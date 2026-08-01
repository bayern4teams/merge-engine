"""
Unit tests for src/mcp_server/connectors/postgres.py

Tests PostgresConnector's schema discovery, data extraction, import flagging,
type mapping, and error handling. All tests use mocks to avoid requiring
real database connections.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text


# =============================================================================
# Helper: Async context manager mock factory
# =============================================================================

def make_async_cm(mock_obj):
    """Return a MagicMock configured as an async context manager wrapping mock_obj.

    Usage:
        cm = make_async_cm(my_conn)
        # cm.__aenter__.return_value is my_conn
    """
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_obj)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def make_query_result(rows, total=None):
    """Create a mock SQLAlchemy query result.

    The source code uses:
        dr = await conn.execute(text(sql), params)
        rows = [dict(r) for r in dr.mappings().fetchall()]
        total = cr.scalar()

    So we need:
      - result.mappings() to return an object with .fetchall() -> rows
      - result.scalar() to return total
    Both .mappings and .scalar must be regular callables (not AsyncMock).
    """
    result = MagicMock()
    mf = MagicMock()
    mf.fetchall.return_value = rows
    result.mappings.return_value = mf
    result.scalar.return_value = total
    return result


def make_single_result(fetchall_rows, scalar_val=None, fetchone_val=None):
    """Create a mock result that supports both mappings().fetchall() and mappings().fetchone()."""
    result = MagicMock()
    mf = MagicMock()
    mf.fetchall.return_value = fetchall_rows
    if fetchone_val is not None:
        mf.fetchone.return_value = fetchone_val
    result.mappings.return_value = mf
    if scalar_val is not None:
        result.scalar.return_value = scalar_val
    return result


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def postgres_config():
    """Basic PostgreSQL connection config dict."""
    return {
        "connection": {
            "host": "localhost",
            "port": 5432,
            "user": "testuser",
            "password": "testpass",
            "database": "testdb",
        }
    }


def _build_connector(postgres_config, *conn_list):
    """Build a PostgresConnector with a fully mocked engine.

    conn_list: list of (execute_side_effect, commit_mock) tuples.
    Each connection is wrapped in an async context manager.
    """
    from src.mcp_server.connectors.postgres import PostgresConnector

    connector = PostgresConnector(postgres_config)
    engine = MagicMock()

    async_conns = []
    for execute_se, commit_mock in conn_list:
        c = AsyncMock()
        c.execute = AsyncMock(side_effect=execute_se)
        c.commit = commit_mock
        async_conns.append(c)

    engine.connect = MagicMock(side_effect=[make_async_cm(c) for c in async_conns])
    connector._engine = engine
    return connector, async_conns


@pytest.fixture
def mock_cursor():
    """Mock synchronous cursor for Databricks connector."""
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    cursor.close = MagicMock()
    return cursor


# =============================================================================
# Constructor & Lifecycle tests
# =============================================================================

class TestPostgresConnectorInit:
    """Tests for PostgresConnector initialization."""

    def test_init_sets_up_engine_to_none(self, postgres_config):
        """__init__ should set _engine to None before first use."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        connector = PostgresConnector(postgres_config)
        assert connector._engine is None
        assert connector.config == postgres_config

    def test_init_stores_config(self, postgres_config):
        """__init__ should store the config dict."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        connector = PostgresConnector(postgres_config)
        assert connector.config is postgres_config


# =============================================================================
# _ensure_engine tests
# =============================================================================

class TestEnsureEngine:
    """Tests for _ensure_engine lifecycle method."""

    @pytest.mark.asyncio
    async def test_creates_engine_on_first_call(self, postgres_config):
        """_ensure_engine should create an AsyncEngine on first call."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        connector = PostgresConnector(postgres_config)

        with patch(
            "src.mcp_server.connectors.postgres.create_async_engine"
        ) as mock_create:
            mock_engine = AsyncMock()
            mock_create.return_value = mock_engine

            engine = await connector._ensure_engine()

            assert engine is mock_engine
            mock_create.assert_called_once()
            call_args = mock_create.call_args
            url = call_args[0][0]
            assert "postgresql+asyncpg" in url
            assert "testuser:testpass@localhost:5432/testdb" in url

    @pytest.mark.asyncio
    async def test_reuses_existing_engine(self, postgres_config):
        """_ensure_engine should reuse engine if already created."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        connector = PostgresConnector(postgres_config)
        existing_engine = AsyncMock()
        connector._engine = existing_engine

        with patch(
            "src.mcp_server.connectors.postgres.create_async_engine"
        ) as mock_create:
            engine = await connector._ensure_engine()

            assert engine is existing_engine
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_defaults_port_to_5432(self, postgres_config):
        """_ensure_engine should default port to 5432 when not specified."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        config = {"connection": {"host": "db.example.com", "user": "u", "password": "p", "database": "d"}}
        connector = PostgresConnector(config)

        with patch(
            "src.mcp_server.connectors.postgres.create_async_engine"
        ) as mock_create:
            mock_create.return_value = AsyncMock()
            await connector._ensure_engine()

            url = mock_create.call_args[0][0]
            assert "@db.example.com:5432/" in url

    @pytest.mark.asyncio
    async def test_engine_disable_echo(self, postgres_config):
        """_ensure_engine should create engine with echo=False."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        connector = PostgresConnector(postgres_config)

        with patch(
            "src.mcp_server.connectors.postgres.create_async_engine"
        ) as mock_create:
            mock_create.return_value = AsyncMock()
            await connector._ensure_engine()

            mock_create.assert_called_once()
            kwargs = mock_create.call_args[1]
            assert kwargs["echo"] is False
            assert kwargs["pool_pre_ping"] is True


# =============================================================================
# close() tests
# =============================================================================

class TestClose:
    """Tests for the close() method."""

    @pytest.mark.asyncio
    async def test_disposes_engine(self, postgres_config):
        """close() should call dispose on the engine."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        connector = PostgresConnector(postgres_config)
        mock_engine = AsyncMock()
        connector._engine = mock_engine

        await connector.close()

        mock_engine.dispose.assert_called_once()
        assert connector._engine is None

    @pytest.mark.asyncio
    async def test_no_op_when_no_engine(self, postgres_config):
        """close() should do nothing when engine is None."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        connector = PostgresConnector(postgres_config)
        await connector.close()  # Should not raise


# =============================================================================
# discover_schema() tests
# =============================================================================

class TestDiscoverSchema:
    """Tests for discover_schema method."""

    @pytest.mark.asyncio
    async def test_discovers_tables(self, postgres_config):
        """discover_schema should return list of user tables."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        table_rows = [
            {"table_schema": "public", "table_name": "users"},
            {"table_schema": "public", "table_name": "orders"},
        ]
        table_result = make_single_result(table_rows)

        # discover_schema calls engine.connect() once for tables query.
        # Since there are 2 tables, it will try to connect for each, but columns/FK/PK/sample fail silently.
        # We need 1 connection for the tables query; subsequent calls return empty results.
        engine = MagicMock()
        # discover_schema: 1 call for tables, then for each table: 4 more calls (cols, fk, pk, sample)
        # For 2 tables with 4 calls each = 9 total. Use side_effect for all.
        all_results = [table_result] + [make_single_result([]) for _ in range(8)]

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=all_results)
        async_conn.commit = AsyncMock()

        # discover_schema opens one connection for tables query, then for
        # each table it opens 4 more connections (cols, fk, pk, sample).
        # With 2 tables, that's 1 + 2*4 = 9 connections needed.
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn)] * 9)
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        schema = await connector.discover_schema()

        assert "tables" in schema
        assert len(schema["tables"]) == 2
        assert schema["tables"][0]["name"] == "users"
        assert schema["tables"][0]["schema"] == "public"
        assert schema["tables"][0]["full_name"] == "public.users"

    @pytest.mark.asyncio
    async def test_discovers_columns(self, postgres_config):
        """discover_schema should return column info for each table."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        table_result = make_single_result([{"table_schema": "public", "table_name": "users"}])
        col_result = make_single_result([
            {"column_name": "id", "data_type": "integer", "is_nullable": "NO", "character_maximum_length": None},
            {"column_name": "email", "data_type": "character varying", "is_nullable": "NO", "character_maximum_length": 255},
            {"column_name": "bio", "data_type": "text", "is_nullable": "YES", "character_maximum_length": None},
        ])
        fk_result = make_single_result([])
        pk_result = make_single_result([])
        sample_result = make_single_result([], fetchone_val=None)

        # 1 tables + 1 cols + 1 fk + 1 pk + 1 sample = 5 connections
        all_results = [table_result, col_result, fk_result, pk_result, sample_result]

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=all_results)
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn)] * 5)
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        schema = await connector.discover_schema()

        columns = schema["tables"][0]["columns"]
        assert len(columns) == 3
        assert columns[0]["name"] == "id"
        assert columns[0]["type"] == "INTEGER"
        assert columns[0]["nullable"] is False
        assert columns[1]["name"] == "email"
        assert columns[1]["type"] == "VARCHAR(255)"
        assert columns[2]["name"] == "bio"
        assert columns[2]["type"] == "TEXT"

    @pytest.mark.asyncio
    async def test_discovers_foreign_keys(self, postgres_config):
        """discover_schema should return foreign key info."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        table_result = make_single_result([{"table_schema": "public", "table_name": "orders"}])
        col_result = make_single_result([])
        fk_result = make_single_result([
            {
                "column_name": "user_id",
                "constraint_name": "fk_orders_user",
                "ref_table": "users",
                "ref_column": "id",
            }
        ])
        pk_result = make_single_result([])
        sample_result = make_single_result([], fetchone_val=None)

        all_results = [table_result, col_result, fk_result, pk_result, sample_result]

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=all_results)
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn)] * 5)
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        schema = await connector.discover_schema()

        fks = schema["tables"][0]["foreign_keys"]
        assert len(fks) == 1
        assert fks[0]["column"] == "user_id"
        assert fks[0]["ref_table"] == "users"
        assert fks[0]["ref_column"] == "id"

    @pytest.mark.asyncio
    async def test_includes_sample_row(self, postgres_config):
        """discover_schema should include a sample row when available."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        table_result = make_single_result([{"table_schema": "public", "table_name": "users"}])
        col_result = make_single_result([])
        fk_result = make_single_result([])
        pk_result = make_single_result([])
        sample_result = make_single_result([], fetchone_val={
            "id": 1, "name": "Alice", "email": "alice@example.com"
        })

        all_results = [table_result, col_result, fk_result, pk_result, sample_result]

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=all_results)
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn)] * 5)
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        schema = await connector.discover_schema()

        sample = schema["tables"][0]["sample_row"]
        assert sample["id"] == 1
        assert sample["name"] == "Alice"
        assert sample["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_handles_table_discovery_error(self, postgres_config):
        """discover_schema should skip tables that fail to discover."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        table_result = make_single_result([{"table_schema": "public", "table_name": "bad_table"}])

        # 1 tables query + 3 more for columns/fk/sample (all raise)
        all_results = [table_result, Exception("permission denied"), Exception("permission denied"), Exception("permission denied")]

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=all_results)
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn)] * 4)
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        schema = await connector.discover_schema()

        assert schema["tables"] == []

    @pytest.mark.asyncio
    async def test_excludes_system_schemas(self, postgres_config):
        """discover_schema should exclude pg_catalog and information_schema."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        table_result = make_single_result([
            {"table_schema": "public", "table_name": "products"},
        ])
        col_result = make_single_result([
            {"column_name": "id", "data_type": "integer", "is_nullable": "NO", "character_maximum_length": None},
        ])
        fk_result = make_single_result([])
        pk_result = make_single_result([])
        sample_result = make_single_result([], fetchone_val=None)

        all_results = [table_result, col_result, fk_result, pk_result, sample_result]

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=all_results)
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn)] * 5)
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        schema = await connector.discover_schema()

        table_names = [t["name"] for t in schema["tables"]]
        assert "products" in table_names


# =============================================================================
# extract() tests
# =============================================================================

class TestExtract:
    """Tests for extract method."""

    @pytest.mark.asyncio
    async def test_extract_basic(self, postgres_config):
        """extract should return paginated rows with metadata."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=150)
        data_result = make_query_result([
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ])

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        # extract() opens two separate connections: one for the count query,
        # one for the data query.
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        result = await connector.extract("users", ["id", "name"], batch_size=1000)

        assert len(result["rows"]) == 2
        assert result["rows"][0]["name"] == "Alice"
        assert result["total_count"] == 150
        assert result["next_cursor"] is None
        assert len(result["origin_ids"]) == 2

    @pytest.mark.asyncio
    async def test_extract_with_imported_filter(self, postgres_config):
        """extract should filter by _imported flag."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=10)
        data_result = make_query_result([{"id": 1, "name": "Alice"}])

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        result = await connector.extract(
            "users", ["id", "name"],
            filter={"_imported": False},
        )

        assert result["total_count"] == 10

    @pytest.mark.asyncio
    async def test_extract_custom_filter(self, postgres_config):
        """extract should support custom filter parameters."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=5)
        data_result = make_query_result([{"id": 1, "name": "Alice"}])

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        result = await connector.extract(
            "users", ["id", "name"],
            filter={"active": True},
        )

        assert result["total_count"] == 5

    @pytest.mark.asyncio
    async def test_extract_with_cursor_pagination(self, postgres_config):
        """extract should support OFFSET/LIMIT pagination via cursor."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=100)
        data_result = make_query_result([
            {"id": 101, "name": "Charlie"},
            {"id": 102, "name": "Diana"},
        ])

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        result = await connector.extract(
            "users", ["id", "name"],
            cursor="100",
            batch_size=50,
        )

        assert len(result["rows"]) == 2

    @pytest.mark.asyncio
    async def test_extract_handles_more_than_batch(self, postgres_config):
        """extract should set next_cursor when rows == batch_size."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=1000)
        mock_batch = [{"id": i, "name": f"user_{i}"} for i in range(1000)]
        data_result = make_query_result(mock_batch)

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        result = await connector.extract(
            "users", ["id", "name"],
            batch_size=1000,
        )

        assert result["next_cursor"] is not None
        assert len(result["rows"]) == 1000

    @pytest.mark.asyncio
    async def test_extract_empty_result(self, postgres_config):
        """extract should handle empty result gracefully."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=0)
        data_result = make_query_result([])

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        result = await connector.extract("users", ["id"])

        assert result["rows"] == []
        assert result["column_names"] == []
        assert result["total_count"] == 0
        assert result["next_cursor"] is None

    @pytest.mark.asyncio
    async def test_extract_defaults_to_public_schema(self, postgres_config):
        """extract should prefix table with 'public' when schema-qualified."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=5)
        data_result = make_query_result([{"id": 1}])

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        await connector.extract("users", ["id"])

        calls = async_conn.execute.call_args_list
        sql = str(calls[1][0][0])
        assert "public.users" in sql

    @pytest.mark.asyncio
    async def test_extract_schema_qualified_table(self, postgres_config):
        """extract should not double-prefix schema-qualified table names."""
        from src.mcp_server.connectors.postgres import PostgresConnector

        count_result = make_query_result([], total=5)
        data_result = make_query_result([{"id": 1}])

        async_conn = AsyncMock()
        async_conn.execute = AsyncMock(side_effect=[count_result, data_result])
        async_conn.commit = AsyncMock()

        engine = MagicMock()
        engine.connect = MagicMock(side_effect=[make_async_cm(async_conn), make_async_cm(async_conn)])
        connector = PostgresConnector(postgres_config)
        connector._engine = engine

        await connector.extract("analytics.events", ["id"])

        calls = async_conn.execute.call_args_list
        sql = str(calls[1][0][0])
        assert "analytics.events" in sql
        assert "public.analytics.events" not in sql


# =============================================================================
# mark_imported() tests
# =============================================================================

# =============================================================================
# _map_sql_type() tests
# =============================================================================

class TestMapSqlType:
    """Tests for the static _map_sql_type helper."""

    def test_maps_varchar(self):
        """_map_sql_type should map 'character varying' to VARCHAR."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("character varying") == "VARCHAR"

    def test_maps_varchar_with_length(self):
        """_map_sql_type should include length for VARCHAR."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("character varying", 255) == "VARCHAR(255)"

    def test_maps_text(self):
        """_map_sql_type should map 'text' to TEXT."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("text") == "TEXT"

    def test_maps_integer(self):
        """_map_sql_type should map 'integer' to INTEGER."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("integer") == "INTEGER"

    def test_maps_bigint(self):
        """_map_sql_type should map 'bigint' to BIGINT."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("bigint") == "BIGINT"

    def test_maps_numeric(self):
        """_map_sql_type should map 'numeric' to DECIMAL."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("numeric") == "DECIMAL"

    def test_maps_boolean(self):
        """_map_sql_type should map 'boolean' to BOOLEAN."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("boolean") == "BOOLEAN"

    def test_maps_timestamp_types(self):
        """_map_sql_type should handle timestamp variants."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("timestamp without time zone") == "TIMESTAMP"
        assert PostgresConnector._map_sql_type("timestamp with time zone") == "TIMESTAMPTZ"

    def test_maps_date(self):
        """_map_sql_type should map 'date' to DATE."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("date") == "DATE"

    def test_maps_json_types(self):
        """_map_sql_type should handle JSON/JSONB types."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("json") == "JSON"
        assert PostgresConnector._map_sql_type("jsonb") == "JSONB"

    def test_maps_uuid(self):
        """_map_sql_type should map 'uuid' to UUID."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("uuid") == "UUID"

    def test_maps_unknown_to_upper(self):
        """_map_sql_type should uppercase unknown types."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("my_custom_type") == "MY_CUSTOM_TYPE"

    def test_maps_char(self):
        """_map_sql_type should handle character type."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        assert PostgresConnector._map_sql_type("character") == "CHAR"
        # Source maps 'character' with char_max to VARCHAR(length) same as 'character varying'
        assert PostgresConnector._map_sql_type("character", 10) == "VARCHAR(10)"