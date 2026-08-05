"""Unit tests for discover_schema MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.discover_schema import (
    discover_schema_handler,
    discover_schema_impl,
)


class TestDiscoverSchemaImpl:
    """Tests for discover_schema_impl."""

    @pytest.fixture
    def valid_origin_config(self) -> dict[str, Any]:
        """Valid origin configuration."""
        return {
            "id": "postgres_main",
            "type": "postgres",
            "connection": {
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
                "password": "testpass",
            },
            "priority": 1,
        }

    @pytest.mark.asyncio
    async def test_discover_schema_success(self, valid_origin_config: dict[str, Any]) -> None:
        """Test successful schema discovery."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.discover_schema = AsyncMock(
            return_value={
                "tables": [
                    {
                        "name": "users",
                        "columns": [
                            {"name": "id", "type": "INTEGER", "nullable": False},
                            {"name": "name", "type": "VARCHAR", "nullable": False},
                        ],
                        "foreign_keys": [],
                        "sample_row": {"id": 1, "name": "Alice"},
                    }
                ]
            }
        )

        with patch(
            "src.mcp_server.tools.discover_schema.create_connector",
            return_value=mock_connector,
        ):
            result = await discover_schema_impl(valid_origin_config)

        assert result["origin_id"] == "postgres_main"
        assert result["origin_type"] == "postgres"
        assert len(result["tables"]) == 1
        assert result["tables"][0]["name"] == "users"
        mock_connector.discover_schema.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_schema_multiple_tables(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test discovery with multiple tables."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.discover_schema = AsyncMock(
            return_value={
                "tables": [
                    {
                        "name": "users",
                        "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
                        "foreign_keys": [],
                        "sample_row": {"id": 1},
                    },
                    {
                        "name": "orders",
                        "columns": [
                            {"name": "id", "type": "INTEGER", "nullable": False},
                            {"name": "user_id", "type": "INTEGER", "nullable": False},
                        ],
                        "foreign_keys": [
                            {
                                "column": "user_id",
                                "ref_table": "users",
                                "ref_column": "id",
                            }
                        ],
                        "sample_row": {"id": 1, "user_id": 1},
                    },
                ]
            }
        )

        with patch(
            "src.mcp_server.tools.discover_schema.create_connector",
            return_value=mock_connector,
        ):
            result = await discover_schema_impl(valid_origin_config)

        assert len(result["tables"]) == 2
        assert result["tables"][1]["name"] == "orders"
        assert len(result["tables"][1]["foreign_keys"]) == 1

    @pytest.mark.asyncio
    async def test_discover_schema_missing_id(self) -> None:
        """Test discovery fails with missing id."""
        config = {
            "id": "",
            "type": "postgres",
        }
        with pytest.raises(ValueError, match="origin_config must have 'id' field"):
            await discover_schema_impl(config)

    @pytest.mark.asyncio
    async def test_discover_schema_missing_type(self) -> None:
        """Test discovery fails with missing type."""
        config = {
            "id": "test",
            "type": "",
        }
        with pytest.raises(ValueError, match="origin_config must have 'type' field"):
            await discover_schema_impl(config)

    @pytest.mark.asyncio
    async def test_discover_schema_missing_connection(self) -> None:
        """Test discovery fails with missing connection."""
        config = {
            "id": "test",
            "type": "postgres",
        }
        with pytest.raises(ValueError, match="origin_config must have 'connection' dict"):
            await discover_schema_impl(config)

    @pytest.mark.asyncio
    async def test_discover_schema_connector_error(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test handling of connector discovery failure."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.discover_schema = AsyncMock(side_effect=Exception("Connection refused"))

        with patch(
            "src.mcp_server.tools.discover_schema.create_connector",
            return_value=mock_connector,
        ):
            from src.orchestrator.utils.retry import PermanentError

            with pytest.raises(PermanentError, match="Schema discovery failed"):
                await discover_schema_impl(valid_origin_config)

    @pytest.mark.asyncio
    async def test_discover_schema_mongodb_type(
        self,
    ) -> None:
        """Test discovery with MongoDB origin type."""
        config = {
            "id": "mongo_analytics",
            "type": "mongodb",
            "connection": {
                "uri": "mongodb://localhost:27017/analytics",
            },
        }
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.discover_schema = AsyncMock(
            return_value={
                "tables": [
                    {
                        "name": "events",
                        "columns": [
                            {"name": "_id", "type": "ObjectId", "nullable": False},
                            {"name": "type", "type": "STRING", "nullable": False},
                        ],
                        "foreign_keys": [],
                        "sample_row": {"_id": "evt1", "type": "click"},
                    }
                ]
            }
        )

        with patch(
            "src.mcp_server.tools.discover_schema.create_connector",
            return_value=mock_connector,
        ):
            result = await discover_schema_impl(config)

        assert result["origin_id"] == "mongo_analytics"
        assert result["origin_type"] == "mongodb"
        assert result["tables"][0]["name"] == "events"


class TestDiscoverSchemaHandler:
    """Tests for discover_schema_handler."""

    @pytest.mark.asyncio
    async def test_handler_with_origin_config(
        self,
    ) -> None:
        """Test handler with origin_config parameter."""
        origin_config = {
            "id": "test_origin",
            "type": "postgres",
            "connection": {
                "host": "localhost",
                "database": "testdb",
            },
        }
        mock_impl = AsyncMock(
            return_value={
                "origin_id": "test_origin",
                "origin_type": "postgres",
                "tables": [],
            }
        )

        with patch(
            "src.mcp_server.tools.discover_schema.discover_schema_impl",
            mock_impl,
        ):
            result = await discover_schema_handler(
                {
                    "origin_config": origin_config,
                }
            )

        mock_impl.assert_called_once_with(origin_config)
        assert result["origin_id"] == "test_origin"

    @pytest.mark.asyncio
    async def test_handler_with_origin_id_legacy(
        self,
    ) -> None:
        """Test handler with legacy origin_id parameter."""
        mock_config = MagicMock()
        mock_origin = MagicMock()
        mock_origin.id = "test_origin"
        mock_origin.type = "postgres"
        mock_origin.priority = 1
        mock_origin.connection = MagicMock()
        mock_origin.connection.model_dump.return_value = {"host": "localhost"}
        mock_config.origins = [mock_origin]

        mock_conn = MagicMock()

        mock_conn.close = AsyncMock()
        mock_conn.discover_schema = AsyncMock(
            return_value={
                "tables": [],
                "origin_id": "test_origin",
                "origin_type": "postgres",
            }
        )

        with (
            patch(
                "src.mcp_server.server._get_config",
                return_value=mock_config,
            ),
            patch(
                "src.mcp_server.tools.discover_schema.create_connector",
                return_value=mock_conn,
            ),
        ):
            result = await discover_schema_handler(
                {
                    "origin_id": "test_origin",
                }
            )

        assert result["origin_id"] == "test_origin"

    @pytest.mark.asyncio
    async def test_handler_missing_both_origin_config_and_origin_id(self) -> None:
        """Test handler fails when neither origin_config nor origin_id provided."""
        with pytest.raises(
            ValueError, match="Either 'origin_id' or 'origin_config' must be provided"
        ):
            await discover_schema_handler({})

    @pytest.mark.asyncio
    async def test_handler_origin_id_not_found(
        self,
    ) -> None:
        """Test handler fails when origin_id not in config."""
        mock_config = MagicMock()
        mock_origin = MagicMock()
        mock_origin.id = "other_origin"
        mock_config.origins = [mock_origin]

        with patch(
            "src.mcp_server.server._get_config",
            return_value=mock_config,
        ):
            with pytest.raises(ValueError, match="Unknown origin 'test_origin'"):
                await discover_schema_handler({"origin_id": "test_origin"})
