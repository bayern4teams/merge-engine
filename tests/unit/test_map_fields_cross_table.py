"""Unit tests for Phase 6.2: map_fields_impl's widened, family-aware search.

Before this, `map_fields_impl` only ever considered one destination table
per entity, so a source field with no home there (Company.totalInvestment,
with `finance` sitting right there unused) was dropped. `family_schemas`
(from `dest_family.build_family_map`, Phase 6.1) lets every phase --
exact-name, confident-heuristic, and LLM -- also consider sibling tables,
with the primary table always preferred on a tie.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.map_fields import map_fields_impl

_LLM_CONFIG = {
    "provider": "openai",
    "model": "test-model",
    "api_key": "test",
    "field_mapping_enabled": True,
}


def _mock_llm(return_value: Any = None, side_effect: Any = None) -> MagicMock:
    client = MagicMock()
    client.call_json = AsyncMock(return_value=return_value, side_effect=side_effect)
    return client


@pytest.fixture
def dest_schema() -> dict[str, Any]:
    return {
        "table": "company",
        "columns": [
            {"name": "id", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
            {"name": "_source_ids", "type": "TEXT"},
        ],
    }


@pytest.fixture
def family_schemas() -> list[dict[str, Any]]:
    return [
        {
            "table": "finance",
            "columns": [
                {"name": "id", "type": "TEXT"},
                {"name": "company_id", "type": "TEXT"},
                {"name": "amount", "type": "DOUBLE PRECISION"},
            ],
        },
        {
            "table": "contact_email",
            "columns": [
                {"name": "id", "type": "TEXT"},
                {"name": "company_id", "type": "TEXT"},
                {"name": "email", "type": "TEXT"},
            ],
        },
    ]


class TestHeuristicCrossTableMatching:
    @pytest.mark.asyncio
    async def test_exact_name_match_on_family_table_is_found(
        self, dest_schema: dict[str, Any], family_schemas: list[dict[str, Any]]
    ) -> None:
        unified = {"amount": {"type": "float", "sample_values": [4_200_000.0]}}
        result = await map_fields_impl(
            "company", unified, dest_schema, family_schemas=family_schemas
        )
        mapping = result["field_mapping"]["amount"]
        assert mapping["dest_column"] == "amount"
        assert mapping["dest_table"] == "finance"

    @pytest.mark.asyncio
    async def test_confident_heuristic_match_on_family_table(
        self, dest_schema: dict[str, Any], family_schemas: list[dict[str, Any]]
    ) -> None:
        unified = {"email_addr": {"type": "str", "sample_values": ["a@b.com"]}}
        result = await map_fields_impl(
            "company", unified, dest_schema, family_schemas=family_schemas
        )
        mapping = result["field_mapping"]["email_addr"]
        assert mapping["dest_column"] == "email"
        assert mapping["dest_table"] == "contact_email"

    @pytest.mark.asyncio
    async def test_no_family_schemas_behaves_exactly_as_before(
        self, dest_schema: dict[str, Any]
    ) -> None:
        unified = {"name": {"type": "str", "sample_values": ["Acme"]}}
        result = await map_fields_impl("company", unified, dest_schema)
        mapping = result["field_mapping"]["name"]
        assert mapping["dest_column"] == "name"
        assert mapping["dest_table"] == "company"

    @pytest.mark.asyncio
    async def test_primary_table_preferred_on_a_scoring_tie(self) -> None:
        # Both the primary and a family table have an "amount" column with
        # identical type -- the primary-table match must win, since
        # cross-table is the fallback, not the default.
        dest = {
            "table": "company",
            "columns": [
                {"name": "id", "type": "TEXT"},
                {"name": "amount", "type": "DOUBLE PRECISION"},
            ],
        }
        family = [
            {
                "table": "finance",
                "columns": [
                    {"name": "id", "type": "TEXT"},
                    {"name": "amount", "type": "DOUBLE PRECISION"},
                ],
            }
        ]
        unified = {"amount": {"type": "float", "sample_values": [1.0]}}
        result = await map_fields_impl("company", unified, dest, family_schemas=family)
        mapping = result["field_mapping"]["amount"]
        assert mapping["dest_table"] == "company"

    @pytest.mark.asyncio
    async def test_reserved_columns_excluded_from_family_tables_too(
        self, dest_schema: dict[str, Any]
    ) -> None:
        family = [
            {
                "table": "finance",
                "columns": [
                    {"name": "id", "type": "TEXT"},
                    {"name": "_source_ids", "type": "TEXT"},
                ],
            }
        ]
        # A field that would otherwise substring-match "id" must not land on
        # finance.id -- the reserved-column guard applies per family table.
        unified = {"financeid": {"type": "str", "sample_values": ["xyz"]}}
        result = await map_fields_impl("company", unified, dest_schema, family_schemas=family)
        assert "financeid" not in result["field_mapping"]

    @pytest.mark.asyncio
    async def test_is_coalesced_keyed_by_table_not_bare_column_name(self) -> None:
        # "amount" exists on both `company` and `finance`. Two DIFFERENT
        # source fields land on the two DIFFERENT tables' "amount" columns --
        # that must not be flagged as coalesced just because the bare column
        # name matches; coalescing means sharing one destination cell.
        dest = {
            "table": "company",
            "columns": [
                {"name": "id", "type": "TEXT"},
                {"name": "amount", "type": "DOUBLE PRECISION"},
            ],
        }
        family = [
            {
                "table": "finance",
                "columns": [
                    {"name": "id", "type": "TEXT"},
                    {"name": "amount", "type": "DOUBLE PRECISION"},
                ],
            }
        ]
        unified = {
            "amount": {"type": "float", "sample_values": [1.0]},
            "totalInvestment": {"type": "float", "sample_values": [2.0]},
        }
        result = await map_fields_impl("company", unified, dest, family_schemas=family)
        # Asserted BEFORE the loop below on purpose: a regression that drops
        # every field to unmapped_fields would leave field_mapping empty,
        # and a loop body that never executes still "passes" a bare
        # `for ... : assert` -- the vacuous-test failure mode this guards
        # against. "amount" exact-matches company.amount; totalInvestment
        # does NOT clear the heuristic tiers' confidence threshold against
        # finance.amount without an LLM call (none is configured here), so
        # it correctly falls to unmapped_fields rather than a guessed match.
        assert set(result["field_mapping"]) == {"amount"}
        assert result["unmapped_fields"][0]["field"] == "totalInvestment"
        # Neither mapped entry should claim is_coalesced against another
        # table's same-named column just because the bare name matches.
        for entry in result["field_mapping"].values():
            assert entry["is_coalesced"] is False


class TestLLMCrossTableMatching:
    @pytest.mark.asyncio
    async def test_llm_maps_field_to_qualified_family_column(
        self, dest_schema: dict[str, Any], family_schemas: list[dict[str, Any]]
    ) -> None:
        unified = {"zzqx_amount": {"type": "float", "sample_values": [4_200_000.0]}}
        client = _mock_llm(
            return_value={"zzqx_amount": {"action": "map", "dest_column": "finance.amount"}}
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
                family_schemas=family_schemas,
            )

        mapping = result["field_mapping"]["zzqx_amount"]
        assert mapping["dest_column"] == "amount"
        assert mapping["dest_table"] == "finance"
        assert mapping["mapped_by"] == "llm"

        # The prompt must actually offer the family table for this to be a
        # real decision, not a lucky guess.
        prompt = client.call_json.call_args.kwargs["user_prompt"]
        assert "finance.amount" in prompt

    @pytest.mark.asyncio
    async def test_llm_bare_column_name_resolves_to_primary_table(
        self, dest_schema: dict[str, Any], family_schemas: list[dict[str, Any]]
    ) -> None:
        unified = {"org_title": {"type": "str", "sample_values": ["Acme Corp"]}}
        client = _mock_llm(return_value={"org_title": {"action": "map", "dest_column": "name"}})
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
                family_schemas=family_schemas,
            )

        mapping = result["field_mapping"]["org_title"]
        assert mapping["dest_column"] == "name"
        assert mapping["dest_table"] == "company"

    @pytest.mark.asyncio
    async def test_llm_qualified_name_for_nonexistent_family_column_is_rejected(
        self, dest_schema: dict[str, Any], family_schemas: list[dict[str, Any]]
    ) -> None:
        unified = {"zzqx_field": {"type": "str", "sample_values": ["x"]}}
        client = _mock_llm(
            return_value={"zzqx_field": {"action": "map", "dest_column": "finance.nonexistent"}}
        )
        with patch("src.llm.LLMClient", return_value=client):
            result = await map_fields_impl(
                "company",
                unified,
                dest_schema,
                llm_config=_LLM_CONFIG,
                family_schemas=family_schemas,
            )

        assert "zzqx_field" not in result["field_mapping"]
        assert [u["field"] for u in result["unmapped_fields"]] == ["zzqx_field"]
