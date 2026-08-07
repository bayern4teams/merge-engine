"""Unit tests for tools/relational_evidence.py (Task B4.2)."""

from __future__ import annotations

from src.mcp_server.tools.relational_evidence import (
    MAX_NEIGHBOUR_WEIGHT,
    compute_relational_score_boost,
    inject_relational_evidence,
)


def test_neighbour_evidence_never_alone_triggers_auto_match() -> None:
    # A pair with 0.0 base score (e.g. completely different names)
    rec_a = {"first_name": "Alice", "employer_cluster_id": "cluster_boeing_100"}
    rec_b = {"first_name": "Bob", "employer_cluster_id": "cluster_boeing_100"}

    base_score = 0.0
    boost = compute_relational_score_boost(rec_a, rec_b)
    final_score = base_score + boost

    assert boost <= MAX_NEIGHBOUR_WEIGHT
    assert boost <= 0.15
    # Auto-match bar is 0.95 — boost alone must stay far below 0.95
    assert final_score < 0.95, (
        f"Neighbour evidence boost ({final_score}) illegally exceeded auto_match threshold 0.95"
    )


def test_compute_relational_score_boost_matching_neighbours() -> None:
    rec_a = {
        "employer_cluster_id": "cluster_boeing_100",
        "ceo_cluster_id": "cluster_calhoun_200",
    }
    rec_b = {
        "employer_cluster_id": "cluster_boeing_100",
        "ceo_cluster_id": "cluster_calhoun_200",
    }

    boost = compute_relational_score_boost(rec_a, rec_b)
    assert 0.0 < boost <= MAX_NEIGHBOUR_WEIGHT


def test_inject_relational_evidence() -> None:
    records = [
        {"full_name": "Dave Calhoun", "company_name": "Boeing"},
    ]
    resolved_cluster_map = {"Boeing": "cluster_boeing_100"}

    enriched = inject_relational_evidence(records, resolved_cluster_map)
    assert len(enriched) == 1
    assert enriched[0]["employer_cluster_id"] == "cluster_boeing_100"
