"""Integration tests for field mapping node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFieldMappingNodeIntegration:
    """Test map_fields_node integrated with orchestrator state."""

    @pytest.mark.asyncio
    async def test_map_fields_node_basic_flow(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test field mapping node produces mappings for target entities."""
        from src.orchestrator.nodes.map_fields_node import map_fields

        dependency_state["target_entities"] = ["company", "person"]
        dependency_state["unified_entities"] = {
            "company": [
                {"name": "Acme Corp", "email": "info@acme.com"},
            ],
            "person": [
                {"name": "John Smith", "email": "john@example.com"},
            ],
        }
        dependency_state["field_mappings"] = {}
        dependency_state["destination"] = MagicMock(
            type="databricks",
            tables={"company": "companies", "person": "people"},
            connection=MagicMock(model_dump=MagicMock(return_value={
                "type": "databricks", "host": "test", "token": "test",
                "schema": "test", "tables": {"company": "companies", "person": "people"},
            })),
            catalog="main",
            schema="test",
        )

        result = await map_fields(dependency_state)

        assert "field_mappings" in result
        assert "errors" in result

    @pytest.mark.asyncio
    async def test_map_fields_node_empty_entities(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test field mapping with no target entities."""
        from src.orchestrator.nodes.map_fields_node import map_fields

        dependency_state["target_entities"] = []
        dependency_state["field_mappings"] = {}

        result = await map_fields(dependency_state)

        assert "field_mappings" in result
        assert isinstance(result["field_mappings"], dict)

    @pytest.mark.asyncio
    async def test_map_fields_node_handles_llm_failure(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test that errors are caught and logged."""
        from src.orchestrator.nodes.map_fields_node import map_fields

        dependency_state["target_entities"] = ["company"]
        dependency_state["unified_entities"] = {"company": []}
        dependency_state["field_mappings"] = {}
        dependency_state["destination"] = MagicMock(
            type="databricks",
            tables={"company": "companies"},
            connection=MagicMock(model_dump=MagicMock(return_value={
                "type": "databricks", "host": "test", "token": "test",
            })),
            catalog="main",
            schema="test",
        )

        result = await map_fields(dependency_state)

        assert "errors" in result

    @pytest.mark.asyncio
    async def test_map_fields_node_multiple_entities(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test field mapping across multiple entity types."""
        from src.orchestrator.nodes.map_fields_node import map_fields

        dependency_state["target_entities"] = ["company", "person", "country"]
        dependency_state["unified_entities"] = {
            "company": [{"name": "Acme", "email": "info@acme.com"}],
            "person": [{"name": "John", "email": "john@example.com"}],
            "country": [{"name": "USA", "code": "US"}],
        }
        dependency_state["field_mappings"] = {}
        dependency_state["destination"] = MagicMock(
            type="databricks",
            tables={"company": "companies", "person": "people", "country": "countries"},
            connection=MagicMock(model_dump=MagicMock(return_value={
                "type": "databricks", "host": "test", "token": "test",
            })),
            catalog="main",
            schema="test",
        )

        result = await map_fields(dependency_state)

        assert "field_mappings" in result
        assert isinstance(result["field_mappings"], dict)


class TestFieldMappingIntegration:
    """Test map_fields MCP tool integration."""

    @pytest.mark.asyncio
    async def test_map_fields_tool_produces_mapping(
        self,
        mock_config: Any,
        patch_server_config: Any,
    ) -> None:
        """Test map_fields tool returns field mappings via heuristic matching."""
        from src.mcp_server.tools.map_fields import map_fields_impl

        unified_schema = {
            "name": {"type": "VARCHAR", "sample_value": "Acme Corp"},
            "email": {"type": "VARCHAR", "sample_value": "info@acme.com"},
        }

        destination_schema = {
            "table": "companies",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "company_name", "type": "VARCHAR"},
                {"name": "contact_email", "type": "VARCHAR"},
            ],
        }

        result = await map_fields_impl(
            entity_type="company",
            unified_schema=unified_schema,
            destination_schema=destination_schema,
        )

        assert "field_mapping" in result

    @pytest.mark.asyncio
    async def test_map_fields_tool_entity_not_in_tables(
        self,
        mock_config: Any,
        patch_server_config: Any,
    ) -> None:
        """Test map_fields tool returns mapping even when no matching columns."""
        from src.mcp_server.tools.map_fields import map_fields_impl

        unified_schema = {
            "name": {"type": "VARCHAR", "sample_value": "Test"},
        }

        destination_schema = {
            "table": "companies",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "company_name", "type": "VARCHAR"},
            ],
        }

        result = await map_fields_impl(
            entity_type="company",
            unified_schema=unified_schema,
            destination_schema=destination_schema,
        )

        assert "field_mapping" in result

    @pytest.mark.asyncio
    async def test_map_fields_tool_exact_match_confidence(
        self,
        mock_config: Any,
        patch_server_config: Any,
    ) -> None:
        """Test that exact name matches produce high confidence."""
        from src.mcp_server.tools.map_fields import map_fields_impl

        unified_schema = {
            "name": {"type": "VARCHAR", "sample_value": "Acme Corp"},
            "email": {"type": "VARCHAR", "sample_value": "info@acme.com"},
        }

        destination_schema = {
            "table": "companies",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "VARCHAR"},
                {"name": "email", "type": "VARCHAR"},
            ],
        }

        result = await map_fields_impl(
            entity_type="company",
            unified_schema=unified_schema,
            destination_schema=destination_schema,
        )

        # Exact matches should have confidence 1.0
        mapping = result["field_mapping"]
        assert mapping["name"]["confidence"] == 1.0
        assert mapping["email"]["confidence"] == 1.0