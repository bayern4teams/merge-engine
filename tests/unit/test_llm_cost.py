"""LLM cost controls — Phase O.5.

The cache's whole value depends on it never changing an answer, so most of
these tests are about what must NOT collide: a different model, a different
temperature, a different response format, a different question.
"""

from __future__ import annotations

import pytest

from src.llm.cost import (
    CostLedger,
    SemanticCache,
    cache_key,
    normalise_prompt,
    token_usage,
)


def key(**overrides) -> str:
    base = {
        "model": "qwen3.6-flash",
        "temperature": 0.2,
        "system_prompt": "You judge whether two records match.",
        "user_prompt": "A: Rory Fox\nB: Rory Fox",
        "response_format": None,
        "json_mode": False,
    }
    base.update(overrides)
    return cache_key(**base)


class TestKeyNormalisation:
    def test_whitespace_differences_share_a_key(self) -> None:
        """The same pair asked twice must hit, not miss on formatting."""
        assert key(user_prompt="A: Rory  Fox") == key(user_prompt="A: Rory Fox")

    def test_leading_and_trailing_space_is_ignored(self) -> None:
        assert key(user_prompt="  A: Rory Fox\nB: Rory Fox  ") == key()

    def test_newline_versus_space_is_ignored(self) -> None:
        assert normalise_prompt("a\n b") == normalise_prompt("a b")

    def test_a_different_question_gets_a_different_key(self) -> None:
        assert key(user_prompt="A: Rory Fox\nB: Jane Doe") != key()

    def test_a_different_model_never_shares_a_cached_answer(self) -> None:
        assert key(model="qwen3.6-plus") != key()

    def test_a_different_temperature_never_shares_an_answer(self) -> None:
        assert key(temperature=0.9) != key()

    def test_a_different_system_prompt_never_shares_an_answer(self) -> None:
        assert key(system_prompt="You extract fields.") != key()

    def test_structured_output_never_shares_with_freeform(self) -> None:
        """Same prompt, different output shape — serving one for the other
        would hand the caller the wrong type."""
        assert key(response_format={"type": "object"}) != key()

    def test_json_mode_never_shares_with_freeform(self) -> None:
        assert key(json_mode=True) != key()


class TestSemanticCache:
    def test_miss_then_hit(self) -> None:
        cache = SemanticCache()
        assert cache.get("k") == (False, None)
        cache.put("k", "answer")
        assert cache.get("k") == (True, "answer")

    def test_a_cached_none_is_distinguishable_from_a_miss(self) -> None:
        cache = SemanticCache()
        cache.put("k", None)
        assert cache.get("k") == (True, None)

    def test_it_is_bounded(self) -> None:
        cache = SemanticCache(max_entries=10)
        for i in range(50):
            cache.put(f"k{i}", i)
        assert cache.size == 10

    def test_eviction_is_least_recently_used(self) -> None:
        cache = SemanticCache(max_entries=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")            # 'a' is now the most recent
        cache.put("c", 3)         # evicts 'b'
        assert cache.get("a")[0] is True
        assert cache.get("b")[0] is False

    def test_stats_report_the_hit_rate(self) -> None:
        cache = SemanticCache()
        cache.put("k", 1)
        cache.get("k")
        cache.get("missing")
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_clear_resets_counters_too(self) -> None:
        cache = SemanticCache()
        cache.put("k", 1)
        cache.get("k")
        cache.clear()
        assert cache.stats() == {
            "hits": 0, "misses": 0, "lookups": 0,
            "hit_rate": 0.0, "entries": 0,
        }

    def test_it_survives_concurrent_use(self) -> None:
        """The graph runs on a background thread while the API reads status."""
        import threading

        cache = SemanticCache(max_entries=500)
        errors: list[Exception] = []

        def hammer(offset: int) -> None:
            try:
                for i in range(200):
                    cache.put(f"k{offset}-{i}", i)
                    cache.get(f"k{offset}-{i}")
            except Exception as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert cache.size <= 500


class TestCostLedger:
    def test_an_unknown_run_reports_zeroes_not_an_error(self) -> None:
        assert CostLedger().for_run("nope")["calls"] == 0

    def test_tokens_accumulate_per_run(self) -> None:
        ledger = CostLedger()
        ledger.record("run1", model="m", input_tokens=100, output_tokens=20)
        ledger.record("run1", model="m", input_tokens=50, output_tokens=10)
        entry = ledger.for_run("run1")
        assert entry["calls"] == 2
        assert entry["input_tokens"] == 150
        assert entry["total_tokens"] == 180

    def test_runs_are_kept_separate(self) -> None:
        ledger = CostLedger()
        ledger.record("a", input_tokens=10)
        ledger.record("b", input_tokens=99)
        assert ledger.for_run("a")["input_tokens"] == 10

    def test_a_cache_hit_costs_nothing_but_records_the_saving(self) -> None:
        ledger = CostLedger()
        ledger.record("r", input_tokens=400, cached=True)
        entry = ledger.for_run("r")
        assert entry["input_tokens"] == 0, "a cache hit must not bill tokens"
        assert entry["tokens_saved"] == 400
        assert entry["cache_hit_rate"] == 1.0

    def test_the_model_mix_is_recorded(self) -> None:
        """The fallback chain switches models mid-run; the report should say so."""
        ledger = CostLedger()
        ledger.record("r", model="flash")
        ledger.record("r", model="plus")
        ledger.record("r", model="flash")
        assert ledger.for_run("r")["models"] == {"flash": 2, "plus": 1}

    def test_reset_clears_one_run(self) -> None:
        ledger = CostLedger()
        ledger.record("a", input_tokens=1)
        ledger.record("b", input_tokens=1)
        ledger.reset("a")
        assert ledger.for_run("a")["calls"] == 0
        assert ledger.for_run("b")["calls"] == 1


class TestTokenUsageExtraction:
    def test_usage_metadata_is_read(self) -> None:
        class R:
            usage_metadata = {"input_tokens": 12, "output_tokens": 3}

        assert token_usage(R()) == (12, 3)

    def test_openai_style_names_are_read(self) -> None:
        class R:
            response_metadata = {
                "token_usage": {"prompt_tokens": 7, "completion_tokens": 2}}

        assert token_usage(R()) == (7, 2)

    def test_a_provider_that_reports_nothing_yields_zero_not_a_guess(
        self,
    ) -> None:
        class R:
            response_metadata: dict = {}

        assert token_usage(R()) == (0, 0)

    def test_a_response_without_metadata_does_not_raise(self) -> None:
        assert token_usage(object()) == (0, 0)
