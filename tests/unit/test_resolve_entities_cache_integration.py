"""Integration test for Part 1.5 D.7 at the resolve_entities_impl level: a
second call with identical records must skip blocking/scoring/clustering
entirely, and must still assign the CURRENT call's known_cluster_ids rather
than replaying whatever the first call happened to produce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools.resolve_entities import resolve_entities_impl


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolve_cache, "CACHE_PATH", tmp_path / "resolve_cache_test.duckdb")
    monkeypatch.delenv("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", raising=False)


def _records() -> list[dict[str, Any]]:
    # >1 record and clearly-matching names so a real (non-trivial) cluster
    # forms — the empty/singleton-record early-returns in
    # resolve_entities_impl bypass the cache path entirely and would not
    # exercise this test.
    return [
        {
            "_origin_id": "o1",
            "_origin_table": "companies",
            "_origin_id_row": "1",
            "name": "Boeing Company",
            "website": "boeing.com",
        },
        {
            "_origin_id": "o2",
            "_origin_table": "orgs",
            "_origin_id_row": "9",
            "name": "Boeing Co",
            "website": "boeing.com",
        },
    ]


class TestResolveEntitiesCacheIntegration:
    async def test_second_call_skips_generate_candidate_pairs(self) -> None:
        records = _records()

        with patch(
            "src.mcp_server.tools.resolve_entities._generate_candidate_pairs",
            wraps=__import__(
                "src.mcp_server.tools.resolve_entities", fromlist=["_generate_candidate_pairs"]
            )._generate_candidate_pairs,
        ) as spy:
            await resolve_entities_impl("company", records, llm_config=None)
            assert spy.call_count == 1

            await resolve_entities_impl("company", records, llm_config=None)
            # Cache hit -- the second call never re-runs blocking at all.
            assert spy.call_count == 1

    async def test_cluster_id_reflects_the_calling_runs_known_cluster_ids(self) -> None:
        records = _records()

        first = await resolve_entities_impl("company", records, llm_config=None)
        assert len(first["clusters"]) == 1  # sanity: they matched into one cluster
        first_id = first["clusters"][0]["cluster_id"]

        # Second call: same records (cache HIT), but a destination mapping
        # that already assigned one member to a real row id from a prior
        # load. The returned cluster_id must reflect THIS known_cluster_ids,
        # not whatever the first, cache-populating call produced.
        known = {"o1:companies:1": "dest-row-boeing"}
        second = await resolve_entities_impl(
            "company", records, llm_config=None, known_cluster_ids=known
        )
        assert len(second["clusters"]) == 1
        assert second["clusters"][0]["cluster_id"] == "dest-row-boeing"
        assert second["clusters"][0]["cluster_id"] != first_id
        # Everything else about the match decision came from the cache,
        # unchanged.
        assert second["clusters"][0]["record_ids"] == first["clusters"][0]["record_ids"]
        assert second["auto_matched"] == first["auto_matched"]

    async def test_different_records_are_not_served_a_stale_cache_entry(self) -> None:
        # content_hash's sensitivity to llm_requested/matching_rules/subject
        # is unit-tested directly in test_resolve_cache.py (no need to risk
        # a real LLM round-trip here); this covers the same "wrong cache
        # entry never served" property via the simplest safe lever: record
        # content itself.
        records = _records()
        await resolve_entities_impl("company", records, llm_config=None)

        changed = _records()
        changed[1]["name"] = "Something Completely Different"
        changed[1]["website"] = "not-boeing.com"

        with patch(
            "src.mcp_server.tools.resolve_entities._generate_candidate_pairs",
            wraps=__import__(
                "src.mcp_server.tools.resolve_entities", fromlist=["_generate_candidate_pairs"]
            )._generate_candidate_pairs,
        ) as spy:
            result = await resolve_entities_impl("company", changed, llm_config=None)
            assert spy.call_count == 1  # recomputed, not served the prior entry
        # And the two records must NOT have been folded into one cluster --
        # proof this was a real, fresh computation on the new content.
        assert len(result["clusters"]) == 2
