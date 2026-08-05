"""Integration tests for extract_data tool with connector interface."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_server.tools.extract_data import extract_data_impl
from src.orchestrator.utils.retry import PermanentError


class TestExtractDataIntegration:
    """Test extract_data tool integrated with connector interface."""

    @pytest.mark.asyncio
    async def test_successful_extraction_with_pagination(
        self,
        mock_connector_factory: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that extraction with cursor-based pagination works end-to-end."""
        sample_rows = [
            {"id": i, "name": f"Company {i}", "email": f"comp{i}@test.com"} for i in range(1, 21)
        ]

        mock_conn = mock_connector_factory(extract_rows=sample_rows, extract_total=20)

        with patch("src.mcp_server.tools.extract_data.create_connector") as mock_create:
            mock_create.return_value = mock_conn

            # First batch
            result = await extract_data_impl(
                origin_config=postgres_origin_config,
                table_name="companies",
                columns=["*"],
                batch_size=10,
            )

            assert result["origin_id"] == "postgres_main"
            assert result["table_name"] == "companies"
            assert len(result["rows"]) == 10
            assert result["total_count"] == 20
            assert result["next_cursor"] is not None

            # Second batch using cursor
            result2 = await extract_data_impl(
                origin_config=postgres_origin_config,
                table_name="companies",
                columns=["*"],
                batch_size=10,
                cursor="10",
            )

            assert len(result2["rows"]) == 10
            assert result2["next_cursor"] is None

    @pytest.mark.asyncio
    async def test_extraction_single_batch(
        self,
        mock_connector_factory: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test extraction where all data fits in a single batch."""
        sample_rows = [{"id": i, "name": f"Item {i}"} for i in range(1, 6)]

        mock_conn = mock_connector_factory(extract_rows=sample_rows, extract_total=5)

        with patch("src.mcp_server.tools.extract_data.create_connector") as mock_create:
            mock_create.return_value = mock_conn

            result = await extract_data_impl(
                origin_config=postgres_origin_config,
                table_name="items",
                columns=["id", "name"],
                batch_size=100,
            )

            assert len(result["rows"]) == 5
            assert result["total_count"] == 5
            assert result["next_cursor"] is None

    @pytest.mark.asyncio
    async def test_extraction_missing_origin_config(
        self,
    ) -> None:
        """Test that a missing origin config raises a descriptive error."""
        with pytest.raises(ValueError) as exc_info:
            await extract_data_impl(
                origin_config={},
                table_name="companies",
                columns=["*"],
            )

        # Empty dict fails on 'connection' check (id passes with "unknown")
        assert "origin_config must have" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extraction_error_becomes_permanent(
        self,
        mock_connector_factory: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that connector exceptions are wrapped as PermanentError."""
        mock_conn = mock_connector_factory()
        mock_conn.extract = AsyncMock(side_effect=ConnectionError("Database unreachable"))

        with patch("src.mcp_server.tools.extract_data.create_connector") as mock_create:
            mock_create.return_value = mock_conn

            with pytest.raises(PermanentError) as exc_info:
                await extract_data_impl(
                    origin_config=postgres_origin_config,
                    table_name="companies",
                    columns=["*"],
                )

            assert "Extraction failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extraction_with_filter(
        self,
        mock_connector_factory: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test extraction passes filter to connector correctly."""
        sample_rows = [{"id": i, "name": f"Item {i}", "_imported": False} for i in range(1, 4)]

        mock_conn = mock_connector_factory(extract_rows=sample_rows, extract_total=3)

        with patch("src.mcp_server.tools.extract_data.create_connector") as mock_create:
            mock_create.return_value = mock_conn

            filter_cond = {"_imported": False}
            await extract_data_impl(
                origin_config=postgres_origin_config,
                table_name="items",
                columns=["id", "name"],
                filter=filter_cond,
            )

            # Verify filter was passed to the connector
            mock_conn.extract.assert_called_once()
            call_kwargs = mock_conn.extract.call_args[1]
            assert call_kwargs["filter"] == filter_cond

    @pytest.mark.asyncio
    async def test_extraction_batch_size_respected(
        self,
        mock_connector_factory: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that batch_size is passed through to the connector."""
        sample_rows = [{"id": i, "name": f"Item {i}"} for i in range(1, 11)]

        mock_conn = mock_connector_factory(extract_rows=sample_rows, extract_total=10)

        with patch("src.mcp_server.tools.extract_data.create_connector") as mock_create:
            mock_create.return_value = mock_conn

            await extract_data_impl(
                origin_config=postgres_origin_config,
                table_name="items",
                columns=["*"],
                batch_size=3,
            )

            call_kwargs = mock_conn.extract.call_args[1]
            assert call_kwargs["batch_size"] == 3
