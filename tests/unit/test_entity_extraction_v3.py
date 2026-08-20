"""Unit tests for V3 entity anchors, subgraph, coverage, extraction
(Part B, P-9).

Covers: anchor discovery never auto-picking a winner (§31), the traversal
policy's every cap (§65) including cycle prevention, per-record context
scoring (§33), the coverage matrix (§34), and extraction planning/execution
(§35, §55, §56) -- including the value-propagation-by-column fix (a node's
filter must only ever use its OWN join column's values, never every value
that ever appeared anywhere in the seed row).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_server.tools.entity_extraction_v3 import (
    TraversalPolicy,
    build_coverage_matrix,
    compute_context_score,
    discover_anchors,
    execute_extraction,
    plan_extraction,
    traverse_subgraph,
)
from src.mcp_server.tools.join_graph import JoinEdge, JoinGraph, PathHop


def _edge(
    child_table: str,
    child_column: str,
    parent_table: str,
    parent_column: str,
    *,
    origin: str = "o1",
    confidence: str = "verified",
) -> JoinEdge:
    return JoinEdge(
        child_origin=origin,
        child_table=child_table,
        child_column=child_column,
        parent_origin=origin,
        parent_table=parent_table,
        parent_column=parent_column,
        confidence=confidence,
        overlap_ratio=1.0,
        rationale="test edge",
    )


def _node(table: str, origin: str = "o1") -> tuple[str, str]:
    return (origin, table)


def _hop(child_table, child_column, parent_table, parent_column, *, confidence="verified"):
    return PathHop(
        edge=_edge(child_table, child_column, parent_table, parent_column, confidence=confidence),
        direction="narrow",
    )


class TestDiscoverAnchors:
    @pytest.mark.asyncio
    async def test_returns_ranked_candidates_never_a_single_winner(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock(
            return_value={
                "rows": [
                    {"id": "1", "name": "Boeing"},
                    {"id": "2", "name": "Boeing United Kingdom"},
                ]
            }
        )
        result = await discover_anchors(connector, "o1", "Company", ["name"], "Boeing")
        assert len(result) == 2
        assert result[0]["score"] >= result[1]["score"]
        # An exact match must score strictly higher than a longer superset.
        assert result[0]["matched_value"] == "Boeing"
        assert result[0]["score"] == 1.0

    @pytest.mark.asyncio
    async def test_empty_subject_returns_no_candidates(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock()
        result = await discover_anchors(connector, "o1", "Company", ["name"], "   ")
        assert result == []
        connector.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_column_query_does_not_abort_the_others(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock(
            side_effect=[RuntimeError("boom"), {"rows": [{"id": "1", "legal_name": "Boeing"}]}]
        )
        result = await discover_anchors(
            connector, "o1", "Company", ["name", "legal_name"], "Boeing"
        )
        assert len(result) == 1
        assert result[0]["matched_column"] == "legal_name"

    @pytest.mark.asyncio
    async def test_results_are_capped_at_limit(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock(
            return_value={"rows": [{"id": str(i), "name": f"Boeing {i}"} for i in range(10)]}
        )
        result = await discover_anchors(connector, "o1", "Company", ["name"], "Boeing", limit=3)
        assert len(result) == 3


class TestTraversalPolicy:
    def test_min_edge_confidence_blocks_weaker_edges(self) -> None:
        policy = TraversalPolicy(min_edge_confidence="verified")
        assert policy.edge_allowed(_edge("a", "x", "b", "y", confidence="verified"))
        assert not policy.edge_allowed(_edge("a", "x", "b", "y", confidence="probable"))

    def test_blocked_edge_types_override_min_confidence(self) -> None:
        policy = TraversalPolicy(
            min_edge_confidence="heuristic", blocked_edge_types=frozenset({"probable"})
        )
        assert not policy.edge_allowed(_edge("a", "x", "b", "y", confidence="probable"))
        assert policy.edge_allowed(_edge("a", "x", "b", "y", confidence="heuristic"))

    def test_allowed_edge_types_is_an_exclusive_whitelist(self) -> None:
        policy = TraversalPolicy(allowed_edge_types=frozenset({"declared"}))
        assert policy.edge_allowed(_edge("a", "x", "b", "y", confidence="declared"))
        assert not policy.edge_allowed(_edge("a", "x", "b", "y", confidence="verified"))

    def test_source_restriction_blocks_edges_outside_allowed_origins(self) -> None:
        policy = TraversalPolicy(allowed_sources=frozenset({"o1"}))
        assert policy.edge_allowed(_edge("a", "x", "b", "y", origin="o1"))
        assert not policy.edge_allowed(_edge("a", "x", "b", "y", origin="o2"))


class TestComputeContextScore:
    def test_anchor_itself_scores_1_0(self) -> None:
        assert compute_context_score([]) == 1.0

    def test_a_declared_single_hop_scores_higher_than_a_heuristic_one(self) -> None:
        declared = compute_context_score([_hop("a", "x", "b", "y", confidence="declared")])
        heuristic = compute_context_score([_hop("a", "x", "b", "y", confidence="heuristic")])
        assert declared > heuristic

    def test_more_hops_scores_lower_even_at_the_same_confidence(self) -> None:
        one_hop = compute_context_score([_hop("a", "x", "b", "y", confidence="declared")])
        two_hop = compute_context_score(
            [
                _hop("a", "x", "b", "y", confidence="declared"),
                _hop("b", "y", "c", "z", confidence="declared"),
            ]
        )
        assert two_hop < one_hop


class TestTraverseSubgraph:
    def _linear_graph(self) -> JoinGraph:
        graph = JoinGraph()
        graph.add_edge(_edge("Company", "id", "Location", "company_id"))
        graph.add_edge(_edge("Location", "id", "Contact", "location_id"))
        return graph

    def test_bfs_reaches_every_node_within_depth(self) -> None:
        graph = self._linear_graph()
        result = traverse_subgraph(graph, [_node("Company")], TraversalPolicy(max_depth=5))
        assert _node("Company") in result.nodes
        assert _node("Location") in result.nodes
        assert _node("Contact") in result.nodes

    def test_max_depth_truncates_the_traversal(self) -> None:
        graph = self._linear_graph()
        result = traverse_subgraph(graph, [_node("Company")], TraversalPolicy(max_depth=1))
        assert _node("Location") in result.nodes
        assert _node("Contact") not in result.nodes
        assert result.truncated is True

    def test_max_nodes_caps_the_traversal(self) -> None:
        graph = self._linear_graph()
        result = traverse_subgraph(
            graph, [_node("Company")], TraversalPolicy(max_depth=5, max_nodes=2)
        )
        assert len(result.nodes) <= 2
        assert result.truncated is True

    def test_cycle_does_not_infinite_loop_or_revisit(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("A", "id", "B", "a_id"))
        graph.add_edge(_edge("B", "id", "C", "b_id"))
        graph.add_edge(_edge("C", "id", "A", "c_id"))  # closes the cycle
        result = traverse_subgraph(graph, [_node("A")], TraversalPolicy(max_depth=10))
        assert result.nodes == {_node("A"), _node("B"), _node("C")}
        assert result.truncated is False

    def test_an_unreachable_anchor_returns_an_empty_result(self) -> None:
        graph = self._linear_graph()
        result = traverse_subgraph(graph, [_node("DoesNotExist")], TraversalPolicy())
        assert result.nodes == set()

    def test_provenance_path_grows_with_distance_from_the_anchor(self) -> None:
        graph = self._linear_graph()
        result = traverse_subgraph(graph, [_node("Company")], TraversalPolicy(max_depth=5))
        assert len(result.provenance[_node("Company")].path) == 0
        assert len(result.provenance[_node("Location")].path) == 1
        assert len(result.provenance[_node("Contact")].path) == 2

    def test_weak_edges_below_min_confidence_are_not_traversed(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Company", "id", "Location", "company_id", confidence="heuristic"))
        result = traverse_subgraph(
            graph, [_node("Company")], TraversalPolicy(min_edge_confidence="verified")
        )
        assert _node("Location") not in result.nodes


class TestBuildCoverageMatrix:
    def test_table_and_field_level_coverage_for_reached_tables(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Company", "id", "Location", "company_id"))
        traversal = traverse_subgraph(graph, [_node("Company")], TraversalPolicy())
        schemas = {
            "o1": {
                "tables": [
                    {"name": "Company", "columns": [{"name": "id"}, {"name": "name"}]},
                    {"name": "Location", "columns": [{"name": "id"}, {"name": "city"}]},
                    {"name": "Unrelated", "columns": [{"name": "id"}]},
                ]
            }
        }
        matrix = build_coverage_matrix(traversal, schemas)
        assert matrix["table_coverage"]["o1:Company"] is True
        assert matrix["table_coverage"]["o1:Location"] is True
        assert matrix["table_coverage"]["o1:Unrelated"] is False
        assert matrix["field_coverage"]["o1:Company"] == {"id": True, "name": True}
        assert "o1:Unrelated" not in matrix["field_coverage"]
        assert matrix["tables_reached"] == 2
        assert matrix["tables_total"] == 3
        assert matrix["coverage_ratio"] == pytest.approx(2 / 3, abs=1e-3)


class TestPlanExtraction:
    @pytest.mark.asyncio
    async def test_estimates_cost_per_reached_table(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Company", "id", "Location", "company_id"))
        traversal = traverse_subgraph(graph, [_node("Company")], TraversalPolicy())
        connector = MagicMock()
        connector.estimate_query_cost = AsyncMock(return_value={"estimated_rows": 100})
        plan = await plan_extraction(traversal, {"o1": connector})
        assert len(plan.tables) == 2
        assert plan.total_estimated_rows() == 200

    @pytest.mark.asyncio
    async def test_a_cost_estimate_failure_is_not_fatal(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Company", "id", "Location", "company_id"))
        traversal = traverse_subgraph(graph, [_node("Company")], TraversalPolicy())
        connector = MagicMock()
        connector.estimate_query_cost = AsyncMock(side_effect=RuntimeError("boom"))
        plan = await plan_extraction(traversal, {"o1": connector})
        assert all(t.estimated_cost is None for t in plan.tables)
        assert plan.total_estimated_rows() is None

    @pytest.mark.asyncio
    async def test_no_connector_for_an_origin_still_produces_a_plan_entry(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Company", "id", "Location", "company_id"))
        traversal = traverse_subgraph(graph, [_node("Company")], TraversalPolicy())
        plan = await plan_extraction(traversal, {})
        assert len(plan.tables) == 2
        assert all(t.estimated_cost is None for t in plan.tables)


class TestExecuteExtraction:
    @pytest.mark.asyncio
    async def test_propagates_only_the_correct_join_columns_values(self) -> None:
        """The core value-propagation bug this module was written to avoid:
        a seed row's OTHER columns (e.g. a phone-shaped value) must never
        leak into the filter for the next hop -- only the actual join
        column's own values."""
        graph = JoinGraph()
        graph.add_edge(_edge("Location", "company_id", "Company", "id"))
        traversal = traverse_subgraph(graph, [_node("Company")], TraversalPolicy())

        location_calls = []

        async def fake_extract(table, *args, **kwargs):
            if table == "Company":
                return {
                    "rows": [{"id": "c1", "phone": "555-0100"}, {"id": "c2", "phone": "555-0200"}]
                }
            location_calls.append(kwargs)
            return {"rows": [{"id": "l1", "company_id": "c1"}]}

        connectors = {"o1": MagicMock()}
        connectors["o1"].extract = AsyncMock(side_effect=fake_extract)

        results = await execute_extraction(
            traversal, [_node("Company")], {"id": {"op": "ilike", "value": "%test%"}}, connectors
        )

        assert _node("Company") in results
        assert _node("Location") in results
        assert len(location_calls) == 1
        filter_arg = location_calls[0]["filter"]
        assert filter_arg["company_id"]["op"] == "in"
        assert set(filter_arg["company_id"]["value"]) == {"c1", "c2"}
        # Critically, "555-0100"/"555-0200" (the phone values) must NEVER
        # appear in the propagated filter.
        assert "555-0100" not in filter_arg["company_id"]["value"]

    @pytest.mark.asyncio
    async def test_propagated_filter_values_keep_their_original_case(self) -> None:
        """Live-caught (BP-B, real ClickHouse origin1, 2026-08-19): seed
        values were being run through join_graph.py's normalize_val()
        (lowercases, strips URL prefixes -- meant for cross-column equality
        COMPARISON, not for building a real query filter) before being used
        in the propagated `IN` filter. A real, case-sensitive companyID like
        "E-xSjHM5bMJmwwRfrYLwwkw" filtered on its lowercased form matched
        zero rows in the real database -- every downstream hop silently
        came back empty. The propagated filter must carry the seed values
        verbatim."""
        graph = JoinGraph()
        graph.add_edge(_edge("Location", "company_id", "Company", "id"))
        traversal = traverse_subgraph(graph, [_node("Company")], TraversalPolicy())

        location_calls = []

        async def fake_extract(table, *args, **kwargs):
            if table == "Company":
                return {"rows": [{"id": "E-xSjHM5bMJmwwRfrYLwwkw"}]}
            location_calls.append(kwargs)
            return {"rows": []}

        connectors = {"o1": MagicMock()}
        connectors["o1"].extract = AsyncMock(side_effect=fake_extract)

        await execute_extraction(traversal, [_node("Company")], {}, connectors)

        assert len(location_calls) == 1
        filter_arg = location_calls[0]["filter"]
        assert filter_arg["company_id"]["value"] == ["E-xSjHM5bMJmwwRfrYLwwkw"]

    @pytest.mark.asyncio
    async def test_a_node_with_no_seed_values_is_skipped_not_a_crash(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Location", "company_id", "Company", "id"))
        traversal = traverse_subgraph(graph, [_node("Company")], TraversalPolicy())
        connector = MagicMock()
        connector.extract = AsyncMock(return_value={"rows": []})
        results = await execute_extraction(traversal, [_node("Company")], {}, {"o1": connector})
        assert _node("Location") not in results
