"""Integration tests for load_data tool and node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLoadDataNodeIntegration:
    """Test load_data node integrated with orchestrator state."""

    @pytest.mark.asyncio
    async def test_load_data_node_basic_flow(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test load data node with valid unified data."""
        from src.orchestrator.nodes.load_data import load_data_node

        dependency_state["target_entities"] = ["company", "person"]
        dependency_state["unified_entities"] = {
            "company": [
                {"id": 1, "name": "Acme Corp", "email": "info@acme.com"},
            ],
            "person": [
                {"id": 1, "name": "John Smith", "email": "john@example.com"},
            ],
        }
        dependency_state["field_mappings"] = {
            "company": {
                "name": {"dest_column": "name", "confidence": 0.98},
                "email": {"dest_column": "email", "confidence": 0.95},
            },
            "person": {
                "name": {"dest_column": "name", "confidence": 0.97},
                "email": {"dest_column": "email", "confidence": 0.96},
            },
        }
        dependency_state["extraction_order"] = ["company", "person"]
        dependency_state["destination"] = {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "schema": "public",
        }

        mock_databricks = MagicMock()
        mock_databricks.load_batch = AsyncMock(
            return_value={"inserted": 2, "updated": 0}
        )
        mock_databricks.close = AsyncMock()

        with patch("src.orchestrator.nodes.load_data.create_connector") as mock_create:
            mock_create.return_value = mock_databricks
            result = await load_data_node(dependency_state)

        assert "errors" in result

    @pytest.mark.asyncio
    async def test_load_data_node_no_unified_data(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test load data with empty unified entities."""
        from src.orchestrator.nodes.load_data import load_data_node

        dependency_state["target_entities"] = ["company"]
        dependency_state["unified_entities"] = {}
        dependency_state["field_mappings"] = {}
        dependency_state["extraction_order"] = ["company"]
        dependency_state["destination"] = {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "schema": "public",
        }

        mock_databricks = MagicMock()
        mock_databricks.load_batch = AsyncMock(
            return_value={"inserted": 0, "updated": 0}
        )
        mock_databricks.close = AsyncMock()

        with patch("src.orchestrator.nodes.load_data.create_connector") as mock_create:
            mock_create.return_value = mock_databricks
            result = await load_data_node(dependency_state)

        assert "errors" in result

    @pytest.mark.asyncio
    async def test_load_data_node_connector_failure(
        self,
        dependency_state: dict[str, Any],
    ) -> None:
        """Test load data handles connector errors gracefully."""
        from src.orchestrator.nodes.load_data import load_data_node

        dependency_state["target_entities"] = ["company"]
        dependency_state["unified_entities"] = {
            "company": [{"id": 1, "name": "Test"}],
        }
        dependency_state["field_mappings"] = {
            "company": {"name": {"dest_column": "name", "confidence": 0.98}},
        }
        dependency_state["extraction_order"] = ["company"]
        dependency_state["destination"] = {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "schema": "public",
        }

        mock_pg = MagicMock()
        mock_pg.load_batch = AsyncMock(
            side_effect=ConnectionError("PostgreSQL unreachable")
        )

        with patch("src.orchestrator.nodes.load_data.create_connector") as mock_create:
            mock_create.return_value = mock_pg
            result = await load_data_node(dependency_state)

        assert "errors" in result
        assert len(result["errors"]) > 0


class TestLoadDataToolIntegration:
    """Test load_data MCP tool."""

    @pytest.mark.asyncio
    async def test_load_data_tool_success(
        self,
    ) -> None:
        """Test load_data tool with mocked connector."""
        from src.mcp_server.tools.load_data import load_data_impl

        records = [
            {"id": "uuid-1", "name": "Acme Corp", "email": "info@acme.com"},
            {"id": "uuid-2", "name": "Beta Inc", "email": "beta@inc.com"},
        ]
        mapping = {
            "name": {"dest_column": "company_name", "confidence": 0.98},
            "email": {"dest_column": "contact_email", "confidence": 0.95},
        }

        dest_config = {
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "schema": "public",
        }

        # load_batch is a SYNC method (decorated with @retry_on_transient)
        mock_connector = MagicMock()
        mock_connector.load_batch.return_value = {"inserted": 2, "updated": 0}

        with patch("src.mcp_server.tools.load_data._get_destination_connector", return_value=mock_connector):
            result = await load_data_impl(
                entity_type="company",
                records=records,
                mapping=mapping,
                destination_config=dest_config,
            )

        assert result["success"] is True
        assert result["total_records"] == 2
        assert result["entity_type"] == "company"

    @pytest.mark.asyncio
    async def test_load_data_tool_empty_records(
        self,
    ) -> None:
        """Test load_data tool with empty records list."""
        from src.mcp_server.tools.load_data import load_data_impl

        result = await load_data_impl(
            entity_type="company",
            records=[],
            mapping=None,
        )

        assert result["success"] is True
        assert result["total_records"] == 0
        assert result["inserted"] == 0
        assert result["updated"] == 0

    @pytest.mark.asyncio
    async def test_load_data_tool_missing_mapping(
        self,
    ) -> None:
        """Test load_data tool handles no mapping by passing through fields."""
        from src.mcp_server.tools.load_data import load_data_impl

        records = [
            {"id": "uuid-1", "name": "Acme Corp", "email": "info@acme.com"},
        ]

        dest_config = {
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "schema": "public",
        }

        mock_connector = MagicMock()
        mock_connector.load_batch.return_value = {"inserted": 1, "updated": 0}

        with patch("src.mcp_server.tools.load_data._get_destination_connector", return_value=mock_connector):
            result = await load_data_impl(
                entity_type="company",
                records=records,
                mapping=None,
                destination_config=dest_config,
            )

        assert result["success"] is True
        assert result["total_records"] == 1

    @pytest.mark.asyncio
    async def test_load_data_tool_transform_records(
        self,
        mock_config: Any,
        patch_server_config: Any,
    ) -> None:
        """Test that records are transformed correctly using the mapping."""
        from src.mcp_server.tools.load_data import _transform_records

        records = [
            {"id": "uuid-1", "name": "Acme Corp", "email": "info@acme.com"},
        ]
        mapping = {
            "name": {"dest_column": "company_name", "confidence": 0.98},
            "email": {"dest_column": "contact_email", "confidence": 0.95},
        }

        transformed = _transform_records(records, mapping, "companies")

        assert len(transformed) == 1
        assert transformed[0]["company_name"] == "Acme Corp"
        assert transformed[0]["contact_email"] == "info@acme.com"
        assert "id" in transformed[0]
        assert "_source_ids" in transformed[0]

    @pytest.mark.asyncio
    async def test_load_data_tool_transform_without_mapping(
        self,
        mock_config: Any,
        patch_server_config: Any,
    ) -> None:
        """Test transform passes through non-internal fields when no mapping."""
        from src.mcp_server.tools.load_data import _transform_records

        records = [
            {"id": "uuid-1", "name": "Acme Corp", "_internal": "skip"},
        ]

        transformed = _transform_records(records, None, "companies")

        assert len(transformed) == 1
        assert transformed[0]["name"] == "Acme Corp"
        assert "_internal" not in transformed[0]
        assert "id" in transformed[0]
