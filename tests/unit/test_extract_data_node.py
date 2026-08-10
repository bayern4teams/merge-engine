"""Unit tests for extract_data node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.extract_data import (
    _MAX_IN_FILTER_VALUES,
    _chunked_filters,
    _find_entity_tables,
    _resolve_link_path,
    _scratch_filter,
    _table_matches_entity,
    extract_data,
)
from src.orchestrator.spool import entity_rows
from src.orchestrator.utils.retry import TransientError


class TestTableMatchesEntity:
    """Tests for the _table_matches_entity helper."""

    def test_exact_match(self) -> None:
        """Test exact entity-to-table name match."""
        assert _table_matches_entity("customers", "public.customers", "customer") is True

    def test_entity_in_table_name(self) -> None:
        """Test entity type found within table name."""
        assert _table_matches_entity("person_data", "public.person_data", "person") is True

    def test_irregular_plural_alias(self) -> None:
        """Irregular plurals/synonyms match via the shared alias groups."""
        assert _table_matches_entity("people", "schema.people", "person") is True
        assert _table_matches_entity("organizations", "public.organizations", "company") is True

    def test_concept_table_does_not_leak_via_token(self) -> None:
        """A table that IS a known concept never token-matches another entity."""
        assert _table_matches_entity("org_founders", "public.org_founders", "company") is False
        assert _table_matches_entity("org_founders", "public.org_founders", "founder") is True

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
            },
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


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    """Give every test its own spool directory.

    Extraction now writes rows to logs/spool/<run_id>.duckdb; without this
    the tests would share files with each other and with real runs.
    """
    import src.orchestrator.spool as spool_mod

    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


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
        return AsyncMock(
            return_value={
                "rows": [
                    {"id": 1, "name": "Acme Corp"},
                    {"id": 2, "name": "Beta Inc"},
                ],
                "next_cursor": None,
            }
        )

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
        assert len(entity_rows(result, "company")) == 2
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_extract_data_tags_origin_metadata(self, base_state: dict[str, Any]) -> None:
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

        for row in entity_rows(result, "company"):
            assert row["_origin_id"] == "postgres_main"
            assert row["_origin_table"] == "public.company"

        for row in entity_rows(result, "person"):
            assert row["_origin_id"] == "postgres_main"
            assert row["_origin_table"] == "public.person"

    @pytest.mark.asyncio
    async def test_extract_data_no_tables_found(self, base_state: dict[str, Any]) -> None:
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

        assert entity_rows(result, "company") == []
        assert len(result["errors"]) == 2  # One per entity with no tables
        # Default (no explicit_target_entities in state): both entities are
        # treated as explicit, so both escalate.
        assert result["failure"]["error_class"] == "DataQualityError"
        assert result["failure"]["disposition"] == "escalate"
        assert set(result["failure"]["context"]["missing_entities"]) == {"company", "person"}
        assert result["failure"]["context"]["dropped_inferred_entities"] == []

    @pytest.mark.asyncio
    async def test_extract_data_inferred_missing_warns_not_escalates(
        self, base_state: dict[str, Any]
    ) -> None:
        """remediation #1 (2026-07-30): a wildcard-INFERRED entity with no
        source table must not abort the run -- only an EXPLICITLY requested
        one may. This is the fix for the old unconditional 'product' wildcard
        turning a valid company-only corpus into a hard error."""
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
        # Neither company nor person is explicit -- both were wildcard-inferred.
        base_state["explicit_target_entities"] = []

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            AsyncMock(return_value={"rows": [], "next_cursor": None}),
        ):
            result = await extract_data(base_state)

        assert entity_rows(result, "company") == []
        assert result["failure"] == {}  # no escalation
        assert set(result["dropped_inferred_entities"]) == {"company", "person"}

    @pytest.mark.asyncio
    async def test_extract_data_explicit_missing_still_escalates(
        self, base_state: dict[str, Any]
    ) -> None:
        """A mix: 'company' was explicitly requested and has no table (a real
        gap, must escalate); 'person' was only inferred and has no table
        either (must be silently dropped, not escalated)."""
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
        base_state["explicit_target_entities"] = ["company"]

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            AsyncMock(return_value={"rows": [], "next_cursor": None}),
        ):
            result = await extract_data(base_state)

        assert result["failure"]["error_class"] == "DataQualityError"
        assert result["failure"]["disposition"] == "escalate"
        assert result["failure"]["context"]["missing_entities"] == ["company"]
        assert result["failure"]["context"]["dropped_inferred_entities"] == ["person"]
        assert result["dropped_inferred_entities"] == ["person"]

    @pytest.mark.asyncio
    async def test_extract_data_transient_error(self, base_state: dict[str, Any]) -> None:
        """A failure mid-table records the error and stops the run."""
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

        assert any("Rate limit exceeded" in e for e in result["errors"])
        assert result["failure"]["node"] == "extract_data"

    @pytest.mark.asyncio
    async def test_extract_data_generic_error(self, base_state: dict[str, Any]) -> None:
        """Test handling of generic errors during extraction."""

        async def fail_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Connection lost")

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=fail_side_effect,
        ):
            result = await extract_data(base_state)

        assert len(result["errors"]) >= 2
        assert any("Connection lost" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_partial_table_is_discarded_not_merged(self, base_state: dict[str, Any]) -> None:
        """The core M.4 contract: half a table must never look like all of it.

        Keeping the rows that did arrive is what let an interrupted
        extraction merge as though the table were complete — a wrong answer
        delivered with full confidence.
        """
        calls = [0]

        async def fail_on_second_batch(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls[0] += 1
            if calls[0] == 1:
                return {"rows": [{"id": 1}, {"id": 2}], "next_cursor": "p2"}
            raise Exception("Connection lost")

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=fail_on_second_batch,
        ):
            result = await extract_data(base_state)

        for entity_type in result["extracted_data"]:
            rows = entity_rows(result, entity_type)
            assert rows == [], (
                f"entity '{entity_type}' kept {len(rows)} partially-extracted "
                "row(s) from a table that failed"
            )
        assert result["failure"]["error_class"] == "DataQualityError"
        assert result["failure"]["context"]["failed_tables"]
        assert result["failure"]["context"]["failed_tables"][0]["rows_discarded"] == 2

    @pytest.mark.asyncio
    async def test_failed_table_does_not_advance_its_watermark(
        self, base_state: dict[str, Any]
    ) -> None:
        """Advancing past rows never read would lose them on the next run."""

        async def always_fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Connection lost")

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=always_fail,
        ):
            result = await extract_data(base_state)

        assert result["extraction_watermarks"] == {}

    @pytest.mark.asyncio
    async def test_successful_extraction_records_no_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """The happy path must leave `failure` empty, or the router stops it."""
        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            AsyncMock(return_value={"rows": [{"id": 1}], "next_cursor": None}),
        ):
            result = await extract_data(base_state)

        assert result["failure"] == {}

    @pytest.mark.asyncio
    async def test_extract_data_cursor_pagination(self, base_state: dict[str, Any]) -> None:
        """Test that extraction uses cursor for pagination."""
        # Track calls by table_name since entity_type is not passed to extract_data_impl
        call_counts = {}  # table_name -> count
        call_details = {}  # table_name -> list of cursors used

        async def track_calls(*args: Any, **kwargs: Any) -> dict[str, Any]:
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
            assert call_details[table] == [None, "page2"], (
                f"Unexpected cursor sequence for {table}: {call_details[table]}"
            )

    @pytest.mark.asyncio
    async def test_extract_data_empty_batch_stops_loop(self, base_state: dict[str, Any]) -> None:
        """Test that empty batch with no next_cursor stops extraction."""
        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            AsyncMock(return_value={"rows": [], "next_cursor": None}),
        ):
            result = await extract_data(base_state)

        # Should have extracted (0 rows) but not errored
        assert "company" in result["extracted_data"]
        assert entity_rows(result, "company") == []

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
    async def test_extract_data_column_star_when_empty(self, base_state: dict[str, Any]) -> None:
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
        """Existing errors survive a call to this node via MergeState's real
        operator.add reducer, not because the node re-returns the old
        history itself (Phase 0 Sec 0.8 - see analyze_dependencies' test of
        the same name for why the previous version of this test asserted
        the actual bug)."""
        base_state["errors"] = ["pre-existing"]

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            mock_extract_data_impl,
        ):
            result = await extract_data(base_state)

        assert "pre-existing" not in result.get("errors", [])
        combined = base_state["errors"] + result.get("errors", [])
        assert "pre-existing" in combined

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


class TestWatermarkTypeCoercion:
    """Numeric watermarks from the TEXT tracking column must be re-typed."""

    @pytest.mark.asyncio
    async def test_numeric_last_value_coerced_to_int(self) -> None:
        state = {
            "extraction_order": ["company"],
            "dependency_tree": {"company": []},
            "schemas": {
                "origin_1": {
                    "tables": [
                        {
                            "name": "companies",
                            "full_name": "public.companies",
                            "columns": [{"name": "company_id"}],
                            "watermark_column": "company_id",
                            "watermark_kind": "numeric",
                        }
                    ],
                },
            },
            "origins": [{"id": "origin_1", "type": "postgres", "connection": {}}],
            "destination": {"type": "postgresql"},
            "extraction_config": {"default_batch_size": 10},
        }

        dest = MagicMock()
        dest.get_extraction_state.return_value = {"last_value": "1149"}

        captured: dict[str, Any] = {}

        async def fake_extract(**kwargs):
            captured.update(kwargs)
            return {"rows": [], "next_cursor": None}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=dest,
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            await extract_data(state)

        assert captured["watermark"] == {
            "column": "company_id",
            "since": 1149,
            "kind": "numeric",
        }
        assert isinstance(captured["watermark"]["since"], int)


class TestScopedExtraction:
    """A scoped merge extracts the subject's rows first, then only its links."""

    def _state(self) -> dict[str, Any]:
        return {
            "run_id": "scoped_test",
            "extraction_order": ["company", "person"],
            "dependency_tree": {"company": [], "person": ["company"]},
            "schemas": {
                "crm": {
                    "tables": [
                        {
                            "name": "companies",
                            "full_name": "public.companies",
                            "columns": [{"name": "id"}, {"name": "name"}],
                            "primary_key": ["id"],
                            "foreign_keys": [],
                        },
                        {
                            "name": "employees",
                            "full_name": "public.employees",
                            "columns": [
                                {"name": "id"},
                                {"name": "full_name"},
                                {"name": "company_id"},
                            ],
                            "primary_key": ["id"],
                            "foreign_keys": [
                                {
                                    "column": "company_id",
                                    "ref_table": "companies",
                                    "ref_column": "id",
                                }
                            ],
                        },
                    ],
                },
            },
            "entity_tables": {
                "company": [{"origin_id": "crm", "table": "public.companies"}],
                "person": [{"origin_id": "crm", "table": "public.employees"}],
            },
            "origins": [{"id": "crm", "type": "postgres", "connection": {"host": "h"}}],
            "destination": {"type": "postgresql"},
            "extraction_config": {"default_batch_size": 100},
            "merge_scope": {"active": True, "subject": "Nova", "scope_type": "company"},
            "scope_plan": {
                "subject": "Nova",
                "subject_entity": "company",
                "entities": {
                    "company": [
                        {
                            "origin_id": "crm",
                            "table": "public.companies",
                            "mode": "match",
                            "columns": ["name"],
                        }
                    ],
                    "person": [
                        {
                            "origin_id": "crm",
                            "table": "public.employees",
                            "mode": "link_path",
                            "path": [
                                {
                                    "from_table": "companies",
                                    "from_column": "id",
                                    "to_table": "employees",
                                    "to_column": "company_id",
                                }
                            ],
                            "hops": 1,
                            "link_entity": "company",
                        }
                    ],
                },
            },
        }

    @pytest.mark.asyncio
    async def test_two_phase_link_uses_collected_subject_ids(self) -> None:
        calls: list[dict[str, Any]] = []

        async def fake_extract(**kwargs):
            calls.append({"table": kwargs["table_name"], "filter": kwargs.get("filter")})
            if kwargs["table_name"] == "public.companies":
                return {
                    "rows": [{"id": 10, "name": "Nova"}, {"id": 11, "name": "Nova Labs"}],
                    "next_cursor": None,
                }
            return {"rows": [{"id": 1, "full_name": "A", "company_id": 10}], "next_cursor": None}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            result = await extract_data(self._state())

        company_call = next(c for c in calls if c["table"] == "public.companies")
        person_call = next(c for c in calls if c["table"] == "public.employees")
        # Phase 1: company matched by name substring.
        assert company_call["filter"] == {"name": {"op": "ilike", "value": "Nova"}}
        # Phase 2: employees restricted to the two company ids just collected.
        assert person_call["filter"] == {"company_id": {"op": "in", "value": [10, 11]}}
        assert result.get("failure", {}) == {}

    @pytest.mark.asyncio
    async def test_unchecked_scope_row_is_skipped_entirely(self) -> None:
        """A row a human unchecked at the scope gate (Phase 5.1/5.2) --
        enabled: False -- must contribute zero rows and zero extract calls,
        the same as a table absent from the plan entirely."""
        state = self._state()
        state["scope_plan"]["entities"]["person"][0]["enabled"] = False
        calls: list[dict[str, Any]] = []

        async def fake_extract(**kwargs):
            calls.append({"table": kwargs["table_name"]})
            if kwargs["table_name"] == "public.companies":
                return {"rows": [{"id": 10, "name": "Nova"}], "next_cursor": None}
            return {"rows": [{"id": 1, "full_name": "A", "company_id": 10}], "next_cursor": None}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            result = await extract_data(state)

        assert not any(c["table"] == "public.employees" for c in calls)
        assert result["extracted_data"]["person"]["row_count"] == 0
        assert result.get("failure", {}) == {}

    @pytest.mark.asyncio
    async def test_two_hop_link_path_reads_the_junction_then_the_entity(self) -> None:
        """A 2-hop link_path plan reads the junction, then the entity by its
        keys -- what "link_via" used to be, now just a shorter case of the
        same N-hop walk."""
        state = self._state()
        state["dependency_tree"] = {"company": [], "person": ["company"]}
        state["scope_plan"]["entities"]["person"] = [
            {
                "origin_id": "crm",
                "table": "public.people",
                "mode": "link_path",
                "path": [
                    {
                        "from_table": "companies",
                        "from_column": "id",
                        "to_table": "jobs",
                        "to_column": "company_id",
                    },
                    {
                        "from_table": "jobs",
                        "from_column": "person_id",
                        "to_table": "people",
                        "to_column": "person_id",
                    },
                ],
                "hops": 2,
            }
        ]
        state["entity_tables"]["person"] = [{"origin_id": "crm", "table": "public.people"}]
        state["schemas"]["crm"]["tables"].append(
            {
                "name": "people",
                "full_name": "public.people",
                "columns": [{"name": "person_id"}, {"name": "full_name"}],
                "primary_key": ["person_id"],
                "foreign_keys": [],
            }
        )
        state["schemas"]["crm"]["tables"] = [
            t for t in state["schemas"]["crm"]["tables"] if t["full_name"] != "public.employees"
        ]

        calls: list[dict[str, Any]] = []

        async def fake_extract(**kwargs):
            calls.append({"table": kwargs["table_name"], "filter": kwargs.get("filter")})
            if kwargs["table_name"] == "public.companies":
                return {"rows": [{"id": 10, "name": "Nova"}], "next_cursor": None}
            if kwargs["table_name"] == "jobs":
                # jobs for company 10 point at person 1 and 2. The hop's
                # to_table is the join graph's bare name ("jobs"), not the
                # entity table's own full_name ("public.people") -- the two
                # conventions coexist deliberately (plan_scope.py's
                # _bare_table_name docstring), and postgres.py's real
                # connector already defaults a bare name to "public.<name>".
                return {"rows": [{"person_id": 1}, {"person_id": 2}], "next_cursor": None}
            return {"rows": [{"person_id": 1, "full_name": "A"}], "next_cursor": None}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            await extract_data(state)

        jobs_call = next(c for c in calls if c["table"] == "jobs")
        people_call = next(c for c in calls if c["table"] == "public.people")
        # The junction was filtered by the company id...
        assert jobs_call["filter"] == {"company_id": {"op": "in", "value": [10]}}
        # ...and people by the person ids the junction yielded.
        assert people_call["filter"] == {"person_id": {"op": "in", "value": [1, 2]}}

    @pytest.mark.asyncio
    async def test_no_subject_rows_means_dependents_are_skipped(self) -> None:
        calls: list[dict[str, Any]] = []

        async def fake_extract(**kwargs):
            calls.append(kwargs["table_name"])
            # The subject matches nothing.
            return {"rows": [], "next_cursor": None}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            await extract_data(self._state())

        # companies was queried; employees was NOT (no subject ids to link on,
        # and extracting it unfiltered would pull in every company's staff).
        assert "public.companies" in calls
        assert "public.employees" not in calls

    @pytest.mark.asyncio
    async def test_a_large_linked_id_list_is_split_across_several_calls(self) -> None:
        """The real bug this guards against: a live Boeing run's link_via
        filter carried ~35,000 person ids in one IN (...) list and ClickHouse's
        HTTP interface rejected the query outright ('Field value too long').
        The extraction must instead issue several smaller-filter calls."""
        state = self._state()
        many_ids = list(range(1, 1201))  # > 2 * _MAX_IN_FILTER_VALUES

        calls: list[dict[str, Any]] = []

        async def fake_extract(**kwargs):
            calls.append({"table": kwargs["table_name"], "filter": kwargs.get("filter")})
            if kwargs["table_name"] == "public.companies":
                # One matched company, but "linking" it produces a huge
                # employee id set (mirrors a real large-employer Boeing run).
                return {"rows": [{"id": i, "name": "Nova"} for i in many_ids], "next_cursor": None}
            filt = kwargs.get("filter") or {}
            ids_here = filt.get("company_id", {}).get("value", [])
            return {
                "rows": [{"id": i, "full_name": f"P{i}", "company_id": i} for i in ids_here],
                "next_cursor": None,
            }

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            result = await extract_data(state)

        employee_calls = [c for c in calls if c["table"] == "public.employees"]
        # Split into multiple calls, none exceeding the chunk size...
        assert len(employee_calls) > 1
        for c in employee_calls:
            assert len(c["filter"]["company_id"]["value"]) <= _MAX_IN_FILTER_VALUES
        # ...and every id across all chunks was covered, none dropped.
        all_queried_ids = {v for c in employee_calls for v in c["filter"]["company_id"]["value"]}
        assert all_queried_ids == set(many_ids)
        assert result.get("failure", {}) == {}

    @pytest.mark.asyncio
    async def test_three_hop_link_path_walks_every_intermediate_hop(self) -> None:
        """The exact issue-6 chain the old link/link_via shapes could never
        express: Company -> Experience -> Person -> PersonContact, 3 hops.
        Only the middle two hops (Company->Experience, Experience->Person)
        are queried by _resolve_link_path; the final hop's to_table
        (PersonContact) is the entity table itself, extracted separately by
        the outer loop with the resolved key set."""
        state = self._state()
        state["extraction_order"] = ["company", "person", "contact"]
        state["dependency_tree"] = {
            "company": [],
            "person": ["company"],
            "contact": ["person"],
        }
        state["schemas"]["crm"]["tables"] = [
            t
            for t in state["schemas"]["crm"]["tables"]
            if t["full_name"] not in ("public.employees",)
        ] + [
            {
                "name": "experience",
                "full_name": "public.experience",
                "columns": [
                    {"name": "experience_id"},
                    {"name": "company_id"},
                    {"name": "person_id"},
                ],
                "primary_key": ["experience_id"],
                "foreign_keys": [],
            },
            {
                "name": "people",
                "full_name": "public.people",
                "columns": [{"name": "person_id"}, {"name": "full_name"}],
                "primary_key": ["person_id"],
                "foreign_keys": [],
            },
            {
                "name": "contacts",
                "full_name": "public.contacts",
                "columns": [{"name": "contact_id"}, {"name": "person_id"}, {"name": "email"}],
                "primary_key": ["contact_id"],
                "foreign_keys": [],
            },
        ]
        state["entity_tables"] = {
            "company": [{"origin_id": "crm", "table": "public.companies"}],
            "person": [{"origin_id": "crm", "table": "public.people"}],
            "contact": [{"origin_id": "crm", "table": "public.contacts"}],
        }
        state["scope_plan"]["entities"] = {
            "company": state["scope_plan"]["entities"]["company"],
            "person": [
                {
                    "origin_id": "crm",
                    "table": "public.people",
                    "mode": "link_path",
                    "path": [
                        {
                            "from_table": "companies",
                            "from_column": "id",
                            "to_table": "experience",
                            "to_column": "company_id",
                        },
                        {
                            "from_table": "experience",
                            "from_column": "person_id",
                            "to_table": "people",
                            "to_column": "person_id",
                        },
                    ],
                    "hops": 2,
                }
            ],
            "contact": [
                {
                    "origin_id": "crm",
                    "table": "public.contacts",
                    "mode": "link_path",
                    "path": [
                        {
                            "from_table": "companies",
                            "from_column": "id",
                            "to_table": "experience",
                            "to_column": "company_id",
                        },
                        {
                            "from_table": "experience",
                            "from_column": "person_id",
                            "to_table": "people",
                            "to_column": "person_id",
                        },
                        {
                            "from_table": "people",
                            "from_column": "person_id",
                            "to_table": "contacts",
                            "to_column": "person_id",
                        },
                    ],
                    "hops": 3,
                }
            ],
        }

        calls: list[dict[str, Any]] = []

        async def fake_extract(**kwargs):
            calls.append({"table": kwargs["table_name"], "filter": kwargs.get("filter")})
            table = kwargs["table_name"]
            if table == "public.companies":
                return {"rows": [{"id": 10, "name": "Nova"}], "next_cursor": None}
            if table == "experience":
                # company 10 -> two experience rows -> person 1 and 2.
                return {
                    "rows": [{"person_id": 1}, {"person_id": 2}],
                    "next_cursor": None,
                }
            if table == "people":
                # Intermediate hop for the "contact" chain (experience ->
                # people -> contacts): projects people's own key back out,
                # distinct from "public.people" below, which is the real
                # entity extraction for the "person" entity itself.
                return {
                    "rows": [{"person_id": 1}, {"person_id": 2}],
                    "next_cursor": None,
                }
            if table == "public.people":
                return {"rows": [{"person_id": 1, "full_name": "A"}], "next_cursor": None}
            if table == "public.contacts":
                return {
                    "rows": [{"contact_id": 100, "person_id": 1, "email": "a@x.com"}],
                    "next_cursor": None,
                }
            raise AssertionError(f"unexpected table queried: {table}")

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            result = await extract_data(state)

        experience_calls = [c for c in calls if c["table"] == "experience"]
        assert len(experience_calls) == 2  # once for person, once for contact
        for c in experience_calls:
            assert c["filter"] == {"company_id": {"op": "in", "value": [10]}}

        people_call = next(c for c in calls if c["table"] == "public.people")
        assert people_call["filter"] == {"person_id": {"op": "in", "value": [1, 2]}}

        contacts_call = next(c for c in calls if c["table"] == "public.contacts")
        assert contacts_call["filter"] == {"person_id": {"op": "in", "value": [1, 2]}}
        assert result.get("failure", {}) == {}

    @pytest.mark.asyncio
    async def test_resolve_link_path_1hop_never_queries_anything(self) -> None:
        """A 1-hop path (what "link" used to be) is subject_ids itself --
        _resolve_link_path must not make any extraction call at all."""
        path = [
            {
                "from_table": "companies",
                "from_column": "id",
                "to_table": "employees",
                "to_column": "company_id",
            }
        ]
        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl",
            side_effect=AssertionError("should never be called for a 1-hop path"),
        ):
            result = await _resolve_link_path({"type": "postgres"}, path, [10, 11])
        assert result == [10, 11]

    @pytest.mark.asyncio
    async def test_resolve_link_path_empty_hop_result_returns_empty(self) -> None:
        path = [
            {
                "from_table": "companies",
                "from_column": "id",
                "to_table": "jobs",
                "to_column": "company_id",
            },
            {
                "from_table": "jobs",
                "from_column": "person_id",
                "to_table": "people",
                "to_column": "person_id",
            },
        ]

        async def fake_extract(**kwargs):
            return {"rows": [], "next_cursor": None}

        with patch(
            "src.orchestrator.nodes.extract_data.extract_data_impl", side_effect=fake_extract
        ):
            result = await _resolve_link_path({"type": "postgres"}, path, [10])
        assert result == []

    @pytest.mark.asyncio
    async def test_scoped_merge_with_no_plan_entities_fails_loudly(self) -> None:
        """The full-extract-leak fix: merge_scope.active=True but the scope
        plan produced no entities means nothing tells the loop how to
        restrict any table, so silently reading everything unscoped would
        report a scoped merge that pulled in every organisation's data. This
        must stop the run, not degrade into an unscoped extraction."""
        state = self._state()
        state["scope_plan"]["entities"] = {}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=AssertionError(
                    "no extraction call should be made when the scope plan is empty"
                ),
            ),
        ):
            result = await extract_data(state)

        assert result["failure"] != {}
        assert result["failure"]["error_class"] == "DataQualityError"
        assert "no entities to restrict" in result["failure"]["error"]
        assert result["extracted_data"] == {}

    @pytest.mark.asyncio
    async def test_scoped_out_entity_recorded_separately_from_missing_or_failed(self) -> None:
        """An entity with a real candidate table that the scope plan simply
        never reaches (no plan entry for that table) ends with 0 rows for a
        reason worth surfacing -- distinct from missing_entities (no table
        existed anywhere) and from failed_tables (a real extraction error)."""
        state = self._state()
        # The plan has no entry at all for public.employees under "person".
        state["scope_plan"]["entities"]["person"] = []

        async def fake_extract(**kwargs):
            if kwargs["table_name"] == "public.companies":
                return {"rows": [{"id": 10, "name": "Nova"}], "next_cursor": None}
            raise AssertionError("public.employees must not be queried at all")

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=MagicMock(get_extraction_state=MagicMock(return_value=None)),
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            result = await extract_data(state)

        assert result["scoped_out_entities"] == ["person"]
        assert result["dropped_inferred_entities"] == []
        assert result["failure"] == {}


class TestChunkedFilters:
    """Unit tests for _chunked_filters in isolation (Phase 7 fix: a scoped
    merge's linked-id filter can be too large for one ClickHouse HTTP query)."""

    def test_none_passes_through_unchanged(self) -> None:
        assert _chunked_filters(None) == [None]

    def test_a_small_in_filter_is_not_split(self) -> None:
        f = {"company_id": {"op": "in", "value": [1, 2, 3]}}
        assert _chunked_filters(f, chunk_size=500) == [f]

    def test_an_ilike_filter_is_never_split_regardless_of_size(self) -> None:
        f = {"name": {"op": "ilike", "value": "Boeing"}}
        assert _chunked_filters(f, chunk_size=2) == [f]

    def test_a_large_in_filter_is_split_into_equal_chunks(self) -> None:
        f = {"id": {"op": "in", "value": list(range(10))}}
        chunks = _chunked_filters(f, chunk_size=3)
        assert len(chunks) == 4
        assert [c["id"]["value"] for c in chunks] == [
            [0, 1, 2],
            [3, 4, 5],
            [6, 7, 8],
            [9],
        ]
        # Every id covered exactly once, in order, none dropped or duplicated.
        assert [v for c in chunks for v in c["id"]["value"]] == list(range(10))

    def test_exactly_at_the_boundary_is_not_split(self) -> None:
        f = {"id": {"op": "in", "value": list(range(5))}}
        assert _chunked_filters(f, chunk_size=5) == [f]

    def test_one_over_the_boundary_is_split(self) -> None:
        f = {"id": {"op": "in", "value": list(range(6))}}
        chunks = _chunked_filters(f, chunk_size=5)
        assert len(chunks) == 2


class TestScratchFilter:
    """Unit tests for _scratch_filter (Part 1.5 D.6): a large ClickHouse
    linked-id filter gets one scratch-table subquery instead of many
    chunked IN(...) round-trips. Every non-ClickHouse or small-list path
    must fall back to (None, None) -- meaning "use _chunked_filters" --
    unchanged."""

    async def test_non_clickhouse_origin_returns_none(self) -> None:
        origin = {"type": "postgres", "connection": {}}
        big_list = list(range(_MAX_IN_FILTER_VALUES + 1))
        result_filter, cleanup = await _scratch_filter(origin, "id", big_list)
        assert result_filter is None
        assert cleanup is None

    async def test_small_list_returns_none_even_for_clickhouse(self) -> None:
        origin = {"type": "clickhouse", "connection": {}}
        small_list = list(range(_MAX_IN_FILTER_VALUES))
        result_filter, cleanup = await _scratch_filter(origin, "id", small_list)
        assert result_filter is None
        assert cleanup is None

    async def test_clickhouse_large_list_builds_in_scratch_filter(self) -> None:
        origin = {"type": "clickhouse", "connection": {}}
        big_list = list(range(_MAX_IN_FILTER_VALUES + 1))
        mock_connector = MagicMock()
        mock_connector.create_id_scratch_table = AsyncMock(return_value="merge_engine_scratch_x")
        mock_connector.drop_scratch_table = AsyncMock()
        mock_connector.close = AsyncMock()

        with patch(
            "src.orchestrator.nodes.extract_data.ClickHouseConnector", return_value=mock_connector
        ):
            result_filter, cleanup = await _scratch_filter(origin, "company_id", big_list)

        assert result_filter == {
            "company_id": {"op": "in_scratch", "value": "merge_engine_scratch_x"}
        }
        mock_connector.create_id_scratch_table.assert_awaited_once_with(big_list)

        assert cleanup is not None
        await cleanup()
        mock_connector.drop_scratch_table.assert_awaited_once_with("merge_engine_scratch_x")
        mock_connector.close.assert_awaited_once()

    async def test_scratch_table_creation_failure_degrades_to_none(self) -> None:
        # Setup failing must never be the thing that breaks extraction --
        # the caller falls back to _chunked_filters.
        origin = {"type": "clickhouse", "connection": {}}
        big_list = list(range(_MAX_IN_FILTER_VALUES + 1))
        mock_connector = MagicMock()
        mock_connector.create_id_scratch_table = AsyncMock(side_effect=RuntimeError("no space"))

        with patch(
            "src.orchestrator.nodes.extract_data.ClickHouseConnector", return_value=mock_connector
        ):
            result_filter, cleanup = await _scratch_filter(origin, "id", big_list)

        assert result_filter is None
        assert cleanup is None


class TestForceFullExtraction:
    """`force_full_extraction` re-reads every table, ignoring the watermark.

    This is the fix for the reported bug: deleting rows from the destination
    left the origin's high-water mark in place, so the next run extracted
    nothing new and the destination stayed empty. A fresh run must ignore it.
    """

    def _state(self, force: bool) -> dict[str, Any]:
        return {
            "extraction_order": ["company"],
            "dependency_tree": {"company": []},
            "schemas": {
                "origin_1": {
                    "tables": [
                        {
                            "name": "companies",
                            "full_name": "public.companies",
                            "columns": [{"name": "company_id"}],
                            "watermark_column": "company_id",
                            "watermark_kind": "numeric",
                        }
                    ],
                },
            },
            "origins": [{"id": "origin_1", "type": "postgres", "connection": {}}],
            "destination": {"type": "postgresql"},
            "extraction_config": {"default_batch_size": 10},
            "force_full_extraction": force,
        }

    @pytest.mark.asyncio
    async def test_fresh_run_ignores_the_watermark(self) -> None:
        dest = MagicMock()
        dest.get_extraction_state.return_value = {"last_value": "1149"}
        captured: dict[str, Any] = {}

        async def fake_extract(**kwargs):
            captured.update(kwargs)
            return {"rows": [], "next_cursor": None}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=dest,
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            await extract_data(self._state(force=True))

        # No watermark pushed down, and the tracking table is never even read.
        assert captured["watermark"] is None
        dest.get_extraction_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_incremental_run_still_uses_the_watermark(self) -> None:
        dest = MagicMock()
        dest.get_extraction_state.return_value = {"last_value": "1149"}
        captured: dict[str, Any] = {}

        async def fake_extract(**kwargs):
            captured.update(kwargs)
            return {"rows": [], "next_cursor": None}

        with (
            patch(
                "src.orchestrator.nodes.extract_data._get_destination_connector",
                return_value=dest,
            ),
            patch(
                "src.orchestrator.nodes.extract_data.extract_data_impl",
                side_effect=fake_extract,
            ),
        ):
            await extract_data(self._state(force=False))

        # Default behaviour is unchanged: the watermark is read and pushed down.
        assert captured["watermark"] == {
            "column": "company_id",
            "since": 1149,
            "kind": "numeric",
        }
        dest.get_extraction_state.assert_called_once()
