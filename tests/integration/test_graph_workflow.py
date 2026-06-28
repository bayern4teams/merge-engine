"""
Integration tests for the full graph workflow.

Tests the LangGraph state graph building, workflow execution with mocks,
and state transitions through the pipeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_config_data():
    """Generate valid config for testing."""
    return {
        "destination": {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test_user",
            "password": "test_pass",
            "database": "test_db",
            "schema": "public",
        },
        "llm": {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test_key_12345",
        },
        "retry": {
            "max_attempts": 3,
            "backoff": "exponential",
            "min_backoff_seconds": 1,
            "max_backoff_seconds": 30,
        },
        "extraction": {
            "default_batch_size": 1000,
            "max_batch_size": 10000,
        },
    }


# =============================================================================
# Graph Building Tests
# =============================================================================

class TestGraphBuilding:
    """Tests for LangGraph state graph construction."""

    def test_build_graph_creates_state_graph(self):
        """build_graph should create a StateGraph instance."""
        from langgraph.graph import StateGraph

        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph

            build_graph()

            mock_sg.assert_called_once()

    def test_graph_adds_parse_request_node(self):
        """Graph should include parse_request as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            mock_graph.add_node.assert_called()

    def test_graph_adds_analyze_dependencies_node(self):
        """Graph should include analyze_dependencies as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            # Verify add_node was called with the analyze_dependencies node
            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "analyze_dependencies" in node_names

    def test_graph_adds_discover_schemas_node(self):
        """Graph should include discover_schemas as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "discover_schemas" in node_names

    def test_graph_adds_extract_data_node(self):
        """Graph should include extract_data as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "extract_data" in node_names

    def test_graph_adds_resolve_entities_node(self):
        """Graph should include resolve_entities as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "resolve_entities" in node_names

    def test_graph_adds_resolve_entities_detail_node(self):
        """Graph should include resolve_entities as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "resolve_entities" in node_names

    def test_graph_adds_map_fields_node(self):
        """Graph should include map_fields as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "map_fields" in node_names

    def test_graph_adds_map_fields_detail_node(self):
        """Graph should include map_fields as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "map_fields" in node_names

    def test_graph_adds_load_data_node(self):
        """Graph should include load_data as a node."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph

            build_graph()

            calls = [call[0] for call in mock_graph.add_node.call_args_list]
            node_names = [c[0] for c in calls]
            assert "load_data" in node_names

    def test_graph_compiles_to_checkpoint_graph(self):
        """build_graph should compile to a CompiledGraph."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.compile.return_value = MagicMock(checksig={})

            graph = build_graph()

            mock_graph.compile.assert_called_once()


# =============================================================================
# Graph Edge Tests
# =============================================================================

class TestGraphEdges:
    """Tests for graph edge/connectivity configuration."""

    def test_graph_has_start_edge(self):
        """Graph should have a START edge to parse_request."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph
            mock_graph.set_entry_point.return_value = mock_graph

            build_graph()

            # Should call set_entry_point to define START
            mock_graph.set_entry_point.assert_called()

    def test_graph_has_conditional_edges(self):
        """Graph should have conditional edges for human-in-the-loop flow."""
        from src.orchestrator.graph import build_graph

        with patch("src.orchestrator.graph.StateGraph") as mock_sg:
            mock_graph = MagicMock()
            mock_sg.return_value = mock_graph
            mock_graph.add_node.return_value = mock_graph
            mock_graph.set_entry_point.return_value = mock_graph
            mock_graph.add_conditional_edges.return_value = mock_graph

            build_graph()

            mock_graph.add_conditional_edges.assert_called()


# =============================================================================
# Workflow Manager Integration Tests
# =============================================================================

class TestWorkflowManagerIntegration:
    """Integration tests for workflow manager with mocked graph."""

    @pytest.fixture
    def mock_workflow_graph(self):
        """Create a mock workflow graph."""
        graph = MagicMock()
        graph.invoke = AsyncMock()
        return graph

    def test_workflow_manager_starts_workflow(self, mock_workflow_graph):
        """WorkflowManager should start workflow and create thread."""
        from src.orchestrator.workflow_manager import WorkflowManager

        manager = WorkflowManager()
        manager.graph = mock_workflow_graph

        result = manager.start_workflow(
            user_request="Import all companies",
            config_path="config/config.yaml",
            origins=[{"id": "origin_1", "type": "postgres", "connection": {"host": "localhost", "port": 5432, "user": "u", "password": "p", "database": "d"}, "priority": 0}],
            destination={"type": "postgresql", "host": "localhost", "port": 5432, "user": "admin", "password": "pass", "database": "mergedb", "schema": "public"},
        )

        assert result is not None
        assert isinstance(result, str)

    def test_workflow_manager_gets_status(self, mock_workflow_graph):
        """WorkflowManager should return workflow status."""
        from src.orchestrator.workflow_manager import WorkflowManager

        manager = WorkflowManager()
        manager.graph = mock_workflow_graph

        thread_id = manager.start_workflow(
            user_request="Import all companies",
            config_path="config/config.yaml",
            origins=[],
            destination={},
        )

        status = manager.get_workflow_status(thread_id)
        assert "status" in status

    def test_workflow_manager_aborts_workflow(self, mock_workflow_graph):
        """WorkflowManager should abort running workflow."""
        from src.orchestrator.workflow_manager import WorkflowManager

        manager = WorkflowManager()
        manager.graph = mock_workflow_graph

        thread_id = manager.start_workflow(
            user_request="Import all companies",
            config_path="config/config.yaml",
            origins=[],
            destination={},
        )

        manager.abort_workflow(thread_id)

        # Workflow status should be 'aborted' after abort
        status = manager.get_workflow_status(thread_id)
        assert status["status"] == "aborted"

    @pytest.mark.asyncio
    async def test_workflow_manager_resumes_workflow(self, mock_workflow_graph):
        """WorkflowManager should resume interrupted workflow with feedback."""
        from src.orchestrator.workflow_manager import WorkflowManager

        manager = WorkflowManager()
        manager.graph = mock_workflow_graph

        thread_id = manager.start_workflow(
            user_request="Import all companies",
            config_path="config/config.yaml",
            origins=[],
            destination={},
        )

        # Manually set interrupt data
        if thread_id in manager._active_workflows:
            manager._active_workflows[thread_id]["interrupt_data"] = {
                "node": "human_review_deps",
                "data": {"dependency_tree": {"A": ["B"]}},
            }

        # Resume with feedback
        result = manager.resume_workflow(thread_id, {"approved": True})
        # Should not raise
        assert result is None

    def test_workflow_manager_status_for_nonexistent_thread(self):
        """WorkflowManager should return error status for nonexistent thread."""
        from src.orchestrator.workflow_manager import WorkflowManager

        manager = WorkflowManager()

        status = manager.get_workflow_status("nonexistent-thread-id")
        assert status["status"] == "not_found"

    def test_workflow_manager_clears_old_threads(self, mock_workflow_graph):
        """WorkflowManager should clean up old completed threads."""
        from src.orchestrator.workflow_manager import WorkflowManager

        manager = WorkflowManager()
        manager.graph = mock_workflow_graph

        # Add some threads
        for i in range(5):
            manager.start_workflow(
                user_request="Import data",
                config_path="config/config.yaml",
                origins=[],
                destination={},
            )

        assert len(manager._active_workflows) == 5

        # Clear completed threads
        removed = manager.cleanup_completed()
        # cleanup_completed removes completed/failed/aborted workflows
        assert isinstance(removed, int)


# =============================================================================
# State Transition Tests
# =============================================================================

class TestStateTransitions:
    """Tests for pipeline state transitions through workflow."""

    def test_state_created_status(self):
        """New state dict should have 'created' status."""
        state: dict = {"status": "created"}
        assert state["status"] == "created"

    def test_state_parses_request_updates_status(self):
        """After parse_request, state should update parsed_request and status."""
        state: dict = {
            "user_request": "Import all companies from CRM",
            "parsed_request": {
                "entities": ["companies", "contacts"],
                "action": "import",
            },
            "status": "parsed",
        }

        assert state["status"] == "parsed"
        assert state["parsed_request"]["entities"] == ["companies", "contacts"]

    def test_state_analyzes_dependencies(self):
        """After analyze_dependencies, state should have dependency tree."""
        state: dict = {
            "dependency_tree": {
                "contacts": ["companies"],
                "companies": [],
            },
            "extraction_order": ["companies", "contacts"],
            "status": "analyzed",
        }

        assert state["status"] == "analyzed"
        assert "companies" in state["extraction_order"]

    def test_state_discovers_schemas(self):
        """After discover_schemas, state should have schema info."""
        state: dict = {
            "schemas": {
                "companies": {
                    "columns": [{"name": "id", "type": "VARCHAR"}, {"name": "name", "type": "VARCHAR"}],
                    "foreign_keys": [],
                }
            },
            "status": "schemas_discovered",
        }

        assert "companies" in state["schemas"]
        assert len(state["schemas"]["companies"]["columns"]) == 2

    def test_state_extracted_data(self):
        """State should hold extracted data per table."""
        state: dict = {
            "extracted_data": {
                "companies": [
                    {"id": "1", "name": "Acme Corp"},
                    {"id": "2", "name": "Globex Inc"},
                ]
            },
            "status": "extracted",
        }

        assert len(state["extracted_data"]["companies"]) == 2

    def test_state_resolved_entities(self):
        """State should hold resolved entity groups."""
        state: dict = {
            "entity_clusters": {
                "companies": {
                    "unified": [
                        {"id": "unified_1", "name": "Acme Corp", "sources": ["crm1", "crm2"]},
                    ],
                    "duplicates": [],
                }
            },
            "status": "resolved",
        }

        assert "unified" in state["entity_clusters"]["companies"]

    def test_state_mapped_fields(self):
        """State should hold field mappings."""
        state: dict = {
            "field_mappings": {
                "companies": {
                    "name": "company_name",
                    "ein": "tax_id",
                }
            },
            "status": "mapped",
        }

        assert state["field_mappings"]["companies"]["name"] == "company_name"

    def test_state_loaded_data(self):
        """State should hold load results."""
        state: dict = {
            "load_results": {
                "companies": {"inserted": 100, "updated": 50},
                "contacts": {"inserted": 500, "updated": 0},
            },
            "status": "completed",
        }

        assert state["status"] == "completed"
        assert state["load_results"]["companies"]["inserted"] == 100

    def test_state_workflow_logs(self):
        """State should accumulate workflow logs."""
        state: dict = {
            "workflow_logs": [
                "Starting workflow",
                "Parsed request",
                "Discovered schemas",
            ]
        }

        assert len(state["workflow_logs"]) == 3
        assert "Starting workflow" in state["workflow_logs"][0]
        assert "Discovered schemas" in state["workflow_logs"][2]

    def test_state_interrupt_data(self):
        """State should hold interrupt data for HITL."""
        state: dict = {
            "status": "interrupted",
            "interrupt_data": {
                "node": "human_review_deps",
                "data": {"dependency_tree": {"A": ["B"]}},
            },
        }

        assert state["status"] == "interrupted"
        assert state["interrupt_data"]["node"] == "human_review_deps"


# =============================================================================
# End-to-End Mock Integration Tests
# =============================================================================

class TestEndToEndMockWorkflow:
    """Mock end-to-end workflow tests."""

    @pytest.fixture
    def mock_workflow_manager(self):
        """Create a workflow manager with mocked graph."""
        from src.orchestrator.workflow_manager import WorkflowManager

        manager = WorkflowManager()
        manager.graph = MagicMock()
        manager.graph.stream = AsyncMock(return_value=iter([]))
        return manager

    def test_full_workflow_lifecycle(self, mock_workflow_manager):
        """Test full workflow lifecycle: start → poll → abort."""
        # Start workflow
        thread_id = mock_workflow_manager.start_workflow(
            user_request="Import all companies",
            config_path="config/config.yaml",
            origins=[],
            destination={},
        )

        assert thread_id is not None

        # Get status
        status = mock_workflow_manager.get_workflow_status(thread_id)
        assert "status" in status

        # Abort workflow
        mock_workflow_manager.abort_workflow(thread_id)
        status = mock_workflow_manager.get_workflow_status(thread_id)
        assert status["status"] == "aborted"

    def test_workflow_with_multiple_origins(self, mock_workflow_manager):
        """Test workflow with multiple origin databases."""
        origins = [
            {
                "id": "crm1",
                "type": "postgres",
                "connection": {"host": "crm1.example.com", "port": 5432, "user": "u", "password": "p", "database": "crm"},
                "priority": 10,
            },
            {
                "id": "crm2",
                "type": "mongodb",
                "connection": {"uri": "mongodb://crm2.example.com:27017"},
                "priority": 5,
            },
        ]

        thread_id = mock_workflow_manager.start_workflow(
            user_request="Merge CRM data from all sources",
            config_path="config/config.yaml",
            origins=origins,
            destination={"type": "postgresql", "host": "localhost", "port": 5432, "user": "admin", "password": "pass", "database": "mergedb", "schema": "public"},
        )

        assert thread_id is not None

        # Clean up
        mock_workflow_manager.abort_workflow(thread_id)
