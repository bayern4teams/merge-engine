"""Unit tests for the editable mapping review gate (Phase 4).

Covers WorkflowManager._build_interrupt_data mapping rows,
apply_mapping_feedback validation/rebuild, and the UI dataframe
round-trip helper.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.workflow_manager import WorkflowManager


@pytest.fixture
def manager():
    with patch("src.orchestrator.workflow_manager.build_graph") as mock_build:
        mock_build.return_value = MagicMock()
        yield WorkflowManager()


class TestBuildInterruptDataMappings:
    def test_map_row(self, manager):
        values = {
            "field_mappings": {
                "company": {"org_title": {"dest_column": "name", "confidence": 0.9}},
            },
        }
        data = manager._build_interrupt_data("human_review_mappings", values)["data"]
        rows = data["mapping_rows"]
        assert rows == [{
            "entity_type": "company", "source_field": "org_title",
            "action": "map", "dest_column": "name",
            "new_column_name": "", "new_column_type": "", "confidence": 0.9,
        }]

    def test_new_column_row_pulls_type_from_schema_changes(self, manager):
        values = {
            "field_mappings": {
                "company": {
                    "esg_score": {
                        "dest_column": "esg_score", "confidence": 0.9,
                        "requires_schema_change": True,
                    },
                },
            },
            "pending_schema_changes": [
                {"table": "companies", "column": "esg_score", "data_type": "DOUBLE PRECISION"},
            ],
        }
        data = manager._build_interrupt_data("human_review_mappings", values)["data"]
        row = data["mapping_rows"][0]
        assert row["action"] == "new_column"
        assert row["new_column_name"] == "esg_score"
        assert row["new_column_type"] == "DOUBLE PRECISION"
        assert row["dest_column"] == ""

    def test_unmapped_fields_become_skip_rows(self, manager):
        values = {
            "field_mappings": {},
            "unmapped_fields": {
                "person": [{"field": "misc", "confidence": 0.4, "suggested_column": "notes"}],
            },
        }
        data = manager._build_interrupt_data("human_review_mappings", values)["data"]
        row = data["mapping_rows"][0]
        assert row["action"] == "skip"
        assert row["source_field"] == "misc"
        assert row["dest_column"] == "notes"
        assert data["unmapped_fields"] == [
            {"entity_type": "person", "source_field": "misc"},
        ]


def _mock_dest_connector(columns: list[str]):
    conn = MagicMock()
    conn.list_tables.return_value = ["companies", "people"]
    conn.get_table_schema.return_value = {
        "columns": [{"name": c, "type": "TEXT"} for c in columns]
    }
    return conn


class TestApplyMappingFeedback:
    def _run(self, manager, edits, columns=("id", "name", "industry", "_source_ids"),
             state_values=None):
        manager.graph.get_state.return_value = MagicMock(
            values=state_values or {"destination": {"type": "postgresql"}},
        )
        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=_mock_dest_connector(list(columns)),
        ):
            return manager.apply_mapping_feedback({"configurable": {}}, edits)

    def test_valid_map_edit(self, manager):
        ok, update = self._run(manager, [{
            "entity_type": "company", "source_field": "org_title",
            "action": "map", "dest_column": "name",
        }])
        assert ok
        assert update["field_mappings"]["company"]["org_title"] == {
            "dest_column": "name", "confidence": 1.0, "mapped_by": "human",
        }
        assert update["pending_schema_changes"] == []

    def test_valid_new_column_edit(self, manager):
        ok, update = self._run(manager, [{
            "entity_type": "company", "source_field": "esg",
            "action": "new_column", "new_column_name": "esg_score",
            "new_column_type": "DOUBLE PRECISION",
        }])
        assert ok
        entry = update["field_mappings"]["company"]["esg"]
        assert entry["requires_schema_change"] is True
        assert entry["dest_column"] == "esg_score"
        assert update["pending_schema_changes"] == [
            {"table": "companies", "column": "esg_score", "data_type": "DOUBLE PRECISION"},
        ]

    def test_skip_drops_field(self, manager):
        ok, update = self._run(manager, [{
            "entity_type": "company", "source_field": "junk", "action": "skip",
        }])
        assert ok
        assert update["field_mappings"]["company"] == {}
        assert update["unmapped_fields"]["company"] == []

    def test_invalid_dest_column_rejected(self, manager):
        ok, err = self._run(manager, [{
            "entity_type": "company", "source_field": "org_title",
            "action": "map", "dest_column": "no_such_col",
        }])
        assert not ok
        assert "no_such_col" in err

    def test_map_without_dest_column_rejected(self, manager):
        ok, err = self._run(manager, [{
            "entity_type": "company", "source_field": "org_title",
            "action": "map", "dest_column": "",
        }])
        assert not ok
        assert "requires dest_column" in err

    def test_invalid_identifier_rejected(self, manager):
        ok, err = self._run(manager, [{
            "entity_type": "company", "source_field": "x",
            "action": "new_column", "new_column_name": "bad name!",
        }])
        assert not ok
        assert "invalid new column name" in err

    def test_colliding_new_column_rejected(self, manager):
        ok, err = self._run(manager, [{
            "entity_type": "company", "source_field": "x",
            "action": "new_column", "new_column_name": "name",
        }])
        assert not ok
        assert "already exists" in err

    def test_unknown_action_rejected(self, manager):
        ok, err = self._run(manager, [{
            "entity_type": "company", "source_field": "x", "action": "banana",
        }])
        assert not ok
        assert "unknown action" in err

    def test_invalid_type_falls_back_to_text(self, manager):
        ok, update = self._run(manager, [{
            "entity_type": "company", "source_field": "x",
            "action": "new_column", "new_column_name": "x_col",
            "new_column_type": "MEDIUMBLOB",
        }])
        assert ok
        assert update["pending_schema_changes"][0]["data_type"] == "TEXT"
