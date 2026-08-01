"""Unit tests for conflict tiebreaks and the reconciliation report (Phase 6)."""

from __future__ import annotations

import json

import pytest

from src.mcp_server.tools.unify_entity_group import unify_entity_group_impl
from src.orchestrator.nodes.load_data import (
    _scoped_merge_guardrail_failure,
    _write_run_report,
    build_reconciliation_report,
)


def _conflict_for(result, field):
    return next(c for c in result["conflicts"] if c["field"] == field)


class TestConflictTiebreaks:
    @pytest.mark.asyncio
    async def test_priority_wins(self):
        result = await unify_entity_group_impl(
            "c1",
            [
                {"_origin_id": "low", "name": "Acme"},
                {"_origin_id": "high", "name": "Acme Corp"},
            ],
            origins=[{"id": "low", "priority": 1}, {"id": "high", "priority": 5}],
        )
        conflict = _conflict_for(result, "name")
        assert conflict["selected_origin"] == "high"
        assert conflict["winning_rule"] == "priority"
        assert result["unified_record"]["name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_completeness_breaks_priority_tie(self):
        # "description" is in the smart per-field-class default table's
        # completeness-preferring group; "name" is deliberately NOT (see
        # test_name_conflicts_do_not_favor_the_longer_string below) — this
        # test now uses a field that class actually covers.
        result = await unify_entity_group_impl(
            "c2",
            [
                {"_origin_id": "a", "description": "Acme"},
                {"_origin_id": "b", "description": "Acme International Corp"},
            ],
            origins=[{"id": "a", "priority": 1}, {"id": "b", "priority": 1}],
        )
        conflict = _conflict_for(result, "description")
        assert conflict["winning_rule"] == "completeness"
        assert result["unified_record"]["description"] == "Acme International Corp"

    @pytest.mark.asyncio
    async def test_name_conflicts_do_not_favor_the_longer_string(self):
        """Phase 5 fix: an identity field like `name` used to fall to
        DEFAULT_POLICY, whose second rule is `completeness` — pure string
        length — so a longer-but-not-necessarily-more-correct value (e.g. a
        stale "Acme International Corp, LLC (formerly Acme)") could beat the
        actually-current name just for being longer. `name` now resolves
        `priority -> non_null -> recency`, with no completeness step, so a
        same-priority tie falls straight through to first_seen instead of to
        whichever value is longer."""
        result = await unify_entity_group_impl(
            "c2b",
            [
                {"_origin_id": "a", "name": "Acme"},
                {"_origin_id": "b", "name": "Acme International Corp"},
            ],
            origins=[{"id": "a", "priority": 1}, {"id": "b", "priority": 1}],
        )
        conflict = _conflict_for(result, "name")
        assert conflict["winning_rule"] != "completeness"

    @pytest.mark.asyncio
    async def test_recency_breaks_completeness_tie(self):
        result = await unify_entity_group_impl(
            "c3",
            [
                {"_origin_id": "a", "city": "Berlin", "updated_at": "2024-01-01"},
                {"_origin_id": "b", "city": "Munich", "updated_at": "2025-06-01"},
            ],
            origins=[{"id": "a", "priority": 1}, {"id": "b", "priority": 1}],
        )
        conflict = _conflict_for(result, "city")
        assert conflict["winning_rule"] == "recency"
        assert result["unified_record"]["city"] == "Munich"

    @pytest.mark.asyncio
    async def test_first_seen_fallback(self):
        result = await unify_entity_group_impl(
            "c4",
            [
                {"_origin_id": "a", "city": "Berlin"},
                {"_origin_id": "b", "city": "Munich"},
            ],
            origins=[{"id": "a", "priority": 1}, {"id": "b", "priority": 1}],
        )
        conflict = _conflict_for(result, "city")
        assert conflict["winning_rule"] == "first_seen"

    @pytest.mark.asyncio
    async def test_numeric_max_picks_the_larger_number_not_the_longer_string(self):
        """Phase 5 fix: `revenue` used to fall to DEFAULT_POLICY, whose
        second rule is `completeness` (raw string length) — "$6.1 Million"
        (12 chars) would beat "$6.7 Billion" (12 chars, a tie) or even a
        genuinely smaller-but-longer-formatted string. `revenue` now uses
        `numeric_max`, comparing actual parsed magnitude."""
        result = await unify_entity_group_impl(
            "c6",
            [
                {"_origin_id": "a", "revenue": "$6.1 Million"},
                {"_origin_id": "b", "revenue": "$6.7 Billion"},
            ],
            origins=[{"id": "a", "priority": 1}, {"id": "b", "priority": 1}],
        )
        conflict = _conflict_for(result, "revenue")
        assert conflict["winning_rule"] == "numeric_max"
        assert result["unified_record"]["revenue"] == "$6.7 Billion"

    @pytest.mark.asyncio
    async def test_numeric_max_falls_through_when_nothing_is_numeric(self):
        """A hex id or other non-numeric value must not crash numeric_max —
        it has nothing to discriminate on, so the chain moves to the next
        rule instead of raising or arbitrarily picking one."""
        result = await unify_entity_group_impl(
            "c7",
            [
                {"_origin_id": "a", "employee_count": "unknown"},
                {"_origin_id": "b", "employee_count": "n/a"},
            ],
            origins=[{"id": "a", "priority": 1}, {"id": "b", "priority": 2}],
        )
        conflict = _conflict_for(result, "employee_count")
        assert conflict["winning_rule"] == "priority"
        assert result["unified_record"]["employee_count"] == "n/a"

    @pytest.mark.asyncio
    async def test_explicit_first_seen_rule_picks_the_earliest_candidate(self):
        result = await unify_entity_group_impl(
            "c8",
            [
                {"_origin_id": "a", "note": "first"},
                {"_origin_id": "b", "note": "second"},
            ],
            origins=[{"id": "a", "priority": 1}, {"id": "b", "priority": 1}],
            conflict_policy={"note": ["first_seen"]},
        )
        conflict = _conflict_for(result, "note")
        assert conflict["winning_rule"] == "first_seen"
        assert result["unified_record"]["note"] == "first"

    @pytest.mark.asyncio
    async def test_no_shadowing_regression_multiple_conflicts(self):
        """Multiple conflicting fields must not crash on the (formerly
        shadowed) origins variable."""
        result = await unify_entity_group_impl(
            "c5",
            [
                {"_origin_id": "a", "name": "X", "city": "Berlin"},
                {"_origin_id": "b", "name": "Y", "city": "Munich"},
            ],
            origins=[{"id": "a", "priority": 2}, {"id": "b", "priority": 1}],
        )
        assert len(result["conflicts"]) == 2
        assert all(c["selected_origin"] == "a" for c in result["conflicts"])


class TestReconciliationReport:
    def _state(self):
        return {
            "run_id": "run-42",
            "extracted_data": {
                "company": [
                    {"_origin_id": "pg", "name": "Acme"},
                    {"_origin_id": "pg", "name": "Globex"},
                    {"_origin_id": "ch", "name": "Acme Intl"},
                ],
            },
            "entity_clusters": {
                "company": [
                    {"cluster_id": "k1", "record_ids": ["a", "b"], "required_human_review": False},
                    {"cluster_id": "k2", "record_ids": ["c"], "required_human_review": False},
                    {"cluster_id": "k3", "record_ids": ["d", "e"], "required_human_review": True},
                ],
            },
            "unified_entities": {
                "company": [
                    {"name": "Acme", "_conflicts": [
                        {"field": "city", "winning_rule": "priority"},
                        {"field": "city", "winning_rule": "completeness"},
                    ]},
                    {"name": "Globex"},
                ],
            },
            "pending_schema_changes": [
                {"table": "companies", "column": "esg", "data_type": "TEXT"},
            ],
        }

    def test_report_numbers(self):
        report = build_reconciliation_report(
            self._state(), {"company": {"inserted": 2, "updated": 1}}, ["oops"],
        )
        ent = report["entities"]["company"]
        assert ent["extracted_per_origin"] == {"pg": 2, "ch": 1}
        assert ent["extracted_total"] == 3
        assert ent["clusters_formed"] == 3
        assert ent["auto_merged_clusters"] == 1
        assert ent["human_reviewed_clusters"] == 1
        assert ent["unified_records"] == 2
        assert ent["conflicts"] == 2
        assert ent["conflict_fields"] == {"city": 2}
        assert ent["conflict_winning_rules"] == {"priority": 1, "completeness": 1}
        assert ent["rows_inserted"] == 2
        assert ent["rows_updated"] == 1
        assert report["run_id"] == "run-42"
        assert report["new_columns"] == [
            {"table": "companies", "column": "esg", "data_type": "TEXT"},
        ]
        assert report["errors"] == ["oops"]

    def test_empty_state(self):
        report = build_reconciliation_report({"run_id": "r"}, {}, [])
        assert report["entities"] == {}
        assert report["errors"] == []

    def test_a_clean_run_grades_a_with_no_red_flags(self):
        state = self._state()
        state["target_entities"] = ["company"]
        report = build_reconciliation_report(
            state, {"company": {"inserted": 2, "updated": 0}}, [],
        )
        assert report["data_quality"] == {"grade": "A", "red_flags": []}
        assert report["families_requested"] == 1
        assert report["families_loaded"] == 1
        assert report["completeness_pct"] == 100.0

    def test_rows_dropped_reflects_unified_minus_landed(self):
        report = build_reconciliation_report(
            self._state(), {"company": {"inserted": 1, "updated": 0}}, [],
        )
        # 2 unified records, only 1 landed.
        assert report["entities"]["company"]["rows_dropped"] == 1

    def test_a_rolled_back_load_grades_f_and_flags_first(self):
        report = build_reconciliation_report(
            self._state(), {}, ["boom"], rolled_back=True,
        )
        assert report["data_quality"]["grade"] == "F"
        assert "rolled back" in report["data_quality"]["red_flags"][0]
        # Nothing landed, so every unified record for the entity is "dropped".
        assert report["entities"]["company"]["rows_dropped"] == 2

    def test_type_coercion_failures_grade_c(self):
        state = self._state()
        report = build_reconciliation_report(
            state,
            {"company": {"inserted": 2, "updated": 0, "type_coercion_failures": 3}},
            [],
        )
        assert report["entities"]["company"]["type_coercion_failures"] == 3
        assert report["data_quality"]["grade"] == "C"
        assert any("coerced" in f for f in report["data_quality"]["red_flags"])

    def test_fields_genuinely_dropped_from_mapping_are_reported(self):
        state = self._state()
        state["unmapped_fields"] = {
            "company": [
                {"field": "notes", "suggested_column": None, "reason": "LLM proposed skipping"},
                {"field": "phone_raw", "suggested_column": "phone_raw", "reason": "incompatible type"},
            ],
        }
        # "phone_raw" already has a field_mappings entry (Phase 4.2's
        # redirect-to-a-new-column path) so it is NOT dropped; "notes" has
        # no mapping entry at all, so it genuinely is.
        state["field_mappings"] = {
            "company": {"phone_raw": {"dest_column": "phone_raw", "requires_schema_change": True}},
        }
        report = build_reconciliation_report(
            state, {"company": {"inserted": 2, "updated": 0}}, [],
        )
        assert report["entities"]["company"]["fields_dropped_from_mapping"] == ["notes"]
        assert report["data_quality"]["grade"] == "C"

    def test_dropped_inferred_entities_surface_top_level_and_grade_b(self):
        state = self._state()
        state["dropped_inferred_entities"] = ["product"]
        report = build_reconciliation_report(
            state, {"company": {"inserted": 2, "updated": 0}}, [],
        )
        assert report["dropped_inferred_entities"] == ["product"]
        assert report["data_quality"]["grade"] == "B"

    def test_subject_unification_ratio_only_appears_for_the_scoped_subject(self):
        state = self._state()
        state["merge_scope"] = {"active": True, "subject": "Acme", "scope_type": "company"}
        report = build_reconciliation_report(
            state, {"company": {"inserted": 2, "updated": 0}}, [],
        )
        # 2 unified / 3 extracted
        assert report["entities"]["company"]["subject_unification_ratio"] == round(2 / 3, 4)

    def test_unscoped_run_has_no_subject_unification_ratio(self):
        report = build_reconciliation_report(
            self._state(), {"company": {"inserted": 2, "updated": 0}}, [],
        )
        assert "subject_unification_ratio" not in report["entities"]["company"]

    def test_families_requested_uses_target_entities_when_present(self):
        state = self._state()
        state["target_entities"] = ["company", "person", "finance"]
        report = build_reconciliation_report(
            state, {"company": {"inserted": 2, "updated": 0}}, [],
        )
        assert report["families_requested"] == 3
        assert report["families_loaded"] == 1
        assert report["completeness_pct"] == round(100 / 3, 1)

    def test_write_run_report(self, tmp_path, monkeypatch):
        import src.orchestrator.nodes.load_data as mod
        monkeypatch.setattr(mod, "_RUN_REPORTS_DIR", tmp_path)
        report = {"run_id": "r-1", "entities": {}}
        path = _write_run_report(report)
        assert path is not None
        assert json.loads((tmp_path / "r-1.json").read_text(encoding="utf-8")) == report


class TestScopedMergeGuardrail:
    """Phase 6.2: catch the exact Airbus-20->20 / Lockheed-9->9 signature
    (a scoped merge's subject entity forms several clusters and NONE of them
    auto-merge) before the load ever opens a connector."""

    def _clusters(self, n: int, merged: int = 0) -> list[dict]:
        clusters = []
        for i in range(merged):
            clusters.append({
                "cluster_id": f"m{i}", "record_ids": ["a", "b"],
                "required_human_review": False,
            })
        for i in range(n - merged):
            clusters.append({
                "cluster_id": f"s{i}", "record_ids": ["a"],
                "required_human_review": False,
            })
        return clusters

    def test_fires_when_the_subject_entity_never_auto_merges(self):
        state = {
            "merge_scope": {"active": True, "subject": "Airbus", "scope_type": "company"},
            "entity_clusters": {"company": self._clusters(20, merged=0)},
        }
        failure = _scoped_merge_guardrail_failure(state)
        assert failure is not None
        assert failure["error_class"] == "DataQualityError"
        assert failure["disposition"] == "escalate"
        assert failure["context"]["clusters_formed"] == 20

    def test_does_not_fire_when_some_clusters_auto_merged(self):
        state = {
            "merge_scope": {"active": True, "subject": "Airbus", "scope_type": "company"},
            "entity_clusters": {"company": self._clusters(20, merged=2)},
        }
        assert _scoped_merge_guardrail_failure(state) is None

    def test_does_not_fire_below_the_cluster_threshold(self):
        state = {
            "merge_scope": {"active": True, "subject": "Small Co", "scope_type": "company"},
            "entity_clusters": {"company": self._clusters(3, merged=0)},
        }
        assert _scoped_merge_guardrail_failure(state) is None

    def test_does_not_fire_for_an_unscoped_run(self):
        state = {
            "merge_scope": {"active": False},
            "entity_clusters": {"company": self._clusters(20, merged=0)},
        }
        assert _scoped_merge_guardrail_failure(state) is None

    def test_does_not_fire_with_no_merge_scope_at_all(self):
        state = {"entity_clusters": {"company": self._clusters(20, merged=0)}}
        assert _scoped_merge_guardrail_failure(state) is None
