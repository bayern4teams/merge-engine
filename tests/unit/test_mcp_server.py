"""Unit tests for MCP server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestInitializeServer:
    """Tests for initialize_server and _get_config."""

    def test_initialize_server_sets_config(self) -> None:
        """Test that initialize_server sets the global config."""
        from src.mcp_server.server import _get_config, initialize_server

        mock_config = MagicMock()
        initialize_server(mock_config)

        assert _get_config() is mock_config

    def test_get_config_raises_when_not_initialized(self) -> None:
        """Test _get_config raises RuntimeError when not initialized."""
        # Reset the global config
        import src.mcp_server.server as server_mod
        from src.mcp_server.server import _get_config

        original = server_mod._config
        try:
            server_mod._config = None
            with pytest.raises(RuntimeError, match="MCP server not initialized"):
                _get_config()
        finally:
            server_mod._config = original


class TestListTools:
    """Tests for list_tools."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_tools(self) -> None:
        """Test that list_tools returns all expected tools."""
        from src.mcp_server.server import list_tools

        tools = await list_tools()
        tool_names = [t.name for t in tools]

        expected_tools = [
            "discover_schema",
            "extract_data",
            "resolve_entities",
            "unify_entity_group",
            "map_fields",
            "load_data",
            "extend_schema",
        ]

        for name in expected_tools:
            assert name in tool_names, f"Expected tool '{name}' not found"

    @pytest.mark.asyncio
    async def test_list_tools_has_correct_count(self) -> None:
        """Test that list_tools returns the expected number of tools."""
        from src.mcp_server.server import list_tools

        tools = await list_tools()
        # mark_imported was removed: origins are read-only, incremental
        # tracking now lives in the destination's extraction-state table.
        assert len(tools) == 7

    @pytest.mark.asyncio
    async def test_list_tools_discover_schema_schema(self) -> None:
        """Test discover_schema tool input schema."""
        from src.mcp_server.server import list_tools

        tools = await list_tools()
        discover_tool = next(t for t in tools if t.name == "discover_schema")

        assert "origin_id" in discover_tool.inputSchema["properties"]
        assert discover_tool.inputSchema["properties"]["origin_id"]["type"] == "string"

    @pytest.mark.asyncio
    async def test_list_tools_extract_data_schema(self) -> None:
        """Test extract_data tool input schema."""
        from src.mcp_server.server import list_tools

        tools = await list_tools()
        extract_tool = next(t for t in tools if t.name == "extract_data")

        required = extract_tool.inputSchema.get("required", [])
        assert "origin_id" in required
        assert "table_name" in required
        assert "columns" in required

    @pytest.mark.asyncio
    async def test_list_tools_resolve_entities_schema(self) -> None:
        """Test resolve_entities tool input schema."""
        from src.mcp_server.server import list_tools

        tools = await list_tools()
        resolve_tool = next(t for t in tools if t.name == "resolve_entities")

        required = resolve_tool.inputSchema.get("required", [])
        assert "entity_type" in required
        assert "records" in required

    @pytest.mark.asyncio
    async def test_list_tools_map_fields_schema(self) -> None:
        """Test map_fields tool input schema."""
        from src.mcp_server.server import list_tools

        tools = await list_tools()
        map_tool = next(t for t in tools if t.name == "map_fields")

        required = map_tool.inputSchema.get("required", [])
        assert "entity_type" in required
        assert "unified_schema" in required
        assert "destination_schema" in required


class TestCallTool:
    """Tests for call_tool dispatch."""

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool(self) -> None:
        """Test that calling an unknown tool raises ValueError."""
        from src.mcp_server.server import call_tool

        with pytest.raises(ValueError, match="Unknown tool"):
            await call_tool("nonexistent_tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_discover_schema(self) -> None:
        """Test calling discover_schema tool."""
        from src.mcp_server.server import call_tool

        mock_config = MagicMock()
        mock_origin = MagicMock()
        mock_origin.id = "test_origin"
        mock_origin.type = "postgres"
        mock_origin.priority = 1
        mock_origin.connection = MagicMock()
        mock_origin.connection.model_dump.return_value = {"host": "localhost"}
        mock_config.origins = [mock_origin]

        with (
            patch(
                "src.mcp_server.server._get_config",
                return_value=mock_config,
            ),
            patch("src.mcp_server.tools.discover_schema.create_connector") as mock_create,
        ):
            mock_conn = MagicMock()
            mock_conn.close = AsyncMock()
            mock_conn.discover_schema = AsyncMock(
                return_value={
                    "tables": [],
                }
            )
            mock_create.return_value = mock_conn

            result = await call_tool(
                "discover_schema",
                {
                    "origin_id": "test_origin",
                },
            )

        assert len(result) == 1
        assert result[0].type == "text"
        import json

        parsed = json.loads(result[0].text)
        assert "origin_id" in parsed


class TestToolHandlersRegistry:
    """Tests for _TOOL_HANDLERS registry."""

    def test_tool_handlers_registered(self) -> None:
        """Test that all expected tools are registered in handlers."""
        from src.mcp_server.server import _TOOL_HANDLERS

        expected_handlers = [
            "discover_schema",
            "extract_data",
            "resolve_entities",
            "unify_entity_group",
            "map_fields",
            "load_data",
            "extend_schema",
        ]

        for name in expected_handlers:
            assert name in _TOOL_HANDLERS, f"Handler for '{name}' not registered"

    def test_register_tool_adds_handler(self) -> None:
        """Test that register_tool adds a handler to the registry."""
        from src.mcp_server.server import _TOOL_HANDLERS, register_tool

        mock_handler = MagicMock()
        register_tool("custom_tool", mock_handler)

        assert "custom_tool" in _TOOL_HANDLERS
        assert _TOOL_HANDLERS["custom_tool"] is mock_handler

        # Clean up
        del _TOOL_HANDLERS["custom_tool"]
