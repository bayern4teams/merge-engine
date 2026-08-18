"""Unit tests for build_reconciliation_report's data-quality grade (Phase A3
out-of-plan fix): the grading rule table used to be blind to zero-row target
entities and to validate_data's own "fail" severity, so Grade A could print
in the same run where the final-approval gate's own zero-row-families banner
and a hard data-quality failure were both showing. Live-observed: 3 zero-row
entity families ("classification", "investment_round", "investor"), Grade A.
"""

from __future__ import annotations

from typing import Any

from src.orchestrator.nodes.load_data import build_reconciliation_report


def _base_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run_id": "run-1",
        "extracted_data": {},
        "entity_clusters": {},
        "unified_entities": {},
        "target_entities": [],
    }
    state.update(overrides)
    return state


class TestGradeReflectsZeroRowFamilies:
    def test_all_families_loaded_is_grade_a(self) -> None:
        state = _base_state(
            extracted_data={"company": [{"id": 1}]},
            entity_clusters={"company": [{"cluster_id": "c1", "record_ids": ["x"]}]},
            unified_entities={"company": [{"id": "c1"}]},
            target_entities=["company"],
        )
        load_results = {"company": {"inserted": 1, "updated": 0}}
        report = build_reconciliation_report(state, load_results, [])
        assert report["data_quality"]["grade"] == "A"
        assert report["data_quality"]["red_flags"] == []

    def test_a_zero_row_family_is_not_grade_a(self) -> None:
        """Regression: this used to be Grade A with no red flag at all."""
        state = _base_state(
            extracted_data={"company": [{"id": 1}], "classification": []},
            entity_clusters={"company": [{"cluster_id": "c1", "record_ids": ["x"]}]},
            unified_entities={"company": [{"id": "c1"}]},
            target_entities=["company", "classification"],
        )
        load_results = {"company": {"inserted": 1, "updated": 0}}
        report = build_reconciliation_report(state, load_results, [])
        assert report["data_quality"]["grade"] != "A"
        assert any(
            "classification" in f and "zero rows" in f for f in report["data_quality"]["red_flags"]
        )

    def test_quality_failure_forces_grade_f(self) -> None:
        """validate_data's severity="fail" (Phase 4.5) must be consulted by
        the grade, not just shown separately at the gate."""
        state = _base_state(
            extracted_data={"company": [{"id": 1}]},
            entity_clusters={"company": [{"cluster_id": "c1", "record_ids": ["x"]}]},
            unified_entities={"company": [{"id": "c1"}]},
            target_entities=["company"],
            has_quality_failure=True,
            quality_report={
                "status": "completed",
                "severity": "fail",
                "entities": {"company": {"severity": "fail", "row_count": 0, "checks": []}},
            },
        )
        load_results = {"company": {"inserted": 1, "updated": 0}}
        report = build_reconciliation_report(state, load_results, [])
        assert report["data_quality"]["grade"] == "F"
        assert any(
            "data-quality check failed" in f and "company" in f
            for f in report["data_quality"]["red_flags"]
        )

    def test_rollback_still_forces_grade_f(self) -> None:
        """Existing behaviour must survive the new checks being added."""
        state = _base_state(target_entities=["company"])
        report = build_reconciliation_report(state, {}, [], rolled_back=True)
        assert report["data_quality"]["grade"] == "F"
