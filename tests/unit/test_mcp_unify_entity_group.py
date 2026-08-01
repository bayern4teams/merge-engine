"""Unit tests for unify_entity_group MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_server.tools.unify_entity_group import (
    unify_entity_group_impl,
    unify_entity_group_handler,
)


class TestUnifyEntityGroupImpl:
    """Tests for unify_entity_group_impl."""

    @pytest.fixture
    def sample_origins(self) -> list[dict[str, Any]]:
        """Sample origins with priorities."""
        return [
            {"id": "postgres_main", "priority": 10},
            {"id": "mongo_analytics", "priority": 5},
        ]

    @pytest.fixture
    def sample_cluster_records(self) -> list[dict[str, Any]]:
        """Sample records in a cluster."""
        return [
            {
                "_origin_id": "postgres_main",
                "name": "Alice Smith",
                "email": "alice@example.com",
                "phone": None,
            },
            {
                "_origin_id": "mongo_analytics",
                "name": "Alice S.",
                "email": "alice@example.com",
                "phone": "555-1234",
            },
        ]

    @pytest.mark.asyncio
    async def test_unify_entity_group_success(
        self, sample_cluster_records: list[dict[str, Any]], sample_origins: list[dict[str, Any]]
    ) -> None:
        """Test successful entity unification."""
        result = await unify_entity_group_impl(
            cluster_id="cluster-1",
            records=sample_cluster_records,
            origins=sample_origins,
        )

        assert result["cluster_id"] == "cluster-1"
        assert "unified_record" in result
        assert result["unified_record"]["_cluster_id"] == "cluster-1"
        assert result["unified_record"]["_source_count"] == 2
        assert "conflicts" in result

    @pytest.mark.asyncio
    async def test_unify_entity_group_null_complement(
        self,
    ) -> None:
        """Test that null fields are complemented with non-null values."""
        records = [
            {"_origin_id": "a", "name": "Alice", "email": None},
            {"_origin_id": "b", "name": "Alice", "email": "alice@example.com"},
        ]
        result = await unify_entity_group_impl("c1", records)

        assert "null_filled_fields" in result
        assert "email" in result["null_filled_fields"]
        assert result["unified_record"]["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_unify_entity_group_conflict_resolution(
        self,
    ) -> None:
        """Test conflict resolution picks highest priority origin."""
        records = [
            {"_origin_id": "mongo_analytics", "name": "Alice S.", "email": "alice@mongo.com"},
            {"_origin_id": "postgres_main", "name": "Alice Smith", "email": "alice@pg.com"},
        ]
        origins = [
            {"id": "postgres_main", "priority": 10},
            {"id": "mongo_analytics", "priority": 5},
        ]

        result = await unify_entity_group_impl("c1", records, origins=origins)

        # postgres_main has higher priority (10 > 5)
        assert len(result["conflicts"]) > 0
        conflict = result["conflicts"][0]
        assert conflict["selected_origin"] == "postgres_main"

    @pytest.mark.asyncio
    async def test_unify_entity_group_empty_records(self) -> None:
        """Test unification with empty record list raises error."""
        with pytest.raises(ValueError, match="Cannot unify empty record list"):
            await unify_entity_group_impl("c1", [])

    @pytest.mark.asyncio
    async def test_unify_entity_group_no_conflicts(self) -> None:
        """Test unification with no conflicts."""
        records = [
            {"_origin_id": "a", "name": "Alice", "email": "alice@example.com"},
            {"_origin_id": "b", "name": "Alice", "email": "alice@example.com"},
        ]
        result = await unify_entity_group_impl("c1", records)

        assert len(result["conflicts"]) == 0
        assert result["unified_record"]["name"] == "Alice"
        assert result["unified_record"]["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_unify_entity_group_skips_internal_fields(
        self,
    ) -> None:
        """Test that fields starting with _ are skipped."""
        records = [
            {"_origin_id": "a", "_cluster_id": "c1", "name": "Alice"},
            {"_origin_id": "b", "_cluster_id": "c1", "name": "Alice"},
        ]
        result = await unify_entity_group_impl("c1", records)

        # _origin_id should not appear in unified record values
        # but _cluster_id should be set by the function
        assert "_cluster_id" in result["unified_record"]

    @pytest.mark.asyncio
    async def test_unify_entity_group_all_null_values(
        self,
    ) -> None:
        """Test unification with all null values for a field."""
        records = [
            {"_origin_id": "a", "name": None, "email": None},
            {"_origin_id": "b", "name": None, "email": None},
        ]
        result = await unify_entity_group_impl("c1", records)

        # All-null fields should be skipped
        assert "name" not in result["unified_record"]
        assert "email" not in result["unified_record"]

    @pytest.mark.asyncio
    async def test_unify_entity_group_source_count(
        self, sample_cluster_records: list[dict[str, Any]]
    ) -> None:
        """Test that source count reflects number of unique origins."""
        result = await unify_entity_group_impl("c1", sample_cluster_records)

        assert result["source_count"] == 2


class TestUnifyEntityGroupHandler:
    """Tests for unify_entity_group_handler."""

    @pytest.mark.asyncio
    async def test_handler_calls_impl(self) -> None:
        """Test handler delegates to impl."""
        mock_impl = AsyncMock(return_value={
            "cluster_id": "c1",
            "unified_record": {"name": "Alice", "_cluster_id": "c1", "_source_count": 2},
            "conflicts": [],
            "null_filled_fields": ["email"],
            "source_count": 2,
        })

        with patch(
            "src.mcp_server.tools.unify_entity_group.unify_entity_group_impl",
            mock_impl,
        ):
            result = await unify_entity_group_handler({
                "cluster_id": "c1",
                "records": [
                    {"_origin_id": "a", "name": "Alice"},
                    {"_origin_id": "b", "name": "Alice"},
                ],
            })

        mock_impl.assert_called_once()
        assert result["cluster_id"] == "c1"
        assert result["source_count"] == 2
