"""Unit tests for human_nodes (HITL interrupt nodes)."""

from __future__ import annotations

from typing import Any

import pytest

from src.orchestrator.nodes.human_nodes import (
    handle_error,
    human_final_approval,
    human_review_deps,
    human_review_mappings,
    human_review_matches,
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
    async def test_human_review_deps_approved(self, base_state: dict[str, Any]) -> None:
        """Test that human_review_deps processes approved feedback."""
        base_state["human_feedback"] = {"dependency_tree": {"person": [], "company": []}}

        result = await human_review_deps(base_state)

        assert "interrupt_data" not in result
        assert result["dependency_tree"] == {"person": [], "company": []}

    @pytest.mark.asyncio
    async def test_human_review_deps_modified_feedback(self, base_state: dict[str, Any]) -> None:
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
        # This gate adds no new errors of its own (Phase 0 Sec 0.8); the
        # existing history survives via MergeState's real operator.add
        # reducer, not because this node re-returns it.
        assert "errors" not in result


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
    async def test_human_review_matches_no_low_confidence(self, base_state: dict[str, Any]) -> None:
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
    async def test_human_review_matches_approved(self, base_state: dict[str, Any]) -> None:
        """Test that human_review_matches processes cluster decisions."""
        base_state["human_feedback"] = {
            "cluster_decisions": [{"cluster_id": "c1", "action": "merge", "keep_ids": ["r1", "r2"]}]
        }

        result = await human_review_matches(base_state)

        assert "interrupt_data" not in result
        assert "errors" not in result

    @pytest.mark.asyncio
    async def test_human_review_matches_empty_clusters(self, base_state: dict[str, Any]) -> None:
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
                    "unknown_field": {"confidence": 0.0, "requires_schema_change": True},
                }
            },
            "unmapped_fields": {
                "company": [
                    {
                        "field": "unknown_field",
                        "confidence": 0.4,
                        "suggested_column": None,
                        "reason": "Low confidence match; requires human review",
                    }
                ],
            },
            "pending_schema_changes": [{"column": "new_column", "data_type": "VARCHAR"}],
            "errors": [],
            "human_feedback": None,
        }

    @pytest.mark.asyncio
    async def test_human_review_mappings_interrupts(self, base_state: dict[str, Any]) -> None:
        """Test that human_review_mappings returns interrupt_data."""
        result = await human_review_mappings(base_state)

        assert "interrupt_data" in result
        data = result["interrupt_data"]["data"]
        assert len(data["unmapped_fields"]) == 1
        assert data["unmapped_fields"][0]["source_field"] == "unknown_field"
        assert len(data["pending_schema_changes"]) == 1

    @pytest.mark.asyncio
    async def test_human_review_mappings_approved(self, base_state: dict[str, Any]) -> None:
        """Test that human_review_mappings processes mapping decisions."""
        base_state["human_feedback"] = {
            "mapping_decisions": [],
            "schema_changes": [{"column": "approved_col", "data_type": "TEXT"}],
        }

        result = await human_review_mappings(base_state)

        assert "interrupt_data" not in result
        assert result["pending_schema_changes"] == [{"column": "approved_col", "data_type": "TEXT"}]

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
        base_state["unmapped_fields"] = {}
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
        base_state["unmapped_fields"] = {}

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
    async def test_human_final_approval_interrupts(self, base_state: dict[str, Any]) -> None:
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
    async def test_human_final_approval_approved(self, base_state: dict[str, Any]) -> None:
        """Test that human_final_approval processes approval."""
        base_state["human_feedback"] = {"approved": True}

        result = await human_final_approval(base_state)

        assert "interrupt_data" not in result
        assert result["load_approval"] is True
        assert "errors" not in result

    @pytest.mark.asyncio
    async def test_human_final_approval_rejected(self, base_state: dict[str, Any]) -> None:
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
    """Tests for the handle_error escalation gate.

    The gate no longer counts retries on its own — LangGraph's per-node
    RetryPolicy handles transient failures automatically, and what reaches
    this gate is a failure a human has to make a call on. Its whole job is
    to record that call and clear the failure so the router can act.
    """

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """A run stopped by a failure in extract_data."""
        return {
            "errors": ["Something went wrong"],
            "retry_counts": {},
            "failure": {
                "node": "extract_data",
                "error": "table orders failed on batch 3",
                "error_class": "DataQualityError",
                "disposition": "escalate",
                "context": {"failed_tables": [{"table_name": "orders"}]},
            },
            "human_feedback": {"decision": "retry"},
        }

    @pytest.mark.asyncio
    async def test_no_failure_is_a_no_op(self, base_state: dict[str, Any]) -> None:
        """With nothing to handle the gate must not invent state."""
        base_state["failure"] = {}

        result = await handle_error(base_state)

        assert result == {}

    @pytest.mark.asyncio
    async def test_failure_is_cleared_so_a_retry_can_proceed(
        self, base_state: dict[str, Any]
    ) -> None:
        """Leaving `failure` set would make the node's own router re-catch it."""
        result = await handle_error(base_state)

        assert result["failure"] == {}

    @pytest.mark.asyncio
    async def test_decision_travels_with_the_handled_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """The outbound router reads node+decision from handled_failures."""
        result = await handle_error(base_state)

        assert len(result["handled_failures"]) == 1
        handled = result["handled_failures"][0]
        assert handled["node"] == "extract_data"
        assert handled["decision"] == "retry"
        assert handled["error"] == "table orders failed on batch 3"

    @pytest.mark.asyncio
    async def test_retry_increments_that_node_s_attempt_count(
        self, base_state: dict[str, Any]
    ) -> None:
        """The router's loop guard reads this count."""
        base_state["retry_counts"] = {"extract_data": 1}

        result = await handle_error(base_state)

        assert result["retry_counts"]["extract_data"] == 2

    @pytest.mark.asyncio
    async def test_skip_does_not_count_as_an_attempt(self, base_state: dict[str, Any]) -> None:
        """Skipping is not retrying — it must not consume a retry budget."""
        base_state["human_feedback"] = {"decision": "skip"}

        result = await handle_error(base_state)

        assert result["retry_counts"].get("extract_data", 0) == 0
        assert result["handled_failures"][0]["decision"] == "skip"

    @pytest.mark.asyncio
    async def test_missing_feedback_is_recorded_as_abort(self, base_state: dict[str, Any]) -> None:
        """No decision must never be read as permission to continue."""
        base_state["human_feedback"] = None

        result = await handle_error(base_state)

        assert result["handled_failures"][0]["decision"] == "abort"

    @pytest.mark.asyncio
    async def test_retry_counts_are_per_node(self, base_state: dict[str, Any]) -> None:
        """A failure in one node must not spend another node's budget."""
        base_state["failure"] = {**base_state["failure"], "node": "resolve_entities"}
        base_state["retry_counts"] = {"extract_data": 3}

        result = await handle_error(base_state)

        assert result["retry_counts"]["resolve_entities"] == 1
        assert result["retry_counts"]["extract_data"] == 3


class TestBuildErrorInterrupt:
    """Tests for the payload the error gate shows a human."""

    @pytest.fixture
    def state(self) -> dict[str, Any]:
        return {
            "failure": {
                "node": "extract_data",
                "error": "orders failed on batch 3",
                "error_class": "DataQualityError",
                "disposition": "escalate",
                "context": {"rows_discarded": 2000},
            },
            "retry_counts": {"extract_data": 1},
            "errors": ["a", "b", "c", "d", "e", "f", "g"],
        }

    def test_names_the_failed_step_and_the_error(self, state: dict[str, Any]) -> None:
        from src.orchestrator.nodes.human_nodes import build_error_interrupt

        payload = build_error_interrupt(state)

        assert payload["node"] == "handle_error"
        assert "extract_data" in payload["title"]
        assert payload["data"]["failed_node"] == "extract_data"
        assert payload["data"]["error"] == "orders failed on batch 3"
        assert payload["data"]["error_class"] == "DataQualityError"
        assert payload["data"]["attempts"] == 1
        assert payload["data"]["context"]["rows_discarded"] == 2000

    def test_offers_retry_skip_and_abort(self, state: dict[str, Any]) -> None:
        from src.orchestrator.nodes.human_nodes import build_error_interrupt

        options = build_error_interrupt(state)["data"]["options"]

        assert [o["decision"] for o in options] == ["retry", "skip", "abort"]

    def test_skip_option_says_where_the_run_would_resume(self, state: dict[str, Any]) -> None:
        """ "Skip" sounds harmless; the wording has to say what it costs."""
        from src.orchestrator.nodes.human_nodes import build_error_interrupt

        options = build_error_interrupt(state)["data"]["options"]
        skip = next(o for o in options if o["decision"] == "skip")

        # extract_data's successor per NODE_SUCCESSOR (Phase 4.5: validate_data).
        assert "validate_data" in skip["detail"]
        assert "incomplete" in skip["detail"].lower()

    def test_recent_errors_are_capped(self, state: dict[str, Any]) -> None:
        from src.orchestrator.nodes.human_nodes import build_error_interrupt

        assert build_error_interrupt(state)["data"]["recent_errors"] == [
            "c",
            "d",
            "e",
            "f",
            "g",
        ]
