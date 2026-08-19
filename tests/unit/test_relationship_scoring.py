"""Unit tests for V3 relationship discovery & scoring (Part B, P-6).

Each of the ten signals PreSplink §21 asks for is tested independently
(they must never be collapsed into one number without keeping the
breakdown -- §22's "no opaque score"), plus the aggregation into an overall
confidence and relationship_type.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_server.tools.relationship_scoring import (
    SIGNAL_NAMES,
    VALID_RELATIONSHIP_TYPES,
    score_cardinality,
    score_datatype,
    score_identifier_likelihood,
    score_inclusion_coverage,
    score_name_similarity,
    score_null_behaviour,
    score_relationship,
    score_semantic_type,
    score_table_semantics,
    score_target_uniqueness,
    score_value_overlap,
)


class TestScoreDatatype:
    def test_matching_type_families_score_high_despite_different_spelling(self) -> None:
        # ClickHouse "String" vs PostgreSQL "character varying" -- same
        # family, different engine spelling.
        result = score_datatype("String", "character varying")
        assert result["score"] == 1.0

    def test_genuinely_different_families_score_zero(self) -> None:
        result = score_datatype("Int64", "text")
        assert result["score"] == 0.0


class TestScoreSemanticType:
    def test_child_pointing_at_a_parent_identifier_is_strong_evidence(self) -> None:
        result = score_semantic_type("FOREIGN_KEY", "IDENTIFIER")
        assert result["score"] == 1.0

    def test_unrelated_semantic_types_are_evidence_against(self) -> None:
        result = score_semantic_type("EMAIL", "CURRENCY_AMOUNT")
        assert result["score"] <= 0.2

    def test_unclassified_columns_are_neutral_not_penalized(self) -> None:
        result = score_semantic_type(None, None)
        assert result["score"] == 0.5


class TestScoreNameSimilarity:
    def test_exact_column_name_match_scores_perfectly(self) -> None:
        result = score_name_similarity("Experience", "companyID", "Company", "companyID")
        assert result["score"] == 1.0
        assert result["evidence"]["exact_column_match"] is True

    def test_convention_match_on_singularised_parent_table_scores_perfectly(self) -> None:
        # "companies" -> singular "company" -> "company_id" convention
        result = score_name_similarity("orders", "company_id", "companies", "id")
        assert result["score"] == 1.0
        assert result["evidence"]["convention_match"] is True

    def test_unrelated_names_score_low(self) -> None:
        result = score_name_similarity("t1", "xyz_field", "t2", "description")
        assert result["score"] < 0.5


class TestScoreValueOverlap:
    def test_full_overlap_scores_1_0(self) -> None:
        result = score_value_overlap(["a", "b", "c"], ["a", "b", "c", "d"])
        assert result["score"] == 1.0

    def test_partial_overlap_is_proportional(self) -> None:
        result = score_value_overlap(["a", "b", "c", "d"], ["a", "b"])
        assert result["score"] == 0.5

    def test_empty_child_sample_scores_zero_not_a_division_error(self) -> None:
        result = score_value_overlap([], ["a", "b"])
        assert result["score"] == 0.0


class TestScoreInclusionCoverage:
    @pytest.mark.asyncio
    async def test_full_coverage_from_a_targeted_parent_query(self) -> None:
        connector = MagicMock()
        connector.sample_distinct_values = AsyncMock(
            side_effect=[["c1", "c2"], ["c1", "c2"]]  # child sample, then parent match
        )
        result = await score_inclusion_coverage(
            connector, "experience", "company_id", "company", "id"
        )
        assert result["score"] == 1.0

    @pytest.mark.asyncio
    async def test_no_usable_child_values_returns_none_not_a_crash(self) -> None:
        connector = MagicMock()
        connector.sample_distinct_values = AsyncMock(return_value=[])
        result = await score_inclusion_coverage(connector, "t", "c", "p", "pc")
        assert result["score"] is None

    @pytest.mark.asyncio
    async def test_a_sampling_failure_is_caught_not_propagated(self) -> None:
        connector = MagicMock()
        connector.sample_distinct_values = AsyncMock(side_effect=RuntimeError("connection lost"))
        result = await score_inclusion_coverage(connector, "t", "c", "p", "pc")
        assert result["score"] is None
        assert "error" in result["evidence"]


class TestScoreTargetUniqueness:
    def test_a_declared_key_scores_1_0(self) -> None:
        result = score_target_uniqueness(["DECLARED_PRIMARY_KEY"])
        assert result["score"] == 1.0

    def test_an_inferred_only_key_scores_lower(self) -> None:
        result = score_target_uniqueness(["INFERRED_SINGLE_COLUMN"])
        assert result["score"] == 0.6

    def test_no_key_at_all_scores_zero(self) -> None:
        result = score_target_uniqueness([])
        assert result["score"] == 0.0


class TestScoreCardinality:
    def test_the_classic_fk_shape_is_the_top_score(self) -> None:
        result = score_cardinality(child_unique_ratio=0.3, parent_unique_ratio=0.999)
        assert result["evidence"]["inferred_cardinality"] == "many_to_one"
        assert result["score"] == 1.0

    def test_both_sides_unique_infers_one_to_one(self) -> None:
        result = score_cardinality(child_unique_ratio=0.99, parent_unique_ratio=0.999)
        assert result["evidence"]["inferred_cardinality"] == "one_to_one"

    def test_neither_side_unique_is_unclear_and_scores_low(self) -> None:
        result = score_cardinality(child_unique_ratio=0.3, parent_unique_ratio=0.3)
        assert result["evidence"]["inferred_cardinality"] == "many_to_many_or_unclear"
        assert result["score"] < 0.5


class TestScoreNullBehaviour:
    def test_zero_nulls_scores_1_0(self) -> None:
        assert score_null_behaviour(0.0)["score"] == 1.0

    def test_heavily_null_scores_low(self) -> None:
        assert score_null_behaviour(0.9)["score"] == pytest.approx(0.1)


class TestScoreTableSemantics:
    def test_child_table_name_referencing_the_parent_scores_high(self) -> None:
        result = score_table_semantics("CompanyFinancials", "Company")
        assert result["score"] == 1.0

    def test_unrelated_table_names_score_lower(self) -> None:
        result = score_table_semantics("Location", "Person")
        assert result["score"] < 1.0


class TestScoreIdentifierLikelihood:
    def test_p4_foreign_key_classification_is_used_when_available(self) -> None:
        result = score_identifier_likelihood("FOREIGN_KEY", "some_weird_name")
        assert result["score"] == 1.0
        assert result["evidence"]["source"] == "semantic_type"

    def test_falls_back_to_name_shape_when_unclassified(self) -> None:
        result = score_identifier_likelihood(None, "company_id")
        assert result["score"] == 0.7
        assert result["evidence"]["source"] == "name_shape"

    def test_camelcase_id_columns_get_the_name_shape_signal_too(self) -> None:
        """Live-caught (BP-B, real ClickHouse origin2.Company.companyID):
        the name-shape fallback regex only matched snake_case (`_id$`),
        scoring an obviously ID-shaped camelCase column at 0.1 (no signal)
        instead of 0.7 -- fixed by normalizing through the same
        camelCase-aware normalizer P-4 already built, shared via
        src.utils.column_naming."""
        result = score_identifier_likelihood(None, "companyID")
        assert result["score"] == 0.7
        assert result["evidence"]["source"] == "name_shape"

    def test_no_signal_at_all_scores_low(self) -> None:
        result = score_identifier_likelihood(None, "description")
        assert result["score"] < 0.5


class TestScoreRelationship:
    def test_strong_signals_across_the_board_classify_as_foreign_key(self) -> None:
        signals = {
            "datatype": {"score": 1.0, "evidence": {}},
            "semantic_type": {"score": 1.0, "evidence": {}},
            "name_similarity": {"score": 1.0, "evidence": {}},
            "value_overlap": {"score": 0.95, "evidence": {}},
            "inclusion_coverage": {"score": 0.98, "evidence": {}},
            "target_uniqueness": {"score": 1.0, "evidence": {}},
            "cardinality": {"score": 1.0, "evidence": {"inferred_cardinality": "many_to_one"}},
            "null_behaviour": {"score": 0.95, "evidence": {}},
            "table_semantics": {"score": 1.0, "evidence": {}},
            "identifier_likelihood": {"score": 1.0, "evidence": {}},
        }
        result = score_relationship(signals)
        assert result["relationship_type"] == "FOREIGN_KEY"
        assert result["confidence"] > 0.9
        # every individual signal is preserved, not collapsed away
        assert set(result["signals"]) == set(SIGNAL_NAMES)

    def test_weak_signals_across_the_board_classify_as_unrelated(self) -> None:
        signals = {
            name: {"score": 0.05, "evidence": {}} for name in SIGNAL_NAMES if name != "cardinality"
        }
        signals["cardinality"] = {
            "score": 0.1,
            "evidence": {"inferred_cardinality": "many_to_many_or_unclear"},
        }
        result = score_relationship(signals)
        assert result["relationship_type"] == "UNRELATED"

    def test_a_none_score_signal_is_excluded_from_the_average_not_treated_as_zero(self) -> None:
        signals = {name: {"score": 0.9, "evidence": {}} for name in SIGNAL_NAMES}
        signals["inclusion_coverage"] = {"score": None, "evidence": {"reason": "sampling failed"}}
        result = score_relationship(signals)
        # averaging the 9 present scores (all 0.9) should stay near 0.9, not
        # be dragged toward 0 by treating the missing one as zero.
        assert result["confidence"] > 0.85

    def test_relationship_type_is_always_one_of_the_valid_set(self) -> None:
        signals = {name: {"score": 0.5, "evidence": {}} for name in SIGNAL_NAMES}
        signals["cardinality"]["evidence"] = {"inferred_cardinality": "many_to_one"}
        result = score_relationship(signals)
        assert result["relationship_type"] in VALID_RELATIONSHIP_TYPES
