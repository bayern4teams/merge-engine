"""Unit tests for mark_imported MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.mark_imported import (
    mark_imported_impl,
    mark_imported_handler,
)


class TestMarkImportedImpl:
    """Tests for mark_imported_impl."""

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
    async def test_mark_imported_success(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test successful mark imported."""
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.mcp_server.tools.mark_imported.create_connector",
            return_value=mock_connector,
        ):
            result = await mark_imported_impl(
                origin_config=valid_origin_config,
                table_name="users",
                ids=["row_1", "row_2", "row_3"],
            )

        assert result["success"] is True
        assert result["marked_count"] == 3
        assert result["origin_id"] == "postgres_main"
        assert result["table_name"] == "users"
        mock_connector.mark_imported.assert_called_once_with(
            table_name="users",
            ids=["row_1", "row_2", "row_3"],
        )

    @pytest.mark.asyncio
    async def test_mark_imported_empty_ids(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test mark imported with empty ids list."""
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.mcp_server.tools.mark_imported.create_connector",
            return_value=mock_connector,
        ):
            result = await mark_imported_impl(
                origin_config=valid_origin_config,
                table_name="users",
                ids=[],
            )

        assert result["marked_count"] == 0
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_mark_imported_failure(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test mark imported when connector returns False."""
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=False)

        with patch(
            "src.mcp_server.tools.mark_imported.create_connector",
            return_value=mock_connector,
        ):
            result = await mark_imported_impl(
                origin_config=valid_origin_config,
                table_name="users",
                ids=["row_1"],
            )

        assert result["success"] is False
        assert result["marked_count"] == 1

    @pytest.mark.asyncio
    async def test_mark_imported_missing_id(self) -> None:
        """Test mark imported fails with missing id."""
        config = {
            "id": "",
            "type": "postgres",
        }
        with pytest.raises(ValueError, match="origin_config must have 'id' field"):
            await mark_imported_impl(
                origin_config=config,
                table_name="users",
                ids=["row_1"],
            )

    @pytest.mark.asyncio
    async def test_mark_imported_missing_type(self) -> None:
        """Test mark imported fails with missing type."""
        config = {
            "id": "test",
            "type": "",
        }
        with pytest.raises(ValueError, match="origin_config must have 'type' field"):
            await mark_imported_impl(
                origin_config=config,
                table_name="users",
                ids=["row_1"],
            )

    @pytest.mark.asyncio
    async def test_mark_imported_missing_connection(self) -> None:
        """Test mark imported fails with missing connection."""
        config = {
            "id": "test",
            "type": "postgres",
        }
        with pytest.raises(ValueError, match="origin_config must have 'connection' dict"):
            await mark_imported_impl(
                origin_config=config,
                table_name="users",
                ids=["row_1"],
            )

    @pytest.mark.asyncio
    async def test_mark_imported_connector_error(
        self, valid_origin_config: dict[str, Any]
    ) -> None:
        """Test handling of connector mark imported failure."""
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(
            side_effect=Exception("Database connection lost")
        )

        with patch(
            "src.mcp_server.tools.mark_imported.create_connector",
            return_value=mock_connector,
        ):
            from src.orchestrator.utils.retry import PermanentError
            with pytest.raises(PermanentError, match="Mark imported failed"):
                await mark_imported_impl(
                    origin_config=valid_origin_config,
                    table_name="users",
                    ids=["row_1"],
                )

    @pytest.mark.asyncio
    async def test_mark_imported_mongodb_type(self) -> None:
        """Test mark imported with MongoDB origin type."""
        config = {
            "id": "mongo_analytics",
            "type": "mongodb",
            "connection": {
                "uri": "mongodb://localhost:27017/analytics",
            },
        }
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.mcp_server.tools.mark_imported.create_connector",
            return_value=mock_connector,
        ):
            result = await mark_imported_impl(
                origin_config=config,
                table_name="events",
                ids=["evt_1", "evt_2"],
            )

        assert result["origin_id"] == "mongo_analytics"
        assert result["table_name"] == "events"
        assert result["marked_count"] == 2


class TestMarkImportedHandler:
    """Tests for mark_imported_handler."""

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
            "success": True,
            "marked_count": 5,
            "origin_id": "test_origin",
            "table_name": "users",
        })

        with patch(
            "src.mcp_server.tools.mark_imported.mark_imported_impl",
            mock_impl,
        ):
            result = await mark_imported_handler({
                "origin_config": origin_config,
                "table_name": "users",
                "ids": ["row_1", "row_2"],
            })

        mock_impl.assert_called_once()
        assert result["marked_count"] == 5

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
        mock_conn.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.mcp_server.server._get_config",
            return_value=mock_config,
        ), patch(
            "src.mcp_server.tools.mark_imported.create_connector",
            return_value=mock_conn,
        ):
            result = await mark_imported_handler({
                "origin_id": "test_origin",
                "table_name": "users",
                "ids": ["row_1"],
            })

        assert result["origin_id"] == "test_origin"

    @pytest.mark.asyncio
    async def test_handler_missing_both_origin_config_and_origin_id(self) -> None:
        """Test handler fails when neither origin_config nor origin_id provided."""
        with pytest.raises(ValueError, match="Either 'origin_id' or 'origin_config' must be provided"):
            await mark_imported_handler({
                "table_name": "users",
                "ids": ["row_1"],
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
                await mark_imported_handler({
                    "origin_id": "test_origin",
                    "table_name": "users",
                    "ids": ["row_1"],
                })