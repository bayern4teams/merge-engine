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
        assert rows == [
            {
                "entity_type": "company",
                "source_field": "org_title",
                "action": "map",
                "dest_column": "name",
                "confidence": 0.9,
                "origin_id": "",
                "table": "",
                "is_coalesced": False,
            }
        ]

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
        # Not a live mapping (action is skip) — shown as a preview of what
        # the mapper considered before giving up, per Phase 2.3.
        assert row["dest_column"] == "notes"
        assert data["unmapped_fields"] == [
            {
                "entity_type": "person",
                "source_field": "misc",
                "confidence": 0.4,
                "suggested_column": "notes",
                "reason": None,
            },
        ]


def _mock_dest_connector(columns: list[str]):
    conn = MagicMock()
    conn.list_tables.return_value = ["companies", "people"]
    conn.get_table_schema.return_value = {"columns": [{"name": c, "type": "TEXT"} for c in columns]}
    return conn


class TestApplyMappingFeedback:
    def _run(
        self, manager, edits, columns=("id", "name", "industry", "_source_ids"), state_values=None
    ):
        manager.graph.get_state.return_value = MagicMock(
            values=state_values or {"destination": {"type": "postgresql"}},
        )
        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=_mock_dest_connector(list(columns)),
        ):
            return manager.apply_mapping_feedback({"configurable": {}}, edits)

    def test_valid_map_edit(self, manager):
        ok, update = self._run(
            manager,
            [
                {
                    "entity_type": "company",
                    "source_field": "org_title",
                    "action": "map",
                    "dest_column": "name",
                }
            ],
        )
        assert ok
        assert update["field_mappings"]["company"]["org_title"] == {
            "dest_column": "name",
            "confidence": 1.0,
            "mapped_by": "human",
        }
        assert update["pending_schema_changes"] == []

    def test_new_column_edit_rejected(self, manager):
        """A reviewer has no more power to add a column than a node does.

        The UI dropdown no longer offers "new_column" at all (Phase 2.3) —
        this exercises the defence-in-depth backstop for a direct API call
        that sends one anyway.
        """
        ok, err = self._run(
            manager,
            [
                {
                    "entity_type": "company",
                    "source_field": "esg",
                    "action": "new_column",
                }
            ],
        )
        assert not ok
        assert "fixed contract" in err

    def test_skip_drops_field(self, manager):
        ok, update = self._run(
            manager,
            [
                {
                    "entity_type": "company",
                    "source_field": "junk",
                    "action": "skip",
                }
            ],
        )
        assert ok
        assert update["field_mappings"]["company"] == {}
        assert update["unmapped_fields"]["company"] == []

    def test_invalid_dest_column_rejected(self, manager):
        ok, err = self._run(
            manager,
            [
                {
                    "entity_type": "company",
                    "source_field": "org_title",
                    "action": "map",
                    "dest_column": "no_such_col",
                }
            ],
        )
        assert not ok
        assert "no_such_col" in err

    def test_map_without_dest_column_rejected(self, manager):
        ok, err = self._run(
            manager,
            [
                {
                    "entity_type": "company",
                    "source_field": "org_title",
                    "action": "map",
                    "dest_column": "",
                }
            ],
        )
        assert not ok
        assert "requires dest_column" in err

    def test_unknown_action_rejected(self, manager):
        ok, err = self._run(
            manager,
            [
                {
                    "entity_type": "company",
                    "source_field": "x",
                    "action": "banana",
                }
            ],
        )
        assert not ok
        assert "unknown action" in err
