"""Unit tests for V3 candidate & composite key discovery (Part B, P-5).

Three tiers under test in order: declared indexes (pure relabelling of
P-2's discover_indexes()), single-column inference (from P-3 profiles
alone), and pruned+verified composite search (only identifier-like, low-
null columns considered, and only ever verified against a real query, never
stored on a guess). An inferred candidate must never be labelled a primary
key -- key_type itself carries that distinction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_server.tools.candidate_keys import (
    classify_declared_indexes,
    discover_candidate_keys,
    infer_single_column_candidates,
    propose_composite_candidate_pairs,
    store_candidate_keys,
    verify_composite_uniqueness,
)


class TestClassifyDeclaredIndexes:
    def test_a_primary_index_becomes_declared_primary_key(self) -> None:
        indexes = [{"name": "pk1", "columns": ["id"], "is_unique": True, "is_primary": True}]
        result = classify_declared_indexes(indexes)
        assert result == [
            {
                "columns": ["id"],
                "key_type": "DECLARED_PRIMARY_KEY",
                "confidence": 1.0,
                "evidence": {"index_name": "pk1", "kind": None},
            }
        ]

    def test_a_unique_non_primary_index_becomes_declared_unique(self) -> None:
        indexes = [{"name": "uq1", "columns": ["email"], "is_unique": True, "is_primary": False}]
        result = classify_declared_indexes(indexes)
        assert result[0]["key_type"] == "DECLARED_UNIQUE"

    def test_a_plain_non_unique_index_is_ignored(self) -> None:
        indexes = [{"name": "ix1", "columns": ["name"], "is_unique": False, "is_primary": False}]
        assert classify_declared_indexes(indexes) == []

    def test_a_composite_primary_key_keeps_its_column_order(self) -> None:
        indexes = [
            {
                "name": "pk_composite",
                "columns": ["company_id", "year"],
                "is_unique": True,
                "is_primary": True,
            }
        ]
        result = classify_declared_indexes(indexes)
        assert result[0]["columns"] == ["company_id", "year"]


class TestInferSingleColumnCandidates:
    def test_high_unique_low_null_column_is_proposed(self) -> None:
        profiles = {"companyID": {"unique_ratio": 0.999, "null_ratio": 0.0}}
        result = infer_single_column_candidates(profiles)
        assert len(result) == 1
        assert result[0]["columns"] == ["companyID"]
        assert result[0]["key_type"] == "INFERRED_SINGLE_COLUMN"

    def test_never_labelled_a_primary_key(self) -> None:
        profiles = {"companyID": {"unique_ratio": 1.0, "null_ratio": 0.0}}
        result = infer_single_column_candidates(profiles)
        assert result[0]["key_type"] != "DECLARED_PRIMARY_KEY"
        assert "PRIMARY_KEY" not in result[0]["key_type"]

    def test_low_unique_ratio_is_rejected(self) -> None:
        profiles = {"status": {"unique_ratio": 0.02, "null_ratio": 0.0}}
        assert infer_single_column_candidates(profiles) == []

    def test_high_null_ratio_is_rejected_even_if_unique_among_non_nulls(self) -> None:
        profiles = {"legacy_ref": {"unique_ratio": 1.0, "null_ratio": 0.5}}
        assert infer_single_column_candidates(profiles) == []

    def test_missing_profile_fields_are_skipped_not_crashed_on(self) -> None:
        profiles = {"weird_col": {}}
        assert infer_single_column_candidates(profiles) == []


class TestProposeCompositeCandidatePairs:
    def test_only_identifier_like_low_null_columns_are_paired(self) -> None:
        profiles = {
            "company_id": {"null_ratio": 0.0},
            "year": {"null_ratio": 0.0},  # not identifier-like -- excluded
            "person_id": {"null_ratio": 0.0},
        }
        pairs = propose_composite_candidate_pairs(profiles)
        assert pairs == [("company_id", "person_id")]

    def test_a_high_null_identifier_like_column_is_excluded(self) -> None:
        profiles = {
            "company_id": {"null_ratio": 0.0},
            "old_ref_id": {"null_ratio": 0.9},
        }
        assert propose_composite_candidate_pairs(profiles) == []

    def test_never_brute_forces_every_column_only_pairs(self) -> None:
        profiles = {f"col_{i}_id": {"null_ratio": 0.0} for i in range(4)}
        pairs = propose_composite_candidate_pairs(profiles)
        # C(4, 2) = 6 pairs, never triples or higher -- "pruned" per the
        # module's own docstring, capped at 2-column combinations.
        assert len(pairs) == 6
        assert all(len(p) == 2 for p in pairs)


class TestVerifyCompositeUniqueness:
    @pytest.mark.asyncio
    async def test_a_genuinely_unique_pair_scores_1_0(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock(
            return_value={
                "rows": [
                    {"company_id": "c1", "year": 2024},
                    {"company_id": "c1", "year": 2025},
                    {"company_id": "c2", "year": 2024},
                ]
            }
        )
        ratio = await verify_composite_uniqueness(connector, "finance", ("company_id", "year"))
        assert ratio == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_a_duplicated_pair_scores_below_1_0(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock(
            return_value={
                "rows": [
                    {"a": "x", "b": "y"},
                    {"a": "x", "b": "y"},  # exact duplicate combination
                    {"a": "x", "b": "z"},
                ]
            }
        )
        ratio = await verify_composite_uniqueness(connector, "t", ("a", "b"))
        assert ratio == pytest.approx(2 / 3)

    @pytest.mark.asyncio
    async def test_rows_with_a_null_in_either_column_are_excluded_from_both_counts(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock(
            return_value={
                "rows": [
                    {"a": "x", "b": "y"},
                    {"a": None, "b": "y"},
                    {"a": "z", "b": None},
                ]
            }
        )
        ratio = await verify_composite_uniqueness(connector, "t", ("a", "b"))
        assert ratio == pytest.approx(1.0)  # only the one fully-non-null row counts

    @pytest.mark.asyncio
    async def test_no_rows_at_all_scores_0_0_not_a_division_error(self) -> None:
        connector = MagicMock()
        connector.extract = AsyncMock(return_value={"rows": []})
        ratio = await verify_composite_uniqueness(connector, "t", ("a", "b"))
        assert ratio == 0.0


class TestDiscoverCandidateKeysPipeline:
    @pytest.mark.asyncio
    async def test_declared_keys_short_circuit_before_any_inference(self) -> None:
        connector = MagicMock()
        connector.discover_indexes = AsyncMock(
            return_value=[{"name": "pk", "columns": ["id"], "is_unique": True, "is_primary": True}]
        )
        # If inference ran anyway it would need extract() -- absent here,
        # so any call would raise AttributeError and fail the test.
        result = await discover_candidate_keys(connector, "company", {"id": {}})
        assert result[0]["key_type"] == "DECLARED_PRIMARY_KEY"

    @pytest.mark.asyncio
    async def test_no_declared_keys_falls_through_to_single_column_inference(self) -> None:
        connector = MagicMock()
        connector.discover_indexes = AsyncMock(return_value=[])
        profiles = {"companyID": {"unique_ratio": 0.999, "null_ratio": 0.0}}
        result = await discover_candidate_keys(connector, "company", profiles)
        assert result[0]["key_type"] == "INFERRED_SINGLE_COLUMN"

    @pytest.mark.asyncio
    async def test_no_declared_or_single_column_falls_through_to_verified_composite(self) -> None:
        connector = MagicMock()
        connector.discover_indexes = AsyncMock(return_value=[])
        connector.extract = AsyncMock(
            return_value={
                "rows": [
                    {"company_id": "c1", "person_id": "p1"},
                    {"company_id": "c1", "person_id": "p2"},
                ]
            }
        )
        profiles = {
            "company_id": {"unique_ratio": 0.4, "null_ratio": 0.0},
            "person_id": {"unique_ratio": 0.4, "null_ratio": 0.0},
        }
        result = await discover_candidate_keys(connector, "experience", profiles)
        assert len(result) == 1
        assert result[0]["key_type"] == "INFERRED_COMPOSITE"
        assert set(result[0]["columns"]) == {"company_id", "person_id"}

    @pytest.mark.asyncio
    async def test_a_composite_pair_below_the_uniqueness_bar_is_dropped_not_reported(self) -> None:
        connector = MagicMock()
        connector.discover_indexes = AsyncMock(return_value=[])
        connector.extract = AsyncMock(
            return_value={
                "rows": [
                    {"a_id": "x", "b_id": "y"},
                    {"a_id": "x", "b_id": "y"},
                    {"a_id": "x", "b_id": "y"},
                ]
            }
        )
        profiles = {
            "a_id": {"unique_ratio": 0.1, "null_ratio": 0.0},
            "b_id": {"unique_ratio": 0.1, "null_ratio": 0.0},
        }
        result = await discover_candidate_keys(connector, "t", profiles)
        assert result == []


class TestStoreCandidateKeys:
    def test_declared_keys_are_stored_auto_accepted(self) -> None:
        store = MagicMock()
        store.insert_candidate_key.return_value = "key-id-1"
        proposals = [{"columns": ["id"], "key_type": "DECLARED_PRIMARY_KEY", "confidence": 1.0}]
        ids = store_candidate_keys(store, "table-1", proposals)
        assert ids == ["key-id-1"]
        assert store.insert_candidate_key.call_args.kwargs["review_status"] == "AUTO_ACCEPTED"

    def test_inferred_keys_are_stored_needs_review_regardless_of_confidence(self) -> None:
        store = MagicMock()
        store.insert_candidate_key.return_value = "key-id-2"
        proposals = [
            {"columns": ["companyID"], "key_type": "INFERRED_SINGLE_COLUMN", "confidence": 0.99}
        ]
        store_candidate_keys(store, "table-1", proposals)
        assert store.insert_candidate_key.call_args.kwargs["review_status"] == "NEEDS_REVIEW"
