"""Integration tests for human-in-the-loop workflow interruption and resume."""

from __future__ import annotations

from typing import Any

import pytest

from src.orchestrator.nodes.human_nodes import (
    human_final_approval,
    human_review_deps,
    human_review_mappings,
    human_review_matches,
)


class TestHumanInLoopNodes:
    """Test HITL nodes produce correct interrupt data."""

    @pytest.mark.asyncio
    async def test_human_review_matches_interrupts_on_low_confidence(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test that review matches produces interrupt_data when clusters need review."""
        dependency_state["target_entities"] = ["company"]
        dependency_state["entity_clusters"] = {
            "company": [
                {
                    "cluster_id": "cluster-1",
                    "record_ids": ["pg:companies:1", "mongo:companies:abc"],
                    "match_confidence": 0.75,
                    "required_human_review": True,
                },
            ],
        }

        result = await human_review_matches(dependency_state)

        assert "interrupt_data" in result
        assert result["interrupt_data"]["node"] == "human_review_matches"
        assert len(result["interrupt_data"]["data"]["low_confidence_clusters"]) == 1

    @pytest.mark.asyncio
    async def test_human_review_matches_no_interrupt_for_auto_matched(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test that review matches interrupt_data shows no low-confidence clusters."""
        dependency_state["target_entities"] = ["company"]
        dependency_state["entity_clusters"] = {
            "company": [
                {
                    "cluster_id": "cluster-1",
                    "record_ids": ["pg:companies:1", "mongo:companies:abc"],
                    "match_confidence": 0.98,
                    "required_human_review": False,
                },
            ],
        }

        result = await human_review_matches(dependency_state)

        # interrupt_data is always returned, but low_confidence_clusters should be empty
        assert "interrupt_data" in result
        assert result["interrupt_data"]["data"]["total_clustering_decisions"] == 0
        assert len(result["interrupt_data"]["data"]["low_confidence_clusters"]) == 0

    @pytest.mark.asyncio
    async def test_human_review_matches_empty_clusters(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test review matches with no clusters."""
        dependency_state["target_entities"] = ["company"]
        dependency_state["entity_clusters"] = {
            "company": [],
        }

        result = await human_review_matches(dependency_state)

        assert "interrupt_data" in result
        # But low_confidence_clusters should be empty
        assert len(result["interrupt_data"]["data"]["low_confidence_clusters"]) == 0

    @pytest.mark.asyncio
    async def test_human_review_matches_with_feedback(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test review matches processes user feedback on resume."""
        dependency_state["entity_clusters"] = {
            "company": [
                {
                    "cluster_id": "cluster-1",
                    "record_ids": ["pg:companies:1", "mongo:companies:abc"],
                    "match_confidence": 0.75,
                    "required_human_review": True,
                },
            ],
        }
        dependency_state["human_feedback"] = {"cluster_decisions": {"cluster-1": "merge"}}

        result = await human_review_matches(dependency_state)

        # No interrupt on resume — feedback already provided
        assert "interrupt_data" not in result
        # The gate returns only what it changed; it adds no errors of its own
        # and must not re-return the history (Phase 0 Sec 0.8).
        assert "errors" not in result
        assert "entity_clusters" in result

    @pytest.mark.asyncio
    async def test_human_review_mappings_interrupts(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test review mappings produces interrupt data."""
        dependency_state["target_entities"] = ["company", "person"]
        dependency_state["field_mappings"] = {
            "company": {
                "name": {"dest_column": "company_name", "confidence": 0.60},
                "email": {"dest_column": "contact_email", "confidence": 0.95},
            },
            "person": {},
        }
        dependency_state["pending_schema_changes"] = []

        result = await human_review_mappings(dependency_state)

        assert "interrupt_data" in result
        assert result["interrupt_data"]["node"] == "human_review_mappings"

    @pytest.mark.asyncio
    async def test_human_review_mappings_no_pending_changes(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test review mappings with no pending changes still interrupts."""
        dependency_state["target_entities"] = ["company"]
        dependency_state["field_mappings"] = {
            "company": {
                "name": {"dest_column": "company_name", "confidence": 0.98},
            },
        }
        dependency_state["pending_schema_changes"] = []

        result = await human_review_mappings(dependency_state)

        # Still interrupts — just no unmapped fields
        assert "interrupt_data" in result

    @pytest.mark.asyncio
    async def test_human_review_mappings_with_feedback(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test review mappings processes user feedback on resume."""
        dependency_state["field_mappings"] = {}
        dependency_state["pending_schema_changes"] = []
        dependency_state["human_feedback"] = {
            "mapping_decisions": {},
            "schema_changes": [],
        }

        result = await human_review_mappings(dependency_state)

        assert "interrupt_data" not in result
        assert "errors" not in result
        assert "pending_schema_changes" in result

    @pytest.mark.asyncio
    async def test_human_review_deps_interrupts(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test dependency review produces interrupt data."""
        dependency_state["dependency_tree"] = {
            "person": ["company"],
        }
        dependency_state["extraction_order"] = ["company", "person"]

        result = await human_review_deps(dependency_state)

        assert "interrupt_data" in result
        assert result["interrupt_data"]["node"] == "human_review_deps"

    @pytest.mark.asyncio
    async def test_human_review_deps_with_feedback(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test dependency review processes feedback on resume."""
        dependency_state["dependency_tree"] = {"person": ["company"]}
        dependency_state["extraction_order"] = ["company", "person"]
        dependency_state["human_feedback"] = {
            "dependency_tree": {"person": ["company"]},
        }

        result = await human_review_deps(dependency_state)

        assert "interrupt_data" not in result
        assert "dependency_tree" in result

    @pytest.mark.asyncio
    async def test_human_final_approval_interrupts(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test final approval produces interrupt data."""
        dependency_state["unified_entities"] = {
            "company": [{"id": 1, "name": "Acme Corp"}],
        }
        dependency_state["field_mappings"] = {}
        dependency_state["pending_schema_changes"] = []
        dependency_state["errors"] = []

        result = await human_final_approval(dependency_state)

        assert "interrupt_data" in result
        assert result["interrupt_data"]["node"] == "human_final_approval"
        assert "summary" in result["interrupt_data"]["data"]

    @pytest.mark.asyncio
    async def test_human_final_approval_approved(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test final approval with user approval."""
        dependency_state["unified_entities"] = {
            "company": [{"id": 1, "name": "Acme Corp"}],
        }
        dependency_state["field_mappings"] = {}
        dependency_state["pending_schema_changes"] = []
        dependency_state["errors"] = []
        dependency_state["human_feedback"] = {"approved": True}

        result = await human_final_approval(dependency_state)

        assert "interrupt_data" not in result
        assert result["load_approval"] is True


class TestHumanInLoopIntegration:
    """Integration tests for HITL interrupt → feedback → resume flow."""

    @pytest.mark.asyncio
    async def test_hitl_full_flow_review_matches_then_resume(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test full HITL flow: interrupt → feedback → resume."""
        # Step 1: Initial interrupt
        dependency_state["entity_clusters"] = {
            "company": [
                {
                    "cluster_id": "c1",
                    "record_ids": ["r1", "r2"],
                    "match_confidence": 0.70,
                    "required_human_review": True,
                },
            ],
        }

        result1 = await human_review_matches(dependency_state)
        assert "interrupt_data" in result1

        # Step 2: User provides feedback (simulating resume)
        dependency_state["human_feedback"] = {"cluster_decisions": {"c1": "merge"}}

        result2 = await human_review_matches(dependency_state)
        assert "interrupt_data" not in result2

    @pytest.mark.asyncio
    async def test_hitl_full_flow_final_approval_then_resume(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test full HITL flow for final approval."""
        # Step 1: Initial interrupt
        dependency_state["unified_entities"] = {"company": [{"id": 1}]}
        dependency_state["errors"] = []
        dependency_state["pending_schema_changes"] = []
        dependency_state["field_mappings"] = {}

        result1 = await human_final_approval(dependency_state)
        assert "interrupt_data" in result1

        # Step 2: User approves
        dependency_state["human_feedback"] = {"approved": True}

        result2 = await human_final_approval(dependency_state)
        assert "interrupt_data" not in result2
        assert result2["load_approval"] is True

    @pytest.mark.asyncio
    async def test_hitl_full_flow_final_approval_rejected(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test final approval rejection."""
        dependency_state["unified_entities"] = {"company": [{"id": 1}]}
        dependency_state["errors"] = []
        dependency_state["pending_schema_changes"] = []
        dependency_state["field_mappings"] = {}
        dependency_state["human_feedback"] = {"approved": False}

        result = await human_final_approval(dependency_state)

        # When not approved, it should still interrupt (no interrupt_data means it won't)
        # Since human_feedback exists but approved is False, it falls through
        # The node returns interrupt_data when approved is not True
        assert "interrupt_data" in result

    @pytest.mark.asyncio
    async def test_has_low_confidence_clusters_conditional_edge(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test the conditional edge function for low confidence clusters."""
        from src.orchestrator.nodes.resolve_entities import has_low_confidence_clusters

        dependency_state["entity_clusters"] = {
            "company": [
                {
                    "cluster_id": "c1",
                    "record_ids": ["r1", "r2"],
                    "match_confidence": 0.75,
                    "required_human_review": True,
                },
            ],
        }

        assert has_low_confidence_clusters(dependency_state) is True

        dependency_state["entity_clusters"] = {
            "company": [
                {
                    "cluster_id": "c1",
                    "record_ids": ["r1"],
                    "match_confidence": 1.0,
                    "required_human_review": False,
                },
            ],
        }

        assert has_low_confidence_clusters(dependency_state) is False

    @pytest.mark.asyncio
    async def test_error_handler_retry_increments_count(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """handle_error clears the failure and increments retry_counts when
        the human's decision is 'retry'.

        Rewritten (Phase 0 Sec 0.7, while wiring tests/integration into the
        default gate for the first time): the previous version of this test
        predated the error-taxonomy/disposition-routing rewrite and asserted
        on an `interrupt_data` key the node hasn't produced since -
        interrupt payloads are built by WorkflowManager._build_interrupt_data
        now, because HITL node bodies never run on resume (the as_node=
        trap). The node's real, current job is just: read the failure +
        decision, clear the failure, and only a 'retry' decision counts
        toward retry_counts.
        """
        from src.orchestrator.nodes.human_nodes import handle_error

        dependency_state["failure"] = {
            "node": "parse_request",
            "error": "Something went wrong",
            "disposition": "escalate",
        }
        dependency_state["retry_counts"] = {"parse_request": 0}
        dependency_state["human_feedback"] = {"decision": "retry"}

        result = await handle_error(dependency_state)

        assert result["retry_counts"]["parse_request"] == 1
        assert result["failure"] == {}
        assert result["handled_failures"][0]["decision"] == "retry"

    @pytest.mark.asyncio
    async def test_error_handler_abort_does_not_increment_retry_count(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """A non-retry decision (abort) clears the failure without touching
        retry_counts - only an explicit 'retry' should count toward the
        gate's retry budget (see test_error_handler_retry_increments_count's
        docstring for why this replaces the old max-retries test)."""
        from src.orchestrator.nodes.human_nodes import handle_error

        dependency_state["failure"] = {
            "node": "parse_request",
            "error": "Something went wrong",
            "disposition": "escalate",
        }
        dependency_state["retry_counts"] = {"parse_request": 2}
        dependency_state["human_feedback"] = {"decision": "abort"}

        result = await handle_error(dependency_state)

        assert result["retry_counts"]["parse_request"] == 2
        assert result["failure"] == {}
        assert result["handled_failures"][0]["decision"] == "abort"
