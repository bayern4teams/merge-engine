"""google_v1 stage-0 fix: mutual IND deps are arbitrated, cycles never crash.

APP_FINDINGS 2026-10 #2 in action: two sibling tables whose key columns share
a value set verify an IND in BOTH directions, so the entity-level tree gets
A<-B and B<-A and topological_sort aborted the whole run.
"""

from __future__ import annotations

import pytest

from src.mcp_server.tools.join_graph import JoinEdge, JoinGraph
from src.orchestrator.nodes.analyze_dependencies import (
    _has_cycle,
    _join_graph_to_dependency_tree,
)
from src.orchestrator.utils.dependency_sort import topological_sort


def _edge(child_t, child_c, parent_t, parent_c, confidence):
    return JoinEdge(
        child_origin="o",
        child_table=child_t,
        child_column=child_c,
        parent_origin="o",
        parent_table=parent_t,
        parent_column=parent_c,
        confidence=confidence,
        overlap_ratio=1.0,
        rationale="test",
    )


def _graph_with(*edges):
    g = JoinGraph()
    tables = {e.child_table for e in edges} | {e.parent_table for e in edges}
    for t in tables:
        g.add_node(("o", t))
    for e in edges:
        g.add_edge(e)
    return g


ET = {
    e: [{"origin_id": "o", "table": t}]
    for e, t in (("company", "company_master"), ("contact", "contacts"), ("staff", "staff"))
}
TARGETS = ["company", "contact", "staff"]


def test_mutual_pair_declared_beats_verified_ind():
    """google_v1 shape: contacts --declared FK--> company, plus a reverse
    value-verified IND from parent_company_id; the IND dep must lose."""
    g = _graph_with(
        _edge("contacts", "company_id", "company_master", "company_id", "declared"),
        _edge("company_master", "parent_company_id", "contacts", "company_id", "verified"),
    )
    tree, fully, _ = _join_graph_to_dependency_tree(g, TARGETS, ET)
    assert tree["contact"] == ["company"]
    assert tree["company"] == []
    assert _has_cycle(tree) is False
    topological_sort(tree)  # must not raise


def test_mutual_pair_equal_tiers_both_drop_to_llm():
    g = _graph_with(
        _edge("contacts", "company_id", "company_master", "company_id", "verified"),
        _edge("company_master", "parent_company_id", "contacts", "company_id", "verified"),
    )
    tree, fully, _ = _join_graph_to_dependency_tree(g, TARGETS, ET)
    assert tree["contact"] == []
    assert tree["company"] == []
    assert fully is False  # the unknown direction goes to LLM + human gate


def test_residual_three_cycle_demotes_not_crashes():
    """A 3-cycle of same-tier INDs survives pairwise arbitration; the
    ALGORITHMIC path must refuse it (fall to LLM), never abort the run."""
    g = _graph_with(
        _edge("contacts", "staff_id", "staff", "staff_id", "verified"),
        _edge("staff", "company_id", "company_master", "company_id", "verified"),
        _edge("company_master", "contact_id", "contacts", "contact_id", "verified"),
    )
    tree, fully, _ = _join_graph_to_dependency_tree(g, TARGETS, ET)
    if _has_cycle(tree):
        assert fully is False  # demotion is the contract: never trust a cyclic DAG
    else:
        topological_sort(tree)  # arbitration resolved it — still must be a DAG


def test_has_cycle_basic():
    assert _has_cycle({"a": ["b"], "b": ["a"]})
    assert _has_cycle({"a": ["b"], "b": ["c"], "c": ["a"]})
    assert not _has_cycle({"a": ["b"], "b": []})
    assert not _has_cycle({})


def test_has_cycle_wide_and_deep():
    # a diamond is not a cycle; a long ring is
    assert not _has_cycle({"d": ["b", "c"], "b": ["a"], "c": ["a"], "a": []})
    ring = {str(i): [str((i + 1) % 50)] for i in range(50)}
    assert _has_cycle(ring)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
