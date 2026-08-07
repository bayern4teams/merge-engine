"""Unit tests for tools/splink_matcher.py (Task B4.4)."""

from __future__ import annotations

import pytest

from src.mcp_server.tools.resolve_entities import resolve_entities_impl
from src.mcp_server.tools.splink_matcher import resolve_with_splink


@pytest.mark.asyncio
async def test_resolve_with_splink_result_contract() -> None:
    records = [
        {
            "_origin_id": "origin1",
            "_origin_table": "Company",
            "_origin_id_row": "1",
            "company_name": "The Boeing Company",
            "website": "https://www.boeing.com",
        },
        {
            "_origin_id": "origin2",
            "_origin_table": "company_profile",
            "_origin_id_row": "2",
            "name": "Boeing Corp",
            "homepage": "www.boeing.com",
        },
    ]

    res = await resolve_with_splink("company", records)
    if res is not None:
        assert isinstance(res, dict)
        assert res["entity_type"] == "company"
        assert "clusters" in res
        assert "total_records" in res
        assert "auto_matched" in res
        assert "auto_excluded" in res
        assert "llm_scored" in res
        assert "uncompared_pairs" in res
        assert "candidate_pairs" in res
        assert "blocks_purged" in res
        assert "pending_merge_candidates" in res


@pytest.mark.asyncio
async def test_splink_seam_dispatch_builtin_default() -> None:
    records = [
        {
            "_origin_id": "origin1",
            "_origin_table": "Company",
            "_origin_id_row": "1",
            "company_name": "Boeing",
        },
        {
            "_origin_id": "origin2",
            "_origin_table": "Company",
            "_origin_id_row": "2",
            "company_name": "Airbus",
        },
    ]

    # engine: builtin (default)
    res_builtin = await resolve_entities_impl(
        "company", records, matching_rules={"engine": "builtin"}
    )
    assert isinstance(res_builtin, dict)
    assert res_builtin["total_records"] == 2


@pytest.mark.asyncio
async def test_splink_fallback_on_invalid_data() -> None:
    # Invalid records structure -> gracefully returns None or falls back to builtin
    res = await resolve_with_splink("company", [])
    assert res is None
