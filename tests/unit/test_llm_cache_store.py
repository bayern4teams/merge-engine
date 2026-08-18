"""Unit tests for the persistent (SQLite-backed) LLM cache — Phase A5.

cost.SemanticCache (the in-process LRU) dies with the process; this is the
durable second layer underneath it, tested independently of LLMClient here.
"""

from __future__ import annotations

import time

from src.llm.cache_store import CACHE_SCHEMA_VERSION, PersistentLLMCache


def _cache(tmp_path, **kwargs) -> PersistentLLMCache:
    return PersistentLLMCache(tmp_path / "cache.sqlite3", **kwargs)


class TestGetPutRoundTrip:
    def test_miss_on_empty_cache(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        found, value = cache.get("nope")
        assert found is False
        assert value is None

    def test_put_then_get_returns_the_value(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        cache.put("k1", {"answer": 42})
        found, value = cache.get("k1")
        assert found is True
        assert value == {"answer": 42}

    def test_string_values_round_trip_exactly(self, tmp_path) -> None:
        """LLMClient caches response.content, which is always a raw str."""
        cache = _cache(tmp_path)
        cache.put("k1", '{"group_names": {"company": "Company"}}')
        found, value = cache.get("k1")
        assert found is True
        assert value == '{"group_names": {"company": "Company"}}'

    def test_put_overwrites_an_existing_key(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        cache.put("k1", "first")
        cache.put("k1", "second")
        _, value = cache.get("k1")
        assert value == "second"

    def test_survives_a_fresh_instance_over_the_same_path(self, tmp_path) -> None:
        """The actual point of the feature: a new process (a fresh
        PersistentLLMCache, standing in for a restarted `python -m
        src.main`) sees what an earlier instance wrote."""
        db_path = tmp_path / "cache.sqlite3"
        PersistentLLMCache(db_path).put("k1", "cached-answer")

        second = PersistentLLMCache(db_path)
        found, value = second.get("k1")
        assert found is True
        assert value == "cached-answer"


class TestTTL:
    def test_an_expired_entry_misses(self, tmp_path) -> None:
        cache = _cache(tmp_path, ttl_seconds=1)
        cache.put("k1", "value")
        time.sleep(1.1)
        found, value = cache.get("k1")
        assert found is False
        assert value is None

    def test_zero_ttl_never_expires(self, tmp_path) -> None:
        cache = _cache(tmp_path, ttl_seconds=0)
        cache.put("k1", "value")
        time.sleep(0.05)
        found, _ = cache.get("k1")
        assert found is True


class TestMaxRowsEviction:
    def test_oldest_entries_are_evicted_first(self, tmp_path) -> None:
        cache = _cache(tmp_path, max_rows=3)
        cache.put("k1", "v1")
        time.sleep(0.01)
        cache.put("k2", "v2")
        time.sleep(0.01)
        cache.put("k3", "v3")
        time.sleep(0.01)
        cache.put("k4", "v4")  # pushes total to 4 -> evicts the oldest (k1)

        assert cache.get("k1") == (False, None)
        assert cache.get("k2")[0] is True
        assert cache.get("k3")[0] is True
        assert cache.get("k4")[0] is True

    def test_stays_within_capacity_across_many_writes(self, tmp_path) -> None:
        cache = _cache(tmp_path, max_rows=5)
        for i in range(20):
            cache.put(f"k{i}", f"v{i}")
        assert cache.stats()["entries"] <= 5


class TestStatsAndClear:
    def test_hits_and_misses_are_tracked(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        cache.put("k1", "v1")
        cache.get("k1")  # hit
        cache.get("k1")  # hit
        cache.get("nope")  # miss
        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["entries"] == 1

    def test_clear_removes_everything_and_resets_counters(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        cache.put("k1", "v1")
        cache.get("k1")
        cache.clear()
        assert cache.stats() == {
            "hits": 0,
            "misses": 0,
            "lookups": 0,
            "hit_rate": 0.0,
            "entries": 0,
        }
        assert cache.get("k1") == (False, None)


class TestConfigure:
    """LLMClient.__init__ calls this with config.yaml's persistent_cache_*
    values on every construction -- must be idempotent and take effect
    immediately for subsequent get()/put() calls."""

    def test_updates_ttl_and_max_rows_in_place(self, tmp_path) -> None:
        cache = _cache(tmp_path, ttl_seconds=999999, max_rows=999)
        cache.configure(ttl_days=0, max_rows=2)
        cache.put("k1", "v1")
        time.sleep(0.01)
        cache.put("k2", "v2")
        time.sleep(0.01)
        cache.put("k3", "v3")  # over the new max_rows=2 -> evicts k1
        assert cache.get("k1") == (False, None)
        assert cache.stats()["entries"] == 2

    def test_ttl_days_of_zero_means_never_expire(self, tmp_path) -> None:
        cache = _cache(tmp_path, ttl_seconds=1)
        cache.configure(ttl_days=0)
        cache.put("k1", "v1")
        time.sleep(1.1)
        assert cache.get("k1")[0] is True

    def test_none_values_leave_existing_settings_untouched(self, tmp_path) -> None:
        cache = _cache(tmp_path, ttl_seconds=1)
        cache.configure(ttl_days=None, max_rows=None)
        cache.put("k1", "v1")
        time.sleep(1.1)
        # Original ttl_seconds=1 still applies -- configure(None, None) was
        # a no-op, not a reset to some default.
        assert cache.get("k1") == (False, None)


class TestSchemaVersioning:
    def test_a_mismatched_schema_version_clears_stale_rows_not_crashes(self, tmp_path) -> None:
        db_path = tmp_path / "cache.sqlite3"
        cache = PersistentLLMCache(db_path)
        cache.put("k1", "v1")
        cache.close()

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE schema_meta SET version = ?", (CACHE_SCHEMA_VERSION + 999,))
        conn.commit()
        conn.close()

        reopened = PersistentLLMCache(db_path)
        # Never raises, and the stale-format row is gone rather than
        # misread under the new schema.
        assert reopened.get("k1") == (False, None)
