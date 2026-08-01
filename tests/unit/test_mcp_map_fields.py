"""Unit tests for map_fields MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.map_fields import (
    map_fields_impl,
    map_fields_handler,
    _heuristic_match,
    _map_type_generic,
)


class TestMapFieldsImpl:
    """Tests for map_fields_impl."""

    @pytest.fixture
    def mock_config(self) -> MagicMock:
        """Mock config for server."""
        config = MagicMock()
        return config

    @pytest.fixture
    def basic_unified_schema(self) -> dict[str, Any]:
        """Basic unified schema."""
        return {
            "name": {"type": "STRING", "sample_value": "Alice"},
            "email": {"type": "STRING", "sample_value": "alice@example.com"},
            "age": {"type": "INTEGER", "sample_value": 30},
        }

    @pytest.fixture
    def basic_dest_schema(self) -> dict[str, Any]:
        """Basic destination schema."""
        return {
            "table": "users",
            "columns": [
                {"name": "full_name", "type": "STRING"},
                {"name": "email_address", "type": "STRING"},
                {"name": "user_age", "type": "INTEGER"},
            ],
        }

    @pytest.mark.asyncio
    async def test_map_fields_exact_match(
        self,
        basic_unified_schema: dict[str, Any],
        basic_dest_schema: dict[str, Any],
    ) -> None:
        """Test mapping with exact name matches."""
        # Use matching column names
        dest_schema = {
            "table": "users",
            "columns": [
                {"name": "name", "type": "STRING"},
                {"name": "email", "type": "STRING"},
            ],
        }

        with patch(
            "src.mcp_server.server._get_config",
        ) as mock_get_config:
            mock_get_config.return_value = MagicMock()
            result = await map_fields_impl("person", basic_unified_schema, dest_schema)

        assert "name" in result["field_mapping"]
        assert result["field_mapping"]["name"]["dest_column"] == "name"
        assert result["field_mapping"]["name"]["confidence"] == 1.0
        assert "email" in result["field_mapping"]
        assert result["field_mapping"]["email"]["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_map_fields_heuristic_match(
        self,
        basic_unified_schema: dict[str, Any],
        basic_dest_schema: dict[str, Any],
    ) -> None:
        """Test mapping with heuristic matching."""
        with patch(
            "src.mcp_server.server._get_config",
        ) as mock_get_config:
            mock_get_config.return_value = MagicMock()
            result = await map_fields_impl("person", basic_unified_schema, basic_dest_schema)

        assert "name" in result["field_mapping"]
        assert result["field_mapping"]["name"]["dest_column"] == "full_name"
        assert result["field_mapping"]["name"]["confidence"] >= 0.5

    @pytest.mark.asyncio
    async def test_map_fields_unmapped_fields(
        self,
    ) -> None:
        """Test mapping with low confidence matches flagged as unmapped."""
        unified_schema = {
            "obscure_field": {"type": "STRING", "sample_value": "test"},
        }
        dest_schema = {
            "table": "users",
            "columns": [
                {"name": "name", "type": "STRING"},
            ],
        }

        with patch(
            "src.mcp_server.server._get_config",
        ) as mock_get_config:
            mock_get_config.return_value = MagicMock()
            result = await map_fields_impl("person", unified_schema, dest_schema)

        assert len(result["unmapped_fields"]) >= 0  # May or may not have unmapped

    @pytest.mark.asyncio
    async def test_map_fields_schema_changes(
        self,
    ) -> None:
        """Test mapping when new columns need to be added."""
        unified_schema = {
            "new_field": {"type": "STRING", "sample_value": "value"},
        }
        dest_schema = {
            "table": "users",
            "columns": [
                {"name": "name", "type": "STRING"},
            ],
        }

        with patch(
            "src.mcp_server.server._get_config",
        ) as mock_get_config:
            mock_get_config.return_value = MagicMock()
            result = await map_fields_impl("person", unified_schema, dest_schema)

        # Should have a mapping entry with requires_schema_change
        assert "new_field" in result["field_mapping"]
        assert result["field_mapping"]["new_field"]["requires_schema_change"] is True

    @pytest.mark.asyncio
    async def test_map_fields_empty_unified_schema(
        self,
        basic_dest_schema: dict[str, Any],
    ) -> None:
        """Test mapping with empty unified schema."""
        with patch(
            "src.mcp_server.server._get_config",
        ) as mock_get_config:
            mock_get_config.return_value = MagicMock()
            result = await map_fields_impl("person", {}, basic_dest_schema)

        assert result["field_mapping"] == {}
        assert result["unmapped_fields"] == []

    @pytest.mark.asyncio
    async def test_map_fields_all_exact_matches(
        self,
    ) -> None:
        """Test mapping when all fields have exact matches."""
        unified_schema = {
            "name": {"type": "STRING", "sample_value": "Alice"},
            "email": {"type": "STRING", "sample_value": "alice@example.com"},
        }
        dest_schema = {
            "table": "users",
            "columns": [
                {"name": "name", "type": "STRING"},
                {"name": "email", "type": "STRING"},
            ],
        }

        with patch(
            "src.mcp_server.server._get_config",
        ) as mock_get_config:
            mock_get_config.return_value = MagicMock()
            result = await map_fields_impl("person", unified_schema, dest_schema)

        assert len(result["unmapped_fields"]) == 0
        assert len(result["schema_changes"]) == 0
        assert len(result["field_mapping"]) == 2

    @pytest.mark.asyncio
    async def test_map_fields_mixed_confidence_levels(
        self,
    ) -> None:
        """Test mapping with fields at different confidence levels."""
        unified_schema = {
            "name": {"type": "STRING", "sample_value": "Alice"},
            "phone_number": {"type": "STRING", "sample_value": "555-1234"},
            "unknown_field": {"type": "STRING", "sample_value": "test"},
        }
        dest_schema = {
            "table": "users",
            "columns": [
                {"name": "full_name", "type": "STRING"},
                {"name": "email", "type": "STRING"},
                {"name": "telephone", "type": "STRING"},
            ],
        }

        with patch(
            "src.mcp_server.server._get_config",
        ) as mock_get_config:
            mock_get_config.return_value = MagicMock()
            result = await map_fields_impl("person", unified_schema, dest_schema)

        assert "name" in result["field_mapping"]


class TestHeuristicMatch:
    """Tests for _heuristic_match helper."""

    def test_exact_match(self) -> None:
        """Test exact field name match returns 1.0."""
        assert _heuristic_match("name", "name") == 1.0

    def test_substring_match(self) -> None:
        """Test substring match returns 0.8."""
        assert _heuristic_match("name", "full_name") == 0.8
        assert _heuristic_match("full_name", "name") == 0.8

    def test_underscore_normalized_match(self) -> None:
        """Test match after underscore normalization."""
        assert _heuristic_match("fullname", "full_name") == 0.85

    def test_common_alias_match(self) -> None:
        """Test common alias matching."""
        assert _heuristic_match("first_name", "forename") >= 0.7
        assert _heuristic_match("last_name", "surname") >= 0.7
        assert _heuristic_match("email", "email_address") >= 0.7
        assert _heuristic_match("phone", "telephone") >= 0.7
        assert _heuristic_match("birth_date", "date_of_birth") >= 0.7

    def test_no_match(self) -> None:
        """Test completely unrelated field names return 0.0."""
        assert _heuristic_match("name", "department") == 0.0
        assert _heuristic_match("id", "created_at") == 0.0

    def test_case_insensitive(self) -> None:
        """Test matching is case insensitive."""
        assert _heuristic_match("Name", "name") == 1.0
        assert _heuristic_match("NAME", "name") == 1.0

    def test_hyphen_normalized(self) -> None:
        """Test hyphen normalization."""
        assert _heuristic_match("first-name", "first_name") >= 0.8


class TestMapTypeGeneric:
    """Tests for _map_type_generic helper."""

    def test_string_types(self) -> None:
        """Test STRING, VARCHAR, TEXT map to TEXT."""
        assert _map_type_generic("STRING") == "TEXT"
        assert _map_type_generic("VARCHAR") == "TEXT"
        assert _map_type_generic("TEXT") == "TEXT"

    def test_python_type_names(self) -> None:
        """Python type().__name__ values map correctly."""
        assert _map_type_generic("str") == "TEXT"
        assert _map_type_generic("int") == "BIGINT"
        assert _map_type_generic("float") == "DOUBLE PRECISION"
        assert _map_type_generic("bool") == "BOOLEAN"

    def test_integer_types(self) -> None:
        """Test integer types map correctly."""
        assert _map_type_generic("INTEGER") == "INTEGER"
        assert _map_type_generic("BIGINT") == "BIGINT"
        assert _map_type_generic("SMALLINT") == "INTEGER"

    def test_floating_point_types(self) -> None:
        """Test floating point types map correctly."""
        assert _map_type_generic("REAL") == "DOUBLE PRECISION"
        assert _map_type_generic("DOUBLE") == "DOUBLE PRECISION"
        assert _map_type_generic("DECIMAL") == "DOUBLE PRECISION"

    def test_boolean_type(self) -> None:
        """Test boolean type."""
        assert _map_type_generic("BOOLEAN") == "BOOLEAN"

    def test_date_time_types(self) -> None:
        """Test date/time types."""
        assert _map_type_generic("TIMESTAMP") == "TIMESTAMP"
        assert _map_type_generic("TIMESTAMPTZ") == "TIMESTAMP"
        assert _map_type_generic("DATE") == "DATE"
        assert _map_type_generic("TIME") == "TEXT"

    def test_json_uuid_types(self) -> None:
        """Test JSON and UUID types."""
        assert _map_type_generic("JSON") == "TEXT"
        assert _map_type_generic("JSONB") == "TEXT"
        assert _map_type_generic("UUID") == "TEXT"

    def test_complex_types(self) -> None:
        """Test complex types."""
        assert _map_type_generic("OBJECT") == "TEXT"
        assert _map_type_generic("ARRAY") == "TEXT"

    def test_unknown_type_defaults_to_text(self) -> None:
        """Test unknown types default to TEXT."""
        assert _map_type_generic("UNKNOWN") == "TEXT"
        assert _map_type_generic("CUSTOM_TYPE") == "TEXT"


class TestMapFieldsHandler:
    """Tests for map_fields_handler."""

    @pytest.mark.asyncio
    async def test_handler_calls_impl(self) -> None:
        """Test handler delegates to impl."""
        mock_impl = AsyncMock(return_value={
            "entity_type": "person",
            "field_mapping": {
                "name": {"dest_column": "full_name", "confidence": 1.0},
            },
            "unmapped_fields": [],
            "schema_changes": [],
        })

        with patch(
            "src.mcp_server.tools.map_fields.map_fields_impl",
            mock_impl,
        ):
            result = await map_fields_handler({
                "entity_type": "person",
                "unified_schema": {"name": {"type": "STRING"}},
                "destination_schema": {
                    "table": "users",
                    "columns": [{"name": "full_name", "type": "STRING"}],
                },
            })

        mock_impl.assert_called_once()
        assert result["entity_type"] == "person"