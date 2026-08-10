"""Unit tests for src/mcp_server/tools/data_quality.py (salvaged from
B3-quality-lineage, Phase 4.5 of the fix-bugs follow-up plan).

Uses the corrected `sampled_rows` + `row_counts` contract, not B3's
`extracted_data` shape — see data_quality.py's module docstring for why:
`extracted_data` in the current codebase holds spool handles, not row
lists, and B3's version never accounted for that.
"""

from __future__ import annotations

from src.mcp_server.tools.data_quality import run_data_quality_checks


def test_clean_data_passes():
    sampled = {
        "Company": [
            {"id": "c1", "name": "Boeing", "founding_year": 1916},
            {"id": "c2", "name": "Airbus", "founding_year": 1970},
        ]
    }
    matching_rules = {"Company": {"unique_id_fields": ["id"]}}

    res = run_data_quality_checks(sampled, row_counts={"Company": 2}, matching_rules=matching_rules)
    report = res["quality_report"]

    assert res["has_quality_failure"] is False
    assert report["severity"] == "pass"
    assert report["entities"]["Company"]["severity"] == "pass"
    assert report["entities"]["Company"]["row_count"] == 2
    assert report["entities"]["Company"]["sample_size"] == 2
    assert report["entities"]["Company"]["sampled"] is False


def test_zero_rows_fail():
    res = run_data_quality_checks({"Company": []}, row_counts={"Company": 0})

    assert res["has_quality_failure"] is True
    report = res["quality_report"]
    assert report["severity"] == "fail"
    assert report["entities"]["Company"]["severity"] == "fail"
    checks = report["entities"]["Company"]["checks"]
    assert any(c["check"] == "row_count_ge_1" and c["success"] is False for c in checks)


def test_row_count_uses_the_exact_count_not_the_sample_size():
    """The check that matters most for issue 6: an entity's real row count
    (from the spool, via row_counts) must be used even when only a fraction
    of its rows were sampled for the other checks."""
    sampled = {"Person": [{"id": "p1", "name": "A"}]}  # a 1-row sample
    res = run_data_quality_checks(sampled, row_counts={"Person": 43861})

    entity = res["quality_report"]["entities"]["Person"]
    assert entity["row_count"] == 43861
    assert entity["sample_size"] == 1
    assert entity["sampled"] is True
    checks = entity["checks"]
    row_count_check = next(c for c in checks if c["check"] == "row_count_ge_1")
    assert row_count_check["success"] is True
    assert "43861" in row_count_check["details"]


def test_duplicate_unique_id_fails():
    sampled = {
        "Company": [
            {"id": "dup_1", "name": "Boeing"},
            {"id": "dup_1", "name": "Boeing Corp"},
        ]
    }
    matching_rules = {"Company": {"unique_id_fields": ["id"]}}

    res = run_data_quality_checks(sampled, row_counts={"Company": 2}, matching_rules=matching_rules)
    assert res["has_quality_failure"] is True
    report = res["quality_report"]
    assert report["severity"] == "fail"
    checks = report["entities"]["Company"]["checks"]
    assert any("uniqueness:id" in c["check"] and c["success"] is False for c in checks)


def test_null_name_warns():
    sampled = {
        "Company": [
            {"id": "c1", "name": None},
            {"id": "c2", "name": "Airbus"},
        ]
    }
    matching_rules = {"Company": {"unique_id_fields": ["id"]}}

    res = run_data_quality_checks(sampled, row_counts={"Company": 2}, matching_rules=matching_rules)
    assert res["has_quality_failure"] is False
    report = res["quality_report"]
    assert report["severity"] == "warn"
    checks = report["entities"]["Company"]["checks"]
    assert any("not_null:name" in c["check"] and c["success"] is False for c in checks)


def test_bad_year_warns():
    sampled = {
        "Company": [
            {"id": "c1", "name": "Boeing", "founding_year": 1200},  # out of sane bounds
        ]
    }
    res = run_data_quality_checks(sampled, row_counts={"Company": 1})
    assert res["has_quality_failure"] is False
    assert res["quality_report"]["severity"] == "warn"


def test_columns_are_unioned_across_the_whole_sample_not_just_row_zero():
    """The rows[0] bug this replaces: if the first sampled row happens not
    to carry a column later rows do (a real scenario when different
    origins feed the same entity bucket with different columns), the old
    code would never even look for it."""
    sampled = {
        "Company": [
            {"id": "c1"},  # no "name" at all -- came from a sparser origin
            {"id": "c2", "name": None},  # a later row DOES have the column
        ]
    }
    res = run_data_quality_checks(sampled, row_counts={"Company": 2})
    checks = res["quality_report"]["entities"]["Company"]["checks"]
    assert any("not_null:name" in c["check"] for c in checks)


def test_missing_row_counts_falls_back_to_sample_size():
    sampled = {"Company": [{"id": "c1", "name": "Boeing"}]}
    res = run_data_quality_checks(sampled)  # no row_counts supplied at all
    assert res["quality_report"]["entities"]["Company"]["row_count"] == 1
    assert res["quality_report"]["entities"]["Company"]["sampled"] is False


def test_unique_id_fields_falls_back_to_bare_id_column():
    sampled = {
        "Company": [
            {"id": "dup", "name": "A"},
            {"id": "dup", "name": "B"},
        ]
    }
    # No matching_rules at all -- "id" is used as the fallback unique field.
    res = run_data_quality_checks(sampled, row_counts={"Company": 2})
    assert res["has_quality_failure"] is True


def test_degrades_on_internal_exception_without_crashing(monkeypatch):
    """An internal error (never Great Expectations -- that dependency was
    dropped entirely as part of this salvage) degrades to 'validation
    unavailable' rather than crashing the pipeline."""

    def _bad_check(*args, **kwargs):
        raise RuntimeError("Forced internal error")

    monkeypatch.setattr("src.mcp_server.tools.data_quality._detect_name_column", _bad_check)
    sampled = {"Company": [{"id": "c1", "name": "Boeing"}]}

    res = run_data_quality_checks(sampled, row_counts={"Company": 1})
    assert res["has_quality_failure"] is False
    assert res["quality_report"]["status"] == "validation unavailable"
    assert res["quality_report"]["severity"] == "warn"


def test_no_entities_is_a_clean_pass():
    res = run_data_quality_checks({})
    assert res["has_quality_failure"] is False
    assert res["quality_report"]["severity"] == "pass"
    assert res["quality_report"]["entities"] == {}
