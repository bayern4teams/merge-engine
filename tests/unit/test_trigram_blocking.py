"""Unit tests for pg_trgm GIN trigram candidate blocking (audit §13 #13, B.1.3).

``trigram_candidate_pairs`` is a standalone, connector-duck-typed function so
it can be exercised here with a fake connector that just records the SQL it
was asked to run — no real Postgres needed for these tests (the [LIVE] half
lives in TODO.md B.1.3, run against the real destination separately).
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.tools.resolve_entities import (
    _generate_candidate_pairs,
    trigram_candidate_pairs,
)


class _FakeConnector:
    """Records every SQL statement it is asked to run; returns a canned
    result for the final SELECT so the caller has something to parse."""

    def __init__(self, select_result: list[tuple[str, str]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self._select_result = select_result or []

    def execute_sql(self, sql: str, params: tuple[Any, ...] | None = None) -> list[Any]:
        self.calls.append((sql, params))
        if sql.strip().upper().startswith("SELECT A.REC_ID"):
            return self._select_result
        return []


def _records() -> list[dict[str, Any]]:
    return [
        {
            "_origin_id": "diffbot",
            "_origin_table": "company",
            "_origin_id_row": str(i),
            "name": name,
        }
        for i, name in enumerate(
            [
                "Boeing",
                "Boeing Co",
                "The Boeing Company",
                "Boeing Co. The",
                "Boeing Satellite Systems, Inc.",
            ]
        )
    ]


class TestTrigramCandidatePairs:
    def test_emits_expected_sql_shape(self) -> None:
        connector = _FakeConnector()
        trigram_candidate_pairs(connector, _records(), "name")

        sql_statements = "\n".join(sql for sql, _ in connector.calls)
        assert "gin_trgm_ops" in sql_statements
        assert "a.name % b.name" in sql_statements
        assert "a.rec_id < b.rec_id" in sql_statements

    def test_returns_the_connectors_pairs(self) -> None:
        connector = _FakeConnector(select_result=[("diffbot:company:0", "diffbot:company:2")])
        pairs = trigram_candidate_pairs(connector, _records(), "name")
        assert pairs == [("diffbot:company:0", "diffbot:company:2")]

    def test_fewer_than_two_usable_rows_never_calls_the_connector(self) -> None:
        connector = _FakeConnector()
        one_record = [{"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1", "name": "X"}]
        assert trigram_candidate_pairs(connector, one_record, "name") == []
        assert connector.calls == []

    def test_records_missing_the_name_field_are_skipped(self) -> None:
        connector = _FakeConnector()
        records = [
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1"},
            {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "2"},
        ]
        assert trigram_candidate_pairs(connector, records, "name") == []
        assert connector.calls == []


class TestGenerateCandidatePairsUnionsExtraPairs:
    def test_extra_pairs_are_unioned_in(self) -> None:
        # Two records with dissimilar names/blocking keys, so the ordinary
        # blocking pass would never pair them -- only `extra_pairs` does.
        rec_a = {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1", "name": "Zzz"}
        rec_b = {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "2", "name": "Qqq"}
        extra = [("a:t:1", "a:t:2")]
        stats: dict[str, int] = {}
        pairs = _generate_candidate_pairs([rec_a, rec_b], extra_pairs=extra, stats=stats)
        found = {(p[0], p[1]) for p in pairs}
        assert ("a:t:1", "a:t:2") in found
        assert stats["candidate_pairs"] == 1

    def test_extra_pairs_overlapping_block_pairs_are_not_duplicated(self) -> None:
        # Same-name records already pair via the ordinary blocking pass;
        # also handing the identical pair in via extra_pairs must not
        # produce a duplicate entry.
        rec_a = {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1", "name": "Boeing"}
        rec_b = {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "2", "name": "Boeing"}
        extra = [("a:t:1", "a:t:2")]
        stats: dict[str, int] = {}
        pairs = _generate_candidate_pairs([rec_a, rec_b], extra_pairs=extra, stats=stats)
        assert stats["candidate_pairs"] == 1

    def test_extra_pair_referencing_an_unknown_id_is_ignored(self) -> None:
        rec_a = {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": "1", "name": "Boeing"}
        extra = [("a:t:1", "a:t:does-not-exist")]
        stats: dict[str, int] = {}
        pairs = _generate_candidate_pairs([rec_a], extra_pairs=extra, stats=stats)
        assert pairs == []
