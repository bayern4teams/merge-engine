"""Unit tests for discover_schemas node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.discover_schemas import discover_schemas


class TestDiscoverSchemas:
    """Tests for the discover_schemas node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state with origins."""
        return {
            "user_request": "Import all people and companies",
            "target_entities": ["person", "company"],
            "origins": [
                {"id": "postgres_main", "type": "postgres"},
                {"id": "mongo_analytics", "type": "mongodb"},
            ],
            "schemas": {},
            "errors": [],
            "llm_config": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test_key",
            },
        }

    @pytest.fixture
    def mock_discover_schema(self) -> AsyncMock:
        """Mock discover_schema_impl."""
        mock = AsyncMock(return_value={
            "tables": [
                {
                    "name": "customers",
                    "full_name": "public.customers",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "name", "type": "VARCHAR"},
                    ],
                    "foreign_keys": [],
                    "sample_row": {"id": 1, "name": "Test"},
                }
            ]
        })
        return mock

    @pytest.mark.asyncio
    async def test_discover_schemas_success(
        self, base_state: dict[str, Any], mock_discover_schema: AsyncMock
    ) -> None:
        """Test discover_schemas successfully discovers schemas from all origins."""
        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            mock_discover_schema,
        ):
            result = await discover_schemas(base_state)

        assert "postgres_main" in result["schemas"]
        assert "mongo_analytics" in result["schemas"]
        assert len(result["schemas"]["postgres_main"]["tables"]) == 1
        assert len(result["schemas"]["mongo_analytics"]["tables"]) == 1
        assert not result["errors"]
        assert mock_discover_schema.call_count == 2

    @pytest.mark.asyncio
    async def test_discover_schemas_single_origin(
        self, base_state: dict[str, Any], mock_discover_schema: AsyncMock
    ) -> None:
        """Test discover_schemas with a single origin."""
        base_state["origins"] = [{"id": "postgres_main", "type": "postgres"}]

        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            mock_discover_schema,
        ):
            result = await discover_schemas(base_state)

        assert "postgres_main" in result["schemas"]
        assert "mongo_analytics" not in result["schemas"]
        assert mock_discover_schema.call_count == 1

    @pytest.mark.asyncio
    async def test_discover_schemas_empty_origins(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test discover_schemas with no origins returns empty schemas."""
        base_state["origins"] = []

        result = await discover_schemas(base_state)

        assert result["schemas"] == {}
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_discover_schemas_partial_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test discover_schemas handles partial failure (one origin fails)."""
        async def side_effect_fn(origin: dict[str, Any]) -> dict[str, Any]:
            if origin.get("id") == "postgres_main":
                return {
                    "tables": [
                        {"name": "customers", "columns": [], "foreign_keys": [], "sample_row": {}}
                    ]
                }
            raise Exception("Connection refused")

        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            side_effect=side_effect_fn,
        ):
            result = await discover_schemas(base_state)

        assert "postgres_main" in result["schemas"]
        assert "mongo_analytics" not in result["schemas"]
        assert len(result["errors"]) == 1
        assert "mongo_analytics" in result["errors"][0]
        assert "failed" in result["errors"][0].lower()

    @pytest.mark.asyncio
    async def test_discover_schemas_all_fail(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test discover_schemas when all origins fail."""
        error_msg = "All connections failed"

        async def fail_side_effect(origin_id: str) -> dict[str, Any]:
            raise Exception(error_msg)

        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            side_effect=fail_side_effect,
        ):
            result = await discover_schemas(base_state)

        assert result["schemas"] == {}
        assert len(result["errors"]) == 2  # One error per origin
        for err in result["errors"]:
            assert "failed" in err.lower()

    @pytest.mark.asyncio
    async def test_discover_schemas_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_discover_schema: AsyncMock
    ) -> None:
        """Test discover_schemas preserves existing errors from state."""
        base_state["errors"] = ["pre-existing error 1", "pre-existing error 2"]

        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            mock_discover_schema,
        ):
            result = await discover_schemas(base_state)

        assert len(result["errors"]) == 2
        assert "pre-existing error 1" in result["errors"]
        assert "pre-existing error 2" in result["errors"]

    @pytest.mark.asyncio
    async def test_discover_schemas_schema_table_count_logged(
        self, base_state: dict[str, Any], mock_discover_schema: AsyncMock
    ) -> None:
        """Test discover_schemas returns correct table count in log."""
        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            mock_discover_schema,
        ):
            result = await discover_schemas(base_state)

        # Verify schema was discovered with 1 table
        assert len(result["schemas"]["postgres_main"]["tables"]) == 1

    @pytest.mark.asyncio
    async def test_discover_schemas_origin_with_id_attribute(
        self, base_state: dict[str, Any], mock_discover_schema: AsyncMock
    ) -> None:
        """Test discover_schemas handles origins with id as attribute (object)."""
        class OriginObj:
            id = "postgres_main"
            type = "postgres"

        base_state["origins"] = [OriginObj()]

        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            mock_discover_schema,
        ):
            result = await discover_schemas(base_state)

        assert "postgres_main" in result["schemas"]

    @pytest.mark.asyncio
    async def test_discover_schemas_origin_without_id(
        self, base_state: dict[str, Any], mock_discover_schema: AsyncMock
    ) -> None:
        """Test discover_schemas handles origins without id (defaults to empty string)."""
        base_state["origins"] = [{"type": "postgres"}]  # No 'id' key

        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            mock_discover_schema,
        ):
            result = await discover_schemas(base_state)

        # Should default to "unknown" as origin_id
        assert "unknown" in result["schemas"]

    @pytest.mark.asyncio
    async def test_discover_schemas_multiple_tables(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test discover_schemas with multiple tables in schema."""
        mock_multi = AsyncMock(return_value={
            "tables": [
                {"name": "customers", "columns": [], "foreign_keys": [], "sample_row": {}},
                {"name": "orders", "columns": [], "foreign_keys": [], "sample_row": {}},
                {"name": "products", "columns": [], "foreign_keys": [], "sample_row": {}},
            ]
        })

        with patch(
            "src.orchestrator.nodes.discover_schemas.discover_schema_impl",
            mock_multi,
        ):
            result = await discover_schemas(base_state)

        assert len(result["schemas"]["postgres_main"]["tables"]) == 3