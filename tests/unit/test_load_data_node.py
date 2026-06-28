"""Unit tests for load_data node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.load_data import load_data_node, flag_origins_node


class TestLoadDataNode:
    """Tests for the load_data_node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all companies",
            "target_entities": ["company"],
            "extraction_order": ["company"],
            "unified_entities": {
                "company": [
                    {
                        "id": "cluster-1",
                        "name": "Acme Corp",
                        "email": "info@acme.com",
                    },
                    {
                        "id": "cluster-2",
                        "name": "Beta Inc",
                        "email": "info@beta.com",
                    },
                ],
            },
            "field_mappings": {
                "company": {
                    "name": {"dest_column": "name", "requires_schema_change": False},
                    "email": {"dest_column": "email", "requires_schema_change": False},
                },
            },
            "destination": {
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "tables": {
                    "company": "merged_companies",
                },
            },
            "pending_schema_changes": [],
            "errors": [],
        }

    @pytest.fixture
    def mock_load_impl(self) -> AsyncMock:
        """Mock load_data_impl."""
        return AsyncMock(return_value={
            "total_records": 2,
            "inserted": 2,
            "updated": 0,
        })

    @pytest.mark.asyncio
    async def test_load_data_success(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test successful data loading."""
        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        assert not result["errors"]
        mock_load_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_applies_field_mapping(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that field mappings are applied to transformed records."""
        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        call_kwargs = mock_load_impl.call_args[1]
        assert call_kwargs["entity_type"] == "company"
        assert len(call_kwargs["records"]) == 2

    @pytest.mark.asyncio
    async def test_load_data_skips_empty_entity(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that entities with no unified records are skipped."""
        base_state["unified_entities"]["company"] = []

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        mock_load_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_data_load_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of load failure."""
        async def fail_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Load service error")

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            side_effect=fail_impl,
        ):
            result = await load_data_node(base_state)

        assert len(result["errors"]) == 1
        assert "Load failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_load_data_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that existing errors are preserved."""
        base_state["errors"] = ["pre-existing"]

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        assert "pre-existing" in result["errors"]

    @pytest.mark.asyncio
    async def test_load_data_applies_schema_changes(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that pending schema changes are applied before loading."""
        base_state["pending_schema_changes"] = [
            {
                "column": "phone",
                "data_type": "VARCHAR",
                "table": "merged_companies",
                "entity_type": "company",
            }
        ]

        mock_connector = MagicMock()
        mock_connector.extend_schema_batch = AsyncMock(return_value={"added": 1, "failed": 0})

        mock_dest_config = MagicMock()
        mock_dest_config.model_dump = MagicMock(return_value={
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
        })

        mock_destination = MagicMock()
        mock_destination.type = "postgresql"
        mock_destination.tables = {"company": "merged_companies"}
        mock_destination.connection = mock_dest_config

        base_state["destination"] = mock_destination

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ), patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=mock_connector,
        ):
            await load_data_node(base_state)

        mock_connector.extend_schema_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_dict_destination(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test with dict-style destination."""
        base_state["destination"] = {
            "type": "postgresql",
            "tables": {"company": "test_table"},
        }

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        mock_load_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_entity_not_in_mapping(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test loading entity with no field mapping."""
        base_state["field_mappings"]["company"] = {}

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        mock_load_impl.assert_called_once()
        call_kwargs = mock_load_impl.call_args[1]
        assert "records" in call_kwargs

    @pytest.mark.asyncio
    async def test_load_data_skips_schema_change_fields(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that fields requiring schema changes are excluded from load."""
        base_state["field_mappings"]["company"] = {
            "name": {"dest_column": "name", "requires_schema_change": False},
            "phone": {"dest_column": "phone", "requires_schema_change": True},
        }

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        mock_load_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_multiple_entities_in_order(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test loading multiple entities in extraction order."""
        base_state["extraction_order"] = ["company", "person"]
        base_state["unified_entities"]["person"] = [
            {"id": "cluster-3", "name": "John Doe"},
        ]
        base_state["field_mappings"]["person"] = {
            "name": {"dest_column": "name", "requires_schema_change": False},
        }

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        assert mock_load_impl.call_count == 2


class TestFlagOriginsNode:
    """Tests for the flag_origins_node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state with extracted data."""
        return {
            "extracted_data": {
                "company": [
                    {
                        "_origin_id": "postgres_main",
                        "_origin_table": "companies",
                        "_origin_id_row": "1",
                    },
                    {
                        "_origin_id": "postgres_main",
                        "_origin_table": "companies",
                        "_origin_id_row": "2",
                    },
                    {
                        "_origin_id": "mongo_analytics",
                        "_origin_table": "leads",
                        "_origin_id_row": "abc",
                    },
                ],
            },
            "origins": [
                {
                    "id": "postgres_main",
                    "type": "postgres",
                    "connection": {"host": "localhost", "port": 5432, "database": "test"},
                },
                {
                    "id": "mongo_analytics",
                    "type": "mongodb",
                    "connection": {"host": "localhost", "port": 27017, "database": "analytics"},
                },
            ],
            "errors": [],
        }

    @pytest.mark.asyncio
    async def test_flag_origins_success(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test successful origin flagging."""
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=mock_connector,
        ):
            result = await flag_origins_node(base_state)

        assert not result["errors"]
        assert mock_connector.mark_imported.call_count == 2  # Once per origin

    @pytest.mark.asyncio
    async def test_flag_origins_groups_by_origin_and_table(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that rows are grouped by origin and table."""
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=mock_connector,
        ):
            await flag_origins_node(base_state)

        # Should group: postgres_main:companies gets 2 IDs, mongo_analytics:leads gets 1
        calls = mock_connector.mark_imported.call_args_list
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_flag_origins_mark_imported_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of mark_imported failure."""
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(
            side_effect=Exception("Mark failed")
        )

        with patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=mock_connector,
        ):
            result = await flag_origins_node(base_state)

        assert len(result["errors"]) >= 1
        assert any("Mark imported failed" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_flag_origins_unknown_origin(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of unknown origin in extracted data."""
        base_state["extracted_data"]["company"][0]["_origin_id"] = "unknown_origin"

        with patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=MagicMock(),
        ):
            result = await flag_origins_node(base_state)

        assert len(result["errors"]) >= 1
        assert any("Unknown origin" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_flag_origins_preserves_existing_errors(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that existing errors are preserved."""
        base_state["errors"] = ["pre-existing"]
        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=mock_connector,
        ):
            result = await flag_origins_node(base_state)

        assert "pre-existing" in result["errors"]

    @pytest.mark.asyncio
    async def test_flag_origins_no_extracted_data(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with no extracted data."""
        base_state["extracted_data"] = {}

        with patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=MagicMock(),
        ) as mock_create:
            result = await flag_origins_node(base_state)

        assert not result["errors"]
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_flag_origins_object_origin_with_model_dump(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with origin objects that have model_dump method."""
        class OriginObj:
            def __init__(self, oid: str, otype: str) -> None:
                self.id = oid
                self.type = otype
                self.connection = MagicMock()
                self.connection.model_dump = MagicMock(return_value={"host": "localhost"})

        # Both origins must be present since extracted_data references both
        base_state["origins"] = [
            OriginObj("postgres_main", "postgres"),
            OriginObj("mongo_analytics", "mongodb"),
        ]
        base_state["extracted_data"]["company"][0]["_origin_id"] = "postgres_main"

        mock_connector = MagicMock()
        mock_connector.mark_imported = AsyncMock(return_value=True)

        with patch(
            "src.orchestrator.nodes.load_data.create_connector",
            return_value=mock_connector,
        ):
            result = await flag_origins_node(base_state)

        assert not result["errors"]
