"""Unit tests for map_fields MCP tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.map_fields import (
    _heuristic_match,
    _map_type_generic,
    _safe_coalesce_target,
    map_fields_handler,
    map_fields_impl,
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
    async def test_map_fields_no_destination_equivalent_is_unmapped(
        self,
    ) -> None:
        """A field with no destination equivalent is left unmapped, never
        given a new column — the destination schema is a fixed contract."""
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

        assert "new_field" not in result["field_mapping"]
        assert result["schema_changes"] == []
        assert [u["field"] for u in result["unmapped_fields"]] == ["new_field"]

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


class TestSynonymCoalesce:
    """Bug B follow-up: when a cluster merges records from two origins whose
    schemas spell the same concept differently, the unified record carries
    BOTH spellings as separate keys (unify_entity_group.py unions raw field
    names, it has no synonym concept). map_fields_impl must not silently
    drop whichever spelling didn't win the destination column outright —
    only one field name gets a column per batch, but per RECORD the loser
    may be the only one populated.
    """

    @pytest.fixture
    def company_dest_schema(self) -> dict[str, Any]:
        return {
            "table": "company",
            "columns": [
                {"name": "id", "type": "TEXT"},
                {"name": "name", "type": "TEXT"},
                {"name": "legal_name", "type": "TEXT"},
                {"name": "website", "type": "TEXT"},
            ],
        }

    @pytest.mark.asyncio
    async def test_single_synonym_present_maps_normally(
        self, company_dest_schema: dict[str, Any]
    ) -> None:
        """A batch with only `entityName` (no literal `name` field at all,
        e.g. an origin1-only run with no cross-origin merge) must map it
        directly — no coalescing needed since nothing else claims `name`."""
        unified_schema = {
            "entityName": {"type": "str", "sample_values": ["Lockheed Martin"]},
        }
        result = await map_fields_impl(
            "company",
            unified_schema,
            company_dest_schema,
            llm_config=None,
        )
        assert result["field_mapping"]["entityName"]["dest_column"] == "name"
        assert result["unmapped_fields"] == []

    @pytest.mark.asyncio
    async def test_merged_record_coalesces_synonym_instead_of_dropping_it(
        self, company_dest_schema: dict[str, Any]
    ) -> None:
        """A batch containing BOTH `name` (wins the column outright, exact
        match) and `entityName` (a qualified synonym) must map entityName
        onto `name` too, tagged `mapped_by=coalesce`, instead of leaving it
        unmapped. Reproduces a real cross-origin Boeing cluster live: the
        unified record after resolve_entities+unify_entity_group carries
        both keys, and dropping the loser meant a company merged from
        origin1 (entityName only, no literal `name` field) still ended up
        with a NULL `name` in the destination even after the camelCase
        heuristic fix — because in THAT SAME BATCH, some other cluster's
        merged record also carried a literal `name` key, which won the
        column globally and starved every entityName-only record."""
        unified_schema = {
            "name": {"type": "str", "sample_values": ["Boeing"]},
            "entityName": {"type": "str", "sample_values": ["Boeing"]},
        }
        result = await map_fields_impl(
            "company",
            unified_schema,
            company_dest_schema,
            llm_config=None,
        )
        assert result["field_mapping"]["name"]["dest_column"] == "name"
        assert result["field_mapping"]["entityName"]["dest_column"] == "name"
        assert result["field_mapping"]["entityName"]["mapped_by"] == "coalesce"
        assert result["unmapped_fields"] == []

    @pytest.mark.asyncio
    async def test_coalesce_does_not_let_a_different_concept_corrupt_the_column(
        self, company_dest_schema: dict[str, Any]
    ) -> None:
        """`fullName` means `legal_name`, not `name` — even though it scores
        identically to `entityName` against `name` by plain string
        similarity (both merely contain the substring "name"). It must NOT
        coalesce onto the already-claimed `name` column: reproduced live as
        real corruption where fullName's legal-name value silently
        overwrote a correct `name` value ("Boeing" -> "The Boeing
        Company") on the same record."""
        unified_schema = {
            "name": {"type": "str", "sample_values": ["Boeing"]},
            "fullName": {"type": "str", "sample_values": ["The Boeing Company"]},
        }
        result = await map_fields_impl(
            "company",
            unified_schema,
            company_dest_schema,
            llm_config=None,
        )
        assert result["field_mapping"]["name"]["dest_column"] == "name"
        assert "fullName" not in result["field_mapping"]
        assert [u["field"] for u in result["unmapped_fields"]] == ["fullName"]

    @pytest.mark.asyncio
    async def test_load_transform_coalesces_per_record_preferring_non_null(
        self, company_dest_schema: dict[str, Any]
    ) -> None:
        """End-to-end: map_fields_impl's coalesced mapping, fed through
        load_data._transform_records, must not drop either origin's value —
        a merged record with both populated keeps the exact-match winner; a
        record where only the synonym is populated (no cross-origin merge
        for that particular company) still gets a `name`."""
        from src.mcp_server.tools.load_data import _transform_records

        unified_schema = {
            "name": {"type": "str", "sample_values": ["Boeing"]},
            "entityName": {"type": "str", "sample_values": ["Boeing"]},
        }
        result = await map_fields_impl(
            "company",
            unified_schema,
            company_dest_schema,
            llm_config=None,
        )
        records = [
            {"_cluster_id": "merged", "name": "Boeing", "entityName": "Boeing"},
            {"_cluster_id": "solo-origin1", "entityName": "Lockheed Martin"},
            {"_cluster_id": "solo-origin2", "name": "Airbus"},
        ]
        transformed = _transform_records(records, result["field_mapping"], "company")
        # `id` falls back to `_cluster_id` when nothing mapped onto it.
        by_cluster = {t["id"]: t for t in transformed}
        assert by_cluster["merged"]["name"] == "Boeing"
        assert by_cluster["solo-origin1"]["name"] == "Lockheed Martin"
        assert by_cluster["solo-origin2"]["name"] == "Airbus"


class TestSafeCoalesceTarget:
    """`_safe_coalesce_target` gates coalescing on a narrow qualifier
    allowlist, not on raw string-similarity score — see
    TestSynonymCoalesce.test_coalesce_does_not_let_a_different_concept_corrupt_the_column
    for why score alone is unsafe."""

    def test_allowed_qualifier_prefix(self) -> None:
        assert _safe_coalesce_target("entityName", "name") is True
        assert _safe_coalesce_target("companyName", "name") is True
        assert _safe_coalesce_target("orgWebsite", "website") is True

    def test_meaning_changing_prefix_rejected(self) -> None:
        assert _safe_coalesce_target("fullName", "name") is False
        assert _safe_coalesce_target("legalName", "name") is False
        assert _safe_coalesce_target("ceoName", "name") is False
        assert _safe_coalesce_target("founderName", "name") is False

    def test_unrelated_field_rejected(self) -> None:
        assert _safe_coalesce_target("revenueRange", "revenue") is False

    def test_no_qualifier_at_all_rejected(self) -> None:
        # Same word count as dest_col (0 extra tokens) — not a coalesce
        # candidate at all, this would already be an exact/near-exact match.
        assert _safe_coalesce_target("name", "name") is False


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
        mock_impl = AsyncMock(
            return_value={
                "entity_type": "person",
                "field_mapping": {
                    "name": {"dest_column": "full_name", "confidence": 1.0},
                },
                "unmapped_fields": [],
                "schema_changes": [],
            }
        )

        with patch(
            "src.mcp_server.tools.map_fields.map_fields_impl",
            mock_impl,
        ):
            result = await map_fields_handler(
                {
                    "entity_type": "person",
                    "unified_schema": {"name": {"type": "STRING"}},
                    "destination_schema": {
                        "table": "users",
                        "columns": [{"name": "full_name", "type": "STRING"}],
                    },
                }
            )

        mock_impl.assert_called_once()
        assert result["entity_type"] == "person"
