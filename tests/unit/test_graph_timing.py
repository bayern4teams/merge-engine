"""Unit tests for the per-node timing instrumentation (Phase 0 Sec 0.6)."""

from __future__ import annotations

import asyncio
from typing import Any

from src.orchestrator.graph import _timed
from src.orchestrator.nodes.load_data import build_reconciliation_report


class TestTimedWrapper:
    """_timed() merges elapsed wall time into state['timings'] without
    disturbing whatever the wrapped node itself returned."""

    def test_wraps_result_with_timings_key(self) -> None:
        async def fake_node(state: dict[str, Any]) -> dict[str, Any]:
            return {"errors": ["x"]}

        wrapped = _timed("fake_node", fake_node)
        result = asyncio.run(wrapped({}))

        assert result["errors"] == ["x"]
        assert "timings" in result
        assert set(result["timings"]) == {"fake_node"}
        assert result["timings"]["fake_node"] >= 0.0

    def test_node_returning_falsy_dict_still_gets_a_timing_entry(self) -> None:
        async def fake_node(state: dict[str, Any]) -> dict[str, Any]:
            return {}

        wrapped = _timed("fake_node", fake_node)
        result = asyncio.run(wrapped({}))

        assert result == {"timings": {"fake_node": result["timings"]["fake_node"]}}

    def test_two_different_nodes_produce_independent_keys(self) -> None:
        async def node_a(state: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def node_b(state: dict[str, Any]) -> dict[str, Any]:
            return {}

        a = asyncio.run(_timed("a", node_a)({}))
        b = asyncio.run(_timed("b", node_b)({}))

        # These are what get merged via MergeState.timings' operator.or_
        # reducer as the graph runs multiple nodes across a step.
        merged = a["timings"] | b["timings"]
        assert set(merged) == {"a", "b"}


class TestReconciliationReportTimings:
    """build_reconciliation_report must surface state['timings'] verbatim so
    drive_app.py --assert-budgets can read it from the run report."""

    def test_timings_passed_through_from_state(self) -> None:
        state = {
            "run_id": "run-1",
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "timings": {"resolve_entities": 12.5, "load_data": 3.0},
        }
        report = build_reconciliation_report(state, {}, [])
        assert report["timings"] == {"resolve_entities": 12.5, "load_data": 3.0}

    def test_missing_timings_defaults_to_empty_dict(self) -> None:
        state = {
            "run_id": "run-1",
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
        }
        report = build_reconciliation_report(state, {}, [])
        assert report["timings"] == {}
