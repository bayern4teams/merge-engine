"""B.4 unit tests: recommendation rubrics, workbench helpers, review banding,
error semantics.

Covers the fast, pure-logic surface of the phase modules — no Linker, no EM
training (those are covered by the live end-to-end verification and
``test_splink_matcher.py``):

* ``splink_recommender`` (S-A/B/C/D) — the star rubrics, the comparator
  catalog, and the data-read-off threshold split, all from plain DataFrames
  and synthetic observed-distribution dicts.
* ``splink_workbench`` — ``_normalise_rule`` (bare → l./r. form) and the
  workbench record cap.
* ``splink_inference`` (S-P) — review banding: the three bands, the
  distance-from-0.5 ordering, the truncation flag, and the threshold check.
* ``splink_evaluation`` (S-M) — false-positive classification semantics and
  the SDF/chart unwrap helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_HAS_SPLINK = pytest.importorskip("splink") is not None  # type: ignore[func-ret-values]

import splink.comparison_library as cl  # noqa: E402

from src.mcp_server.tools import splink_inference as infer  # noqa: E402
from src.mcp_server.tools import splink_recommender as rec  # noqa: E402
from src.mcp_server.tools import splink_workbench as wb  # noqa: E402
from src.mcp_server.tools.splink_evaluation import _chart_rows, _is_fp  # noqa: E402


def _df(**columns: list) -> pd.DataFrame:
    return pd.DataFrame(columns)


# ── S-A: field stats & star rubrics ──────────────────────────────────────────


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestFieldRoles:
    def test_email_column_is_a_strong_matcher(self) -> None:
        df = _df(email=["a@x.com", "b@x.com", "c@x.com", "d@x.com"])
        stats = rec.field_stats(df)
        assert stats["email"]["completeness"] == 1.0
        assert stats["email"]["uniqueness"] == 1.0
        assert stats["email"]["cardinality"] == 4
        assert stats["email"]["collision_rate"] == 0.0
        assert stats["email"]["top_values"][0]["count"] == 1

    def test_stars_never_zero_and_cap_at_five(self) -> None:
        assert rec._stars(0.0) == 1
        assert rec._stars(1.0) == 5
        assert rec._stars(0.5) == 2  # round(2.5) is 2 in banker's-rounding int() path
        assert 1 <= rec._stars(0.37) <= 5

    def test_unique_column_earns_five_match_stars(self) -> None:
        df = _df(email=[f"{i}@x.com" for i in range(10)])
        roles = rec.recommend_field_roles(df)
        assert roles["email"]["matching"]["stars"] == 5
        assert roles["email"]["blocking"]["stars"] == 5

    def test_constant_column_is_flagged_as_cartesian_product(self) -> None:
        df = _df(kind=["x"] * 10)
        roles = rec.recommend_field_roles(df)
        assert "cartesian" in roles["kind"]["blocking"]["reason"]
        # matching = 0.5*1.0 + 0.5*0.1 = 0.55 → 3 stars (complete but not unique)
        assert roles["kind"]["matching"]["stars"] == 3
        # blocking floored to 0 → 1 star with the cartesian-product reason
        assert roles["kind"]["blocking"]["stars"] == 1
        # TF is meaningless on a constant
        assert roles["kind"]["tf"]["stars"] == 1

    def test_mostly_null_column_is_capped_for_blocking(self) -> None:
        # 2 distinct values (so the constant-column rule doesn't fire first)
        # but only 10/51 ≈ 19.6% complete → the < 0.2 completeness floor rule.
        df = _df(email=["a@x.com"] * 5 + ["b@x.com"] * 5 + [""] * 41)
        roles = rec.recommend_field_roles(df)
        assert roles["email"]["stats"]["completeness"] == pytest.approx(0.196, abs=1e-3)
        assert roles["email"]["stats"]["cardinality"] == 2
        assert "mostly null" in roles["email"]["blocking"]["reason"]


# ── S-B: the comparator catalog ──────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestComparisonRecommendation:
    def test_email_type_maps_to_email_comparison(self) -> None:
        out = rec.recommend_comparison("email", "contact_email")
        assert out["comparator"] == "EmailComparison"
        assert out["fallback"] is False
        assert out["columns_needed"] == 1

    def test_forename_surname_needs_two_columns(self) -> None:
        out = rec.recommend_comparison("forename_surname", "first_name")
        assert out["comparator"] == "ForenameSurnameComparison"
        assert out["columns_needed"] == 2

    def test_unknown_text_type_is_a_fallback_to_jaro_winkler(self) -> None:
        out = rec.recommend_comparison("text", "bio")
        assert out["comparator"] == "JaroWinklerAtThresholds"
        assert out["fallback"] is True

    def test_unknown_numeric_type_is_a_fallback_to_exact_match(self) -> None:
        out = rec.recommend_comparison("number", "revenue")
        assert out["comparator"] == "ExactMatch"
        assert out["fallback"] is True

    def test_id_is_compared_exactly(self) -> None:
        assert rec.recommend_comparison("id", "tax_id")["comparator"] == "ExactMatch"

    def test_settings_hit_builds_real_comparators(self) -> None:
        assert isinstance(rec.comparison_to_settings_hit("email", "e"), cl.EmailComparison)
        assert isinstance(rec.comparison_to_settings_hit("person_name", "n"), cl.NameComparison)
        fs = rec.comparison_to_settings_hit("forename_surname", "first_name", ("last_name",))
        assert isinstance(fs, cl.ForenameSurnameComparison)
        assert isinstance(rec.comparison_to_settings_hit("id", "x"), cl.ExactMatch)

    def test_settings_hit_refuses_mismatched_columns(self) -> None:
        with pytest.raises(ValueError, match="surname"):
            rec.comparison_to_settings_hit("forename_surname", "first_name")


# ── S-C: thresholds read off the observed distribution ───────────────────────


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestRecommendedThresholds:
    def test_split_is_read_from_coverage(self) -> None:
        # Synthetic observed distribution. Coverage is monotonic DECREASING in
        # the threshold (fewer pairs sit at/above a higher bar), so:
        #   match_bar   = highest t with cov(t) >= 0.40  → 0.85 (cov 0.45)
        #   exclude_bar = lowest  t with cov(t) >= 0.90  → 0.5  (cov 1.00)
        # and therefore match_bar > exclude_bar (sane decision bands). The
        # original code iterated the wrong direction for both and produced
        # match=0.5 < exclude=0.8 — an inverted band (a real bug this guards).
        cov = {
            ">=0.95": 0.20,
            ">=0.9": 0.30,
            ">=0.85": 0.45,
            ">=0.8": 0.95,
            ">=0.7": 0.99,
            ">=0.5": 1.00,
        }
        out = rec.recommended_thresholds(
            observed={"column": "name", "pairs_sampled": 500, "threshold_coverage": cov}
        )
        assert out["match_threshold"] == 0.85
        assert out["exclude_threshold"] == 0.5
        assert out["match_threshold"] > out["exclude_threshold"]
        assert "500" in out["reason"]

    def test_small_sample_yields_an_honest_no_recommendation(self) -> None:
        out = rec.recommended_thresholds(
            observed={"column": "name", "pairs_sampled": 5, "threshold_coverage": {}}
        )
        assert out["match_threshold"] is None
        assert out["exclude_threshold"] is None
        assert "not enough" in out["reason"]

    def test_requiring_both_df_and_observed_raises(self) -> None:
        with pytest.raises(ValueError, match="observed= or both df and column"):
            rec.recommended_thresholds()


# ── S-D: term frequency ──────────────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestTermFrequency:
    def test_skewed_column_recommends_tf(self) -> None:
        df = _df(country=["USA"] * 92 + ["UK", "FR"])
        out = rec.recommend_term_frequency(df, "country")
        assert out["recommended"] is True
        # 92 non-null "USA" out of 94 rows total
        assert out["max_value_frequency"] == pytest.approx(92 / 94, abs=1e-3)
        assert out["frequency_table"][0]["value"] == "USA"
        assert out["frequency_table"][0]["count"] == 92

    def test_flat_column_does_not_recommend_tf(self) -> None:
        df = _df(name=[f"n{i}" for i in range(100)])
        out = rec.recommend_term_frequency(df, "name")
        assert out["recommended"] is False
        assert "flat enough" in out["reason"]

    def test_empty_column_reports_it(self) -> None:
        out = rec.recommend_term_frequency(_df(col=[""] * 5), "col")
        assert out["recommended"] is False
        assert "entirely empty" in out["reason"]


# ── workbench: rule normalisation + record cap ───────────────────────────────


class TestWorkbenchHelpers:
    def test_bare_equality_becomes_l_r_form(self) -> None:
        assert wb._normalise_rule("name = name") == 'l."name" = r."name"'

    def test_and_of_equalities_is_split_and_prefixed(self) -> None:
        out = wb._normalise_rule("first_name = first_name and last_name = last_name")
        assert out == 'l."first_name" = r."first_name" AND l."last_name" = r."last_name"'

    def test_quoted_bare_rule_still_normalises(self) -> None:
        assert wb._normalise_rule('"email" = "email"') == 'l."email" = r."email"'

    def test_rule_already_prefixed_passes_through(self) -> None:
        rule = 'l."email" = r."email"'
        assert wb._normalise_rule(rule) == rule

    def test_unrecognised_shape_passes_through_for_splink_to_reject(self) -> None:
        rule = "SUBSTR(name, 1, 2) = SUBSTR(name, 1, 2)"
        assert wb._normalise_rule(rule) == rule

    @pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
    def test_empty_records_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one record"):
            wb._dataframe([], unique_id_column="_u")

    @pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
    def test_record_cap_enforced(self) -> None:
        recs = [
            {"_origin_id": "o", "_origin_table": "t", "_origin_id_row": str(i), "v": i}
            for i in range(wb.MAX_WORKBENCH_RECORDS + 1)
        ]
        with pytest.raises(ValueError, match="exceeds the workbench cap"):
            wb._dataframe(recs, unique_id_column="_u")


# ── S-P: review banding (pure over a predictions list) ──────────────────────


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestReviewPrioritisation:
    def _preds(self) -> list[dict]:
        # one per band: high (auto-match), low (auto-apart), two middle (review).
        return [
            {"match_probability": 0.97},
            {"match_probability": 0.03},
            {"match_probability": 0.52},  # closest to 0.5 → most uncertain
            {"match_probability": 0.30},
        ]

    def test_bands_are_disjoint_and_complete(self) -> None:
        out = infer.prioritise_review(self._preds())
        # defaults: match_threshold=0.5, exclude_threshold=0.2
        # 0.97 → auto_match; 0.52 → auto_match; 0.30 → review (0.2 < 0.30 < 0.5); 0.03 → auto_apart
        assert out["auto_match_count"] == 2
        assert out["auto_apart_count"] == 1
        assert out["review_count"] == 1
        assert out["review_count"] + out["auto_match_count"] + out["auto_apart_count"] == 4

    def test_review_is_ordered_nearest_to_05_first(self) -> None:
        out = infer.prioritise_review(self._preds())
        assert [p["match_probability"] for p in out["review_queue"]] == [0.30]
        assert out["review_queue_truncated"] is False
        assert "0.5" in out["ordering"]

    def test_limit_truncates_the_queue_but_not_the_count(self) -> None:
        # widen the review band so there are several candidates
        preds = [{"match_probability": p} for p in (0.97, 0.03, 0.52, 0.48, 0.40, 0.35, 0.30, 0.25)]
        out = infer.prioritise_review(preds, limit=2)
        assert out["review_count"] >= 3
        assert len(out["review_queue"]) == 2
        assert out["review_queue_truncated"] is True

    def test_inverted_thresholds_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be >= exclude_threshold"):
            infer.prioritise_review([], match_threshold=0.1, exclude_threshold=0.9)

    def test_missing_probability_is_treated_as_zero(self) -> None:
        out = infer.prioritise_review([{}])
        assert out["auto_apart_count"] == 1


# ── S-M: error classification + helper unwraps ──────────────────────────────


class TestErrorSemantics:
    def test_fp_from_truth_status(self) -> None:
        assert _is_fp({"truth_status": "false positive"}) is True
        assert _is_fp({"truth_status": "false negative"}) is False

    def test_fp_from_clerical_score_when_status_is_empty(self) -> None:
        # model says match (0.9), label says not (0.0) → FP
        assert _is_fp({"clerical_match_score": 0.0, "match_probability": 0.9}) is True
        # model says match, label agrees → not an error
        assert _is_fp({"clerical_match_score": 1.0, "match_probability": 0.9}) is False

    def test_missing_fields_fall_back_to_status(self) -> None:
        assert _is_fp({"truth_status": "false positive"}) is True
        assert _is_fp({}) is False


class TestSdfAndChartUnwraps:
    def test_sdf_to_records_fixes_nans_and_numpy_scalars(self) -> None:
        df = pd.DataFrame([{"a": float("nan"), "b": 1.0}])
        sdf = SimpleNamespace(as_pandas_dataframe=lambda: df)
        out = infer._sdf_to_records(sdf)
        assert out[0]["a"] is None
        assert out[0]["b"] == 1.0

    def test_chart_rows_reads_altair_inline_data(self) -> None:
        chart = SimpleNamespace(data=SimpleNamespace(values=[{"x": 1}, {"x": 2}]))
        assert _chart_rows(chart) == [{"x": 1}, {"x": 2}]

    def test_chart_rows_degrades_to_empty(self) -> None:
        assert _chart_rows(None) == []
        assert _chart_rows(SimpleNamespace()) == []
        assert _chart_rows(SimpleNamespace(data="not-a-chart")) == []

    def test_nan_coercion_helper_is_recursive(self) -> None:
        # NaN → None, plain floats pass through
        df = pd.DataFrame([{"a": float("nan"), "b": 2.5}])
        out = infer._sdf_to_records(SimpleNamespace(as_pandas_dataframe=lambda: df))
        assert out[0]["a"] is None
        assert out[0]["b"] == 2.5
