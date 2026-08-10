"""Regression guard: HITL gate payloads must not drift by data key.

``interrupt_before=HITL_NODES`` (see graph.py) means a HITL node's own
function body never actually runs when the graph pauses there — only
``WorkflowManager._build_interrupt_data`` runs, reading straight from the
checkpointed state. The two used to be independent implementations for
``human_review_deps`` (missing ``origin_schemas``/``column_dependencies``),
field-mapping rows (missing ``origin_id``/``table``/``is_coalesced``), and
``human_review_matches`` (missing ``auto_match_stats``/
``pending_merge_candidates``/``pending_merge_note``) — three real bugs the
live gate shipped with, because whoever edited one implementation had no
signal that a second, silently different one existed. Phase 2.1 of the
fix-bugs follow-up plan fixed this by extracting one shared builder per gate
in ``nodes/human_nodes.py`` that both the node body and the workflow manager
now call. This test is the mechanical guard against it happening a fourth
time: for every HITL node, the node body's own ``interrupt_data["data"]``
key set and ``WorkflowManager._build_interrupt_data``'s key set must match
(or, where one side is a deliberately richer superset — documented per node
below — the richer side must still contain everything the plainer side has).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.orchestrator.nodes.human_nodes import (
    human_final_approval,
    human_review_deps,
    human_review_mappings,
    human_review_matches,
    human_review_scope,
)
from src.orchestrator.workflow_manager import WorkflowManager


def _keys(interrupt_data: dict[str, Any]) -> set[str]:
    return set(interrupt_data["data"].keys())


class TestInterruptPayloadParity:
    """One node body vs. WorkflowManager._build_interrupt_data, per gate."""

    @pytest.mark.asyncio
    async def test_human_review_deps_exact_parity(self) -> None:
        state: dict[str, Any] = {
            "dependency_tree": {"person": ["company"]},
            "extraction_order": ["company", "person"],
            "column_dependencies": [{"child_table": "person", "parent_table": "company"}],
            "entity_tables": {"company": [{"origin_id": "origin_1", "table": "Company"}]},
            "schemas": {
                "origin_1": {"tables": [{"name": "Company", "columns": [{"name": "companyID"}]}]}
            },
            "table_selection_notes": "",
            "dependency_analysis_warning": "",
            "human_feedback": None,
        }
        node_result = await human_review_deps(dict(state))
        node_keys = _keys(node_result["interrupt_data"])

        manager_keys = _keys(WorkflowManager._build_interrupt_data("human_review_deps", state))

        assert node_keys == manager_keys
        # origin_schemas/column_dependencies are exactly what was missing
        # from the live gate before Phase 2.1 — assert their presence
        # explicitly, not just that the two sides happen to agree.
        assert "origin_schemas" in manager_keys
        assert "column_dependencies" in manager_keys

    @pytest.mark.asyncio
    async def test_human_review_mappings_exact_parity(self) -> None:
        state: dict[str, Any] = {
            "field_mappings": {
                "company": {
                    "name": {
                        "dest_column": "name",
                        "confidence": 0.9,
                        "origin_id": "origin_1",
                        "table": "Company",
                    }
                }
            },
            "unmapped_fields": {
                "company": [{"source_field": "notes", "reason": "no destination equivalent"}]
            },
            "pending_schema_changes": [],
            "schemas": {},
            "entity_tables": {},
            "human_feedback": None,
        }
        node_result = await human_review_mappings(dict(state))
        node_keys = _keys(node_result["interrupt_data"])

        manager_keys = _keys(WorkflowManager._build_interrupt_data("human_review_mappings", state))

        assert node_keys == manager_keys
        # Every row must carry provenance now, on both sides equally.
        node_rows = node_result["interrupt_data"]["data"]["mapping_rows"]
        assert all(r.get("origin_id") for r in node_rows if r["action"] == "map")

    @pytest.mark.asyncio
    async def test_human_review_scope_exact_parity(self) -> None:
        scope_plan = {
            "subject": "Airbus",
            "subject_entity": "company",
            "candidates": [],
            "entities": {
                "company": [{"origin_id": "origin_1", "table": "Company", "mode": "match"}]
            },
        }
        state: dict[str, Any] = {"scope_plan": scope_plan, "human_feedback": None}
        node_result = await human_review_scope(dict(state))
        node_keys = _keys(node_result["interrupt_data"])

        manager_keys = _keys(WorkflowManager._build_interrupt_data("human_review_scope", state))

        assert node_keys == manager_keys

    @pytest.mark.asyncio
    async def test_human_review_matches_exact_parity(self) -> None:
        state: dict[str, Any] = {
            "entity_clusters": {
                "company": [
                    {
                        "cluster_id": "c1",
                        "record_ids": ["origin_1:Company:1", "origin_2:Company:1"],
                        "required_human_review": True,
                        "match_confidence": 0.7,
                    }
                ]
            },
            "extracted_data": {
                "company": [
                    {
                        "_origin_id": "origin_1",
                        "_origin_table": "Company",
                        "_origin_id_row": "1",
                        "name": "Airbus",
                    },
                    {
                        "_origin_id": "origin_2",
                        "_origin_table": "Company",
                        "_origin_id_row": "1",
                        "name": "Airbus SE",
                    },
                ]
            },
            "resolution_stats": {
                "company": {
                    "pending_merge_candidates": [
                        {"record_ids": ["origin_1:Company:2"], "confidence": 0.6}
                    ]
                }
            },
            "human_feedback": None,
        }
        node_result = await human_review_matches(dict(state))
        node_keys = _keys(node_result["interrupt_data"])

        manager_keys = _keys(WorkflowManager._build_interrupt_data("human_review_matches", state))

        assert node_keys == manager_keys
        # These three are exactly what the node body used to lack before
        # Phase 2.1 unified it with the workflow manager's richer version.
        assert "auto_match_stats" in node_keys
        assert "pending_merge_candidates" in node_keys
        assert "pending_merge_note" in node_keys
        # And the record-level field previews that explain WHY a match
        # happened must actually be populated, not just present as a key.
        low_conf = node_result["interrupt_data"]["data"]["low_confidence_clusters"]
        assert low_conf[0]["records"][0]["fields"].get("name")

    @pytest.mark.asyncio
    async def test_handle_error_delegates_to_shared_builder(self) -> None:
        """handle_error's own body never re-pauses with fresh interrupt_data
        (see its docstring on the as_node= resume trap) — the only producer
        of this gate's payload is build_error_interrupt, called from both
        sides. Assert there is no second, independent construction hiding
        in WorkflowManager._build_interrupt_data.
        """
        state: dict[str, Any] = {
            "failure": {
                "node": "extract_data",
                "error": "boom",
                "error_class": "PermanentError",
                "disposition": "escalate",
                "context": {},
            },
            "retry_counts": {},
            "errors": ["boom"],
        }
        from src.orchestrator.nodes.human_nodes import build_error_interrupt

        direct = build_error_interrupt(state)
        via_manager = WorkflowManager._build_interrupt_data("handle_error", state)

        # via_manager carries one extra key, payload_version (Phase 2.2) —
        # a content hash _build_interrupt_data always attaches on top of
        # whatever the per-node builder returned. Strip it before comparing
        # so this test still proves there's no second, independent
        # construction of the actual payload content.
        via_manager_without_version = {
            k: v for k, v in via_manager.items() if k != "payload_version"
        }
        assert direct == via_manager_without_version
        assert "payload_version" in via_manager

    @pytest.mark.asyncio
    async def test_human_final_approval_is_a_superset_not_a_drift(self) -> None:
        """human_final_approval is the one gate where the two sides are NOT
        expected to match exactly: WorkflowManager's version deliberately
        adds reconciliation (build_reconciliation_report — too expensive to
        duplicate in the plain resume-path body), plus quality_report/
        has_quality_failure (Phase 4.5 plumbing). The node's own simpler
        {summary, pending_schema_changes} must still be a strict subset —
        if it ever isn't, the manager stopped showing something the node
        itself considers part of this gate.
        """
        state: dict[str, Any] = {
            "unified_entities": {"company": [{"id": "1", "_conflicts": []}]},
            "pending_schema_changes": [],
            "errors": [],
            "field_mappings": {},
            "unmapped_fields": {},
            "resolution_stats": {},
            "human_feedback": None,
        }
        node_result = await human_final_approval(dict(state))
        node_keys = _keys(node_result["interrupt_data"])

        manager_keys = _keys(WorkflowManager._build_interrupt_data("human_final_approval", state))

        assert node_keys.issubset(manager_keys), (
            f"human_final_approval's own body has keys the manager's payload "
            f"lacks: {node_keys - manager_keys}"
        )
        expected_extra = {
            "reconciliation",
            "field_mappings_rows",
            "auto_match_stats",
            "quality_report",
            "has_quality_failure",
            "clusters_by_entity",
        }
        assert manager_keys - node_keys == expected_extra, (
            "human_final_approval's extra manager-only keys changed — update "
            "expected_extra (and confirm the new key is intentional) rather "
            "than silently widening what this test tolerates."
        )

    def test_a_fail_severity_quality_report_actually_reaches_the_payload(self) -> None:
        """Phase 4.5's single highest-value fix: on B3 (the branch this gate
        was salvaged from), has_quality_failure was computed by validate_data
        and then silently dropped before ever reaching a human. Key
        PRESENCE alone (the superset test above) doesn't prove that — this
        proves the VALUE genuinely flows through _build_interrupt_data
        unmodified, exactly as a live pause at human_final_approval would
        show it."""
        fail_report = {
            "status": "completed",
            "severity": "fail",
            "entities": {
                "person": {
                    "severity": "fail",
                    "row_count": 0,
                    "sample_size": 0,
                    "sampled": False,
                    "checks": [
                        {
                            "check": "row_count_ge_1",
                            "severity": "fail",
                            "success": False,
                            "details": "Entity 'person' has 0 extracted rows (expected >= 1).",
                        }
                    ],
                }
            },
        }
        state: dict[str, Any] = {
            "unified_entities": {},
            "pending_schema_changes": [],
            "errors": [],
            "field_mappings": {},
            "unmapped_fields": {},
            "resolution_stats": {},
            "human_feedback": None,
            "quality_report": fail_report,
            "has_quality_failure": True,
        }
        payload = WorkflowManager._build_interrupt_data("human_final_approval", state)
        assert payload["data"]["has_quality_failure"] is True
        assert payload["data"]["quality_report"] == fail_report


class TestPayloadVersion:
    """Phase 2.2: a stable content hash Gates.tsx can key a React effect on
    instead of the whole `data` object, which is a fresh reference on every
    2-second poll even when nothing in it changed."""

    def test_identical_content_yields_identical_version(self) -> None:
        state = {"dependency_tree": {"person": ["company"]}, "extraction_order": ["company"]}
        v1 = WorkflowManager._build_interrupt_data("human_review_deps", dict(state))[
            "payload_version"
        ]
        v2 = WorkflowManager._build_interrupt_data("human_review_deps", dict(state))[
            "payload_version"
        ]
        assert v1 == v2

    def test_changed_content_yields_different_version(self) -> None:
        state_a = {"dependency_tree": {"person": ["company"]}, "extraction_order": ["company"]}
        state_b = {
            "dependency_tree": {"person": ["company"], "contact": ["person"]},
            "extraction_order": ["company", "person", "contact"],
        }
        v1 = WorkflowManager._build_interrupt_data("human_review_deps", state_a)["payload_version"]
        v2 = WorkflowManager._build_interrupt_data("human_review_deps", state_b)["payload_version"]
        assert v1 != v2

    def test_different_node_yields_different_version_for_same_data_shape(self) -> None:
        """Two different gates must not collide even if their data dicts
        happen to serialize identically (an empty dict, say)."""
        v_deps = WorkflowManager._payload_version("human_review_deps", {})
        v_scope = WorkflowManager._payload_version("human_review_scope", {})
        assert v_deps != v_scope

    def test_mutating_cached_payload_requires_recomputed_version(self) -> None:
        """Simulates the apply_mapping_feedback rejection path: mutating a
        cached interrupt_data's `data` in place must be paired with
        recomputing payload_version, or the frontend's effect (keyed on the
        version string, not on `data`) would never fire for that mutation."""
        base = WorkflowManager._build_interrupt_data("human_review_mappings", {})
        stale_version = base["payload_version"]

        base["data"]["error"] = "rejected: destination schema is a fixed contract"
        recomputed = WorkflowManager._payload_version("human_review_mappings", base["data"], "")

        assert recomputed != stale_version
