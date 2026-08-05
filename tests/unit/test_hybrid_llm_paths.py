"""Unit tests for the hybrid-LLM paths in resolve_entities and map_fields.

Covers: borderline pairs routed to the LLM, LLM judgment applied /
fallback on failure, LLM field-mapping decisions (map / skip,
with new_column refused outright /
skip), validation of bad LLM output, and the config gates.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.map_fields import map_fields_impl
from src.mcp_server.tools.resolve_entities import resolve_entities_impl

_LLM_CONFIG = {
    "provider": "openai",
    "model": "test-model",
    "api_key": "test",
    "entity_resolution_enabled": True,
    "field_mapping_enabled": True,
}


def _borderline_records() -> list[dict[str, Any]]:
    """Two records the heuristic scores in the borderline band.

    Shared first name-token (same block), similar-but-not-identical
    fields so the heuristic lands strictly between 0.70 and 0.95.
    """
    return [
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


def _mock_llm(return_value: Any = None, side_effect: Any = None) -> MagicMock:
    client = MagicMock()
    client.call_json = AsyncMock(return_value=return_value, side_effect=side_effect)
    return client


class TestEntityResolutionLLMPath:
    """LLM judgment for borderline entity pairs."""

    @pytest.mark.asyncio
    async def test_borderline_pair_sent_to_llm_and_match_unions(self) -> None:
        client = _mock_llm(
            return_value={
                "judgments": [
                    {
                        "pair_id": 1,
                        "match": True,
                        "confidence": 0.97,
                        "reason": "same company, abbreviated name",
                    },
                ],
            }
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await resolve_entities_impl(
                "company",
                _borderline_records(),
                llm_config=_LLM_CONFIG,
            )

        client.call_json.assert_called_once()
        assert result["llm_scored"] == 1
        # Confident LLM match -> records unioned into one cluster
        multi = [c for c in result["clusters"] if len(c["record_ids"]) == 2]
        assert len(multi) == 1
        assert multi[0]["required_human_review"] is False

    @pytest.mark.asyncio
    async def test_llm_non_match_splits_pair(self) -> None:
        client = _mock_llm(
            return_value={
                "judgments": [
                    {
                        "pair_id": 1,
                        "match": False,
                        "confidence": 0.9,
                        "reason": "different companies sharing a generic word",
                    },
                ],
            }
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await resolve_entities_impl(
                "company",
                _borderline_records(),
                llm_config=_LLM_CONFIG,
            )

        # Non-match pushed below the union threshold -> two singleton clusters
        assert all(len(c["record_ids"]) == 1 for c in result["clusters"])
        assert len(result["clusters"]) == 2

    @pytest.mark.asyncio
    async def test_llm_failure_leaves_borderline_pairs_unmerged(self) -> None:
        client = _mock_llm(side_effect=Exception("LLM down"))
        with patch("src.llm.LLMClient", return_value=client):
            result = await resolve_entities_impl(
                "company",
                _borderline_records(),
                llm_config=_LLM_CONFIG,
            )

        # The run does not block on the LLM — but an unadjudicated borderline
        # pair is NOT merged. Merging on evidence the system itself flagged as
        # uncertain is what destroyed 26 of 400 distinct people in the
        # evaluation set; leaving duplicates is recoverable, merging is not.
        assert all(len(c["record_ids"]) == 1 for c in result["clusters"])
        assert len(result["clusters"]) == 2

    @pytest.mark.asyncio
    async def test_disabled_flag_skips_llm(self) -> None:
        client = _mock_llm(return_value={"judgments": []})
        cfg = dict(_LLM_CONFIG, entity_resolution_enabled=False)
        with patch("src.llm.LLMClient", return_value=client):
            result = await resolve_entities_impl(
                "company",
                _borderline_records(),
                llm_config=cfg,
            )

        client.call_json.assert_not_called()
        assert result["llm_scored"] == 0

    @pytest.mark.asyncio
    async def test_missing_judgment_falls_back_per_pair(self) -> None:
        # LLM answers with an empty judgments list -> every pair keeps its
        # heuristic score, which for a borderline pair means "undecided".
        client = _mock_llm(return_value={"judgments": []})
        with patch("src.llm.LLMClient", return_value=client):
            result = await resolve_entities_impl(
                "company",
                _borderline_records(),
                llm_config=_LLM_CONFIG,
            )

        # Undecided means not merged, not merged-and-flagged.
        assert all(len(c["record_ids"]) == 1 for c in result["clusters"])
        assert len(result["clusters"]) == 2


class TestFieldMappingLLMPath:
    """LLM decisions for fields the heuristic can't place."""

    @pytest.fixture
    def dest_schema(self) -> dict[str, Any]:
        return {
            "table": "companies",
            "columns": [
                {"name": "id", "type": "BIGINT"},
                {"name": "name", "type": "TEXT"},
                {"name": "email", "type": "TEXT"},
            ],
        }

    @pytest.mark.asyncio
    async def test_llm_maps_semantic_field_to_existing_column(
        self, dest_schema: dict[str, Any]
    ) -> None:
        unified = {"org_title": {"type": "str", "sample_values": ["Acme Corp"]}}
        client = _mock_llm(
            return_value={
                "org_title": {"action": "map", "dest_column": "name"},
            }
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
            )

        mapping = result["field_mapping"]["org_title"]
        assert mapping["dest_column"] == "name"
        assert mapping["mapped_by"] == "llm"
        assert not result["schema_changes"]

    @pytest.mark.asyncio
    async def test_llm_new_column_proposal_is_refused(self, dest_schema: dict[str, Any]) -> None:
        """The destination schema is a fixed contract: a `new_column` reply is
        rejected outright, no matter what the prompt asked for."""
        unified = {"zzqx_score": {"type": "float", "sample_values": [0.7]}}
        client = _mock_llm(
            return_value={
                "zzqx_score": {
                    "action": "new_column",
                    "name": "zzqx_score",
                    "type": "DOUBLE PRECISION",
                },
            }
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
            )

        assert result["schema_changes"] == []
        assert "zzqx_score" not in result["field_mapping"]
        assert [u["field"] for u in result["unmapped_fields"]] == ["zzqx_score"]

    @pytest.mark.asyncio
    async def test_llm_skip_surfaces_as_unmapped(self, dest_schema: dict[str, Any]) -> None:
        unified = {"zzqx_rowver": {"type": "int", "sample_values": [3]}}
        client = _mock_llm(
            return_value={
                "zzqx_rowver": {"action": "skip", "reason": "internal row version"},
            }
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
            )

        assert "zzqx_rowver" not in result["field_mapping"]
        assert len(result["unmapped_fields"]) == 1
        assert "internal row version" in result["unmapped_fields"][0]["reason"]

    @pytest.mark.asyncio
    async def test_invalid_llm_column_falls_back_to_heuristic(
        self, dest_schema: dict[str, Any]
    ) -> None:
        # LLM maps to a column that doesn't exist -> decision rejected,
        # heuristic tier applies (no match -> left unmapped).
        unified = {"zzqx_field": {"type": "str", "sample_values": ["x"]}}
        client = _mock_llm(
            return_value={
                "zzqx_field": {"action": "map", "dest_column": "nonexistent"},
            }
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
            )

        assert result["schema_changes"] == []
        assert "zzqx_field" not in result["field_mapping"]
        assert [u["field"] for u in result["unmapped_fields"]] == ["zzqx_field"]

    @pytest.mark.asyncio
    async def test_llm_failure_uses_heuristic_tiers(self, dest_schema: dict[str, Any]) -> None:
        unified = {"zzqx_field": {"type": "str", "sample_values": ["x"]}}
        client = _mock_llm(side_effect=Exception("LLM down"))
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
            )

        assert result["schema_changes"] == []
        assert [u["field"] for u in result["unmapped_fields"]] == ["zzqx_field"]

    @pytest.mark.asyncio
    async def test_disabled_flag_skips_llm(self, dest_schema: dict[str, Any]) -> None:
        unified = {"zzqx_field": {"type": "str", "sample_values": ["x"]}}
        client = _mock_llm(return_value={})
        cfg = dict(_LLM_CONFIG, field_mapping_enabled=False)
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=cfg,
            )

        client.call_json.assert_not_called()
        assert result["schema_changes"] == []
        assert [u["field"] for u in result["unmapped_fields"]] == ["zzqx_field"]

    @pytest.mark.asyncio
    async def test_exact_matches_never_go_to_llm(self, dest_schema: dict[str, Any]) -> None:
        unified = {
            "name": {"type": "str", "sample_values": ["Acme"]},
            "email": {"type": "str", "sample_values": ["a@b.c"]},
        }
        client = _mock_llm(return_value={})
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
            )

        client.call_json.assert_not_called()
        assert result["field_mapping"]["name"]["confidence"] == 1.0
        assert result["field_mapping"]["email"]["confidence"] == 1.0
