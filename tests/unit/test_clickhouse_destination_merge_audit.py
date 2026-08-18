"""Unit tests for ClickHouseDestinationConnector's merge-lineage audit table
(Phase 7.2, added for the fix-bugs plan's A2 gap-closing: the ClickHouse
destination previously had no `merge_engine_merge_audit` table at all, so a
merge into ClickHouse had zero durable per-pair evidence — only the
PostgreSQL destination did).

All tests mock `_ensure_client()` to avoid requiring a real ClickHouse server.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.mcp_server.connectors.clickhouse_destination import ClickHouseDestinationConnector


@pytest.fixture
def dest_config() -> dict:
    return {
        "host": "localhost",
        "port": 8123,
        "user": "default",
        "password": "",
        "database": "mergedb",
    }


@pytest.fixture
def connector(dest_config: dict) -> ClickHouseDestinationConnector:
    conn = ClickHouseDestinationConnector(dest_config)
    conn._client = MagicMock()
    return conn


class TestEnsureMergeAudit:
    def test_issues_create_table_with_replacing_merge_tree(
        self, connector: ClickHouseDestinationConnector
    ) -> None:
        connector.ensure_merge_audit()
        connector._client.command.assert_called_once()
        sql = connector._client.command.call_args.args[0]
        assert "merge_engine_merge_audit" in sql
        assert "ReplacingMergeTree(created_at)" in sql
        assert "ORDER BY (run_id, entity_type, record_a, record_b)" in sql
        assert "record_a String" in sql
        assert "record_b String" in sql
        assert "evidence String" in sql


class TestSaveMergeAudit:
    def test_empty_rows_is_a_noop(self, connector: ClickHouseDestinationConnector) -> None:
        result = connector.save_merge_audit("company", "run-1", [])
        assert result == 0
        connector._client.command.assert_not_called()
        connector._client.insert.assert_not_called()

    def test_ensures_the_table_before_inserting(
        self, connector: ClickHouseDestinationConnector
    ) -> None:
        connector.save_merge_audit("company", "run-1", [{"record_a": "a", "record_b": "b"}])
        connector._client.command.assert_called_once()
        connector._client.insert.assert_called_once()

    def test_inserts_one_row_per_pair_with_the_right_column_order(
        self, connector: ClickHouseDestinationConnector
    ) -> None:
        rows = [
            {
                "cluster_id": "cid-1",
                "record_a": "o1:t:a",
                "record_b": "o2:t:b",
                "confidence": 0.94,
                "method": "fellegi_sunter",
                "evidence": {"matched_fields": [{"field": "name"}], "mismatched_fields": []},
                "decided_by": "auto",
                "human_override": None,
            }
        ]
        saved = connector.save_merge_audit("company", "run-1", rows)
        assert saved == 1

        call = connector._client.insert.call_args
        assert call.args[0] == "merge_engine_merge_audit"
        data = call.args[1]
        assert len(data) == 1
        record = data[0]
        column_names = call.kwargs["column_names"]
        as_dict = dict(zip(column_names, record, strict=True))
        assert as_dict["run_id"] == "run-1"
        assert as_dict["entity_type"] == "company"
        assert as_dict["cluster_id"] == "cid-1"
        assert as_dict["record_a"] == "o1:t:a"
        assert as_dict["record_b"] == "o2:t:b"
        assert as_dict["confidence"] == 0.94
        assert as_dict["method"] == "fellegi_sunter"
        assert '"matched_fields"' in as_dict["evidence"]
        assert as_dict["decided_by"] == "auto"
        assert as_dict["human_override"] is None

    def test_missing_cluster_id_defaults_to_empty_string(
        self, connector: ClickHouseDestinationConnector
    ) -> None:
        connector.save_merge_audit("company", "run-1", [{"record_a": "a", "record_b": "b"}])
        call = connector._client.insert.call_args
        column_names = call.kwargs["column_names"]
        record = call.args[1][0]
        as_dict = dict(zip(column_names, record, strict=True))
        assert as_dict["cluster_id"] == ""

    def test_human_override_split_round_trips(
        self, connector: ClickHouseDestinationConnector
    ) -> None:
        """The other half of Phase 7.2: a human-split pair's evidence must
        reach ClickHouse the same way it reaches PostgreSQL."""
        rows = [
            {
                "cluster_id": "cid-1",
                "record_a": "a",
                "record_b": "b",
                "decided_by": "human",
                "human_override": "split",
                "evidence": {"reason": "not the same entity"},
            }
        ]
        connector.save_merge_audit("person", "run-1", rows)
        call = connector._client.insert.call_args
        column_names = call.kwargs["column_names"]
        record = call.args[1][0]
        as_dict = dict(zip(column_names, record, strict=True))
        assert as_dict["decided_by"] == "human"
        assert as_dict["human_override"] == "split"
