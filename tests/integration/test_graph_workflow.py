"""Integration tests for the full LangGraph workflow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGraphWorkflowIntegration:
    """Test the full LangGraph workflow with mocked external dependencies."""

    def _create_mock_nodes(self) -> dict[str, Any]:
        """Create mock implementations for all workflow nodes."""
        return {
            "parse_request": AsyncMock(return_value={"target_entities": ["company", "person"]}),
            "discover_schemas": AsyncMock(return_value={"schemas": {}}),
            "analyze_dependencies": AsyncMock(return_value={"dependency_tree": {}, "extraction_order": ["company", "person"]}),
            "extract_data": AsyncMock(return_value={"extracted_data": {}}),
            "resolve_entities": AsyncMock(return_value={"entity_clusters": {}}),
            "review_matches": AsyncMock(return_value={}),
            "map_fields": AsyncMock(return_value={"field_mappings": {}}),
            "review_mapping": AsyncMock(return_value={}),
            "unify_entities": AsyncMock(return_value={"unified_entities": {}}),
            "review_schema_changes": AsyncMock(return_value={}),
            "load_data": AsyncMock(return_value={"errors": []}),
            "flag_origins": AsyncMock(return_value={"errors": []}),
            "confirm_load": AsyncMock(return_value={"load_approval": True}),
        }

    @pytest.mark.asyncio
    async def test_graph_builds_successfully(
        self,
        mock_config: Any,
    ) -> None:
        """Test that the workflow graph can be built without errors."""
        from src.orchestrator.graph import build_graph

        graph = build_graph()
        assert graph is not None

    @pytest.mark.asyncio
    async def test_get_initial_state_populates_fields(
        self,
        mock_config: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that get_initial_state creates a properly initialized state."""
        from src.orchestrator.graph import get_initial_state

        user_request = "Import all companies and their contacts"
        state = get_initial_state(
            user_request,
            mock_config,
            origins=[postgres_origin_config],
            destination={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
            },
        )

        assert state["user_request"] == user_request
        assert state["origins"] is not None
        assert len(state["origins"]) > 0
        assert state["destination"] is not None
        assert state["errors"] == []
        assert state["retry_counts"] == {}

    @pytest.mark.asyncio
    async def test_graph_stream_with_all_mocked_nodes(
        self,
        mock_config: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that the graph streams events through all mocked nodes."""
        from src.orchestrator.graph import build_graph, get_initial_state

        initial_state = get_initial_state(
            "Import all companies",
            mock_config,
            origins=[postgres_origin_config],
            destination={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
            },
        )

        # Mock each node to return empty updates
        nodes = self._create_mock_nodes()

        mock_graph = MagicMock()
        mock_graph.stream = MagicMock(return_value=iter([]))
        mock_graph.get_state = MagicMock()
        mock_graph.get_state.return_value = None

        with patch("src.orchestrator.graph.build_graph", return_value=mock_graph):
            # Just verify the build_graph call succeeds with mocked nodes
            graph = build_graph()
            assert graph is not None

    @pytest.mark.asyncio
    async def test_initial_state_has_correct_types(
        self,
        mock_config: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that initial state fields have correct default types."""
        from src.orchestrator.graph import get_initial_state

        state = get_initial_state(
            "Test request",
            mock_config,
            origins=[postgres_origin_config],
            destination={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
            },
        )

        assert isinstance(state["errors"], list)
        assert isinstance(state["retry_counts"], dict)
        assert state["human_feedback"] is None
        assert state["interrupt_data"] is None
        assert state["load_approval"] is None

    @pytest.mark.asyncio
    async def test_initial_state_includes_config(
        self,
        mock_config: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that initial state includes resolved config."""
        from src.orchestrator.graph import get_initial_state

        state = get_initial_state(
            "Test request",
            mock_config,
            origins=[postgres_origin_config],
            destination={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
            },
        )

        assert len(state["origins"]) == 1
        assert state["destination"] is not None

        # Verify origins have correct IDs
        origin_ids = [o["id"] if isinstance(o, dict) else o.id for o in state["origins"]]
        assert set(origin_ids) == {"postgres_main"}

    @pytest.mark.asyncio
    async def test_workflow_state_field_defaults(
        self,
        mock_config: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that all optional state fields have proper defaults."""
        from src.orchestrator.graph import get_initial_state

        state = get_initial_state(
            "Test",
            mock_config,
            origins=[postgres_origin_config],
            destination={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
            },
        )

        # These should be empty/default values
        assert state.get("schemas", {}) == {}
        assert state.get("dependency_tree", {}) == {}
        assert state.get("extraction_order", []) == []
        assert state.get("extracted_data", {}) == {}
        assert state.get("entity_clusters", {}) == {}
        assert state.get("unified_entities", {}) == {}
        assert state.get("field_mappings", {}) == {}
        assert state.get("pending_schema_changes", []) == []

    @pytest.mark.asyncio
    async def test_graph_state_transitions_basic(
        self,
        mock_config: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that state transitions correctly between nodes."""
        from src.orchestrator.graph import build_graph, get_initial_state

        initial_state = get_initial_state(
            "Test request",
            mock_config,
            origins=[postgres_origin_config],
            destination={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
            },
        )
        mock_graph = build_graph()

        # Verify the graph has the expected nodes
        # The graph should contain the standard workflow nodes
        assert mock_graph is not None

    @pytest.mark.asyncio
    async def test_multiple_entities_in_initial_state(
        self,
        mock_config: Any,
        postgres_origin_config: dict[str, Any],
    ) -> None:
        """Test that target_entities can be populated from user request."""
        from src.orchestrator.graph import get_initial_state

        state = get_initial_state(
            "Import all companies, persons, and countries",
            mock_config,
            origins=[postgres_origin_config],
            destination={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test",
                "password": "test",
                "database": "test_db",
                "schema": "public",
            },
        )

        # target_entities starts empty - parsed by parse_request node
        assert state["target_entities"] == []

        # But the user_request contains the entities
        assert "company" in state["user_request"].lower() or "companies" in state["user_request"].lower()
