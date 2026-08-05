"""N.3: the comparison budget, and the memoisation that made it affordable.

Every pair the budget excludes is evidence the matcher never saw. At the old
10,000-pair budget a 2,484-record merge dropped two thirds of its candidate
pairs, so two runs of the same data could disagree about how many people
there are. These tests pin the properties that fixed that: the comparators
are memoised (so scoring is cheap), the budget is high enough not to bind on
ordinary merges, and when it *does* bind it truncates the same way every time.
"""

from __future__ import annotations

import difflib
from typing import Any

import pytest

from src.mcp_server.tools.resolve_entities import (
    _MAX_COMPARISONS,
    _generate_candidate_pairs,
    _ordered,
    _seq_ratio,
    _string_similarity,
    _tokens_match,
)


def _rec(i: int, first: str, last: str, email: str | None = None) -> dict[str, Any]:
    rec = {
        "_origin_id": "pg",
        "_origin_table": "staff",
        "_origin_id_row": str(i),
        "first_name": first,
        "last_name": last,
    }
    if email:
        rec["email"] = email
    return rec


class TestMemoisationPreservesSemantics:
    """The cache must be invisible: same answers, just not recomputed.

    difflib was 90% of scoring time (33s of 37s on a 1,500-record merge).
    Caching is only acceptable because it cannot change a result.
    """

    @pytest.mark.parametrize(
        "a,b",
        [
            ("rory", "rori"),
            ("smith", "smyth"),
            ("acme corp", "acme corporation"),
            ("", "x"),
            ("jon", "john"),
            ("abc", "xyz"),
            ("elizabeth taylor", "taylor elizabeth"),
        ],
    )
    def test_seq_ratio_matches_difflib_exactly(self, a: str, b: str) -> None:
        assert _seq_ratio(a, b) == difflib.SequenceMatcher(None, a, b).ratio()

    def test_repeated_calls_return_the_same_value(self) -> None:
        first = _string_similarity("acme corp", "acme corporation")
        second = _string_similarity("acme corp", "acme corporation")

        assert first == second

    def test_similarity_is_symmetric(self) -> None:
        """The cache key is order-normalised, so this must hold."""
        assert _string_similarity("rory", "rori") == _string_similarity("rori", "rory")

    def test_identical_strings_short_circuit_to_one(self) -> None:
        assert _string_similarity("acme", "acme") == 1.0

    def test_token_match_is_symmetric(self) -> None:
        assert _tokens_match("corp", "corporation") == _tokens_match("corporation", "corp")

    def test_order_helper_is_stable(self) -> None:
        assert _ordered("b", "a") == ("a", "b") == _ordered("a", "b")

    def test_the_cache_is_actually_being_used(self) -> None:
        """A cache that never hits would leave the 2ms/pair cost in place."""
        _seq_ratio.cache_clear()
        for _ in range(50):
            _seq_ratio("jonathan", "johnathan")

        info = _seq_ratio.cache_info()
        assert info.hits >= 49
        assert info.misses == 1

    def test_the_cache_is_bounded(self) -> None:
        """An unbounded cache in a long-lived server is a slow memory leak."""
        assert _seq_ratio.cache_info().maxsize is not None
        assert _tokens_match.cache_info().maxsize is not None


class TestComparisonBudget:
    def test_budget_is_high_enough_for_ordinary_merges(self) -> None:
        """2,484 records generate ~28k pairs; the old 10k budget dropped 2/3."""
        assert _MAX_COMPARISONS >= 100_000

    def test_a_realistic_merge_compares_every_candidate_pair(self) -> None:
        records = [
            _rec(i, f"first{i % 40}", f"last{i % 37}", f"user{i}@example.com") for i in range(600)
        ]

        stats: dict[str, int] = {}
        _generate_candidate_pairs(records, stats=stats)

        assert stats["uncompared_pairs"] == 0

    def test_shortfall_is_reported_when_the_budget_does_bind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dropped pair must be counted, never silently discarded."""
        import src.mcp_server.tools.resolve_entities as mod

        monkeypatch.setattr(mod, "_MAX_COMPARISONS", 5)
        records = [_rec(i, "same", "name") for i in range(20)]

        stats: dict[str, int] = {}
        pairs = mod._generate_candidate_pairs(records, stats=stats)

        assert len(pairs) == 5
        assert stats["uncompared_pairs"] > 0


class TestDeterminism:
    """Identical input must produce identical output, budget or no budget."""

    def _records(self) -> list[dict[str, Any]]:
        return [_rec(i, f"first{i % 12}", f"last{i % 9}", f"u{i}@example.com") for i in range(120)]

    def test_pairs_are_identical_across_runs(self) -> None:
        first = [(a, b) for a, b, _r1, _r2 in _generate_candidate_pairs(self._records())]
        second = [(a, b) for a, b, _r1, _r2 in _generate_candidate_pairs(self._records())]

        assert first == second

    def test_truncation_picks_the_same_pairs_every_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Equal-sized blocks used to fall back on dict insertion order.

        That made a truncated run unreproducible: the same data could yield
        a different set of comparisons, and therefore a different row count.
        """
        import src.mcp_server.tools.resolve_entities as mod

        monkeypatch.setattr(mod, "_MAX_COMPARISONS", 25)
        first = [(a, b) for a, b, _1, _2 in mod._generate_candidate_pairs(self._records())]
        second = [(a, b) for a, b, _1, _2 in mod._generate_candidate_pairs(self._records())]

        assert len(first) == 25
        assert first == second
