"""Unit tests for human_nodes (HITL interrupt nodes)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.orchestrator.nodes.human_nodes import (
    human_review_deps,
    human_review_matches,
    human_review_mappings,
    human_final_approval,
    handle_error,
)


class TestHumanReviewDeps:
    """Tests for the human_review_deps node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "dependency_tree": {"person": ["company"]},
            "extraction_order": ["company", "person"],
            "errors": [],
            "human_feedback": None,
        }

    @pytest.mark.asyncio
    async def test_human_review_deps_interrupts_no_feedback(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_deps returns interrupt_data when no feedback."""
        result = await human_review_deps(base_state)

        assert "interrupt_data" in result
        interrupt = result["interrupt_data"]
        assert interrupt["node"] == "human_review_deps"
        assert interrupt["title"] == "Review Dependency Tree"
        assert interrupt["data"]["dependency_tree"] == {"person": ["company"]}
        assert interrupt["data"]["extraction_order"] == ["company", "person"]

    @pytest.mark.asyncio
    async def test_human_review_deps_approved(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_deps processes approved feedback."""
        base_state["human_feedback"] = {
            "dependency_tree": {"person": [], "company": []}
        }

        result = await human_review_deps(base_state)

        assert "interrupt_data" not in result
        assert result["dependency_tree"] == {"person": [], "company": []}

    @pytest.mark.asyncio
    async def test_human_review_deps_modified_feedback(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_deps uses modified dependency tree."""
        base_state["human_feedback"] = {
            "dependency_tree": {"person": ["company"], "company": ["country"]},
        }
        base_state["errors"] = ["some error"]

        result = await human_review_deps(base_state)

        assert result["dependency_tree"] == {
            "person": ["company"],
            "company": ["country"],
        }
        assert "some error" in result["errors"]


class TestHumanReviewMatches:
    """Tests for the human_review_matches node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "entity_clusters": {
                "company": [
                    {
                        "cluster_id": "c1",
                        "record_ids": ["r1", "r2"],
                        "match_confidence": 0.6,
                        "required_human_review": True,
                    },
                    {
                        "cluster_id": "c2",
                        "record_ids": ["r3"],
                        "match_confidence": 0.99,
                        "required_human_review": False,
                    },
                ],
                "person": [
                    {
                        "cluster_id": "c3",
                        "record_ids": ["r4", "r5"],
                        "match_confidence": 0.45,
                        "required_human_review": True,
                    },
                ],
            },
            "errors": [],
            "human_feedback": None,
        }

    @pytest.mark.asyncio
    async def test_human_review_matches_interrupts_with_low_confidence(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_matches returns low-confidence clusters."""
        result = await human_review_matches(base_state)

        assert "interrupt_data" in result
        data = result["interrupt_data"]["data"]
        assert data["total_clustering_decisions"] == 2
        assert len(data["low_confidence_clusters"]) == 2

    @pytest.mark.asyncio
    async def test_human_review_matches_no_low_confidence(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_matches with no low-confidence clusters."""
        base_state["entity_clusters"] = {
            "company": [
                {
                    "cluster_id": "c1",
                    "record_ids": ["r1"],
                    "required_human_review": False,
                },
            ],
        }

        result = await human_review_matches(base_state)

        assert "interrupt_data" in result
        data = result["interrupt_data"]["data"]
        assert data["total_clustering_decisions"] == 0

    @pytest.mark.asyncio
    async def test_human_review_matches_approved(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_matches processes cluster decisions."""
        base_state["human_feedback"] = {
            "cluster_decisions": [
                {"cluster_id": "c1", "action": "merge", "keep_ids": ["r1", "r2"]}
            ]
        }

        result = await human_review_matches(base_state)

        assert "interrupt_data" not in result
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_human_review_matches_empty_clusters(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with empty entity clusters."""
        base_state["entity_clusters"] = {}

        result = await human_review_matches(base_state)

        assert "interrupt_data" in result
        assert result["interrupt_data"]["data"]["total_clustering_decisions"] == 0


class TestHumanReviewMappings:
    """Tests for the human_review_mappings node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "field_mappings": {
                "company": {
                    "name": {"confidence": 0.98},
                    "unknown_field": {"confidence": 0.0},
                }
            },
            "pending_schema_changes": [
                {"column": "new_column", "data_type": "VARCHAR"}
            ],
            "errors": [],
            "human_feedback": None,
        }

    @pytest.mark.asyncio
    async def test_human_review_mappings_interrupts(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_mappings returns interrupt_data."""
        result = await human_review_mappings(base_state)

        assert "interrupt_data" in result
        data = result["interrupt_data"]["data"]
        assert len(data["unmapped_fields"]) == 1
        assert data["unmapped_fields"][0]["source_field"] == "unknown_field"
        assert len(data["pending_schema_changes"]) == 1

    @pytest.mark.asyncio
    async def test_human_review_mappings_approved(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_review_mappings processes mapping decisions."""
        base_state["human_feedback"] = {
            "mapping_decisions": [],
            "schema_changes": [
                {"column": "approved_col", "data_type": "TEXT"}
            ],
        }

        result = await human_review_mappings(base_state)

        assert "interrupt_data" not in result
        assert result["pending_schema_changes"] == [
            {"column": "approved_col", "data_type": "TEXT"}
        ]

    @pytest.mark.asyncio
    async def test_human_review_mappings_no_unmapped_no_changes(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with no unmapped fields or schema changes."""
        base_state["field_mappings"] = {
            "company": {
                "name": {"confidence": 1.0},
            }
        }
        base_state["pending_schema_changes"] = []

        result = await human_review_mappings(base_state)

        assert "interrupt_data" in result
        assert len(result["interrupt_data"]["data"]["unmapped_fields"]) == 0

    @pytest.mark.asyncio
    async def test_human_review_mappings_high_confidence_not_unmapped(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that high-confidence mappings are not considered unmapped."""
        base_state["field_mappings"] = {
            "company": {
                "name": {"confidence": 0.98},
                "email": {"confidence": 0.95},
            }
        }

        result = await human_review_mappings(base_state)

        assert len(result["interrupt_data"]["data"]["unmapped_fields"]) == 0


class TestHumanFinalApproval:
    """Tests for the human_final_approval node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "unified_entities": {
                "company": [
                    {"id": "c1", "_conflicts": [{"field": "name"}]},
                    {"id": "c2", "_conflicts": []},
                ],
                "person": [
                    {"id": "p1", "_conflicts": [{"field": "email"}, {"field": "phone"}]},
                ],
            },
            "field_mappings": {"company": {}, "person": {}},
            "pending_schema_changes": [
                {"column": "x"},
                {"column": "y"},
            ],
            "errors": ["warning 1"],
            "human_feedback": None,
        }

    @pytest.mark.asyncio
    async def test_human_final_approval_interrupts(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_final_approval returns interrupt_data."""
        result = await human_final_approval(base_state)

        assert "interrupt_data" in result
        data = result["interrupt_data"]["data"]
        summary = data["summary"]
        assert summary["total_entities"] == 2
        assert summary["entity_counts"]["company"] == 2
        assert summary["entity_counts"]["person"] == 1
        assert summary["total_conflicts"] == 3
        assert summary["schema_changes"] == 2
        assert summary["errors"] == 1

    @pytest.mark.asyncio
    async def test_human_final_approval_approved(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_final_approval processes approval."""
        base_state["human_feedback"] = {"approved": True}

        result = await human_final_approval(base_state)

        assert "interrupt_data" not in result
        assert result["load_approval"] is True
        assert "warning 1" in result["errors"]

    @pytest.mark.asyncio
    async def test_human_final_approval_rejected(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that human_final_approval does not approve when not approved."""
        base_state["human_feedback"] = {"approved": False}

        result = await human_final_approval(base_state)

        assert "interrupt_data" in result
        assert "load_approval" not in result or result.get("load_approval") is not True

    @pytest.mark.asyncio
    async def test_human_final_approval_no_unified_entities(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with no unified entities."""
        base_state["unified_entities"] = {}

        result = await human_final_approval(base_state)

        assert "interrupt_data" in result
        assert result["interrupt_data"]["data"]["summary"]["total_entities"] == 0

    @pytest.mark.asyncio
    async def test_human_final_approval_conflicts_as_string(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with _conflicts as a string instead of list."""
        base_state["unified_entities"] = {
            "company": [
                {"id": "c1", "_conflicts": "some conflicts"},
            ]
        }

        result = await human_final_approval(base_state)

        # Should handle gracefully without crashing
        assert "interrupt_data" in result


class TestHandleError:
    """Tests for the handle_error node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "errors": ["Something went wrong"],
            "retry_counts": {},
            "current_step": "extract_data",
        }

    @pytest.mark.asyncio
    async def test_handle_error_no_errors(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with no errors returns cleanly."""
        base_state["errors"] = []

        result = await handle_error(base_state)

        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_handle_error_first_retry(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test first retry increments count but doesn't interrupt."""
        result = await handle_error(base_state)

        assert result["retry_counts"]["extract_data"] == 1
        assert "interrupt_data" not in result

    @pytest.mark.asyncio
    async def test_handle_error_second_retry(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test second retry still below max."""
        base_state["retry_counts"] = {"extract_data": 1}

        result = await handle_error(base_state)

        assert result["retry_counts"]["extract_data"] == 2
        assert "interrupt_data" not in result

    @pytest.mark.asyncio
    async def test_handle_error_third_retry_exceeds_max(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test third retry triggers interrupt (max=3)."""
        base_state["retry_counts"] = {"extract_data": 2}

        result = await handle_error(base_state)

        assert result["retry_counts"]["extract_data"] == 3
        assert "interrupt_data" in result
        assert result["interrupt_data"]["node"] == "handle_error"
        assert result["interrupt_data"]["title"] == "Error: Max Retries Exceeded"
        assert result["interrupt_data"]["data"]["error"] == "Something went wrong"
        assert result["interrupt_data"]["data"]["retry_count"] == 3

    @pytest.mark.asyncio
    async def test_handle_error_different_node(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that retry counts are per-node."""
        base_state["current_step"] = "resolve_entities"
        base_state["retry_counts"] = {"extract_data": 3}

        result = await handle_error(base_state)

        assert result["retry_counts"]["resolve_entities"] == 1
        assert "interrupt_data" not in result

    @pytest.mark.asyncio
    async def test_handle_error_increments_existing_count(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that existing retry counts are incremented."""
        base_state["retry_counts"] = {"extract_data": 1}

        result = await handle_error(base_state)

        assert result["retry_counts"]["extract_data"] == 2

    @pytest.mark.asyncio
    async def test_handle_error_preserves_errors(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that errors are preserved in result."""
        result = await handle_error(base_state)

        assert result["errors"] == ["Something went wrong"]

    @pytest.mark.asyncio
    async def test_handle_error_max_attempts_is_three(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that max attempts is 3 (checked at count=3)."""
        # Start at count=0
        base_state["retry_counts"] = {}
        base_state["current_step"] = "test_node"

        # First call -> count=1
        result = await handle_error(base_state)
        assert result["retry_counts"]["test_node"] == 1

        # Second call at count=1 -> count=2
        base_state["retry_counts"] = {"test_node": 1}
        result = await handle_error(base_state)
        assert result["retry_counts"]["test_node"] == 2

        # Third call at count=2 -> interrupt
        base_state["retry_counts"] = {"test_node": 2}
        result = await handle_error(base_state)
        assert result["retry_counts"]["test_node"] == 3
        assert "interrupt_data" in result