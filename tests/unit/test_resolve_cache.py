"""Unit tests for resolve_cache (Part 1.5 D.7): the cross-run cache that lets
a load_data retry skip re-clustering when the input records are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.mcp_server.tools import resolve_cache


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own cache file and the global test-suite bypass
    (set in tests/conftest.py) turned back off, so the cache under test is
    actually exercised."""
    monkeypatch.setattr(resolve_cache, "CACHE_PATH", tmp_path / "resolve_cache_test.duckdb")
    monkeypatch.delenv("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", raising=False)


def _rec(rid: str, **fields: Any) -> dict[str, Any]:
    origin, table, row = rid.split(":")
    return {"_origin_id": origin, "_origin_table": table, "_origin_id_row": row, **fields}


class TestContentHash:
    def test_deterministic_for_identical_input(self) -> None:
        records = [_rec("o1:t:1", name="Boeing"), _rec("o1:t:2", name="Airbus")]
        h1 = resolve_cache.content_hash("company", records, {}, None, None, True)
        h2 = resolve_cache.content_hash("company", records, {}, None, None, True)
        assert h1 == h2

    def test_order_independent(self) -> None:
        r1 = [_rec("o1:t:1", name="Boeing"), _rec("o1:t:2", name="Airbus")]
        r2 = list(reversed(r1))
        h1 = resolve_cache.content_hash("company", r1, {}, None, None, True)
        h2 = resolve_cache.content_hash("company", r2, {}, None, None, True)
        assert h1 == h2

    def test_sensitive_to_record_content(self) -> None:
        r1 = [_rec("o1:t:1", name="Boeing")]
        r2 = [_rec("o1:t:1", name="Boeing Co")]
        assert resolve_cache.content_hash(
            "company", r1, {}, None, None, True
        ) != resolve_cache.content_hash("company", r2, {}, None, None, True)

    def test_sensitive_to_record_membership(self) -> None:
        r1 = [_rec("o1:t:1", name="Boeing")]
        r2 = [_rec("o1:t:1", name="Boeing"), _rec("o1:t:2", name="Airbus")]
        assert resolve_cache.content_hash(
            "company", r1, {}, None, None, True
        ) != resolve_cache.content_hash("company", r2, {}, None, None, True)

    def test_sensitive_to_entity_type(self) -> None:
        records = [_rec("o1:t:1", name="Boeing")]
        assert resolve_cache.content_hash(
            "company", records, {}, None, None, True
        ) != resolve_cache.content_hash("person", records, {}, None, None, True)

    def test_sensitive_to_matching_rules(self) -> None:
        records = [_rec("o1:t:1", name="Boeing")]
        assert resolve_cache.content_hash(
            "company", records, {"strictness": "strict"}, None, None, True
        ) != resolve_cache.content_hash("company", records, {}, None, None, True)

    def test_sensitive_to_subject(self) -> None:
        records = [_rec("o1:t:1", name="Boeing")]
        assert resolve_cache.content_hash(
            "company", records, {}, "Boeing", None, True
        ) != resolve_cache.content_hash("company", records, {}, None, None, True)

    def test_sensitive_to_blocking_keys(self) -> None:
        records = [_rec("o1:t:1", name="Boeing")]
        assert resolve_cache.content_hash(
            "company", records, {}, None, ["name"], True
        ) != resolve_cache.content_hash("company", records, {}, None, None, True)

    def test_sensitive_to_llm_requested(self) -> None:
        # This is the correctness-critical one: a heuristic-only cached
        # result must never be served to a caller that wanted LLM
        # adjudication.
        records = [_rec("o1:t:1", name="Boeing")]
        assert resolve_cache.content_hash(
            "company", records, {}, None, None, True
        ) != resolve_cache.content_hash("company", records, {}, None, None, False)


class TestGetPut:
    def test_miss_on_empty_cache(self) -> None:
        assert resolve_cache.get("nonexistent") is None

    def test_round_trip(self) -> None:
        result = {"entity_type": "company", "clusters": [], "auto_matched": 0}
        resolve_cache.put("abc123", "company", result)
        assert resolve_cache.get("abc123") == result

    def test_put_overwrites_existing_key(self) -> None:
        resolve_cache.put("k", "company", {"v": 1})
        resolve_cache.put("k", "company", {"v": 2})
        assert resolve_cache.get("k") == {"v": 2}

    def test_disable_env_var_forces_miss_even_when_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolve_cache.put("k", "company", {"v": 1})
        monkeypatch.setenv("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", "1")
        assert resolve_cache.get("k") is None

    def test_get_failure_degrades_to_none(self) -> None:
        resolve_cache.put("k", "company", {"v": 1})
        with patch.object(resolve_cache, "_connect", side_effect=RuntimeError("locked")):
            assert resolve_cache.get("k") is None  # must not raise

    def test_put_failure_is_swallowed(self) -> None:
        with patch.object(resolve_cache, "_connect", side_effect=RuntimeError("locked")):
            resolve_cache.put("k", "company", {"v": 1})  # must not raise


class TestApplyKnownClusterIds:
    def test_reassigns_cluster_id_from_current_run_known_ids(self) -> None:
        cached = {
            "clusters": [
                {
                    "cluster_id": "STALE-FROM-CACHE-WRITER-RUN",
                    "record_ids": ["o1:t:1", "o2:t:5"],
                    "match_confidence": 0.97,
                    "required_human_review": False,
                }
            ]
        }
        # This run's destination already has o1:t:1 mapped to a real row id —
        # the reassigned cluster_id must be THAT, not the stale cached one.
        known = {"o1:t:1": "dest-row-42"}
        out = resolve_cache.apply_known_cluster_ids(cached, "company", known)
        assert out["clusters"][0]["cluster_id"] == "dest-row-42"
        # Everything else about the cluster is preserved untouched.
        assert out["clusters"][0]["record_ids"] == ["o1:t:1", "o2:t:5"]
        assert out["clusters"][0]["match_confidence"] == 0.97

    def test_no_known_ids_derives_a_fresh_deterministic_id(self) -> None:
        cached = {
            "clusters": [
                {
                    "cluster_id": "whatever-was-cached",
                    "record_ids": ["o1:t:1"],
                    "match_confidence": 1.0,
                    "required_human_review": False,
                }
            ]
        }
        out1 = resolve_cache.apply_known_cluster_ids(cached, "company", None)
        out2 = resolve_cache.apply_known_cluster_ids(cached, "company", None)
        # Deterministic: same entity_type + same members -> same fresh id
        # every time, and never the stale cached string.
        assert out1["clusters"][0]["cluster_id"] == out2["clusters"][0]["cluster_id"]
        assert out1["clusters"][0]["cluster_id"] != "whatever-was-cached"
