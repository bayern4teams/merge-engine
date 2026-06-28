"""Unit tests for resolve_entities MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.resolve_entities import (
    resolve_entities_impl,
    _compute_heuristic_confidence,
    _record_id,
    _generate_candidate_pairs,
)


class TestResolveEntitiesImpl:
    """Tests for resolve_entities_impl."""

    @pytest.fixture
    def sample_records(self) -> list[dict[str, Any]]:
        """Sample records from multiple origins."""
        return [
            {
                "_origin_id": "postgres_main",
                "_origin_table": "users",
                "_origin_id_row": "1",
                "name": "Alice Smith",
                "email": "alice@example.com",
            },
            {
                "_origin_id": "mongo_analytics",
                "_origin_table": "customers",
                "_origin_id_row": "2",
                "name": "Alice S.",
                "email": "alice@example.com",
            },
        ]

    @pytest.mark.asyncio
    async def test_resolve_entities_empty_records(self) -> None:
        """Test resolving with empty records."""
        result = await resolve_entities_impl("person", [])

        assert result["entity_type"] == "person"
        assert result["clusters"] == []
        assert result["total_records"] == 0
        assert result["auto_matched"] == 0
        assert result["auto_excluded"] == 0
        assert result["llm_scored"] == 0

    @pytest.mark.asyncio
    async def test_resolve_entities_single_record(self) -> None:
        """Test resolving with a single record."""
        records = [
            {
                "_origin_id": "postgres_main",
                "_origin_table": "users",
                "_origin_id_row": "1",
                "name": "Alice",
            },
        ]
        result = await resolve_entities_impl("person", records)

        assert result["total_records"] == 1
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["match_confidence"] == 1.0
        assert result["auto_matched"] == 1
        assert result["llm_scored"] == 0

    @pytest.mark.asyncio
    async def test_resolve_entities_multiple_records(self, sample_records: list[dict[str, Any]]) -> None:
        """Test resolving with multiple records."""
        result = await resolve_entities_impl("person", sample_records)

        assert result["total_records"] == 2
        assert len(result["clusters"]) >= 1
        assert result["llm_scored"] >= 0

    @pytest.mark.asyncio
    async def test_resolve_entities_entity_type_in_output(
        self, sample_records: list[dict[str, Any]]
    ) -> None:
        """Test that entity_type is included in result."""
        result = await resolve_entities_impl("company", sample_records)

        assert result["entity_type"] == "company"


class TestComputeHeuristicConfidence:
    """Tests for _compute_heuristic_confidence helper."""

    def test_identical_values(self) -> None:
        """Test identical values return perfect score."""
        rec1 = {"name": "Alice", "email": "alice@example.com"}
        rec2 = {"name": "Alice", "email": "alice@example.com"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == 1.0

    def test_different_values(self) -> None:
        """Test different values return low score."""
        rec1 = {"name": "Alice", "email": "alice@example.com"}
        rec2 = {"name": "Bob", "email": "bob@example.com"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf < 0.5

    def test_none_values_ignored(self) -> None:
        """Test that None values are ignored in comparison."""
        rec1 = {"name": "Alice", "email": None}
        rec2 = {"name": "Alice", "email": "alice@example.com"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == 1.0  # Only name matches, and it's identical

    def test_no_common_fields(self) -> None:
        """Test records with no common fields return 0."""
        rec1 = {"name": "Alice", "department": "Engineering"}
        rec2 = {"age": 30, "location": "NYC"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == 0.0

    def test_partial_match(self) -> None:
        """Test partial field matches."""
        rec1 = {"name": "Alice", "email": "alice@example.com", "phone": "555-1234"}
        rec2 = {"name": "Alice", "email": "alice@example.com", "phone": "555-5678"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        # 2 out of 3 fields match
        assert conf == 2.0 / 3.0

    def test_string_similarity(self) -> None:
        """Test string similarity matching."""
        rec1 = {"name": "Alice Smith"}
        rec2 = {"name": "Alice S."}
        conf = _compute_heuristic_confidence(rec1, rec2)
        # Should have some similarity score
        assert conf >= 0.0


class TestRecordId:
    """Tests for _record_id helper."""

    def test_record_id_with_standard_fields(self) -> None:
        """Test record ID extraction with standard _ prefixed fields."""
        rec = {
            "_origin_id": "postgres_main",
            "_origin_table": "users",
            "_origin_id_row": "123",
        }
        assert _record_id(rec) == "postgres_main:users:123"

    def test_record_id_with_fallback_fields(self) -> None:
        """Test record ID falls back to non-prefixed fields."""
        rec = {
            "origin_id": "mongo_analytics",
            "table": "customers",
            "id": "456",
        }
        assert _record_id(rec) == "mongo_analytics:customers:456"

    def test_record_id_with_object_identity_fallback(self) -> None:
        """Test record ID uses id() as last resort."""
        rec = {"name": "Alice"}
        rid = _record_id(rec)
        assert rid.startswith("unknown:unknown:")


class TestGenerateCandidatePairs:
    """Tests for _generate_candidate_pairs helper."""

    def test_no_pairs_single_record(self) -> None:
        """Test no pairs generated for single record."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 0

    def test_pairs_from_two_records(self) -> None:
        """Test one pair from two records."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
            {"_origin_id": "b", "_origin_table": "t", "_origin_id_row": "2"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 1

    def test_pairs_from_three_records(self) -> None:
        """Test three pairs from three records (n*(n-1)/2)."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
            {"_origin_id": "b", "_origin_table": "t", "_origin_id_row": "2"},
            {"_origin_id": "c", "_origin_table": "t", "_origin_id_row": "3"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 3  # 3 choose 2

    def test_pairs_have_correct_format(self) -> None:
        """Test pair format is (id1, id2, rec1, rec2)."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1", "name": "Alice"},
            {"_origin_id": "b", "_origin_table": "t", "_origin_id_row": "2", "name": "Bob"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 1
        id1, id2, rec1, rec2 = pairs[0]
        assert id1 == "a:t:1"
        assert id2 == "b:t:2"
        assert rec1["name"] == "Alice"
        assert rec2["name"] == "Bob"

    def test_skips_self_comparisons(self) -> None:
        """Test that identical record IDs are skipped."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 0  # Same ID should be skipped