"""Unit tests for load_data MCP tool."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.load_data import (
    _get_destination_connector,
    _transform_records,
    _translate_foreign_keys,
    load_data_handler,
    load_data_impl,
)


class TestLoadDataImpl:
    """Tests for load_data_impl."""

    @pytest.fixture
    def mock_config(self) -> MagicMock:
        """Mock config with destination."""
        config = MagicMock()
        config.destination.type = "databricks"
        config.destination.host = "localhost"
        config.destination.port = 443
        config.destination.user = "testuser"
        config.destination.password = "testpass"
        config.destination.database = "testdb"
        config.destination.schema_name = "public"
        return config

    @pytest.fixture
    def sample_records(self) -> list[dict[str, Any]]:
        """Sample unified records."""
        return [
            {
                "id": "uuid-1",
                "name": "Alice",
                "email": "alice@example.com",
                "_cluster_id": "cluster-1",
                "_source_ids": json.dumps([{"cluster_id": "uuid-1"}]),
                "_source_count": 2,
            },
            {
                "id": "uuid-2",
                "name": "Bob",
                "email": "bob@example.com",
                "_cluster_id": "cluster-2",
                "_source_ids": json.dumps([{"cluster_id": "uuid-2"}]),
                "_source_count": 1,
            },
        ]

    @pytest.mark.asyncio
    async def test_load_data_success(
        self,
        sample_records: list[dict[str, Any]],
    ) -> None:
        """Test successful data loading."""
        mock_connector = MagicMock()
        mock_connector.load_batch = MagicMock(
            return_value={
                "inserted": 2,
                "updated": 0,
            }
        )

        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            result = await load_data_impl(
                entity_type="person",
                records=sample_records,
            )

        assert result["success"] is True
        assert result["entity_type"] == "person"
        assert result["total_records"] == 2
        assert result["inserted"] == 2
        assert result["updated"] == 0
        mock_connector.load_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_translates_foreign_keys_after_mapping(self) -> None:
        # The FK column ("industry_id") only exists on the record after
        # _transform_records renames the raw source field ("sourceCategory")
        # to it — translation must see the post-mapping shape, not the raw one.
        records = [{"id": "c1", "sourceCategory": "raw-industry-id"}]
        mapping = {"sourceCategory": {"dest_column": "industry_id"}}
        mock_connector = MagicMock()
        mock_connector.load_batch = MagicMock(return_value={"inserted": 1, "updated": 0})

        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            await load_data_impl(
                entity_type="company",
                records=records,
                mapping=mapping,
                table_name="company",
                connector=mock_connector,
                foreign_keys={"industry_id": "industry"},
                cluster_lookup_by_table={"industry": {"raw-industry-id": "industry-cluster-1"}},
            )

        loaded = mock_connector.load_batch.call_args.kwargs["records"]
        assert loaded[0]["industry_id"] == "industry-cluster-1"

    @pytest.mark.asyncio
    async def test_load_data_empty_records(self) -> None:
        """Test load_data with empty records list."""
        result = await load_data_impl(
            entity_type="person",
            records=[],
        )

        assert result["success"] is True
        assert result["total_records"] == 0
        assert result["inserted"] == 0
        assert result["updated"] == 0
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_load_data_with_mapping(
        self,
        sample_records: list[dict[str, Any]],
    ) -> None:
        """Test data loading with field mapping."""
        mapping = {
            "name": {"dest_column": "full_name", "confidence": 0.95},
            "email": {"dest_column": "email_address", "confidence": 0.90},
        }
        mock_connector = MagicMock()
        mock_connector.load_batch = MagicMock(
            return_value={
                "inserted": 2,
                "updated": 0,
            }
        )

        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            result = await load_data_impl(
                entity_type="person",
                records=sample_records,
                mapping=mapping,
            )

        assert result["success"] is True
        assert result["total_records"] == 2

    @pytest.mark.asyncio
    async def test_load_data_with_conflict_data(
        self,
    ) -> None:
        """Test data loading with conflict data in records."""
        records = [
            {
                "id": "uuid-1",
                "name": {
                    "conflicts": [
                        {"origin": "A", "value": "Alice"},
                        {"origin": "B", "value": "Alicia"},
                    ]
                },
                "_cluster_id": "cluster-1",
                "_source_ids": json.dumps([{"cluster_id": "uuid-1"}]),
            },
        ]
        mock_connector = MagicMock()
        mock_connector.load_batch = MagicMock(
            return_value={
                "inserted": 1,
                "updated": 0,
            }
        )

        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            result = await load_data_impl(
                entity_type="person",
                records=records,
            )

        assert result["success"] is True
        assert result["total_records"] == 1

    @pytest.mark.asyncio
    async def test_load_data_destination_connector_failure(self) -> None:
        """Test load_data when destination connector creation fails."""
        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            side_effect=Exception("Destination config error"),
        ):
            from src.orchestrator.utils.retry import PermanentError

            with pytest.raises(PermanentError, match="Failed to create destination connector"):
                await load_data_impl(
                    entity_type="person",
                    records=[{"id": "uuid-1"}],
                )

    @pytest.mark.asyncio
    async def test_load_data_load_batch_failure(
        self,
        sample_records: list[dict[str, Any]],
    ) -> None:
        """Test load_data when load_batch fails."""
        mock_connector = MagicMock()
        mock_connector.load_batch = MagicMock(side_effect=Exception("INSERT failed"))

        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            from src.orchestrator.utils.retry import PermanentError

            with pytest.raises(PermanentError, match="Load failed for entity 'person'"):
                await load_data_impl(
                    entity_type="person",
                    records=sample_records,
                )

    @pytest.mark.asyncio
    async def test_load_data_with_destination_config(
        self,
        sample_records: list[dict[str, Any]],
    ) -> None:
        """Test load_data with explicit destination config."""
        dest_config = {
            "host": "custom-host.databricks.com",
            "port": 443,
            "user": "custom_user",
            "password": "custom_pass",
            "database": "custom_db",
            "schema": "custom_schema",
        }
        mock_connector = MagicMock()
        mock_connector.load_batch = MagicMock(
            return_value={
                "inserted": 2,
                "updated": 0,
            }
        )

        with patch(
            "src.mcp_server.tools.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            result = await load_data_impl(
                entity_type="person",
                records=sample_records,
                destination_config=dest_config,
            )

        assert result["total_records"] == 2


class TestTranslateForeignKeys:
    """Fix #7 (part 2): FK translation must run on transformed (dest-column
    keyed) records — a free-text field that was heuristically mapped straight
    onto an FK column (e.g. company's own "sourceCategory" -> industry_id,
    never a real pointer to any industry row) must be nulled rather than
    inserted as-is, which is exactly what crashed load_data on Boeing.
    """

    def test_free_text_value_with_no_matching_cluster_is_nulled(self) -> None:
        records = [{"industry_id": "Space And Satellite Manufacturers"}]
        translated = _translate_foreign_keys(
            records,
            {"industry_id": "industry"},
            {"industry": {"3e53fd25b09b25ae": "industry-cid-1"}},
        )
        assert translated == 1
        assert records[0]["industry_id"] is None

    def test_genuine_source_id_is_translated(self) -> None:
        records = [{"industry_id": "3e53fd25b09b25ae"}]
        translated = _translate_foreign_keys(
            records,
            {"industry_id": "industry"},
            {"industry": {"3e53fd25b09b25ae": "industry-cid-1"}},
        )
        assert translated == 1
        assert records[0]["industry_id"] == "industry-cid-1"

    def test_referenced_entity_with_zero_clusters_still_nulls_the_value(self) -> None:
        # technologic_category was resolved to entity type "technology_category"
        # but that entity type had no records extracted this run at all — the
        # lookup for it is a real, empty dict (key present), not an absent one.
        # Every value pointing there is unconditionally unresolvable.
        records = [{"category_id": '["IT Asset Management Software"]'}]
        translated = _translate_foreign_keys(
            records,
            {"category_id": "technologic_category"},
            {"technologic_category": {}},
        )
        assert translated == 1
        assert records[0]["category_id"] is None

    def test_referenced_table_absent_entirely_leaves_value_untouched(self) -> None:
        # Genuinely unknown (caller never even attempted to resolve this
        # table to an entity type) is different from "resolved, zero
        # clusters" — leave it for the database to judge instead of guessing.
        records = [{"category_id": "some-id"}]
        translated = _translate_foreign_keys(records, {"category_id": "technologic_category"}, {})
        assert translated == 0
        assert records[0]["category_id"] == "some-id"


class TestTransformRecords:
    """Tests for _transform_records."""

    def test_transform_with_mapping(self) -> None:
        """Test transforming records with field mapping."""
        records = [
            {
                "id": "uuid-1",
                "name": "Alice",
                "age": 30,
                "_cluster_id": "cluster-1",
                "_source_ids": json.dumps([{"id": "row_1"}]),
            },
        ]
        mapping = {
            "name": {"dest_column": "full_name", "confidence": 0.95},
            "age": {"dest_column": "user_age", "confidence": 0.90},
        }

        result = _transform_records(records, mapping, "users")

        assert len(result) == 1
        assert result[0]["full_name"] == "Alice"
        assert result[0]["user_age"] == 30
        assert "id" in result[0]

    def test_many_to_one_mapping_coalesces(self) -> None:
        """Two source fields mapping to one dest column must coalesce:
        the absent origin's None must not clobber the mapped value."""
        records = [
            {"org_title": "Titan Labs", "_cluster_id": "c1"},
            {"company_name": "Acme Corp", "_cluster_id": "c2"},
        ]
        mapping = {
            # Iteration order deliberately puts each record's present
            # field first for one record and last for the other.
            "company_name": {"dest_column": "name", "confidence": 0.95},
            "org_title": {"dest_column": "name", "confidence": 0.9},
        }
        result = _transform_records(records, mapping, "companies")
        assert result[0]["name"] == "Titan Labs"
        assert result[1]["name"] == "Acme Corp"

    def test_null_mapped_id_is_backfilled(self) -> None:
        """A mapping like person_id -> id must not leave id NULL for
        records whose origin lacks that source field."""
        records = [
            {"staff_id": 7, "_cluster_id": "cluster-77"},
        ]
        mapping = {
            "person_id": {"dest_column": "id", "confidence": 0.9},
            "staff_id": {"dest_column": "staff_id", "confidence": 0.9},
        }
        result = _transform_records(records, mapping, "persons")
        assert result[0]["id"] == "cluster-77"

    def test_transform_no_mapping_passes_through(self) -> None:
        """Test that records without mapping pass through non-internal fields."""
        records = [
            {
                "id": "uuid-1",
                "name": "Alice",
                "_internal_field": "should_be_removed",
                "_cluster_id": "cluster-1",
            },
        ]

        result = _transform_records(records, None, "users")

        assert len(result) == 1
        assert result[0]["name"] == "Alice"
        assert "_internal_field" not in result[0]
        assert "_cluster_id" in result[0]

    def test_transform_keeps_schema_change_fields(self) -> None:
        """Fields requiring schema changes are kept — their columns are
        added before load, and load_batch drops any still-missing column."""
        records = [
            {
                "id": "uuid-1",
                "new_field": "value",
                "_cluster_id": "cluster-1",
            },
        ]
        mapping = {
            "new_field": {
                "dest_column": None,
                "confidence": 0.0,
                "requires_schema_change": True,
            },
        }

        result = _transform_records(records, mapping, "users")

        assert result[0]["new_field"] == "value"

    def test_transform_generates_uuid_if_missing(self) -> None:
        """Test that UUID is generated if neither id nor _cluster_id exists."""
        records = [
            {"name": "Alice"},
        ]

        with patch(
            "src.mcp_server.tools.load_data.generate_uuid",
            return_value="generated-uuid",
        ):
            result = _transform_records(records, None, "users")

        assert result[0]["id"] == "generated-uuid"

    def test_transform_serializes_source_ids(self) -> None:
        """Test that source_ids are properly serialized."""
        records = [
            {
                "id": "uuid-1",
                "_cluster_id": "cluster-1",
                "_source_ids": json.dumps(
                    [
                        {"cluster_id": "uuid-1"},
                        {"row_id": "row_1"},
                    ]
                ),
            },
        ]

        result = _transform_records(records, None, "users")

        assert "_source_ids" in result[0]
        source_ids = json.loads(result[0]["_source_ids"])
        assert len(source_ids) == 2

    def test_transform_handles_invalid_source_ids_json(self) -> None:
        """Test handling of invalid source_ids JSON."""
        records = [
            {
                "id": "uuid-1",
                "_cluster_id": "cluster-1",
                "_source_ids": "invalid-json",
            },
        ]

        result = _transform_records(records, None, "users")

        assert "_source_ids" in result[0]


class TestGetDestinationConnector:
    """Tests for _get_destination_connector."""

    def test_get_destination_connector_with_config(self) -> None:
        """Test getting destination connector with explicit config."""
        config = {
            "host": "custom-host",
            "port": 9999,
            "user": "custom_user",
            "password": "custom_pass",
            "database": "custom_db",
            "schema": "custom_schema",
        }

        with patch("src.mcp_server.tools.load_data.PostgreSQLDestinationConnector") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            result = _get_destination_connector(config)

        mock_cls.assert_called_once_with(
            {
                "host": "custom-host",
                "port": 9999,
                "user": "custom_user",
                "password": "custom_pass",
                "database": "custom_db",
                "schema": "custom_schema",
            }
        )
        assert result == mock_instance

    def test_get_destination_connector_with_defaults(self) -> None:
        """Test getting destination connector with defaults from global config."""
        mock_config = MagicMock()
        mock_config.destination.type = "databricks"
        mock_config.destination.host = "global-host"
        mock_config.destination.port = 443
        mock_config.destination.user = "global_user"
        mock_config.destination.password = "global_pass"
        mock_config.destination.database = "global_db"
        mock_config.destination.schema_name = "public"

        mock_connector_instance = MagicMock()

        with (
            patch(
                "src.mcp_server.server._get_config",
                return_value=mock_config,
            ),
            patch(
                "src.mcp_server.tools.load_data.PostgreSQLDestinationConnector",
                return_value=mock_connector_instance,
            ),
        ):
            result = _get_destination_connector(None)

        assert result == mock_connector_instance


class TestLoadDataHandler:
    """Tests for load_data_handler."""

    @pytest.mark.asyncio
    async def test_handler_calls_impl(
        self,
    ) -> None:
        """Test handler delegates to impl."""
        mock_impl = AsyncMock(
            return_value={
                "success": True,
                "entity_type": "person",
                "total_records": 5,
                "inserted": 5,
                "updated": 0,
                "failed": 0,
            }
        )

        with patch(
            "src.mcp_server.tools.load_data.load_data_impl",
            mock_impl,
        ):
            result = await load_data_handler(
                {
                    "entity_type": "person",
                    "records": [{"id": "1"}, {"id": "2"}],
                    "mapping": {"name": {"dest_column": "full_name", "confidence": 0.9}},
                }
            )

        mock_impl.assert_called_once()
        assert result["total_records"] == 5
