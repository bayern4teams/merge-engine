"""Unit tests for analyze_dependencies node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.analyze_dependencies import (
    _build_schema_summary,
    _table_may_be_related,
    analyze_dependencies,
)


class TestTableMayBeRelated:
    """Tests for the _table_may_be_related helper."""

    def test_exact_match(self) -> None:
        """Test exact name match."""
        assert _table_may_be_related("customers", ["customer"]) is True

    def test_entity_in_table_name(self) -> None:
        """Test entity type found within table name."""
        assert _table_may_be_related("people_table", ["people"]) is True

    def test_table_in_entity_name(self) -> None:
        """Test table name found within entity type."""
        assert _table_may_be_related("crm_person", ["person"]) is True

    def test_plural_match(self) -> None:
        """Test plural form matching."""
        assert _table_may_be_related("customers", ["customer"]) is True

    def test_possessive_match(self) -> None:
        """Test possessive form matching."""
        assert _table_may_be_related("customer's_data", ["customer"]) is True

    def test_no_match(self) -> None:
        """Test unrelated table is filtered out."""
        assert _table_may_be_related("unrelated_table_xyz", ["person"]) is False

    def test_irregular_plural_match(self) -> None:
        """Test irregular plural (person -> people) matches."""
        assert _table_may_be_related("people", ["person"]) is True

    def test_underscore_replacement(self) -> None:
        """Test that underscores are replaced with spaces."""
        assert _table_may_be_related("person_data", ["person"]) is True

    def test_dash_replacement(self) -> None:
        """Test that dashes are replaced with spaces."""
        assert _table_may_be_related("person-data", ["person"]) is True

    def test_empty_entities_returns_true(self) -> None:
        """Test with empty target entities returns True (default inclusion)."""
        assert _table_may_be_related("any_table", []) is True


class TestBuildSchemaSummary:
    """Tests for the _build_schema_summary helper."""

    def test_empty_schemas(self) -> None:
        """Test with empty schemas dict."""
        result = _build_schema_summary({}, ["person"])
        assert result == ""

    def test_single_origin_single_table(self) -> None:
        """Test with one origin and one table."""
        schemas = {
            "postgres_main": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "customers",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "name", "type": "VARCHAR"},
                        ],
                        "foreign_keys": [],
                        "sample_row": {"id": 1, "name": "Test"},
                    }
                ],
            }
        }
        result = _build_schema_summary(schemas, ["customer"])
        assert "=== Origin: postgres_main (postgres) ===" in result
        assert "Table: customers" in result
        assert "id, name" in result

    def test_with_foreign_keys(self) -> None:
        """Test that foreign keys are included in summary."""
        schemas = {
            "postgres_main": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "orders",
                        "columns": [{"name": "id", "type": "INTEGER"}],
                        "foreign_keys": [
                            {
                                "column": "customer_id",
                                "ref_table": "customers",
                                "ref_column": "id",
                            }
                        ],
                        "sample_row": {},
                    }
                ],
            }
        }
        result = _build_schema_summary(schemas, ["order"])
        assert "FK: orders.customer_id -> customers.id" in result

    def test_with_sample_row(self) -> None:
        """Test that sample row fields are included (up to 3)."""
        schemas = {
            "postgres_main": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "customers",
                        "columns": [],
                        "foreign_keys": [],
                        "sample_row": {
                            "id": 1,
                            "name": "Test",
                            "email": "test@example.com",
                            "phone": "123",  # 4th field, should be excluded
                        },
                    }
                ],
            }
        }
        result = _build_schema_summary(schemas, ["customer"])
        assert "id=1" in result
        assert "name='Test'" in result
        assert "email='test@example.com'" in result
        assert "phone" not in result  # 4th field excluded


class TestAnalyzeDependencies:
    """Tests for the analyze_dependencies node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all people and companies",
            "target_entities": ["person", "company"],
            "schemas": {
                "postgres_main": {
                    "origin_type": "postgres",
                    "tables": [
                        {
                            "name": "customers",
                            "columns": [
                                {"name": "id", "type": "INTEGER"},
                                {"name": "name", "type": "VARCHAR"},
                            ],
                            "foreign_keys": [
                                {
                                    "column": "company_id",
                                    "ref_table": "companies",
                                    "ref_column": "id",
                                }
                            ],
                            "sample_row": {"id": 1, "name": "Test"},
                        },
                        {
                            "name": "companies",
                            "columns": [
                                {"name": "id", "type": "INTEGER"},
                                {"name": "name", "type": "VARCHAR"},
                            ],
                            "foreign_keys": [],
                            "sample_row": {"id": 1, "name": "Acme"},
                        },
                    ],
                }
            },
            "dependency_tree": {},
            "extraction_order": [],
            "errors": [],
            "llm_config": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test_key",
            },
        }

    @pytest.fixture
    def mock_llm_client(self) -> MagicMock:
        """Mock LLMClient."""
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "dependency_tree": {"person": ["company"], "company": []},
                "notes": "People reference companies",
            }
        )
        return client

    @pytest.mark.asyncio
    async def test_analyze_dependencies_success(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test successful dependency analysis."""
        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {
                    "dependency_analysis": {
                        "system": "Analyze schemas. Return only JSON.",
                        "human": "Analyze: {target_entities}\n\n{schema_info}",
                    }
                },
            ),
        ):
            result = await analyze_dependencies(base_state)

        assert result["dependency_tree"] == {"person": ["company"], "company": []}
        assert "company" in result["extraction_order"]
        assert "person" in result["extraction_order"]
        assert result["extraction_order"].index("company") < result["extraction_order"].index(
            "person"
        )
        assert not result.get("errors")
        assert result["dependency_analysis_warning"] == ""

    @pytest.mark.asyncio
    async def test_analyze_dependencies_empty_target_entities(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with no target entities."""
        base_state["target_entities"] = []

        result = await analyze_dependencies(base_state)

        assert result["dependency_tree"] == {}
        assert result["extraction_order"] == []
        assert len(result["errors"]) == 1
        assert "No target entities" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_analyze_dependencies_llm_failure_returns_fallback(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test that LLM failure returns fallback dependency structure."""
        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(side_effect=Exception("LLM unavailable"))

        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {},  # Use empty dict to trigger hardcoded fallback prompt
            ),
        ):
            result = await analyze_dependencies(base_state)

        # Fallback: each entity has no dependencies when entity_tables is empty, linear order
        assert result["dependency_tree"] == {
            "person": [],
            "company": [],
        }
        assert result["extraction_order"] == ["company", "person"]
        assert len(result["errors"]) == 1
        assert "Dependency analysis failed" in result["errors"][0]
        assert "UNVERIFIED" in result["dependency_analysis_warning"]

    @pytest.mark.asyncio
    async def test_analyze_dependencies_missing_prompt_template(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test with missing prompt template uses hardcoded fallback."""
        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {},  # Empty dict triggers hardcoded fallback
            ),
        ):
            result = await analyze_dependencies(base_state)

        assert result["dependency_tree"] == {"person": ["company"], "company": []}

    @pytest.mark.asyncio
    async def test_analyze_dependencies_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Existing errors survive a call to this node - via MergeState's
        real operator.add reducer combining state with whatever NEW errors
        the node returns, not because the node re-returns the old history
        itself. Rewritten (Phase 0 Sec 0.8): the previous version of this
        test asserted the node's own return dict contained the OLD error,
        which was actually the bug this fix removes - every node doing that
        meant the entire error history got appended a second time onto
        state on every single call, via the reducer, forever.
        """
        base_state["errors"] = ["existing error"]

        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {
                    "dependency_analysis": {
                        "system": "Analyze schemas. Return only JSON.",
                        "human": "Analyze: {target_entities}\n\n{schema_info}",
                    }
                },
            ),
        ):
            result = await analyze_dependencies(base_state)

        # The node itself must not re-report the pre-existing error.
        assert "existing error" not in result.get("errors", [])
        # But operator.add's reducer still combines it into the real state.
        combined = base_state["errors"] + result.get("errors", [])
        assert "existing error" in combined

    @pytest.mark.asyncio
    async def test_analyze_dependencies_empty_llm_result(self, base_state: dict[str, Any]) -> None:
        """Test with empty LLM response returns fallback."""
        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(return_value={})

        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {
                    "dependency_analysis": {
                        "system": "Analyze schemas. Return only JSON.",
                        "human": "Analyze: {target_entities}\n\n{schema_info}",
                    }
                },
            ),
        ):
            result = await analyze_dependencies(base_state)

        # Empty dict means empty dependency_tree, topological sort handles this
        assert "extraction_order" in result

    @pytest.mark.asyncio
    async def test_analyze_dependencies_complex_dependency_tree(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test with a more complex dependency tree."""
        base_state["target_entities"] = ["country", "company", "person", "order"]

        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(
            return_value={
                "dependency_tree": {
                    "person": ["company"],
                    "order": ["company", "person"],
                    "company": [],
                    "country": [],
                },
                "notes": "Complex dependency graph",
            }
        )

        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {
                    "dependency_analysis": {
                        "system": "Analyze schemas. Return only JSON.",
                        "human": "Analyze: {target_entities}\n\n{schema_info}",
                    }
                },
            ),
        ):
            result = await analyze_dependencies(base_state)

        # Topological sort should place dependencies before dependents
        order = result["extraction_order"]
        assert "country" in order
        assert "company" in order
        assert "person" in order
        assert "order" in order
        assert order.index("company") < order.index("person")
        assert order.index("person") < order.index("order")

    @pytest.mark.asyncio
    async def test_analyze_dependencies_circular_dependency_handled(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that circular dependencies are handled by topological sort."""
        base_state["target_entities"] = ["a", "b", "c"]

        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(
            return_value={
                "dependency_tree": {
                    "a": ["b"],
                    "b": ["c"],
                    "c": ["a"],  # Circular!
                },
                "notes": "Circular dependencies",
            }
        )

        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {
                    "dependency_analysis": {
                        "system": "Analyze schemas. Return only JSON.",
                        "human": "Analyze: {target_entities}\n\n{schema_info}",
                    }
                },
            ),
        ):
            result = await analyze_dependencies(base_state)

        # Topological sort should handle this gracefully
        assert len(result["extraction_order"]) == 3

    def test_build_schema_summary_multiple_origins(self) -> None:
        """Test schema summary with multiple origins."""
        schemas = {
            "postgres_main": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "customers",
                        "columns": [{"name": "id", "type": "INTEGER"}],
                        "foreign_keys": [],
                        "sample_row": {},
                    }
                ],
            },
            "mongo_analytics": {
                "origin_type": "mongodb",
                "tables": [
                    {
                        "name": "leads",
                        "columns": [{"name": "_id", "type": "STRING"}],
                        "foreign_keys": [],
                        "sample_row": {},
                    }
                ],
            },
        }
        result = _build_schema_summary(schemas, ["customer"])
        assert "postgres_main" in result
        assert "mongo_analytics" in result
        assert "customers" in result
        # "leads" is unrelated to the "customer" entity, so the relatedness
        # filter excludes it from the LLM prompt.
        assert "leads" not in result

    def test_build_coverage_matrix(self) -> None:
        """Test _build_coverage_matrix identifies FK matches."""
        from src.orchestrator.nodes.analyze_dependencies import _build_coverage_matrix

        schemas = {
            "origin_1": {
                "origin_type": "clickhouse",
                "tables": [
                    {
                        "name": "Company",
                        "columns": [{"name": "companyID", "type": "String"}],
                        "primary_key": ["companyID"],
                    },
                    {
                        "name": "CompanyFinancials",
                        "columns": [
                            {"name": "financialID", "type": "String"},
                            {"name": "companyID", "type": "String"},
                        ],
                    },
                ],
            }
        }
        entity_tables = {
            "company": [{"origin_id": "origin_1", "table": "Company"}],
            "finance": [{"origin_id": "origin_1", "table": "CompanyFinancials"}],
        }
        matrix = _build_coverage_matrix(schemas, entity_tables, ["company", "finance"])
        finance_entry = next(m for m in matrix if m["child_table"] == "CompanyFinancials")
        assert finance_entry["child_entity"] == "finance"
        assert finance_entry["parent_entity"] == "company"
        # Regression: this used to hardcode parent_column="id" regardless of
        # what the parent table's real primary key was named. Company's PK
        # here is "companyID", not "id" -- the entry must reflect that, or
        # every dependency-gate row built from it fails
        # _sanitise_column_dependencies' real-column check and silently
        # vanishes the moment a human approves without editing (live-observed
        # as 14 rejected "unknown parent column: ...Company.id" entries in
        # one run against a schema where Company has no "id" column at all).
        assert finance_entry["parent_column"] == "companyID"

    def test_build_coverage_matrix_omits_entry_when_no_real_parent_key_exists(self) -> None:
        """When the parent table has neither a declared primary_key nor a
        literal "id" column, the heuristic must NOT fabricate one -- an
        omitted entry is an honest gap; a guessed "id" is a dependency-gate
        row guaranteed to fail validation."""
        from src.orchestrator.nodes.analyze_dependencies import _build_coverage_matrix

        schemas = {
            "origin_1": {
                "origin_type": "clickhouse",
                "tables": [
                    {
                        "name": "Company",
                        "columns": [{"name": "companyID", "type": "String"}],
                        # No primary_key declared, and no literal "id" column.
                    },
                    {
                        "name": "CompanyFinancials",
                        "columns": [
                            {"name": "financialID", "type": "String"},
                            {"name": "companyID", "type": "String"},
                        ],
                    },
                ],
            }
        }
        entity_tables = {
            "company": [{"origin_id": "origin_1", "table": "Company"}],
            "finance": [{"origin_id": "origin_1", "table": "CompanyFinancials"}],
        }
        matrix = _build_coverage_matrix(schemas, entity_tables, ["company", "finance"])
        assert not any(m["child_table"] == "CompanyFinancials" for m in matrix)

    @pytest.mark.asyncio
    async def test_empty_dependency_tree_retries_and_falls_back(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that returning {} triggers a retry and then falls back to coverage matrix."""
        mock_client = MagicMock()
        # Returns empty dict both times
        mock_client.call_json = AsyncMock(return_value={})

        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {},
            ),
        ):
            result = await analyze_dependencies(base_state)

        # Should call twice (initial + retry)
        assert mock_client.call_json.call_count == 2
        # Dependency tree should be populated from coverage matrix fallback
        assert "person" in result["dependency_tree"]
        assert "company" in result["dependency_tree"]
        assert "fallback" in result["dependency_analysis_warning"].lower()

    @pytest.mark.asyncio
    async def test_partial_dependency_tree_omitting_entities_retries_and_falls_back(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that omitting target entities in dependency_tree triggers retry and fallback."""
        base_state["target_entities"] = ["person", "company"]
        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(return_value={"dependency_tree": {"company": []}})

        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_client,
            ),
            patch(
                "src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES",
                {},
            ),
        ):
            result = await analyze_dependencies(base_state)

        assert mock_client.call_json.call_count == 2
        assert "person" in result["dependency_tree"]
        assert "company" in result["dependency_tree"]
        assert "fallback" in result["dependency_analysis_warning"].lower()
