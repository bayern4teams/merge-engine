"""Unit tests for measured per-origin field reliability (Phase A6).

unify_entity_group.py already had a real, per-field-class conflict-rule
chain (priority -> completeness -> recency, with smart defaults). The
genuine gap this closes: "priority" is a static, user-typed number that
defaults to 0 for everyone when unconfigured -- a no-op tiebreak. This
module measures what it was standing in for: each origin's OBSERVED
completeness for a specific field, across everything it contributed.
"""

from __future__ import annotations

from src.mcp_server.tools.survivorship import measure_field_reliability


class TestMeasureFieldReliability:
    def test_full_completeness_scores_one(self) -> None:
        records = [
            {"_origin_id": "a", "name": "Acme"},
            {"_origin_id": "a", "name": "Beta"},
        ]
        reliability = measure_field_reliability(records)
        assert reliability["a"]["name"] == 1.0

    def test_partial_completeness_is_the_non_null_ratio(self) -> None:
        records = [
            {"_origin_id": "a", "name": "Acme"},
            {"_origin_id": "a", "name": None},
            {"_origin_id": "a", "name": "Beta"},
            {"_origin_id": "a", "name": None},
        ]
        reliability = measure_field_reliability(records)
        assert reliability["a"]["name"] == 0.5

    def test_blank_strings_count_as_missing_not_present(self) -> None:
        records = [
            {"_origin_id": "a", "name": "Acme"},
            {"_origin_id": "a", "name": "   "},
        ]
        reliability = measure_field_reliability(records)
        assert reliability["a"]["name"] == 0.5

    def test_each_origin_is_measured_independently(self) -> None:
        records = [
            {"_origin_id": "sparse", "name": "Acme"},
            {"_origin_id": "sparse", "name": None},
            {"_origin_id": "rich", "name": "Acme Corp"},
            {"_origin_id": "rich", "name": "Beta Inc"},
        ]
        reliability = measure_field_reliability(records)
        assert reliability["sparse"]["name"] == 0.5
        assert reliability["rich"]["name"] == 1.0

    def test_each_field_is_measured_independently(self) -> None:
        """An origin can be reliable for one field and unreliable for
        another -- the score must not be a single per-origin number."""
        records = [
            {"_origin_id": "a", "name": "Acme", "email": None},
            {"_origin_id": "a", "name": "Beta", "email": None},
        ]
        reliability = measure_field_reliability(records)
        assert reliability["a"]["name"] == 1.0
        assert "email" not in reliability["a"]

    def test_internal_underscore_fields_are_excluded(self) -> None:
        records = [{"_origin_id": "a", "_cluster_id": "c1", "name": "Acme"}]
        reliability = measure_field_reliability(records)
        assert "_cluster_id" not in reliability["a"]
        assert "_origin_id" not in reliability["a"]

    def test_missing_origin_id_falls_back_to_a_placeholder_not_a_crash(self) -> None:
        records = [{"name": "Acme"}]
        reliability = measure_field_reliability(records)
        assert "?" in reliability

    def test_empty_records_yields_empty_reliability(self) -> None:
        assert measure_field_reliability([]) == {}

    def test_a_field_absent_from_every_record_is_not_a_zero_score(self) -> None:
        """A field an origin never even carries should be ABSENT from its
        reliability dict, not present with a score of 0.0 -- the caller
        (_apply_rule's "measured_reliability") treats 0.0 as "no measured
        data" via .get(field, 0.0), and the two must be indistinguishable
        for that to be correct."""
        records = [{"_origin_id": "a", "name": "Acme"}]
        reliability = measure_field_reliability(records)
        assert "phone" not in reliability["a"]
