"""Phase K: HITL gates must actually gate.

Before Phase K, Reject was a no-op at every gate — the graph edged past each
one unconditionally and ``load_data_node`` never looked at ``load_approval``,
so rejecting the final approval still wrote to the destination. Cluster
decisions from the matches gate were discarded outright, and cluster ids were
random, so re-running the same data duplicated every destination row.

These tests pin each of those behaviours down.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.orchestrator.graph import MAX_GATE_RETRIES, _gate_router
from src.orchestrator.nodes.load_data import load_data_node
from src.orchestrator.utils.cluster_edits import apply_cluster_decisions
from src.utils.id_utils import deterministic_uuid, validate_uuid


# ── K.1 / K.2: the load approval guard ───────────────────────────────


def _loadable_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run_id": "test-run",
        "extraction_order": ["company"],
        "unified_entities": {"company": [{"id": "c1", "name": "Acme"}]},
        "field_mappings": {"company": {"name": {"dest_column": "name"}}},
        "destination": {"type": "postgresql", "host": "localhost", "port": 5432},
        "pending_schema_changes": [],
        "errors": [],
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", [False, None, "no", 0])
async def test_load_refuses_without_explicit_approval(approval: Any) -> None:
    """Anything other than a literal True writes nothing."""
    mock_load = AsyncMock()
    with patch("src.orchestrator.nodes.load_data.load_data_impl", mock_load), \
         patch("src.orchestrator.nodes.load_data._write_run_report"):
        result = await load_data_node(_loadable_state(load_approval=approval))

    mock_load.assert_not_called()
    assert result["reconciliation_report"]["load_rejected"] is True


@pytest.mark.asyncio
async def test_load_reports_the_rejection_reason() -> None:
    state = _loadable_state(
        load_approval=False,
        human_feedback={"decision": "reject", "reason": "wrong destination"},
    )
    with patch("src.orchestrator.nodes.load_data.load_data_impl", AsyncMock()), \
         patch("src.orchestrator.nodes.load_data._write_run_report"):
        result = await load_data_node(state)

    assert result["reconciliation_report"]["load_rejected_reason"] == "wrong destination"


# ── K.2: gate routing ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "feedback,expected",
    [
        ({"decision": "approve"}, "forward"),
        ({"decision": "edit"}, "forward"),
        ({"decision": "retry"}, "retry"),
        ({"decision": "reject"}, "abort"),
        ({"decision": "abort"}, "abort"),
        ({"decision": "REJECT"}, "abort"),        # case-insensitive
        ({"approved": False}, "abort"),           # legacy shape
        ({"approved": True}, "forward"),          # legacy shape
        ({}, "forward"),                          # no feedback yet
        (None, "forward"),
    ],
)
def test_gate_router_decisions(feedback: Any, expected: str) -> None:
    route = _gate_router("human_review_deps", "extract_data")
    assert route({"human_feedback": feedback}) == expected


def test_gate_retry_is_bounded() -> None:
    """A user cannot loop a gate forever."""
    route = _gate_router("human_review_matches", "unify_entities")
    state = {
        "human_feedback": {"decision": "retry"},
        "gate_retry_counts": {"human_review_matches": MAX_GATE_RETRIES + 1},
    }
    assert route(state) == "forward"


def test_final_approval_gate_has_no_producer_to_retry() -> None:
    """Retry at final approval must not loop; load_data's guard decides."""
    route = _gate_router("human_final_approval", "load_data")
    assert route({"human_feedback": {"decision": "retry"}}) == "forward"


# ── K.3: cluster decisions actually change the clusters ──────────────


@pytest.fixture
def clusters() -> dict[str, list[dict[str, Any]]]:
    return {
        "person": [
            {
                "cluster_id": "c1",
                "record_ids": ["pg:staff:1", "pg:staff:2", "pg:staff:3"],
                "match_confidence": 0.8,
                "required_human_review": True,
            },
        ]
    }


def test_split_breaks_a_cluster_into_singletons(clusters) -> None:
    out, stats = apply_cluster_decisions(
        clusters, [{"cluster_id": "c1", "action": "split"}]
    )
    assert [c["record_ids"] for c in out["person"]] == [
        ["pg:staff:1"], ["pg:staff:2"], ["pg:staff:3"],
    ]
    assert stats["split"] == 1


def test_split_with_groups_honours_the_partition(clusters) -> None:
    out, _ = apply_cluster_decisions(
        clusters,
        [{
            "cluster_id": "c1",
            "action": "split",
            "groups": [["pg:staff:1", "pg:staff:2"]],
        }],
    )
    # staff:3 wasn't placed by the reviewer — it must survive on its own,
    # not be dropped from the run.
    assert [c["record_ids"] for c in out["person"]] == [
        ["pg:staff:1", "pg:staff:2"], ["pg:staff:3"],
    ]


def test_keep_marks_the_cluster_reviewed(clusters) -> None:
    out, stats = apply_cluster_decisions(
        clusters, [{"cluster_id": "c1", "action": "keep"}]
    )
    assert len(out["person"]) == 1
    assert out["person"][0]["required_human_review"] is False
    assert out["person"][0]["reviewed_by_human"] is True
    assert stats["kept"] == 1


def test_unknown_action_splits_rather_than_merges(clusters) -> None:
    """Over-merging is unrecoverable, so ambiguity resolves toward splitting."""
    out, stats = apply_cluster_decisions(
        clusters, [{"cluster_id": "c1", "action": "banana"}]
    )
    assert len(out["person"]) == 3
    assert stats["unknown"] == 1


def test_no_decisions_leaves_clusters_untouched(clusters) -> None:
    out, stats = apply_cluster_decisions(clusters, [])
    assert out is clusters
    assert stats == {"kept": 0, "split": 0, "unknown": 0}


# ── K.4: deterministic cluster ids ───────────────────────────────────


def test_cluster_id_is_stable_across_calls() -> None:
    a = deterministic_uuid("person", "pg:staff:1", "mongo:users:9")
    b = deterministic_uuid("person", "pg:staff:1", "mongo:users:9")
    assert a == b


def test_cluster_id_differs_by_members_and_entity() -> None:
    base = deterministic_uuid("person", "pg:staff:1")
    assert base != deterministic_uuid("person", "pg:staff:2")
    assert base != deterministic_uuid("company", "pg:staff:1")


def test_cluster_id_passes_uuid_validation() -> None:
    """It becomes a destination primary key, so it must look like a UUID."""
    assert validate_uuid(deterministic_uuid("person", "a", "b"))


@pytest.mark.asyncio
async def test_resolve_entities_produces_stable_cluster_ids() -> None:
    """The same records resolved twice yield the same cluster ids."""
    from src.mcp_server.tools.resolve_entities import resolve_entities_impl

    records = [
        {"_origin_id": "pg", "_origin_table": "staff", "_origin_id_row": "1",
         "first_name": "Rory", "last_name": "Ledner", "email": "a@x.com"},
        {"_origin_id": "pg", "_origin_table": "staff", "_origin_id_row": "2",
         "first_name": "Rory", "last_name": "Lowe", "email": "b@x.com"},
    ]
    first = await resolve_entities_impl("person", records)
    second = await resolve_entities_impl("person", records)

    ids_first = sorted(c["cluster_id"] for c in first["clusters"])
    ids_second = sorted(c["cluster_id"] for c in second["clusters"])
    assert ids_first == ids_second
