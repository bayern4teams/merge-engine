"""Unit tests for resolve_entities MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.resolve_entities import (
    _compute_heuristic_confidence,
    _generate_candidate_pairs,
    _record_id,
    resolve_entities_impl,
)


class TestResolveEntitiesImpl:
    """Tests for resolve_entities_impl."""

    @pytest.fixture
    def sample_records(self) -> list[dict[str, Any]]:
        """Sample records from multiple origins."""
        return [
            {
                "_origin_id": "postgres_main",
                "_origin_table": "users",
                "_origin_id_row": "1",
                "name": "Alice Smith",
                "email": "alice@example.com",
            },
            {
                "_origin_id": "mongo_analytics",
                "_origin_table": "customers",
                "_origin_id_row": "2",
                "name": "Alice S.",
                "email": "alice@example.com",
            },
        ]

    @pytest.mark.asyncio
    async def test_resolve_entities_empty_records(self) -> None:
        """Test resolving with empty records."""
        result = await resolve_entities_impl("person", [])

        assert result["entity_type"] == "person"
        assert result["clusters"] == []
        assert result["total_records"] == 0
        assert result["auto_matched"] == 0
        assert result["auto_excluded"] == 0
        assert result["llm_scored"] == 0

    @pytest.mark.asyncio
    async def test_resolve_entities_single_record(self) -> None:
        """Test resolving with a single record."""
        records = [
            {
                "_origin_id": "postgres_main",
                "_origin_table": "users",
                "_origin_id_row": "1",
                "name": "Alice",
            },
        ]
        result = await resolve_entities_impl("person", records)

        assert result["total_records"] == 1
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["match_confidence"] == 1.0
        assert result["auto_matched"] == 1
        assert result["llm_scored"] == 0

    @pytest.mark.asyncio
    async def test_resolve_entities_multiple_records(
        self, sample_records: list[dict[str, Any]]
    ) -> None:
        """Test resolving with multiple records."""
        result = await resolve_entities_impl("person", sample_records)

        assert result["total_records"] == 2
        assert len(result["clusters"]) >= 1
        assert result["llm_scored"] >= 0

    @pytest.mark.asyncio
    async def test_resolve_entities_entity_type_in_output(
        self, sample_records: list[dict[str, Any]]
    ) -> None:
        """Test that entity_type is included in result."""
        result = await resolve_entities_impl("company", sample_records)

        assert result["entity_type"] == "company"

    @pytest.mark.asyncio
    async def test_scoring_loop_yields_control_on_large_pair_counts(self) -> None:
        """A live Boeing run hit ~35k records (up to the 250k-pair scoring
        cap) in one synchronous stretch, and the app stopped answering HTTP
        status polls long enough that the client's requests timed out —
        periodic `await asyncio.sleep(0)` calls give the event loop (and
        other threads sharing the GIL) a chance to run during a long pass."""
        records = [
            {
                "_origin_id": "o1",
                "_origin_table": "t",
                "_origin_id_row": str(i),
                "name": "Alice Smith",
                "email": f"alice{i}@example.com",
            }
            for i in range(50)  # same-name block -> C(50,2) = 1225 pairs
        ]
        with patch(
            "src.mcp_server.tools.resolve_entities.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            result = await resolve_entities_impl("person", records)

        assert result["total_records"] == 50
        mock_sleep.assert_awaited()

    @pytest.mark.asyncio
    async def test_scoring_loop_does_not_yield_for_small_pair_counts(self) -> None:
        """No pointless overhead below the yield threshold."""
        records = [
            {
                "_origin_id": "o1",
                "_origin_table": "t",
                "_origin_id_row": str(i),
                "name": "Bob Jones",
                "email": f"bob{i}@example.com",
            }
            for i in range(5)  # C(5,2) = 10 pairs, well under the threshold
        ]
        with patch(
            "src.mcp_server.tools.resolve_entities.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await resolve_entities_impl("person", records)

        mock_sleep.assert_not_awaited()


class TestComputeHeuristicConfidence:
    """Tests for _compute_heuristic_confidence helper."""

    def test_identical_values(self) -> None:
        """Test identical values return perfect score."""
        rec1 = {"name": "Alice", "email": "alice@example.com"}
        rec2 = {"name": "Alice", "email": "alice@example.com"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == 1.0

    def test_different_values(self) -> None:
        """Test different values return low score."""
        rec1 = {"name": "Alice", "email": "alice@example.com"}
        rec2 = {"name": "Bob", "email": "bob@example.com"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf < 0.5

    def test_none_values_ignored(self) -> None:
        """Test that None values are ignored in comparison."""
        rec1 = {"name": "Alice", "email": None}
        rec2 = {"name": "Alice", "email": "alice@example.com"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == 1.0  # Only name matches, and it's identical

    def test_no_common_fields(self) -> None:
        """Test records with no common fields return 0."""
        rec1 = {"name": "Alice", "department": "Engineering"}
        rec2 = {"age": 30, "location": "NYC"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == 0.0

    def test_partial_match(self) -> None:
        """Partial field matches average, but exact-name evidence dominates."""
        rec1 = {"name": "Alice", "email": "alice@example.com", "phone": "555-1234"}
        rec2 = {"name": "Alice", "email": "alice@example.com", "phone": "555-5678"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        # Field average is 2/3, but the exact name match lifts it to 0.96
        assert conf == 0.96

    def test_partial_match_differing_names(self) -> None:
        """Without name agreement, the common-field average is the score."""
        rec1 = {"label": "aa", "email": "alice@example.com", "phone": "555-1234"}
        rec2 = {"label": "bb", "email": "alice@example.com", "phone": "555-5678"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == pytest.approx(1.0 / 3.0)

    def test_cross_origin_name_fields_exact(self) -> None:
        """Same name under different field names scores just above auto-match."""
        rec1 = {"company_name": "Titan Labs", "industry": "biotech"}
        rec2 = {"org_title": "Titan Labs", "sector": "life sciences"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert conf == 0.96

    def test_cross_origin_name_fields_fuzzy_capped(self) -> None:
        """Fuzzy cross-field name similarity stays below the auto-match band."""
        rec1 = {"company_name": "Titan Labs Incorporated"}
        rec2 = {"org_title": "Titan Labs Inc"}
        conf = _compute_heuristic_confidence(rec1, rec2)
        assert 0.5 < conf <= 0.93

    def test_string_similarity(self) -> None:
        """Test string similarity matching."""
        rec1 = {"name": "Alice Smith"}
        rec2 = {"name": "Alice S."}
        conf = _compute_heuristic_confidence(rec1, rec2)
        # Should have some similarity score
        assert conf >= 0.0


class TestRecordId:
    """Tests for _record_id helper."""

    def test_record_id_with_standard_fields(self) -> None:
        """Test record ID extraction with standard _ prefixed fields."""
        rec = {
            "_origin_id": "postgres_main",
            "_origin_table": "users",
            "_origin_id_row": "123",
        }
        assert _record_id(rec) == "postgres_main:users:123"

    def test_record_id_with_fallback_fields(self) -> None:
        """Test record ID falls back to non-prefixed fields."""
        rec = {
            "origin_id": "mongo_analytics",
            "table": "customers",
            "id": "456",
        }
        assert _record_id(rec) == "mongo_analytics:customers:456"

    def test_record_id_with_object_identity_fallback(self) -> None:
        """Test record ID uses id() as last resort."""
        rec = {"name": "Alice"}
        rid = _record_id(rec)
        assert rid.startswith("unknown:unknown:")


class TestGenerateCandidatePairs:
    """Tests for _generate_candidate_pairs helper."""

    def test_no_pairs_single_record(self) -> None:
        """Test no pairs generated for single record."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 0

    def test_pairs_from_two_records(self) -> None:
        """Test one pair from two records."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
            {"_origin_id": "b", "_origin_table": "t", "_origin_id_row": "2"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 1

    def test_pairs_from_three_records(self) -> None:
        """Test three pairs from three records (n*(n-1)/2)."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
            {"_origin_id": "b", "_origin_table": "t", "_origin_id_row": "2"},
            {"_origin_id": "c", "_origin_table": "t", "_origin_id_row": "3"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 3  # 3 choose 2

    def test_pairs_have_correct_format(self) -> None:
        """Test pair format is (id1, id2, rec1, rec2)."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1", "name": "Alice Smith"},
            {"_origin_id": "b", "_origin_table": "t", "_origin_id_row": "2", "name": "Alice Smyth"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 1
        id1, id2, rec1, rec2 = pairs[0]
        assert id1 == "a:t:1"
        assert id2 == "b:t:2"
        assert rec1["name"] == "Alice Smith"
        assert rec2["name"] == "Alice Smyth"

    def test_blocking_skips_unrelated_names(self) -> None:
        """Records whose name-ish field shares no first token are not compared."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1", "name": "Alice Smith"},
            {"_origin_id": "b", "_origin_table": "t", "_origin_id_row": "2", "name": "Bob Jones"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 0

    def test_explicit_blocking_keys_used(self) -> None:
        """Explicit blocking_keys override the auto-detected name field."""
        records = [
            {
                "_origin_id": "a",
                "_origin_table": "t",
                "_origin_id_row": "1",
                "name": "Alice Smith",
                "city": "Berlin",
            },
            {
                "_origin_id": "b",
                "_origin_table": "t",
                "_origin_id_row": "2",
                "name": "Bob Jones",
                "city": "Berlin",
            },
        ]
        pairs = _generate_candidate_pairs(records, blocking_keys=["city"])
        assert len(pairs) == 1

    def test_max_comparisons_enforced(self) -> None:
        """Pairs are truncated at _MAX_COMPARISONS."""
        from src.mcp_server.tools import resolve_entities as mod

        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": str(i), "name": "Acme Corp"}
            for i in range(30)
        ]
        with patch.object(mod, "_MAX_COMPARISONS", 5):
            pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 5

    def test_a_large_block_past_budget_is_not_materialized(self) -> None:
        """The real bug this guards against: a single never-purged 'full:'
        exact-match block with thousands of records used to be fully expanded
        into O(n^2) (id, id, dict, dict) tuples even once the comparison
        budget was already met from smaller blocks — on a live 34,782-row
        run this alone made one entity's blocking pass never finish. Once
        the budget is met, a later (larger) block must only be COUNTED, not
        expanded, while still reporting an honest uncompared-pairs estimate."""
        from src.mcp_server.tools import resolve_entities as mod

        # A small block (3 records, unique name) that alone meets a budget
        # of 3, sorted first because it is smaller...
        small_block = [
            {
                "_origin_id": "a",
                "_origin_table": "t",
                "_origin_id_row": f"s{i}",
                "name": "Small Block Co",
            }
            for i in range(3)
        ]
        # ...and a much larger block (1000 records, one shared exact name)
        # that must never be expanded once the budget above is already met.
        large_block = [
            {
                "_origin_id": "a",
                "_origin_table": "t",
                "_origin_id_row": f"l{i}",
                "name": "Big Block Corp",
            }
            for i in range(1000)
        ]
        records = small_block + large_block

        stats: dict[str, int] = {}
        with patch.object(mod, "_MAX_COMPARISONS", 3):
            pairs = _generate_candidate_pairs(records, stats=stats)

        assert len(pairs) == 3
        # The large block (1000 choose 2 = 499500 pairs) was only counted,
        # not built — reported as (at least) that much uncompared evidence.
        assert stats["uncompared_pairs"] >= 1000 * 999 // 2

    def test_skips_self_comparisons(self) -> None:
        """Test that identical record IDs are skipped."""
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 0  # Same ID should be skipped

    def test_cross_origin_name_fields_block_together(self) -> None:
        """Records with differently-named name fields still share a block."""
        records = [
            {
                "_origin_id": "pg",
                "_origin_table": "companies",
                "_origin_id_row": "1",
                "company_name": "Titan Labs",
            },
            {
                "_origin_id": "ch",
                "_origin_table": "organizations",
                "_origin_id_row": "2",
                "org_title": "Titan Labs",
            },
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 1

    def test_ref_field_blocking_pass(self) -> None:
        """Names differ but a shared ref-like field token pairs them anyway."""
        records = [
            {
                "_origin_id": "pg",
                "_origin_table": "companies",
                "_origin_id_row": "1",
                "company_name": "Alpha Systems",
                "match_test_ref": "BQXJ-102",
            },
            {
                "_origin_id": "ch",
                "_origin_table": "organizations",
                "_origin_id_row": "2",
                "org_title": "Zeta Holdings",
                "match_test_ref": "BQXJ-1029",
            },
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 1

    def test_oversized_ref_blocks_are_admitted_not_dropped(self) -> None:
        """BUG C / C1: a purge must degrade, never annihilate.

        A ref token shared by too many records is non-discriminative, but
        the old behaviour of dropping the whole block silently zeroed out
        candidate generation for real scoped-merge corpora (every record
        shares the subject token). It must still be admitted and left to
        the size-sorted comparison budget — scoring, not blocking, decides
        whether any of these pairs actually match.
        """
        records = [
            {
                "_origin_id": "a",
                "_origin_table": "t",
                "_origin_id_row": str(i),
                "name": f"Unique Name {i}",
                "ref_code": "SHARED-1",
            }
            for i in range(30)
        ]
        for i, rec in enumerate(records):
            rec["name"] = f"n{i}xx yy"
        stats: dict[str, int] = {}
        pairs = _generate_candidate_pairs(records, stats=stats)
        assert len(pairs) > 0
        assert stats["blocks_purged"] >= 1
        assert stats["uncompared_pairs"] == 0

    def test_no_duplicate_pairs_across_passes(self) -> None:
        """A pair matched by both passes is only generated once."""
        records = [
            {
                "_origin_id": "a",
                "_origin_table": "t",
                "_origin_id_row": "1",
                "name": "Acme Corp",
                "ref_code": "AC-1",
            },
            {
                "_origin_id": "b",
                "_origin_table": "t",
                "_origin_id_row": "2",
                "name": "Acme Inc",
                "ref_code": "AC-2",
            },
        ]
        pairs = _generate_candidate_pairs(records)
        assert len(pairs) == 1


class TestJudgePairsChunking:
    """Tests for the chunked, bounded-concurrency LLM judgment path."""

    @pytest.mark.asyncio
    async def test_chunks_all_judged_and_combined(self) -> None:
        from src.mcp_server.tools import resolve_entities as mod

        borderline = [
            (f"a:t:{i}", f"b:t:{i}", {"name": f"X {i}"}, {"name": f"X {i}b"}, 0.8)
            for i in range(10)
        ]

        calls: list[int] = []

        async def fake_call_json(system_prompt: str, user_prompt: str):
            n_pairs = user_prompt.count("Pair ")
            calls.append(n_pairs)
            return {
                "judgments": [
                    {"pair_id": n, "match": True, "confidence": 0.99} for n in range(1, n_pairs + 1)
                ]
            }

        fake_client = MagicMock()
        fake_client.call_json = AsyncMock(side_effect=fake_call_json)

        with (
            patch.object(mod, "_LLM_CHUNK_SIZE", 4),
            patch("src.llm.LLMClient", return_value=fake_client),
        ):
            results = await mod._judge_pairs_with_llm(
                "company",
                borderline,
                {"provider": "openai"},
            )

        assert len(calls) == 3  # 4 + 4 + 2
        assert len(results) == 10
        assert all(score == 0.99 for _i1, _i2, score in results)

    @pytest.mark.asyncio
    async def test_failed_chunk_keeps_heuristic_scores(self) -> None:
        from src.mcp_server.tools import resolve_entities as mod

        borderline = [
            (f"a:t:{i}", f"b:t:{i}", {"name": f"X {i}"}, {"name": f"X {i}b"}, 0.75)
            for i in range(4)
        ]

        call_count = {"n": 0}

        async def flaky_call_json(system_prompt: str, user_prompt: str):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("LLM down")
            n_pairs = user_prompt.count("Pair ")
            return {
                "judgments": [
                    {"pair_id": n, "match": False, "confidence": 0.9} for n in range(1, n_pairs + 1)
                ]
            }

        fake_client = MagicMock()
        fake_client.call_json = AsyncMock(side_effect=flaky_call_json)

        with (
            patch.object(mod, "_LLM_CHUNK_SIZE", 2),
            patch("src.llm.LLMClient", return_value=fake_client),
        ):
            results = await mod._judge_pairs_with_llm(
                "company",
                borderline,
                {"provider": "openai"},
            )

        assert len(results) == 4
        scores = sorted(score for _i1, _i2, score in results)
        # Failed chunk: heuristic 0.75 kept; judged chunk: non-match pushed to 0.1
        assert scores == [0.1, 0.1, 0.75, 0.75]
