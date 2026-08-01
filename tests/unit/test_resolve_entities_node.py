"""Unit tests for resolve_entities node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.resolve_entities import resolve_entities, has_low_confidence_clusters
from src.orchestrator.spool import clusters_for


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    """Each test gets its own spool: clusters and unified records live there."""
    import src.orchestrator.spool as spool_mod

    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


class TestResolveEntities:
    """Tests for the resolve_entities node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all people and companies",
            "target_entities": ["company", "person"],
            "extracted_data": {
                "company": [
                    {"id": 1, "name": "Acme Corp", "email": "info@acme.com"},
                    {"id": 2, "name": "Acme Corporation", "email": "contact@acme.com"},
                    {"id": 3, "name": "Beta Inc", "email": "info@beta.com"},
                ],
                "person": [
                    {"id": 1, "name": "John Doe", "email": "john@example.com"},
                    {"id": 2, "name": "Jane Smith", "email": "jane@example.com"},
                ],
            },
            "entity_clusters": {},
            "errors": [],
        }

    @pytest.fixture
    def mock_resolve_impl(self) -> AsyncMock:
        """Mock resolve_entities_impl."""
        return AsyncMock(return_value={
            "clusters": [
                {
                    "cluster_id": "cluster-1",
                    "record_ids": ["postgres_main:companies:1", "mongo_analytics:companies:2"],
                    "match_confidence": 0.85,
                    "required_human_review": False,
                },
                {
                    "cluster_id": "cluster-2",
                    "record_ids": ["postgres_main:companies:3"],
                    "match_confidence": 1.0,
                    "required_human_review": False,
                },
            ],
            "total_records": 3,
            "auto_matched": 1,
            "llm_scored": 1,
        })

    @pytest.mark.asyncio
    async def test_resolve_entities_success(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        """Test successful entity resolution."""
        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_resolve_impl,
        ):
            result = await resolve_entities(base_state)

        assert "company" in result["entity_clusters"]
        assert len(clusters_for(result, "company")) == 2
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_resolve_entities_no_records_skipped(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that entities with no records are skipped."""
        base_state["extracted_data"]["person"] = []

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            AsyncMock(return_value={"clusters": [], "total_records": 0, "auto_matched": 0, "llm_scored": 0}),
        ) as mock_impl:
            result = await resolve_entities(base_state)

        # person has no records, so it's set to [] and impl is not called for it
        assert clusters_for(result, "person") == []
        # company has records so impl IS called once for company
        assert mock_impl.call_count == 1
        # Verify person was not passed to impl (only company was)
        call_kwargs = mock_impl.call_args[1]
        assert call_kwargs["entity_type"] == "company"

    @pytest.mark.asyncio
    async def test_resolve_entities_resolution_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of resolution failure for one entity."""
        async def failing_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("entity_type") == "person":
                raise Exception("Resolution service unavailable")
            return {"clusters": [], "total_records": 0, "auto_matched": 0, "llm_scored": 0}

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            side_effect=failing_impl,
        ):
            result = await resolve_entities(base_state)

        assert clusters_for(result, "company") == []
        assert clusters_for(result, "person") == []
        assert len(result["errors"]) == 1
        assert "person" in result["errors"][0]
        assert "failed" in result["errors"][0].lower()

    @pytest.mark.asyncio
    async def test_resolve_entities_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        """Test that existing errors are preserved."""
        base_state["errors"] = ["pre-existing error"]

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_resolve_impl,
        ):
            result = await resolve_entities(base_state)

        assert "pre-existing error" in result["errors"]

    @pytest.mark.asyncio
    async def test_resolve_entities_empty_target_entities(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with no target entities."""
        base_state["target_entities"] = []
        base_state["extracted_data"] = {}

        result = await resolve_entities(base_state)
        assert result["entity_clusters"] == {}
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_resolve_entities_multiple_clusters_per_entity(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test resolution producing multiple clusters."""
        mock_multi = AsyncMock(return_value={
            "clusters": [
                {"cluster_id": "c1", "record_ids": ["r1", "r2"], "match_confidence": 0.9, "required_human_review": False},
                {"cluster_id": "c2", "record_ids": ["r3"], "match_confidence": 1.0, "required_human_review": False},
                {"cluster_id": "c3", "record_ids": ["r4", "r5"], "match_confidence": 0.5, "required_human_review": True},
            ],
            "total_records": 5,
            "auto_matched": 2,
            "llm_scored": 2,
        })

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_multi,
        ):
            result = await resolve_entities(base_state)

        assert len(clusters_for(result, "company")) == 3

    @pytest.mark.asyncio
    async def test_resolve_entities_stats_logged(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        """Test that resolution stats are correctly captured."""
        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_resolve_impl,
        ):
            result = await resolve_entities(base_state)

        clusters = clusters_for(result, "company")
        assert len(clusters) == 2

    @pytest.mark.asyncio
    async def test_resolve_entities_only_single_record_clusters(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test resolution with only single-record clusters."""
        mock_single = AsyncMock(return_value={
            "clusters": [
                {"cluster_id": "c1", "record_ids": ["r1"], "match_confidence": 1.0, "required_human_review": False},
                {"cluster_id": "c2", "record_ids": ["r2"], "match_confidence": 1.0, "required_human_review": False},
            ],
            "total_records": 2,
            "auto_matched": 2,
            "llm_scored": 0,
        })

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_single,
        ):
            result = await resolve_entities(base_state)

        assert len(clusters_for(result, "company")) == 2

    @pytest.mark.asyncio
    async def test_resolve_entities_with_no_records_key(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test when extracted_data doesn't have entity type key."""
        base_state["extracted_data"] = {}

        result = await resolve_entities(base_state)
        assert clusters_for(result, "company") == []
        assert clusters_for(result, "person") == []


class TestHasLowConfidenceClusters:
    """Tests for the has_low_confidence_clusters conditional edge."""

    def test_true_with_low_confidence_multi_record_cluster(self) -> None:
        """Test returns True when there's a low-confidence multi-record cluster."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1", "r2"],
                        "required_human_review": True,
                        "cluster_id": "c1",
                    }
                ]
            }
        }
        assert has_low_confidence_clusters(state) is True

    def test_false_with_no_clusters(self) -> None:
        """Test returns False with no clusters."""
        state = {"entity_clusters": {}}
        assert has_low_confidence_clusters(state) is False

    def test_false_with_empty_entity_clusters(self) -> None:
        """Test returns False with empty entity cluster lists."""
        state = {
            "entity_clusters": {
                "company": [],
                "person": [],
            }
        }
        assert has_low_confidence_clusters(state) is False

    def test_false_with_single_record_cluster(self) -> None:
        """Test returns False even with required_human_review if single record."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1"],
                        "required_human_review": True,
                    }
                ]
            }
        }
        assert has_low_confidence_clusters(state) is False

    def test_false_without_required_human_review(self) -> None:
        """Test returns False when required_human_review is not set."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1", "r2"],
                        "required_human_review": False,
                    }
                ]
            }
        }
        assert has_low_confidence_clusters(state) is False

    def test_false_with_no_entity_clusters_key(self) -> None:
        """Test returns False with no entity_clusters key."""
        state = {}
        assert has_low_confidence_clusters(state) is False

    def test_true_with_multiple_entities_one_low_confidence(self) -> None:
        """Test returns True if any entity has low-confidence cluster."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1"],
                        "required_human_review": False,
                    }
                ],
                "person": [
                    {
                        "record_ids": ["r2", "r3"],
                        "required_human_review": True,
                    }
                ],
            }
        }
        assert has_low_confidence_clusters(state) is True