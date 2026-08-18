"""Unit tests for Phase 7.1: per-pair match evidence retained on clusters.

Before this, `_build_clusters` computed per-pair scores in a local
`edge_weight` dict and kept only `min()` as a cluster's `match_confidence` —
which fields carried the match, and why, was discarded the moment scoring
finished. These tests cover the field-level weight breakdown
(`_pair_field_evidence`), attaching it to a finished cluster's internal
edges (`_evidence_for_members`), and the end-to-end path through
`resolve_entities_impl` for both the probabilistic scorer and the LLM
adjudication path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.fellegi_sunter import FieldModel, MatchModel
from src.mcp_server.tools.resolve_entities import (
    _evidence_for_members,
    _pair_field_evidence,
    resolve_entities_impl,
)

_LLM_CONFIG = {
    "provider": "openai",
    "model": "test-model",
    "api_key": "test",
    "entity_resolution_enabled": True,
}


def _model() -> MatchModel:
    return MatchModel(
        fields={
            "name": FieldModel(
                name="name", m={"exact": 0.9, "disagree": 0.02}, u={"exact": 0.01, "disagree": 0.9}
            ),
            "email": FieldModel(
                name="email",
                m={"exact": 0.95, "disagree": 0.01},
                u={"exact": 0.001, "disagree": 0.9},
            ),
        },
        match_prior=0.01,
    )


class TestPairFieldEvidence:
    def test_exact_agreement_is_matched_positive_weight(self) -> None:
        rec1 = {"name": "Acme Corp", "email": "info@acme.com"}
        rec2 = {"name": "Acme Corp", "email": "info@acme.com"}
        vector = {"name": ("exact", "acme corp"), "email": ("exact", "info@acme.com")}
        matched, mismatched = _pair_field_evidence(rec1, rec2, vector, _model())
        assert mismatched == []
        fields = {e["field"] for e in matched}
        assert fields == {"name", "email"}
        for entry in matched:
            assert entry["weight"] > 0
            assert entry["a"] == entry["b"]  # both sides agree

    def test_disagreement_is_mismatched_negative_weight(self) -> None:
        rec1 = {"name": "Acme Corp"}
        rec2 = {"name": "Globex Inc"}
        vector = {"name": ("disagree", None)}
        matched, mismatched = _pair_field_evidence(rec1, rec2, vector, _model())
        assert matched == []
        assert len(mismatched) == 1
        assert mismatched[0]["field"] == "name"
        assert mismatched[0]["weight"] < 0
        assert mismatched[0]["a"] == "Acme Corp"
        assert mismatched[0]["b"] == "Globex Inc"

    def test_null_level_field_is_dropped(self) -> None:
        rec1 = {"name": "Acme Corp"}
        rec2 = {"name": "Acme Corp", "email": "info@acme.com"}
        vector = {"name": ("exact", "acme corp"), "email": ("null", None)}
        matched, mismatched = _pair_field_evidence(rec1, rec2, vector, _model())
        assert {e["field"] for e in matched} == {"name"}
        assert mismatched == []

    def test_field_with_no_model_entry_is_skipped(self) -> None:
        rec1 = {"phone": "555-1234"}
        rec2 = {"phone": "555-1234"}
        vector = {"phone": ("exact", "5551234")}  # "phone" not in _model()'s fields
        matched, mismatched = _pair_field_evidence(rec1, rec2, vector, _model())
        assert matched == []
        assert mismatched == []

    def test_matched_sorted_strongest_first_mismatched_weakest_first(self) -> None:
        rec1 = {"name": "Acme Corp", "email": "info@acme.com"}
        rec2 = {"name": "Acme Corp", "email": "info@acme.com"}
        vector = {"name": ("exact", "acme corp"), "email": ("exact", "info@acme.com")}
        matched, _ = _pair_field_evidence(rec1, rec2, vector, _model())
        weights = [e["weight"] for e in matched]
        assert weights == sorted(weights, reverse=True)


class TestEvidenceForMembers:
    def test_only_edges_present_in_both_dicts_are_returned(self) -> None:
        edge_weight = {("a", "b"): 0.99, ("b", "c"): 0.97}
        pair_evidence = {
            ("a", "b"): {"a": "a", "b": "b", "confidence": 0.99},
            # ("b", "c") has an edge weight but no recorded evidence (e.g.
            # heuristic-only, no model fit) -- must not be padded in.
        }
        result = _evidence_for_members(["a", "b", "c"], edge_weight, pair_evidence)
        assert result == [{"a": "a", "b": "b", "confidence": 0.99}]

    def test_no_edges_at_all_returns_empty(self) -> None:
        assert _evidence_for_members(["a", "b"], {}, {}) == []

    def test_singleton_has_no_pairs(self) -> None:
        assert _evidence_for_members(["a"], {}, {}) == []


@pytest.mark.asyncio
class TestResolveEntitiesImplMatchEvidence:
    async def test_probabilistic_merge_attaches_fellegi_sunter_evidence(self) -> None:
        # 9 "Widget Traders" records, each with its own distinct email/phone/
        # website (blocks together on name -> C(9,2) = 36 candidate pairs,
        # over _MIN_PAIRS_FOR_MODEL=30 so the probabilistic model actually
        # fits), none of which should merge -- they share only the
        # non-discriminating name. Plus one genuine duplicate pair (Zenith
        # Systems, submitted from two origins) agreeing on name AND email
        # AND phone AND domain -- distinguishing enough evidence to clear
        # the 0.95 auto-match threshold and the discriminating-agreement
        # guard the Widget pairs correctly never reach.
        records = [
            {
                "_origin_id": "o1",
                "_origin_table": "t",
                "_origin_id_row": f"a{i}",
                "name": "Widget Traders",
                "email": f"user{i}@random{i}.example.com",
                "phone": f"555-{1000 + i}",
                "website": f"random{i}.example.com",
            }
            for i in range(9)
        ] + [
            {
                "_origin_id": "o1",
                "_origin_table": "t",
                "_origin_id_row": "z1",
                "name": "Zenith Systems",
                "email": "unique.zenith@zenith.example.com",
                "phone": "555-9999",
                "website": "zenith.example.com",
            },
            {
                "_origin_id": "o2",
                "_origin_table": "t2",
                "_origin_id_row": "z2",
                "name": "Zenith Systems",
                "email": "unique.zenith@zenith.example.com",
                "phone": "555-9999",
                "website": "zenith.example.com",
            },
        ]
        result = await resolve_entities_impl("company", records)

        multi = [c for c in result["clusters"] if len(c["record_ids"]) > 1]
        assert multi, "expected at least one merged cluster"
        cluster = multi[0]
        assert "match_evidence" in cluster
        pairs = cluster["match_evidence"]["pairs"]
        assert pairs, "expected at least one pair of evidence"
        for p in pairs:
            assert p["method"] == "fellegi_sunter"
            assert p["confidence"] > 0
            assert isinstance(p["matched_fields"], list)
            assert isinstance(p["mismatched_fields"], list)
            # The pair's own unique email is what actually merged it -- that
            # field must show up as positive (matched) evidence somewhere.
            matched_field_names = {f["field"] for f in p["matched_fields"]}
            assert "email" in matched_field_names
            email_entry = next(f for f in p["matched_fields"] if f["field"] == "email")
            assert email_entry["weight"] > 0
            assert email_entry["a"] == email_entry["b"]

    async def test_llm_adjudicated_merge_attaches_llm_evidence_with_reason(self) -> None:
        records = [
            {
                "_origin_id": "pg",
                "_origin_table": "companies",
                "_origin_id_row": "1",
                "name": "Acme International Corp",
                "city": "Berlin",
            },
            {
                "_origin_id": "ch",
                "_origin_table": "organizations",
                "_origin_id_row": "9",
                "name": "Acme Intl Corporation",
                "city": "Berlin",
            },
        ]
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "judgments": [
                    {
                        "pair_id": 1,
                        "match": True,
                        "confidence": 0.97,
                        "reason": "same company, abbreviated legal form",
                    },
                ],
            }
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await resolve_entities_impl("company", records, llm_config=_LLM_CONFIG)

        multi = [c for c in result["clusters"] if len(c["record_ids"]) == 2]
        assert len(multi) == 1
        cluster = multi[0]
        assert "match_evidence" in cluster
        pairs = cluster["match_evidence"]["pairs"]
        assert len(pairs) == 1
        assert pairs[0]["method"] == "llm"
        assert pairs[0]["reason"] == "same company, abbreviated legal form"
        assert pairs[0]["llm_match"] is True

    async def test_singleton_cluster_has_no_match_evidence_key(self) -> None:
        records = [
            {
                "_origin_id": "o1",
                "_origin_table": "t",
                "_origin_id_row": "1",
                "name": "Unique Corp",
            }
        ]
        result = await resolve_entities_impl("company", records)
        assert len(result["clusters"]) == 1
        assert "match_evidence" not in result["clusters"][0]
