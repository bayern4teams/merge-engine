"""Unit tests for workflow persistence/rehydration (Phase 5)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.workflow_manager import WorkflowManager


@pytest.fixture
def manager():
    with patch("src.orchestrator.workflow_manager.build_graph") as mock_build:
        mock_build.return_value = MagicMock()
        yield WorkflowManager()


class TestRehydrateWorkflow:
    def test_interrupted_at_hitl_node(self, manager):
        manager.graph.get_state.return_value = MagicMock(
            values={"dependency_tree": {"person": ["company"]}, "extraction_order": []},
            next=("human_review_deps",),
        )
        status = manager.get_workflow_status("t-restart")
        assert status["status"] == "interrupted"
        assert status["current_interrupt_node"] == "human_review_deps"
        assert status["interrupt_data"]["node"] == "human_review_deps"
        # Registered for subsequent resume/abort calls
        assert "t-restart" in manager._active_workflows

    def test_completed_workflow(self, manager):
        manager.graph.get_state.return_value = MagicMock(
            values={"unified_entities": {}}, next=(),
        )
        status = manager.get_workflow_status("t-done")
        assert status["status"] == "completed"

    def test_unknown_thread_not_found(self, manager):
        manager.graph.get_state.return_value = MagicMock(values={}, next=())
        status = manager.get_workflow_status("t-nope")
        assert status["status"] == "not_found"
        assert "t-nope" not in manager._active_workflows

    def test_died_mid_node_reports_failed(self, manager):
        manager.graph.get_state.return_value = MagicMock(
            values={"target_entities": ["company"]}, next=("extract_data",),
        )
        status = manager.get_workflow_status("t-dead")
        assert status["status"] == "failed"

    def test_get_state_error_returns_not_found(self, manager):
        manager.graph.get_state.side_effect = RuntimeError("db locked")
        status = manager.get_workflow_status("t-err")
        assert status["status"] == "not_found"

    def test_in_memory_entry_wins(self, manager):
        manager._active_workflows["t-live"] = {
            "thread_id": "t-live", "config": {"configurable": {"thread_id": "t-live"}},
            "status": "running", "current_interrupt_node": None, "interrupt_data": None,
        }
        status = manager.get_workflow_status("t-live")
        assert status["status"] == "running"
        manager.graph.get_state.assert_not_called()


class TestCreateCheckpointer:
    def test_returns_sqlite_saver(self, tmp_path):
        import src.orchestrator.workflow_manager as wm_mod
        with patch.object(wm_mod, "_CHECKPOINT_DB", tmp_path / "cp.db"):
            cp = wm_mod._create_checkpointer()
        assert cp is not None
        from langgraph.checkpoint.sqlite import SqliteSaver
        assert isinstance(cp, SqliteSaver)
        assert (tmp_path / "cp.db").exists()
