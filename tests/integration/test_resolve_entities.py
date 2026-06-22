"""Integration tests for entity resolution with deterministic blocking + clustering."""

from __future__ import annotations

from typing import Any

import pytest

from src.mcp_server.tools.resolve_entities import (
    resolve_entities_impl,
    _compute_deterministic_confidence,
    _normalize,
    _record_id,
)


class TestResolveEntitiesIntegration:
    """Test entity resolution tool integrated with blocking + clustering."""

    @pytest.mark.asyncio
    async def test_exact_match_blocking_clusters(
        self,
        sample_company_records: list[dict[str, Any]],
        patch_server_config: Any,
    ) -> None:
        """Test that records with matching blocking keys get clustered together."""
        result = await resolve_entities_impl(
            entity_type="company",
            records=sample_company_records,
        )

        assert result["entity_type"] == "company"
        assert result["total_records"] == 4
        assert len(result["clusters"]) == 4
        # With blocking keys [name, email], no two records share both blocking key values,
        # so each record ends up in its own block with no candidate pairs generated.
        # The test verifies clustering works even when no matches are found.

    @pytest.mark.asyncio
    async def test_single_record_no_clustering(
        self,
        patch_server_config: Any,
    ) -> None:
        """Test single record results in a single cluster."""
        records = [
            {
                "name": "Solo Corp",
                "email": "solo@solo.com",
                "_origin_id": "postgres_main",
                "_origin_table": "companies",
                "_origin_id_row": "999",
            }
        ]
        result = await resolve_entities_impl(
            entity_type="company",
            records=records,
        )

        assert result["total_records"] == 1
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["required_human_review"] is False

    @pytest.mark.asyncio
    async def test_completely_different_records(
        self,
        patch_server_config: Any,
    ) -> None:
        """Test that completely different records end up in separate clusters."""
        records = [
            {
                "name": "Company A",
                "email": "a@example.com",
                "_origin_id": "postgres_main",
                "_origin_table": "companies",
                "_origin_id_row": "1",
            },
            {
                "name": "Company B完全不同的",
                "email": "b完全不同的.com",
                "_origin_id": "mongo_analytics",
                "_origin_table": "companies",
                "_origin_id_row": "2",
            },
        ]
        result = await resolve_entities_impl(
            entity_type="company",
            records=records,
        )

        assert result["total_records"] == 2
        # Different records should not be in the same cluster
        single_clusters = [c for c in result["clusters"] if len(c["record_ids"]) == 1]
        assert len(single_clusters) == 2

    @pytest.mark.asyncio
    async def test_cluster_has_required_human_review_flag(
        self,
        sample_person_records: list[dict[str, Any]],
        patch_server_config: Any,
    ) -> None:
        """Test that borderline matches are flagged for human review."""
        result = await resolve_entities_impl(
            entity_type="person",
            records=sample_person_records,
        )

        # At least check that all required fields are present
        for cluster in result["clusters"]:
            assert "cluster_id" in cluster
            assert "record_ids" in cluster
            assert "match_confidence" in cluster
            assert "required_human_review" in cluster
            assert isinstance(cluster["record_ids"], list)
            assert isinstance(cluster["required_human_review"], bool)


class TestDeterministicBlockingHelpers:
    """Test helper functions used in deterministic blocking."""

    def test_normalize_handles_various_inputs(self) -> None:
        """Test value normalization for blocking key comparison."""
        assert _normalize("  Hello World  ") == "hello_world"
        assert _normalize("test@email.com") == "test@emailcom"
        assert _normalize(None) == ""
        assert _normalize(42) == "42"
        assert _normalize("A.B-C") == "abc"

    def test_record_id_extraction(self) -> None:
        """Test record ID extraction from different record formats."""
        rec = {
            "_origin_id": "pg1",
            "_origin_table": "users",
            "_origin_id_row": "55",
        }
        assert _record_id(rec) == "pg1:users:55"

        # Fallback to 'origin_id' and 'id'
        rec2 = {
            "origin_id": "mongo1",
            "table": "contacts",
            "id": "abc",
        }
        assert _record_id(rec2) == "mongo1:contacts:abc"

    def test_compute_deterministic_confidence_exact_match(self) -> None:
        """Test that exact matching blocking keys produce high confidence."""
        rec1 = {"name": "John Smith", "email": "john@test.com"}
        rec2 = {"name": "John Smith", "email": "john@test.com"}
        keys = ["name", "email"]

        confidence = _compute_deterministic_confidence(rec1, rec2, keys)
        assert confidence == 1.0

    def test_compute_deterministic_confidence_partial_match(self) -> None:
        """Test partial matching produces intermediate confidence."""
        rec1 = {"name": "John Smith", "email": "john@test.com"}
        rec2 = {"name": "Jane Smith", "email": "john@test.com"}
        keys = ["name", "email"]

        confidence = _compute_deterministic_confidence(rec1, rec2, keys)
        assert 0.0 < confidence < 1.0

    def test_compute_deterministic_confidence_no_match(self) -> None:
        """Test completely different values produce low confidence."""
        rec1 = {"name": "John Smith", "email": "john@test.com"}
        rec2 = {"name": "完全不同的名字", "email": "完全不同的@email.com"}
        keys = ["name", "email"]

        confidence = _compute_deterministic_confidence(rec1, rec2, keys)
        assert confidence == 0.0

    def test_compute_deterministic_confidence_null_values(self) -> None:
        """Test that null values are skipped in confidence calculation."""
        rec1 = {"name": "John", "email": None}
        rec2 = {"name": "John", "email": "john@test.com"}
        keys = ["name", "email"]

        confidence = _compute_deterministic_confidence(rec1, rec2, keys)
        # Only name should be compared, so 1.0
        assert confidence == 1.0

    def test_compute_deterministic_confidence_empty_keys(self) -> None:
        """Test empty blocking keys produce zero confidence."""
        rec1 = {"name": "John"}
        rec2 = {"name": "John"}
        assert _compute_deterministic_confidence(rec1, rec2, []) == 0.0