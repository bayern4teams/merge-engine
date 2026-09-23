"""Unit tests for map_fields' Phase 0 (Valentine wiring behind existing
safety gates), fix-v3 B.3.1 — the audit's §14.2 headline finding.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.mcp_server.tools.map_fields import map_fields_impl


class _FakeConnector:
    """Duck-typed like PostgresConnector/PostgreSQLDestinationConnector's
    own sample_distinct_values — returns canned values per (table, column)."""

    def __init__(self, values_by_column: dict[str, list[Any]]) -> None:
        self.values_by_column = values_by_column
        self.calls: list[tuple[str, str]] = []

    async def sample_distinct_values(
        self, table_name: str, column: str, limit: int = 500
    ) -> list[Any]:
        self.calls.append((table_name, column))
        return self.values_by_column.get(column, [])


_URLS = [
    "https://boeing.com",
    "https://airbus.com",
    "https://lockheedmartin.com",
    "https://raytheon.com",
]


class TestValentinePhase0:
    @pytest.mark.asyncio
    async def test_zero_name_overlap_field_unmapped_without_connector(self):
        # Baseline: without Phase 0, a field with zero name overlap and no
        # heuristic string similarity to any destination column has nothing
        # to accept it on — this is today's (pre-fix) behaviour.
        unified_schema = {"homepage": {"type": "STRING", "sample_values": _URLS}}
        dest_schema = {"table": "company", "columns": [{"name": "website", "type": "STRING"}]}

        result = await map_fields_impl("company", unified_schema, dest_schema, connector=None)
        assert "homepage" not in result["field_mapping"]

    @pytest.mark.asyncio
    async def test_zero_name_overlap_field_mapped_via_value_overlap_with_connector(self):
        unified_schema = {"homepage": {"type": "STRING", "sample_values": _URLS}}
        dest_schema = {"table": "company", "columns": [{"name": "website", "type": "STRING"}]}
        connector = _FakeConnector({"website": _URLS})

        result = await map_fields_impl("company", unified_schema, dest_schema, connector=connector)
        assert result["field_mapping"]["homepage"]["dest_column"] == "website"
        assert result["field_mapping"]["homepage"]["dest_table"] == "company"
        assert result["field_mapping"]["homepage"]["mapped_by"] == "valentine"
        assert connector.calls == [("company", "website")]

    @pytest.mark.asyncio
    async def test_incompatible_valentine_top_pick_falls_through_not_bypassed(self):
        # Perfect value overlap (Valentine would score this 1.0) but the
        # source values are not coercible to the destination column's
        # declared type — _incompatible_with_column must still refuse it,
        # exactly as it already does for Phase 1/2's heuristic tiers.
        bad_values = ["Q1-2020", "Q2-2020"]
        unified_schema = {"revenue_code": {"type": "STRING", "sample_values": bad_values}}
        dest_schema = {"table": "finance", "columns": [{"name": "amount", "type": "INTEGER"}]}
        connector = _FakeConnector({"amount": bad_values})

        result = await map_fields_impl("finance", unified_schema, dest_schema, connector=connector)
        assert "revenue_code" not in result["field_mapping"]

    @pytest.mark.asyncio
    async def test_empty_destination_column_degrades_without_crash(self):
        unified_schema = {"homepage": {"type": "STRING", "sample_values": _URLS}}
        dest_schema = {"table": "company", "columns": [{"name": "website", "type": "STRING"}]}
        connector = _FakeConnector({"website": []})

        # Must not raise.
        result = await map_fields_impl("company", unified_schema, dest_schema, connector=connector)
        assert "homepage" not in result["field_mapping"] or (
            result["field_mapping"]["homepage"].get("mapped_by") != "valentine"
        )
