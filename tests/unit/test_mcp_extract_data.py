"""Unit tests for extract_data MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.extract_data import (
    extract_data_impl,
    extract_data_handler,
)


class TestExtractDataImpl:
    """Tests for extract_data_impl."""

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
    async def test_extract_data_success(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test successful data extraction."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.extract = AsyncMock(return_value={
            "rows": [
                {"id": 1, "name": "Alice", "email": "alice@example.com"},
                {"id": 2, "name": "Bob", "email": "bob@example.com"},
            ],
            "column_names": ["id", "name", "email"],
            "total_count": 2,
            "next_cursor": None,
        })

        with patch(
            "src.mcp_server.tools.extract_data.create_connector",
            return_value=mock_connector,
        ):
            result = await extract_data_impl(
                origin_config=valid_origin_config,
                table_name="users",
                columns=["id", "name", "email"],
            )

        assert result["origin_id"] == "postgres_main"
        assert result["table_name"] == "users"
        assert len(result["rows"]) == 2
        assert result["rows"][0]["name"] == "Alice"
        assert result["column_names"] == ["id", "name", "email"]
        assert result["total_count"] == 2

    @pytest.mark.asyncio
    async def test_extract_data_with_cursor(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test extraction with pagination cursor."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.extract = AsyncMock(return_value={
            "rows": [{"id": 3, "name": "Charlie"}],
            "column_names": ["id", "name"],
            "total_count": 10,
            "next_cursor": "cursor_abc123",
        })

        with patch(
            "src.mcp_server.tools.extract_data.create_connector",
            return_value=mock_connector,
        ):
            result = await extract_data_impl(
                origin_config=valid_origin_config,
                table_name="users",
                columns=["*"],
                batch_size=500,
                cursor="cursor_abc123",
            )

        mock_connector.extract.assert_called_once_with(
            table_name="users",
            columns=["*"],
            filter=None,
            batch_size=500,
            cursor="cursor_abc123",
            watermark=None,
        )
        assert result["next_cursor"] == "cursor_abc123"

    @pytest.mark.asyncio
    async def test_extract_data_with_filter(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test extraction with filter conditions."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.extract = AsyncMock(return_value={
            "rows": [{"id": 1, "name": "Alice"}],
            "column_names": ["id", "name"],
            "total_count": 1,
            "next_cursor": None,
        })

        with patch(
            "src.mcp_server.tools.extract_data.create_connector",
            return_value=mock_connector,
        ):
            result = await extract_data_impl(
                origin_config=valid_origin_config,
                table_name="users",
                columns=["id", "name"],
                filter={"name": "Alice"},
            )

        mock_connector.extract.assert_called_once_with(
            table_name="users",
            columns=["id", "name"],
            filter={"name": "Alice"},
            batch_size=1000,
            cursor=None,
            watermark=None,
        )

    @pytest.mark.asyncio
    async def test_extract_data_missing_id(self) -> None:
        """Test extraction fails with missing id."""
        config = {
            "id": "",
            "type": "postgres",
        }
        with pytest.raises(ValueError, match="origin_config must have 'id' field"):
            await extract_data_impl(
                origin_config=config,
                table_name="users",
                columns=["id"],
            )

    @pytest.mark.asyncio
    async def test_extract_data_missing_type(self) -> None:
        """Test extraction fails with missing type."""
        config = {
            "id": "test",
            "type": "",
        }
        with pytest.raises(ValueError, match="origin_config must have 'type' field"):
            await extract_data_impl(
                origin_config=config,
                table_name="users",
                columns=["id"],
            )

    @pytest.mark.asyncio
    async def test_extract_data_missing_connection(self) -> None:
        """Test extraction fails with missing connection."""
        config = {
            "id": "test",
            "type": "postgres",
        }
        with pytest.raises(ValueError, match="origin_config must have 'connection' dict"):
            await extract_data_impl(
                origin_config=config,
                table_name="users",
                columns=["id"],
            )

    @pytest.mark.asyncio
    async def test_extract_data_connector_error(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test handling of connector extraction failure."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.extract = AsyncMock(
            side_effect=Exception("Table not found")
        )

        with patch(
            "src.mcp_server.tools.extract_data.create_connector",
            return_value=mock_connector,
        ):
            from src.orchestrator.utils.retry import PermanentError
            with pytest.raises(PermanentError, match="Extraction failed"):
                await extract_data_impl(
                    origin_config=valid_origin_config,
                    table_name="nonexistent_table",
                    columns=["id"],
                )

    @pytest.mark.asyncio
    async def test_extract_data_empty_rows(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test extraction with empty result set."""
        mock_connector = MagicMock()
        mock_connector.close = AsyncMock()
        mock_connector.extract = AsyncMock(return_value={
            "rows": [],
            "column_names": ["id", "name"],
            "total_count": 0,
            "next_cursor": None,
        })

        with patch(
            "src.mcp_server.tools.extract_data.create_connector",
            return_value=mock_connector,
        ):
            result = await extract_data_impl(
                origin_config=valid_origin_config,
                table_name="users",
                columns=["id", "name"],
            )

        assert len(result["rows"]) == 0
        assert result["total_count"] == 0


class TestExtractDataHandler:
    """Tests for extract_data_handler."""

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
        mock_impl = AsyncMock(return_value={
            "rows": [],
            "column_names": ["id"],
            "total_count": 0,
            "next_cursor": None,
            "origin_id": "test_origin",
            "table_name": "users",
        })

        with patch(
            "src.mcp_server.tools.extract_data.extract_data_impl",
            mock_impl,
        ):
            result = await extract_data_handler({
                "origin_config": origin_config,
                "table_name": "users",
                "columns": ["id"],
            })

        mock_impl.assert_called_once()
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
        mock_origin.connection.model_dump.return_value = {
            "host": "localhost",
            "database": "testdb",
        }
        mock_config.origins = [mock_origin]

        mock_conn = MagicMock()

        mock_conn.close = AsyncMock()
        mock_conn.extract = AsyncMock(return_value={
            "rows": [],
            "column_names": ["id"],
            "total_count": 0,
            "next_cursor": None,
        })

        with patch(
            "src.mcp_server.server._get_config",
            return_value=mock_config,
        ), patch(
            "src.mcp_server.tools.extract_data.create_connector",
            return_value=mock_conn,
        ):
            result = await extract_data_handler({
                "origin_id": "test_origin",
                "table_name": "users",
                "columns": ["id"],
            })

        assert result["origin_id"] == "test_origin"

    @pytest.mark.asyncio
    async def test_handler_missing_both_origin_config_and_origin_id(self) -> None:
        """Test handler fails when neither origin_config nor origin_id provided."""
        with pytest.raises(ValueError, match="Either 'origin_id' or 'origin_config' must be provided"):
            await extract_data_handler({
                "table_name": "users",
                "columns": ["id"],
            })

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
                await extract_data_handler({
                    "origin_id": "test_origin",
                    "table_name": "users",
                    "columns": ["id"],
                })