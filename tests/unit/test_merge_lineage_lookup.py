"""Unit tests for Phase 7.3's merge-lineage lookup (db_discovery.py).

`query_merge_lineage` is the Investigate screen's read path over
`merge_engine_merge_audit` (Phase 7.2) -- searchable by a record id (either
side of a pair) or a cluster id, surviving the run's own spool being
cleaned up. Mirrors `sample_table_rows`' existing bounded/read-only/
never-raises contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.ui.components.db_discovery import query_merge_lineage


class TestQueryMergeLineage:
    def test_empty_search_is_rejected_without_a_db_call(self) -> None:
        with patch("psycopg2.connect") as mock_connect:
            result = query_merge_lineage(
                host="localhost", port=5432, user="u", password="p", database="d", search="  "
            )
        assert result["connected"] is False
        assert "search is required" in result["error"]
        mock_connect.assert_not_called()

    def test_search_queries_record_a_record_b_and_cluster_id(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"run_id": "r1", "record_a": "x", "record_b": "y", "confidence": 0.9}
        ]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch("psycopg2.connect", return_value=conn):
            result = query_merge_lineage(
                host="localhost",
                port=5432,
                user="u",
                password="p",
                database="d",
                search="x",
            )
        assert result["connected"] is True
        assert result["error"] is None
        assert result["rows"] == [
            {"run_id": "r1", "record_a": "x", "record_b": "y", "confidence": 0.9}
        ]
        sql, params = cursor.execute.call_args.args
        assert "record_a = %(search)s OR record_b = %(search)s OR cluster_id = %(search)s" in sql
        assert params["search"] == "x"
        assert "entity_type" not in params

    def test_entity_type_filter_is_included_when_given(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch("psycopg2.connect", return_value=conn):
            query_merge_lineage(
                host="localhost",
                port=5432,
                user="u",
                password="p",
                database="d",
                search="x",
                entity_type="company",
            )
        sql, params = cursor.execute.call_args.args
        assert "AND entity_type = %(entity_type)s" in sql
        assert params["entity_type"] == "company"

    def test_limit_is_clamped_to_the_ceiling(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch("psycopg2.connect", return_value=conn):
            query_merge_lineage(
                host="localhost",
                port=5432,
                user="u",
                password="p",
                database="d",
                search="x",
                limit=99999,
            )
        _sql, params = cursor.execute.call_args.args
        assert params["limit"] == 200  # _MAX_LINEAGE_LIMIT

    def test_connection_failure_degrades_gracefully_not_raising(self) -> None:
        with patch("psycopg2.connect", side_effect=RuntimeError("connection refused")):
            result = query_merge_lineage(
                host="localhost", port=5432, user="u", password="p", database="d", search="x"
            )
        assert result["connected"] is False
        assert "connection refused" in result["error"]
        assert result["rows"] == []

    def test_missing_table_degrades_gracefully(self) -> None:
        conn = MagicMock()
        conn.cursor.side_effect = RuntimeError('relation "merge_engine_merge_audit" does not exist')
        with patch("psycopg2.connect", return_value=conn):
            result = query_merge_lineage(
                host="localhost", port=5432, user="u", password="p", database="d", search="x"
            )
        assert result["connected"] is False
        assert "does not exist" in result["error"]
