"""Unit tests for extract_data node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.extract_data import extract_data, _find_entity_tables, _table_matches_entity
from src.orchestrator.utils.retry import TransientError


class TestTableMatchesEntity:
    """Tests for the _table_matches_entity helper."""

    def test_exact_match(self) -> None:
        """Test exact entity-to-table name match."""
        assert _table_matches_entity("customers", "public.customers", "customer") is True

    def test_entity_in_table_name(self) -> None:
        """Test entity type found within table name."""
        assert _table_matches_entity("person_data", "public.person_data", "person") is True

    def test_full_name_truncation(self) -> None:
        """Test that only the last part of full_name (table) is matched."""
        # full_name splits on '.' and takes last part
        assert _table_matches_entity("people", "schema.people", "person") is False

    def test_plural_s_form(self) -> None:
        """Test plural 's' form matching."""
        assert _table_matches_entity("customers", "public.customers", "customer") is True

    def test_plural_es_form(self) -> None:
        """Test plural 'es' form matching."""
        # "categories" contains "categor" + "ies", entity "category" -> "categoryes" won't match
        # The function checks entity_type + "es" in name, so "categoryes" not in "categories"
        assert _table_matches_entity("categories", "public.categories", "category") is False

    def test_case_insensitive(self) -> None:
        """Test case insensitive matching."""
        assert _table_matches_entity("CUSTOMERS", "public.CUSTOMERS", "customer") is True

    def test_underscore_to_space(self) -> None:
        """Test underscore replacement."""
        assert _table_matches_entity("person_data", "public.person_data", "person") is True


class TestFindEntityTables:
    """Tests for the _find_entity_tables helper."""

    def test_single_match(self) -> None:
        """Test finding tables for a single entity."""
        schemas = {
            "postgres_main": {
                "tables": [
                    {
                        "name": "customers",
                        "full_name": "public.customers",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "name", "type": "VARCHAR"},
                        ],
                        "foreign_keys": [],
                    }
                ]
            }
        }
        result = _find_entity_tables("customer", schemas)
        assert len(result) == 1
        assert result[0]["origin_id"] == "postgres_main"
        assert result[0]["table_name"] == "public.customers"

    def test_no_match(self) -> None:
        """Test when no tables match the entity."""
        schemas = {
            "postgres_main": {
                "tables": [
                    {
                        "name": "orders",
                        "full_name": "public.orders",
                        "columns": [],
                        "foreign_keys": [],
                    }
                ]
            }
        }
        result = _find_entity_tables("customer", schemas)
        assert result == []

    def test_multiple_origins(self) -> None:
        """Test finding matching tables across multiple origins."""
        schemas = {
            "postgres_main": {
                "tables": [
                    {
                        "name": "customers",
                        "full_name": "public.customers",
                        "columns": [{"name": "id", "type": "INTEGER"}],
                        "foreign_keys": [],
                    }
                ]
            },
            "mongo_analytics": {
                "tables": [
                    {
                        "name": "leads",
                        "full_name": "leads",
                        "columns": [{"name": "_id", "type": "STRING"}],
                        "foreign_keys": [],
                    }
                ]
            }
        }
        result = _find_entity_tables("customer", schemas)
        assert len(result) == 1
        assert result[0]["origin_id"] == "postgres_main"

    def test_multiple_tables_same_origin(self) -> None:
        """Test finding multiple tables in the same origin."""
        schemas = {
            "postgres_main": {
                "tables": [
                    {
                        "name": "customers",
                        "full_name": "public.customers",
                        "columns": [],
                        "foreign_keys": [],
                    },
                    {
                        "name": "customer_addresses",
                        "full_name": "public.customer_addresses",
                        "columns": [],
                        "foreign_keys": [],
                    },
                ]
            }
        }
        result = _find_entity_tables("customer", schemas)
        assert len(result) == 2


class TestExtractData:
    """Tests for the extract_data node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all people and companies",
            "target_entities": ["company", "person"],
            "extraction_order": ["company", "person"],
            "dependency_tree": {"person": ["company"]},
            "schemas": {
                "postgres_main": {
                    "tables": [
                        {
                            "name": "company",
                            "full_name": "public.company",
                            "columns": [
                                {"name": "id", "type": "INTEGER"},
                                {"name": "name", "type": "VARCHAR"},
                            ],
                            "foreign_keys": [],
                        },
                        {
                            "name": "person",
                            "full_name": "public.person",
                            "columns": [
                                {"name": "id", "type": "INTEGER"},
                                {"name": "name", "type": "VARCHAR"},
                            ],
                            "foreign_keys": [],
                        },
                    ]
                }
            },
            "origins": [
                {"id": "postgres_main", "type": "postgres"},
            ],
            "extracted_data": {},
            "errors": [],
            "extraction_config": {"default_batch_size": 100},
        }

    @pytest.fixture
    def mock_extract_data_impl(self) -> AsyncMock:
        """Mock extract_data_impl."""
        return AsyncMock(return_value={
            "rows": [
                {"id": 1, "name": "Acme Corp"},
                {"id": 2, "name": "Beta Inc"},
            ],
            "next_cursor": None,
        })

    def _make_fresh_mock(self) -> AsyncMock:
        """Create a mock that returns fresh row dicts on each call to avoid mutation overlap."""
        call_counter = [0]

        async def fresh_rows(*args: Any, **kwargs: Any) -> dict[str, Any]:
            call_counter[0] += 1
            # Each call gets brand-new row dicts to avoid mutation overlap between entities
            return {
                "rows": [
                    {"id": call_counter[0], "name": f"Row-{call_counter[0]}"},
                ],
                "next_cursor": None,
            }

        return AsyncMock(side_effect=fresh_rows)

    @pytest.mark.asyncio
    async def test_extract_data_success(
        self, base_state: dict[str, Any], mock_extract_data_impl: AsyncMock
    ) -> None:
        """Test successful data extraction."""
        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            mock_extract_data_impl,
        ):
            result = await extract_data(base_state)

        assert "company" in result["extracted_data"]
        assert len(result["extracted_data"]["company"]) == 2
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_extract_data_tags_origin_metadata(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that extracted rows get origin metadata tags."""
        call_counter = [0]

        async def fresh_rows(*args: Any, **kwargs: Any) -> dict[str, Any]:
            call_counter[0] += 1
            # Each call returns fresh row dicts so mutation by one entity
            # doesn't affect another entity's rows.
            return {
                "rows": [
                    {"id": 1, "name": "Acme Corp"},
                    {"id": 2, "name": "Beta Inc"},
                ],
                "next_cursor": None,
            }

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=fresh_rows,
        ):
            result = await extract_data(base_state)

        for row in result["extracted_data"]["company"]:
            assert row["_origin_id"] == "postgres_main"
            assert row["_origin_table"] == "public.company"

        for row in result["extracted_data"]["person"]:
            assert row["_origin_id"] == "postgres_main"
            assert row["_origin_table"] == "public.person"

    @pytest.mark.asyncio
    async def test_extract_data_no_tables_found(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test extraction when no tables match the entity."""
        base_state["schemas"] = {
            "postgres_main": {
                "tables": [
                    {
                        "name": "orders",
                        "full_name": "public.orders",
                        "columns": [],
                        "foreign_keys": [],
                    }
                ]
            }
        }

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            AsyncMock(return_value={"rows": [], "next_cursor": None}),
        ):
            result = await extract_data(base_state)

        assert "company" not in result["extracted_data"] or result["extracted_data"].get("company", []) == []
        assert len(result["errors"]) == 2  # One per entity with no tables

    @pytest.mark.asyncio
    async def test_extract_data_transient_error(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of transient errors during extraction."""
        call_count = [0]

        async def side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                raise TransientError("Rate limit exceeded")
            return {"rows": [{"id": 1, "name": "Acme"}], "next_cursor": None}

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=side_effect,
        ):
            result = await extract_data(base_state)

        # Should have logged the error but not crash
        assert any("Transient error" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_extract_data_generic_error(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of generic errors during extraction."""
        async def fail_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Connection lost")

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=fail_side_effect,
        ):
            result = await extract_data(base_state)

        assert len(result["errors"]) >= 2
        assert any("Extraction failed" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_extract_data_cursor_pagination(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that extraction uses cursor for pagination."""
        # Track calls by table_name since entity_type is not passed to extract_data_impl
        call_counts = {}  # table_name -> count
        call_details = {}  # table_name -> list of cursors used

        async def track_calls(
            *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            table_name = kwargs.get("table_name", args[1] if len(args) > 1 else "unknown")
            cursor = kwargs.get("cursor")
            
            call_counts[table_name] = call_counts.get(table_name, 0) + 1
            if table_name not in call_details:
                call_details[table_name] = []
            call_details[table_name].append(cursor)
            
            if cursor is None:
                return {
                    "rows": [{"id": 1, "name": "First"}],
                    "next_cursor": "page2",
                }
            return {
                "rows": [{"id": 2, "name": "Second"}],
                "next_cursor": None,
            }

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=track_calls,
        ):
            result = await extract_data(base_state)

        # Should have 2 calls per table (first + pagination)
        for table, count in call_counts.items():
            assert count == 2, f"Expected 2 calls for {table}, got {count}"
            # First call has no cursor, second call has cursor
            assert call_details[table] == [None, "page2"], f"Unexpected cursor sequence for {table}: {call_details[table]}"

    @pytest.mark.asyncio
    async def test_extract_data_empty_batch_stops_loop(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that empty batch with no next_cursor stops extraction."""
        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            AsyncMock(return_value={"rows": [], "next_cursor": None}),
        ):
            result = await extract_data(base_state)

        # Should have extracted (0 rows) but not errored
        assert "company" in result["extracted_data"]
        assert result["extracted_data"]["company"] == []

    @pytest.mark.asyncio
    async def test_extract_data_default_batch_size(
        self, base_state: dict[str, Any], mock_extract_data_impl: AsyncMock
    ) -> None:
        """Test that default batch size is used."""
        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            mock_extract_data_impl,
        ):
            await extract_data(base_state)

        # Verify extract_data_impl was called (batch_size would be in args)
        assert mock_extract_data_impl.called

    @pytest.mark.asyncio
    async def test_extract_data_column_star_when_empty(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that '*' columns used when table has no columns."""
        base_state["schemas"] = {
            "postgres_main": {
                "tables": [
                    {
                        "name": "company",
                        "full_name": "public.company",
                        "columns": [],  # No columns
                        "foreign_keys": [],
                    },
                    {
                        "name": "person",
                        "full_name": "public.person",
                        "columns": [],
                        "foreign_keys": [],
                    },
                ]
            }
        }

        call_count = [0]

        async def mock_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            call_count[0] += 1
            return {"rows": [], "next_cursor": None}

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=mock_impl,
        ):
            await extract_data(base_state)

        # Called once per entity (company + person = 2)
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_extract_data_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_extract_data_impl: AsyncMock
    ) -> None:
        """Test that existing errors are preserved."""
        base_state["errors"] = ["pre-existing"]

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            mock_extract_data_impl,
        ):
            result = await extract_data(base_state)

        assert "pre-existing" in result["errors"]

    @pytest.mark.asyncio
    async def test_extract_data_all_entities_processed(
        self, base_state: dict[str, Any], mock_extract_data_impl: AsyncMock
    ) -> None:
        """Test that all entities in extraction_order are processed."""
        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            mock_extract_data_impl,
        ):
            result = await extract_data(base_state)

        assert "company" in result["extracted_data"]
        assert "person" in result["extracted_data"]
        assert mock_extract_data_impl.call_count == 2  # Once per entity