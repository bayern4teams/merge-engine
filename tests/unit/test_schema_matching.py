"""Unit tests for V3 schema matching & canonical schema (Part B, P-7).

Three tiers under test in order: Valentine's merged candidates, the
deterministic heuristic pass (reusing map_fields.py's own
``_heuristic_match``), and the LLM fallback for what remains ambiguous.
Storage tests cover all three decisions (accept / new_field / ambiguous)
landing correctly in ``md_field_mapping`` / ``md_canonical_field``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

pytest.importorskip("valentine", reason="schema_matching optional extra not installed")

from src.mcp_server.tools.schema_matching import (
    heuristic_candidates_for_column,
    match_columns_to_canonical_schema,
    resolve_ambiguous_columns,
    run_valentine_candidates,
    store_mappings,
)


class TestRunValentineCandidates:
    def test_exact_name_and_value_matches_score_highly(self) -> None:
        source_df = pd.DataFrame(
            {
                "companyID": ["1", "2", "3"],
                "website": ["a.com", "b.com", "c.com"],
            }
        )
        target_df = pd.DataFrame(
            {
                "companyID": ["1", "2", "3"],
                "website": ["a.com", "b.com", "c.com"],
            }
        )
        result = run_valentine_candidates(source_df, target_df)
        assert "companyID" in result
        assert result["companyID"][0]["target_column"] == "companyID"
        assert result["companyID"][0]["score"] > 0.9
        assert "website" in result
        assert result["website"][0]["target_column"] == "website"

    def test_a_column_with_no_counterpart_gets_no_candidates_or_only_weak_ones(self) -> None:
        source_df = pd.DataFrame({"foundingDate": ["2001", "2002", "2003"]})
        target_df = pd.DataFrame({"revenue": ["1000", "2000", "3000"]})
        result = run_valentine_candidates(source_df, target_df)
        # Either no candidates at all, or nothing scoring near 1.0 -- two
        # genuinely unrelated columns must never look like a strong match.
        candidates = result.get("foundingDate", [])
        assert all(c["score"] < 0.9 for c in candidates)

    def test_candidates_are_sorted_descending_by_score(self) -> None:
        source_df = pd.DataFrame({"id": ["1", "2", "3"]})
        target_df = pd.DataFrame({"id": ["1", "2", "3"], "identifier": ["1", "2", "3"]})
        result = run_valentine_candidates(source_df, target_df)
        scores = [c["score"] for c in result.get("id", [])]
        assert scores == sorted(scores, reverse=True)

    def test_a_matcher_failure_does_not_abort_the_others(self) -> None:
        source_df = pd.DataFrame({"companyID": ["1", "2"]})
        target_df = pd.DataFrame({"companyID": ["1", "2"]})
        with patch("src.mcp_server.tools.schema_matching.Coma") as broken_coma:
            broken_coma.side_effect = RuntimeError("boom")
            result = run_valentine_candidates(source_df, target_df)
        # Jaccard/DistributionBased should still have found the exact match.
        assert "companyID" in result


class TestHeuristicCandidatesForColumn:
    def test_exact_name_match_scores_1_0(self) -> None:
        result = heuristic_candidates_for_column("website", ["website", "revenue"])
        assert result[0] == {"target_column": "website", "score": 1.0}

    def test_zero_score_candidates_are_excluded(self) -> None:
        result = heuristic_candidates_for_column("foundingDate", ["revenue", "ticker"])
        assert result == []

    def test_results_sorted_descending(self) -> None:
        result = heuristic_candidates_for_column("company_name", ["name", "company_name_legal"])
        scores = [c["score"] for c in result]
        assert scores == sorted(scores, reverse=True)


class TestMatchColumnsToCanonicalSchema:
    def test_empty_target_schema_makes_every_column_a_new_field(self) -> None:
        source_df = pd.DataFrame({"companyID": ["1"], "name": ["Acme"]})
        target_df = pd.DataFrame()
        result = match_columns_to_canonical_schema(["companyID", "name"], source_df, target_df)
        assert result["companyID"]["decision"] == "new_field"
        assert result["name"]["decision"] == "new_field"

    def test_a_strong_match_is_accepted_without_the_llm(self) -> None:
        source_df = pd.DataFrame({"companyID": ["1", "2", "3"]})
        target_df = pd.DataFrame({"companyID": ["1", "2", "3"]})
        result = match_columns_to_canonical_schema(["companyID"], source_df, target_df)
        assert result["companyID"]["decision"] == "accept"
        assert result["companyID"]["best"]["target_column"] == "companyID"

    def test_a_column_with_no_real_counterpart_becomes_a_new_field(self) -> None:
        source_df = pd.DataFrame({"revenue": ["1000", "2000", "3000"]})
        target_df = pd.DataFrame({"foundingDate": ["2001", "2002", "2003"]})
        result = match_columns_to_canonical_schema(["revenue"], source_df, target_df)
        assert result["revenue"]["decision"] == "new_field"

    def test_a_mid_confidence_match_is_flagged_ambiguous_not_silently_accepted(self) -> None:
        # "ceoName" and "name" share the token "name" (Jaccard 0.5), which
        # map_fields.py's own _heuristic_match deliberately keeps below its
        # accept bar -- must land here as "ambiguous", not "accept".
        source_df = pd.DataFrame({"ceoName": ["Jane Doe"]})
        target_df = pd.DataFrame({"name": ["Acme Inc"]})
        result = match_columns_to_canonical_schema(["ceoName"], source_df, target_df)
        assert result["ceoName"]["decision"] in ("ambiguous", "new_field")
        assert result["ceoName"]["decision"] != "accept"

    def test_only_one_source_column_can_accept_onto_the_same_target(self) -> None:
        """Live-caught (BP-B, real origin2.Person, 2026-08-19): `firstName`,
        `lastName`, and `middleName` each independently score 0.8 against a
        bare `name` seed column (all contain "name" as a substring). Without
        a one-winner-per-target rule, all three would silently "accept" onto
        the same canonical field -- whichever is stored last wins in any
        consumer that treats a canonical field as single-valued. Only the
        first (here: `fullName`, the best single-field candidate) may
        accept; the rest must be flagged ambiguous."""
        source_df = pd.DataFrame(
            {
                "fullName": ["Jane Q Doe"],
                "firstName": ["Jane"],
                "lastName": ["Doe"],
                "middleName": ["Q"],
            }
        )
        target_df = pd.DataFrame({"name": ["Jane Q Doe"]})
        result = match_columns_to_canonical_schema(
            ["fullName", "firstName", "lastName", "middleName"], source_df, target_df
        )
        decisions = {col: entry["decision"] for col, entry in result.items()}
        accepted = [col for col, d in decisions.items() if d == "accept"]
        assert accepted == ["fullName"]
        assert decisions["firstName"] == "ambiguous"
        assert decisions["lastName"] == "ambiguous"
        assert decisions["middleName"] == "ambiguous"


class TestResolveAmbiguousColumns:
    @pytest.mark.asyncio
    async def test_no_llm_config_keeps_the_tier_1_2_result_as_needs_review(self) -> None:
        ambiguous = {
            "ceoName": {
                "decision": "ambiguous",
                "best": {"target_column": "name", "score": 0.5},
                "valentine_candidates": [],
                "heuristic_candidates": [{"target_column": "name", "score": 0.5}],
            }
        }
        resolved = await resolve_ambiguous_columns(ambiguous, llm_config=None)
        assert resolved["ceoName"]["review_status"] == "NEEDS_REVIEW"
        assert resolved["ceoName"]["origin"] == "ALGORITHMIC"

    @pytest.mark.asyncio
    async def test_llm_accept_decision_updates_the_best_candidate(self) -> None:
        ambiguous = {
            "fullName": {
                "decision": "ambiguous",
                "best": {"target_column": "name", "score": 0.5},
                "valentine_candidates": [],
                "heuristic_candidates": [{"target_column": "name", "score": 0.5}],
            }
        }
        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(
            return_value={
                "decision": "accept",
                "target_column": "legal_name",
                "confidence": 0.85,
                "reason": "fullName is the legal name concept",
            }
        )
        with (
            patch(
                "src.llm.PROMPT_TEMPLATES",
                {
                    "schema_matching": {
                        "system": "sys",
                        "human": "{source_column} {candidates_block}",
                    }
                },
            ),
            patch("src.llm.LLMClient", return_value=mock_client),
        ):
            resolved = await resolve_ambiguous_columns(
                ambiguous, llm_config={"field_mapping_enabled": True}
            )
        assert resolved["fullName"]["decision"] == "accept"
        assert resolved["fullName"]["best"]["target_column"] == "legal_name"
        assert resolved["fullName"]["origin"] == "LLM_ASSISTED"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_needs_review_not_a_crash(self) -> None:
        ambiguous = {
            "x": {
                "decision": "ambiguous",
                "best": {"target_column": "y", "score": 0.4},
                "valentine_candidates": [],
                "heuristic_candidates": [],
            }
        }
        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(side_effect=RuntimeError("LLM down"))
        with (
            patch(
                "src.llm.PROMPT_TEMPLATES",
                {
                    "schema_matching": {
                        "system": "sys",
                        "human": "{source_column} {candidates_block}",
                    }
                },
            ),
            patch("src.llm.LLMClient", return_value=mock_client),
        ):
            resolved = await resolve_ambiguous_columns(
                ambiguous, llm_config={"field_mapping_enabled": True}
            )
        assert resolved["x"]["review_status"] == "NEEDS_REVIEW"
        assert resolved["x"]["origin"] == "ALGORITHMIC"


class TestStoreMappings:
    def test_new_field_upserts_a_canonical_field_and_auto_accepts(self) -> None:
        store = MagicMock()
        store.upsert_canonical_field.return_value = "field-1"
        store.insert_field_mapping.return_value = "mapping-1"
        results = {"companyID": {"decision": "new_field"}}
        ids = store_mappings(store, "company", "table-1", {"companyID": "col-1"}, results)
        assert ids == {"companyID": "field-1"}
        store.upsert_canonical_field.assert_called_once_with("company", "company_id")
        assert store.insert_field_mapping.call_args.kwargs["review_status"] == "AUTO_ACCEPTED"
        assert store.insert_field_mapping.call_args.kwargs["confidence"] == 1.0

    def test_accept_decision_maps_onto_the_matched_canonical_field(self) -> None:
        store = MagicMock()
        store.upsert_canonical_field.return_value = "field-2"
        results = {
            "name": {
                "decision": "accept",
                "best": {"target_column": "companyID", "score": 0.9},
                "valentine_candidates": [],
                "heuristic_candidates": [],
            }
        }
        store_mappings(store, "company", "table-1", {"name": "col-2"}, results)
        assert store.insert_field_mapping.call_args.kwargs["review_status"] == "AUTO_ACCEPTED"
        assert store.insert_field_mapping.call_args.kwargs["confidence"] == 0.9

    def test_ambiguous_decision_stores_needs_review(self) -> None:
        store = MagicMock()
        store.upsert_canonical_field.return_value = "field-3"
        results = {
            "ceoName": {
                "decision": "ambiguous",
                "best": {"target_column": "name", "score": 0.5},
                "valentine_candidates": [],
                "heuristic_candidates": [],
                "origin": "ALGORITHMIC",
                "review_status": "NEEDS_REVIEW",
            }
        }
        store_mappings(store, "company", "table-1", {"ceoName": "col-3"}, results)
        assert store.insert_field_mapping.call_args.kwargs["review_status"] == "NEEDS_REVIEW"
