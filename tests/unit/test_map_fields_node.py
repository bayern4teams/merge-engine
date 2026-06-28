"""Unit tests for map_fields node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.map_fields_node import (
    map_fields,
    has_unmapped_fields,
)


class TestMapFields:
    """Tests for the map_fields node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all companies",
            "target_entities": ["company"],
            "unified_entities": {
                "company": [
                    {
                        "id": "cluster-1",
                        "name": "Acme Corp",
                        "email": "info@acme.com",
                        "phone": "555-1234",
                        "address": "123 Main St",
                    },
                    {
                        "id": "cluster-2",
                        "name": "Beta Inc",
                        "email": "info@beta.com",
                    },
                ],
            },
            "destination": {
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
                "tables": {
                    "company": "merged_companies",
                },
            },
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
        }

    @pytest.fixture
    def mock_map_fields_impl(self) -> AsyncMock:
        """Mock map_fields_impl."""
        return AsyncMock(return_value={
            "field_mapping": {
                "name": {"dest_column": "name", "confidence": 0.98, "requires_schema_change": False},
                "email": {"dest_column": "email", "confidence": 0.95, "requires_schema_change": False},
                "phone": {"dest_column": "phone_number", "confidence": 0.88, "requires_schema_change": False},
                "address": {"dest_column": None, "confidence": 0.0, "requires_schema_change": True},
            },
            "schema_changes": [
                {
                    "column": "address",
                    "data_type": "VARCHAR",
                    "table": "merged_companies",
                    "entity_type": "company",
                }
            ],
            "unmapped_fields": [],
        })

    @pytest.mark.asyncio
    async def test_map_fields_success(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test successful field mapping."""
        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            result = await map_fields(base_state)

        assert "company" in result["field_mappings"]
        mapping = result["field_mappings"]["company"]
        assert "name" in mapping
        assert "email" in mapping
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_map_fields_collects_schema_changes(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test that schema changes are collected."""
        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            result = await map_fields(base_state)

        assert len(result["pending_schema_changes"]) == 1
        change = result["pending_schema_changes"][0]
        assert change["column"] == "address"
        assert change["data_type"] == "VARCHAR"

    @pytest.mark.asyncio
    async def test_map_fields_skips_empty_entity(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that entities with no unified records are skipped."""
        base_state["unified_entities"]["company"] = []

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            AsyncMock(return_value={
                "field_mapping": {},
                "schema_changes": [],
                "unmapped_fields": [],
            }),
        ) as mock_impl:
            result = await map_fields(base_state)

        assert result["field_mappings"]["company"] == {}
        mock_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_map_fields_mapping_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of mapping failure."""
        async def failing_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Mapping service error")

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            side_effect=failing_impl,
        ):
            result = await map_fields(base_state)

        assert result["field_mappings"]["company"] == {}
        assert len(result["errors"]) == 1
        assert "Field mapping failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_map_fields_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test that existing errors are preserved."""
        base_state["errors"] = ["pre-existing"]

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            result = await map_fields(base_state)

        assert "pre-existing" in result["errors"]

    @pytest.mark.asyncio
    async def test_map_fields_dict_destination(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test with dict-style destination config."""
        base_state["destination"] = {
            "type": "postgresql",
            "tables": {"company": "custom_company_table"},
        }

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            await map_fields(base_state)

        mock_map_fields_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_map_fields_object_destination(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test with object-style destination config."""
        class MockDestination:
            type = "postgresql"
            tables = {"company": "object_table"}

        base_state["destination"] = MockDestination()

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            await map_fields(base_state)

        mock_map_fields_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_map_fields_no_tables_attr_on_destination(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test with destination that has no tables attribute."""
        base_state["destination"] = {
            "type": "postgresql",
        }

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            result = await map_fields(base_state)

        assert "company" in result["field_mappings"]

    @pytest.mark.asyncio
    async def test_map_fields_preserves_existing_schema_changes(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test that existing schema changes are preserved."""
        base_state["pending_schema_changes"] = [
            {"column": "existing_col", "data_type": "TEXT"}
        ]

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            result = await map_fields(base_state)

        assert len(result["pending_schema_changes"]) == 2
        assert result["pending_schema_changes"][0]["column"] == "existing_col"

    @pytest.mark.asyncio
    async def test_map_fields_preserves_existing_mappings(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test that existing field mappings are preserved."""
        base_state["field_mappings"] = {
            "existing_entity": {"field1": {"dest_column": "col1"}}
        }

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            result = await map_fields(base_state)

        assert "existing_entity" in result["field_mappings"]
        assert "company" in result["field_mappings"]

    @pytest.mark.asyncio
    async def test_map_fields_default_table_name(
        self, base_state: dict[str, Any], mock_map_fields_impl: AsyncMock
    ) -> None:
        """Test that entity_type is used as default table name."""
        base_state["destination"] = {
            "type": "postgresql",
            "tables": {},
        }

        with patch(
            "src.orchestrator.nodes.map_fields_node.map_fields_impl",
            mock_map_fields_impl,
        ):
            await map_fields(base_state)

        # Should be called with entity_type as table
        call_kwargs = mock_map_fields_impl.call_args
        assert call_kwargs is not None


class TestHasUnmappedFields:
    """Tests for the has_unmapped_fields conditional edge."""

    def test_true_with_schema_changes(self) -> None:
        """Test returns True when there are pending schema changes."""
        state = {
            "field_mappings": {"company": {"name": {"confidence": 1.0}}},
            "pending_schema_changes": [{"column": "new_col"}],
        }
        assert has_unmapped_fields(state) is True

    def test_true_with_pending_schema_changes(self) -> None:
        """Test returns True when there are pending schema changes."""
        state = {
            "field_mappings": {
                "company": {
                    "name": {"confidence": 1.0},
                    "address": {"confidence": 0.0},
                }
            },
            "pending_schema_changes": [
                {"entity_type": "company", "field": "created_at", "action": "add_column"}
            ],
        }
        assert has_unmapped_fields(state) is True

    def test_false_with_all_high_confidence(self) -> None:
        """Test returns False when all mappings are high confidence."""
        state = {
            "field_mappings": {
                "company": {
                    "name": {"confidence": 1.0},
                    "email": {"confidence": 0.95},
                }
            },
            "pending_schema_changes": [],
        }
        assert has_unmapped_fields(state) is False

    def test_false_with_empty_state(self) -> None:
        """Test returns False with empty state."""
        state = {}
        assert has_unmapped_fields(state) is False

    def test_false_with_empty_mappings_and_changes(self) -> None:
        """Test returns False with empty mappings and no schema changes."""
        state = {
            "field_mappings": {},
            "pending_schema_changes": [],
        }
        assert has_unmapped_fields(state) is False

    def test_false_with_none_mappings(self) -> None:
        """Test returns False with None mappings."""
        state = {
            "field_mappings": None,
            "pending_schema_changes": None,
        }
        assert has_unmapped_fields(state) is False

    def test_true_with_only_schema_changes_empty_mappings(self) -> None:
        """Test returns True with only schema changes and empty mappings."""
        state = {
            "field_mappings": {},
            "pending_schema_changes": [{"column": "x"}],
        }
        assert has_unmapped_fields(state) is True