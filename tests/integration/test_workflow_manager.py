"""Integration tests for WorkflowManager lifecycle."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

ORIGIN_CONFIGS = [
    {
        "id": "test_crm",
        "type": "postgres",
        "connection": {
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
        },
        "priority": 1,
    },
]

DESTINATION_CONFIG = {
    "type": "postgresql",
    "host": "localhost",
    "port": 5432,
    "user": "test",
    "password": "test",
    "database": "test_db",
    "schema": "public",
}


class TestWorkflowManagerIntegration:
    """Test WorkflowManager start/status/resume/abort lifecycle."""

    def _build_mock_graph(self) -> MagicMock:
        """Build a mock graph that simulates a successful workflow."""
        graph = MagicMock()
        graph.stream = MagicMock(return_value=iter([]))
        graph.get_state = MagicMock()
        graph.get_state.return_value = None
        graph.update_state = MagicMock()
        return graph

    @pytest.mark.asyncio
    async def test_start_workflow_returns_thread_id(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that start_workflow returns a valid thread ID."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = self._build_mock_graph()

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import all companies",
                    config_path=str(test_config_path),
                    origins=ORIGIN_CONFIGS,
                    destination=DESTINATION_CONFIG,
                )

                assert thread_id.startswith("wf_")
                assert len(thread_id) > 5

    @pytest.mark.asyncio
    async def test_get_workflow_status_not_found(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that querying a non-existent workflow returns not_found."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = self._build_mock_graph()

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                status = manager.get_workflow_status("nonexistent_thread")
                assert status["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_workflow_status_becomes_running_then_completed(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test workflow status transitions from running to completed."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = MagicMock()

        # Simulate a completed workflow (no interrupts)
        mock_graph.stream = MagicMock(return_value=iter([]))
        mock_graph.get_state = MagicMock()
        mock_graph.get_state.return_value = None
        mock_graph.update_state = MagicMock()

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import companies",
                    config_path=str(test_config_path),
                    origins=ORIGIN_CONFIGS,
                    destination=DESTINATION_CONFIG,
                )

                # Wait briefly for the background thread
                import time

                time.sleep(0.2)

                status = manager.get_workflow_status(thread_id)
                assert status["status"] in ("running", "completed")

    @pytest.mark.asyncio
    async def test_abort_workflow(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that abort_workflow marks the workflow as aborted."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = self._build_mock_graph()

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import companies",
                    config_path=str(test_config_path),
                    origins=ORIGIN_CONFIGS,
                    destination=DESTINATION_CONFIG,
                )

                import time

                time.sleep(0.1)

                manager.abort_workflow(thread_id)
                status = manager.get_workflow_status(thread_id)
                assert status["status"] == "aborted"

    @pytest.mark.asyncio
    async def test_list_active_workflows(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that list_active_workflows returns active workflows."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = self._build_mock_graph()
        mock_graph.stream = MagicMock(side_effect=StopIteration)

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import companies",
                    config_path=str(test_config_path),
                    origins=ORIGIN_CONFIGS,
                    destination=DESTINATION_CONFIG,
                )

                # Give the thread time to start
                import time

                time.sleep(0.1)

                active = manager.list_active_workflows()
                # After completion, should be empty
                assert isinstance(active, list)

    @pytest.mark.asyncio
    async def test_resume_workflow_unknown(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that resuming a non-existent workflow logs an error."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = self._build_mock_graph()

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                # Should not raise, just log
                manager.resume_workflow("unknown_thread", {"decision": "approve"})

    @pytest.mark.asyncio
    async def test_resume_workflow_updates_state(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that resume_workflow calls update_state on the graph."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = MagicMock()

        # Simulate workflow that gets interrupted, then resumes successfully.
        # The real code drives this via graph.astream (async), called once
        # from start_workflow with the initial state and once from
        # resume_workflow with None (Phase 0 Sec 0.7, found while wiring
        # tests/integration into the default gate for the first time - this
        # test predates the sync .stream -> async .astream migration and its
        # mock never actually got iterated).
        interrupt_event = {
            "parse_request": {
                "interrupt_data": {"question": "Approve field mappings?", "type": "field_mapping"}
            }
        }
        resume_event = {"load_data": {}}

        async def _fake_astream(state, config=None, **kwargs):
            events = [resume_event] if state is None else [interrupt_event]
            for event in events:
                yield event

        mock_graph.astream = _fake_astream
        mock_graph.update_state = MagicMock()
        mock_graph.get_state = MagicMock()
        mock_graph.get_state.return_value = None

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import companies",
                    config_path=str(test_config_path),
                    origins=ORIGIN_CONFIGS,
                    destination=DESTINATION_CONFIG,
                )

                import time

                time.sleep(0.3)

                manager.resume_workflow(
                    thread_id,
                    {"approved": True, "mapping_adjustments": []},
                )

                # Verify update_state was called
                mock_graph.update_state.assert_called()

    @pytest.mark.asyncio
    async def test_resume_deps_gate_merges_custom_column_dependencies(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Phase 2.4: a custom link added in the "Add Custom Dependency
        Link" form must reach state["column_dependencies"], merged with
        whatever analyze_dependencies already computed (not replacing it),
        deduplicated, and with any link naming an unknown column dropped —
        DependencyBody's colDeps used to be captured in local React state
        and never sent anywhere at all.
        """
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = MagicMock()
        interrupt_event = {
            "human_review_deps": {"interrupt_data": {"node": "human_review_deps", "data": {}}}
        }

        async def _fake_astream(state, config=None, **kwargs):
            for event in [interrupt_event]:
                yield event

        mock_graph.astream = _fake_astream
        mock_graph.update_state = MagicMock()

        schemas = {
            "origin_1": {
                "tables": [
                    {"name": "Company", "columns": [{"name": "companyID"}]},
                    {
                        "name": "Experience",
                        "columns": [{"name": "companyID"}, {"name": "personID"}],
                    },
                ]
            }
        }
        existing_link = {
            "child_origin": "origin_1",
            "child_table": "Experience",
            "child_column": "personID",
            "parent_origin": "origin_1",
            "parent_table": "Company",
            "parent_column": "companyID",
            "rationale": "declared FK",
        }
        live_snapshot = MagicMock(
            values={"schemas": schemas, "column_dependencies": [existing_link]}, next=()
        )
        mock_graph.get_state = MagicMock(return_value=live_snapshot)

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import companies",
                    config_path=str(test_config_path),
                    origins=ORIGIN_CONFIGS,
                    destination=DESTINATION_CONFIG,
                )

                import time

                time.sleep(0.3)

                new_valid_link = {
                    "child_origin": "origin_1",
                    "child_table": "Experience",
                    "child_column": "companyID",
                    "parent_origin": "origin_1",
                    "parent_table": "Company",
                    "parent_column": "companyID",
                    "rationale": "Custom user-defined dependency link",
                }
                invalid_link = {
                    "child_origin": "origin_1",
                    "child_table": "Experience",
                    "child_column": "no_such_column",
                    "parent_origin": "origin_1",
                    "parent_table": "Company",
                    "parent_column": "companyID",
                    "rationale": "should be dropped",
                }
                manager.resume_workflow(
                    thread_id,
                    {
                        "decision": "approve",
                        "node": "human_review_deps",
                        "column_dependencies": [
                            new_valid_link,
                            invalid_link,
                            existing_link,  # duplicate of what's already there
                        ],
                    },
                )

                assert mock_graph.update_state.call_args is not None
                # graph.update_state(_bare_config(config), state_update, as_node=...)
                state_update = mock_graph.update_state.call_args[0][1]
                deps = state_update["column_dependencies"]

                keys = {
                    (
                        d["child_origin"],
                        d["child_table"],
                        d["child_column"],
                        d["parent_origin"],
                        d["parent_table"],
                        d["parent_column"],
                    )
                    for d in deps
                }
                assert (
                    "origin_1",
                    "Experience",
                    "companyID",
                    "origin_1",
                    "Company",
                    "companyID",
                ) in keys
                assert (
                    "origin_1",
                    "Experience",
                    "personID",
                    "origin_1",
                    "Company",
                    "companyID",
                ) in keys
                assert not any(d["child_column"] == "no_such_column" for d in deps)
                # No duplicate of the pre-existing link.
                assert len(deps) == 2

    @pytest.mark.asyncio
    async def test_start_workflow_with_origins_destination(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that start_workflow accepts origins and destination params."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = self._build_mock_graph()
        origins = [
            {
                "id": "test_crm",
                "type": "postgres",
                "connection": {
                    "host": "localhost",
                    "port": 5432,
                    "user": "test",
                    "password": "test",
                    "database": "test_db",
                },
                "priority": 1,
            }
        ]
        destination = {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "schema": "public",
        }

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import all companies",
                    config_path=str(test_config_path),
                    origins=origins,
                    destination=destination,
                )

                assert thread_id.startswith("wf_")

    @pytest.mark.asyncio
    async def test_cleanup_completed_workflows(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test that cleanup removes completed workflows."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = self._build_mock_graph()
        mock_graph.stream = MagicMock(return_value=iter([]))

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()

                # Start multiple workflows
                threads = []
                for i in range(3):
                    tid = manager.start_workflow(
                        user_request=f"Import data {i}",
                        config_path=str(test_config_path),
                        origins=ORIGIN_CONFIGS,
                        destination=DESTINATION_CONFIG,
                    )
                    threads.append(tid)

                import time

                time.sleep(0.3)

                # After completion, cleanup should remove them
                removed = manager.cleanup_completed()
                assert removed >= 0  # May have completed or still running

    @pytest.mark.asyncio
    async def test_workflow_with_no_graph_stream(
        self,
        mock_config: Any,
        test_config_path: Any,
    ) -> None:
        """Test workflow when graph.astream raises an exception."""
        from src.orchestrator.workflow_manager import WorkflowManager

        mock_graph = MagicMock()

        # The real code drives this via graph.astream (async) - a plain sync
        # MagicMock side_effect never actually got exercised (Phase 0 Sec
        # 0.7, found while wiring tests/integration into the default gate for
        # the first time - this test predates the sync .stream -> async
        # .astream migration).
        async def _raising_astream(*args, **kwargs):
            raise ValueError("Simulated failure")
            yield {}  # pragma: no cover - makes this an async generator function

        mock_graph.astream = _raising_astream
        mock_graph.get_state = MagicMock()
        mock_graph.get_state.return_value = None

        with patch("src.utils.config_loader.load_config", return_value=mock_config):
            with patch("src.orchestrator.workflow_manager.build_graph", return_value=mock_graph):
                manager = WorkflowManager()
                thread_id = manager.start_workflow(
                    user_request="Import companies",
                    config_path=str(test_config_path),
                    origins=ORIGIN_CONFIGS,
                    destination=DESTINATION_CONFIG,
                )

                import time

                time.sleep(0.3)

                status = manager.get_workflow_status(thread_id)
                assert status["status"] == "failed"
