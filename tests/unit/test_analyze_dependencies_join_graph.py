"""Unit tests for analyze_dependencies' join-graph-first cascade (audit
§14.3, fix-v3 B.3.2): verified join-graph structure first, LLM demoted to
a fallback for whatever the graph can't resolve.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.analyze_dependencies import analyze_dependencies
from src.orchestrator.profiles import stage_cache_set

_PROMPTS = {
    "dependency_analysis": {
        "system": "Analyze schemas. Return only JSON.",
        "human": "Analyze: {target_entities}\n\n{schema_info}\n\n{coverage_matrix}",
    }
}


def _fully_covered_state() -> dict[str, Any]:
    """A declared FK (confidence="declared" unconditionally, per
    build_join_graph's Tier 1 -- no origin connector needed) between the
    two target entities' only tables. Both entities are fully explained
    by this one verified edge.
    """
    return {
        "user_request": "Import customers and companies",
        "target_entities": ["customer", "company"],
        "entity_tables": {
            "customer": [{"origin_id": "pg1", "table": "customers"}],
            "company": [{"origin_id": "pg1", "table": "companies"}],
        },
        "schemas": {
            "pg1": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "customers",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "company_id", "type": "INTEGER"},
                        ],
                        "foreign_keys": [
                            {"column": "company_id", "ref_table": "companies", "ref_column": "id"}
                        ],
                    },
                    {
                        "name": "companies",
                        "columns": [{"name": "id", "type": "INTEGER"}],
                        "foreign_keys": [],
                        "primary_key": ["id"],
                    },
                ],
            }
        },
        "dependency_tree": {},
        "extraction_order": [],
        "errors": [],
        "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "test_key"},
    }


def _partially_covered_state() -> dict[str, Any]:
    """Same declared FK as above, plus a third entity ("person") whose
    only join candidate is a name-heuristic match with no declared FK and
    no origins list to verify it against -- build_join_graph tags that
    "heuristic" confidence, which is NOT enough to skip the LLM.
    """
    state = _fully_covered_state()
    state["target_entities"] = ["customer", "company", "person"]
    state["entity_tables"]["person"] = [{"origin_id": "pg1", "table": "persons"}]
    state["schemas"]["pg1"]["tables"].append(
        {
            "name": "persons",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "company_id", "type": "INTEGER"},
            ],
            "foreign_keys": [],
        }
    )
    return state


class TestJoinGraphFirstCascade:
    @pytest.mark.asyncio
    async def test_fully_covered_graph_skips_the_llm_entirely(self) -> None:
        state = _fully_covered_state()
        with (
            patch("src.orchestrator.nodes.analyze_dependencies.LLMClient") as mock_llm_cls,
            patch("src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES", _PROMPTS),
        ):
            result = await analyze_dependencies(state)

        mock_llm_cls.assert_not_called()
        assert result["dependency_tree"]["customer"] == ["company"]
        assert result["dependency_tree"]["company"] == []
        assert result.get("dependency_analysis_origin") == "ALGORITHMIC"
        assert "customer" in result["extraction_order"]
        assert result["extraction_order"].index("company") < result["extraction_order"].index(
            "customer"
        )

    @pytest.mark.asyncio
    async def test_partial_coverage_calls_the_llm_with_a_confirmed_line(self) -> None:
        state = _partially_covered_state()
        mock_client = MagicMock()
        mock_client.call_json = AsyncMock(
            return_value={
                "dependency_tree": {"customer": ["company"], "company": [], "person": ["company"]}
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.analyze_dependencies.LLMClient",
                return_value=mock_client,
            ),
            patch("src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES", _PROMPTS),
        ):
            result = await analyze_dependencies(state)

        mock_client.call_json.assert_called_once()
        _, kwargs = mock_client.call_json.call_args
        assert "CONFIRMED (verified against live data)" in kwargs["user_prompt"]
        assert "customers.company_id -> companies.id" in kwargs["user_prompt"]
        assert result["dependency_tree"]["person"] == ["company"]
        assert result.get("dependency_analysis_origin") != "ALGORITHMIC"

    @pytest.mark.asyncio
    async def test_repeat_run_with_matching_fingerprint_skips_llm_and_rebuild(self) -> None:
        state = _fully_covered_state()
        with (
            patch("src.orchestrator.nodes.analyze_dependencies.LLMClient") as mock_llm_cls,
            patch("src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES", _PROMPTS),
        ):
            result1 = await analyze_dependencies(state)
        mock_llm_cls.assert_not_called()

        fp = result1["_dependency_tree_fingerprint"]
        assert fp

        profile: dict[str, Any] = {}
        stage_cache_set(
            profile,
            "dependency_tree",
            fp,
            {
                "dependency_tree": result1["dependency_tree"],
                "column_dependencies": result1["column_dependencies"],
            },
        )
        state2 = {**_fully_covered_state(), "_profile_cache": profile}

        with (
            patch("src.orchestrator.nodes.analyze_dependencies.LLMClient") as mock_llm_cls2,
            patch(
                "src.orchestrator.nodes.analyze_dependencies.build_join_graph",
                new=AsyncMock(),
            ) as mock_build,
            patch("src.orchestrator.nodes.analyze_dependencies.PROMPT_TEMPLATES", _PROMPTS),
        ):
            result2 = await analyze_dependencies(state2)

        mock_llm_cls2.assert_not_called()
        mock_build.assert_not_called()
        assert result2["dependency_tree"] == result1["dependency_tree"]
