"""Unit tests for the langgraph-swarm planning subgraph (B2.1 - B2.6)."""

from __future__ import annotations

import pytest

from src.orchestrator.nodes.parse_request import parse_request
from src.orchestrator.planning.bounded_runner import run_planning_swarm
from src.orchestrator.planning.swarm_builder import (
    create_planning_swarm,
    derive_fk_closure,
)


@pytest.fixture
def boeing_synthetic_schemas() -> dict:
    """Synthetic origin schema modeling Boeing origin1 and origin2 structures."""
    return {
        "origin1": {
            "origin_type": "postgresql",
            "tables": [
                {
                    "name": "company",
                    "columns": [{"name": "id"}, {"name": "name"}],
                    "foreign_keys": [],
                },
                {
                    "name": "CompanyFinancials",
                    "columns": [{"name": "id"}, {"name": "company_id"}, {"name": "revenue"}],
                    "foreign_keys": [
                        {"column": "company_id", "ref_table": "company", "ref_column": "id"}
                    ],
                },
                {
                    "name": "CompanyTechnographics",
                    "columns": [{"name": "id"}, {"name": "company_id"}, {"name": "tech_name"}],
                    "foreign_keys": [
                        {"column": "company_id", "ref_table": "company", "ref_column": "id"}
                    ],
                },
                {
                    "name": "CompanyRelation",
                    "columns": [
                        {"name": "id"},
                        {"name": "company_id"},
                        {"name": "related_company_id"},
                    ],
                    "foreign_keys": [
                        {"column": "company_id", "ref_table": "company", "ref_column": "id"}
                    ],
                },
                {
                    "name": "person",
                    "columns": [{"name": "id"}, {"name": "company_id"}, {"name": "name"}],
                    "foreign_keys": [
                        {"column": "company_id", "ref_table": "company", "ref_column": "id"}
                    ],
                },
            ],
        },
        "origin2": {
            "origin_type": "clickhouse",
            "tables": [
                {
                    "name": "experience",
                    "columns": [{"name": "id"}, {"name": "person_id"}, {"name": "title"}],
                    "foreign_keys": [
                        {"column": "person_id", "ref_table": "person", "ref_column": "id"}
                    ],
                },
                {
                    "name": "education",
                    "columns": [{"name": "id"}, {"name": "person_id"}, {"name": "degree"}],
                    "foreign_keys": [
                        {"column": "person_id", "ref_table": "person", "ref_column": "id"}
                    ],
                },
                {
                    "name": "person_skill",
                    "columns": [{"name": "id"}, {"name": "person_id"}, {"name": "skill_id"}],
                    "foreign_keys": [
                        {"column": "person_id", "ref_table": "person", "ref_column": "id"}
                    ],
                },
                {
                    "name": "contact_email",
                    "columns": [{"name": "id"}, {"name": "person_id"}, {"name": "email"}],
                    "foreign_keys": [
                        {"column": "person_id", "ref_table": "person", "ref_column": "id"}
                    ],
                },
            ],
        },
    }


class TestSwarmPlanner:
    def test_swarm_scaffolding(self):
        """B2.1: Verify create_planning_swarm builds a compiled StateGraph."""

        # Using a mock object to verify agent creation
        class MockModel:
            def bind_tools(self, tools, **kwargs):
                return self

            def invoke(self, *args, **kwargs):
                return {"messages": []}

        graph_builder = create_planning_swarm(MockModel())
        assert graph_builder is not None

    def test_boeing_fk_closure_expansion(self, boeing_synthetic_schemas):
        """B2.1 / Gate 1: Boeing FK closure derives ≥ 9 entity families."""
        target_entities, explicit = derive_fk_closure(
            schemas=boeing_synthetic_schemas,
            subject="Boeing",
            scope_type="company",
        )
        # Should include: company, finance, technology, company_relation, person, experience, education, skill, contact
        assert len(target_entities) >= 9, (
            f"Expected >= 9 entity families for Boeing, got {len(target_entities)}: {target_entities}"
        )
        assert "company" in target_entities
        assert "finance" in target_entities
        assert "technology" in target_entities
        assert "company_relation" in target_entities

    @pytest.mark.asyncio
    async def test_state_isolation(self, boeing_synthetic_schemas):
        """B2.3: SwarmState active_agent and messages do not leak into MergeState result."""
        input_state = {
            "user_request": "merge all information about Boeing",
            "schemas": boeing_synthetic_schemas,
            "llm_config": {"provider": "mock"},
            "planning": {"engine": "swarm"},
        }
        res = await run_planning_swarm(input_state)

        # Must NOT contain active_agent or messages
        assert "active_agent" not in res
        assert "messages" not in res

        # Must contain exact expected MergeState output keys
        expected_keys = {
            "target_entities",
            "explicit_target_entities",
            "merge_scope",
            "dependency_tree",
            "extraction_order",
            "entity_tables",
            "table_selection_notes",
            "scope_plan",
        }
        assert expected_keys.issubset(set(res.keys()))

    @pytest.mark.asyncio
    async def test_bounded_loop_fallback(self, boeing_synthetic_schemas):
        """B2.4: Bounded timeout triggers fallback and records reason in notes."""
        input_state = {
            "user_request": "merge all information about Boeing",
            "schemas": boeing_synthetic_schemas,
            "planning": {"engine": "swarm"},
        }
        # Force immediate timeout with 0.00001s timeout budget
        res = await run_planning_swarm(input_state, timeout_seconds=0.00001)

        assert "Fallback:" in res["table_selection_notes"]
        assert "exceeded wall-clock budget" in res["table_selection_notes"]

    @pytest.mark.asyncio
    async def test_default_off_sequential_identical(self):
        """B2.5: Default off posture (engine: sequential) leaves standard execution path."""
        state = {
            "user_request": "merge company data",
            "llm_config": {"provider": "mock"},
            "planning": {"engine": "sequential"},
        }
        res = await parse_request(state)
        assert "target_entities" in res
        assert "explicit_target_entities" in res
