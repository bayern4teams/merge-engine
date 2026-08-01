"""Unit tests for the scope-planning stage (P.2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_server.tools.plan_scope import plan_scope_impl, _heuristic_plan
from src.orchestrator.nodes.plan_scope import plan_scope


def _schemas() -> dict[str, Any]:
    """Two origins with different names for the same concepts.

    crm_origin (postgres) has declared foreign keys; hr_origin (clickhouse) has
    none, so its link column must be inferred from the column name — the case
    that makes an LLM (or the name heuristic) necessary.
    """
    return {
        "crm_origin": {
            "origin_type": "postgres",
            "tables": [
                {
                    "name": "companies", "full_name": "public.companies",
                    "columns": [{"name": "id"}, {"name": "name"}, {"name": "industry"}],
                    "foreign_keys": [], "primary_key": ["id"],
                },
                {
                    "name": "employees", "full_name": "public.employees",
                    "columns": [{"name": "id"}, {"name": "full_name"}, {"name": "company_id"}],
                    "foreign_keys": [
                        {"column": "company_id", "ref_table": "companies", "ref_column": "id"}
                    ],
                    "primary_key": ["id"],
                },
                {
                    "name": "products", "full_name": "public.products",
                    "columns": [{"name": "id"}, {"name": "title"}, {"name": "company_id"}],
                    "foreign_keys": [
                        {"column": "company_id", "ref_table": "companies", "ref_column": "id"}
                    ],
                    "primary_key": ["id"],
                },
            ],
        },
        "hr_origin": {
            "origin_type": "clickhouse",
            "tables": [
                {
                    "name": "organizations", "full_name": "organizations",
                    "columns": [{"name": "org_id"}, {"name": "org_name"}],
                    "foreign_keys": [], "primary_key": ["org_id"],
                },
                {
                    "name": "people", "full_name": "people",
                    "columns": [{"name": "person_id"}, {"name": "name"}, {"name": "org_id"}],
                    "foreign_keys": [], "primary_key": ["person_id"],
                },
            ],
        },
    }


class TestHeuristicPlan:
    def test_subject_entity_matches_on_name(self) -> None:
        plan = _heuristic_plan("company", ["company", "person", "product"], _schemas())
        company = plan["company"]
        # Both origins' company tables are matched on a name-like column.
        crm = next(t for t in company if t["origin_id"] == "crm_origin")
        assert crm["mode"] == "match"
        assert "name" in crm["columns"]
        hr = next(t for t in company if t["origin_id"] == "hr_origin")
        assert hr["mode"] == "match"
        assert "org_name" in hr["columns"]

    def test_dependent_links_via_declared_fk(self) -> None:
        plan = _heuristic_plan("company", ["company", "person", "product"], _schemas())
        emp = next(t for t in plan["person"] if t["origin_id"] == "crm_origin")
        assert emp["mode"] == "link"
        assert emp["columns"] == ["company_id"]
        assert emp["link_entity"] == "company"

    def test_dependent_links_via_name_hint_without_fk(self) -> None:
        # hr_origin.people has no FK; the link column is inferred from "org_id".
        plan = _heuristic_plan("company", ["company", "person"], _schemas())
        ppl = next(t for t in plan["person"] if t["origin_id"] == "hr_origin")
        assert ppl["mode"] == "link"
        assert ppl["columns"] == ["org_id"]

    def test_product_links_in_crm(self) -> None:
        plan = _heuristic_plan("company", ["company", "product"], _schemas())
        prod = next(t for t in plan["product"] if t["origin_id"] == "crm_origin")
        assert prod["mode"] == "link"
        assert prod["columns"] == ["company_id"]

    def test_junction_produces_link_via(self) -> None:
        # people has no company column; jobs (person_id, company_id) links them.
        schema = {
            "crm": {
                "origin_type": "postgres",
                "tables": [
                    {"name": "companies", "full_name": "public.companies",
                     "columns": [{"name": "id"}, {"name": "name"}],
                     "primary_key": ["id"], "foreign_keys": []},
                    {"name": "people", "full_name": "public.people",
                     "columns": [{"name": "person_id"}, {"name": "full_name"}],
                     "primary_key": ["person_id"], "foreign_keys": []},
                    {"name": "jobs", "full_name": "public.jobs",
                     "columns": [{"name": "job_id"}, {"name": "person_id"}, {"name": "company_id"}],
                     "primary_key": ["job_id"], "foreign_keys": []},
                ],
            },
        }
        plan = _heuristic_plan("company", ["company", "person"], schema)
        person = next(t for t in plan["person"] if t["table"] == "public.people")
        assert person["mode"] == "link_via"
        assert person["columns"] == ["person_id"]
        assert person["via_table"] == "public.jobs"
        assert person["via_from_column"] == "company_id"
        assert person["via_to_column"] == "person_id"


class TestPlanScopeImpl:
    @pytest.mark.asyncio
    async def test_llm_disabled_uses_heuristic(self) -> None:
        out = await plan_scope_impl(
            subject="Nova", subject_entity="company",
            target_entities=["company", "person", "product"],
            schemas=_schemas(), llm_config=None,
        )
        assert out["llm_used"] is False
        assert out["scope_plan"]["subject"] == "Nova"
        assert out["scope_plan"]["entities"]["company"]

    @pytest.mark.asyncio
    async def test_llm_plan_is_used_and_sanitized(self) -> None:
        llm_reply = {
            "entities": {
                "company": [
                    {"origin_id": "crm_origin", "table": "companies",
                     "mode": "match", "columns": ["name"], "reason": "own record"},
                ],
                "person": [
                    {"origin_id": "crm_origin", "table": "employees",
                     "mode": "link", "columns": ["company_id"],
                     "link_entity": "company", "reason": "works there"},
                    # A hallucinated table that must be dropped.
                    {"origin_id": "crm_origin", "table": "ghost_table",
                     "mode": "link", "columns": ["company_id"]},
                ],
            },
            "notes": "looks clean",
        }
        client = AsyncMock()
        client.call_json = AsyncMock(return_value=llm_reply)

        prompts = {"scope_planning": {"system": "sys {subject}", "human": "h {table_catalog}"}}
        with patch("src.llm.LLMClient", return_value=client), patch(
            "src.llm.PROMPT_TEMPLATES", prompts
        ):
            out = await plan_scope_impl(
                subject="Nova", subject_entity="company",
                target_entities=["company", "person", "product"],
                schemas=_schemas(),
                llm_config={"model": "x", "scope_planning_enabled": True},
            )

        assert out["llm_used"] is True
        person = out["scope_plan"]["entities"]["person"]
        # The hallucinated table was dropped; the real one kept.
        assert len(person) == 1
        assert person[0]["table"] == "public.employees"
        # product was omitted by the model, so the heuristic filled it in.
        assert out["scope_plan"]["entities"]["product"]

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_heuristic(self) -> None:
        client = AsyncMock()
        client.call_json = AsyncMock(side_effect=Exception("boom"))
        prompts = {"scope_planning": {"system": "s", "human": "h"}}
        with patch("src.llm.LLMClient", return_value=client), patch(
            "src.llm.PROMPT_TEMPLATES", prompts
        ):
            out = await plan_scope_impl(
                subject="Nova", subject_entity="company",
                target_entities=["company", "person"],
                schemas=_schemas(),
                llm_config={"model": "x"},
            )
        assert out["llm_used"] is False
        assert out["scope_plan"]["entities"]["company"]


class TestPlanScopeNode:
    @pytest.mark.asyncio
    async def test_inactive_scope_is_a_noop(self) -> None:
        state = {
            "merge_scope": {"active": False, "subject": "", "scope_type": "none"},
            "target_entities": ["company"],
            "schemas": _schemas(),
            "llm_config": {},
        }
        result = await plan_scope(state)
        assert result["scope_plan"] == {}

    @pytest.mark.asyncio
    async def test_active_scope_produces_plan(self) -> None:
        state = {
            "merge_scope": {"active": True, "subject": "Nova", "scope_type": "company"},
            "target_entities": ["company", "person"],
            "schemas": _schemas(),
            "llm_config": {},  # no LLM -> heuristic floor
            "origins": [],  # no reachable origins -> probe yields nothing, no error
        }
        result = await plan_scope(state)
        assert result["scope_plan"]["subject"] == "Nova"
        assert result["scope_plan"]["entities"]["company"]
        assert result["scope_plan"]["entities"]["person"]
        # The probe ran (empty, since no origins) but never failed the node.
        assert result["scope_plan"]["candidates"] == []


class TestScopeGate:
    def test_router_reviews_active_plan(self) -> None:
        from src.orchestrator.graph import _scope_router
        state = {
            "merge_scope": {"active": True, "subject": "Nova", "scope_type": "company"},
            "scope_plan": {"entities": {"company": [{"table": "x"}]}},
        }
        assert _scope_router(state) == "review"

    def test_router_continues_when_inactive(self) -> None:
        from src.orchestrator.graph import _scope_router
        assert _scope_router({"merge_scope": {"active": False}, "scope_plan": {}}) == "continue"

    def test_router_continues_when_no_plan(self) -> None:
        from src.orchestrator.graph import _scope_router
        state = {"merge_scope": {"active": True}, "scope_plan": {"entities": {}}}
        assert _scope_router(state) == "continue"

    def test_interrupt_payload_shape(self) -> None:
        from src.orchestrator.nodes.human_nodes import _build_scope_interrupt
        plan = {
            "subject": "Nova", "subject_entity": "company",
            "candidates": [{"origin_id": "crm", "fields": {"name": "Nova Labs"}}],
            "entities": {
                "company": [{"origin_id": "crm", "table": "companies",
                             "mode": "match", "columns": ["name"], "reason": "own"}],
                "person": [{"origin_id": "crm", "table": "employees",
                            "mode": "link", "columns": ["company_id"], "reason": "fk"}],
            },
        }
        out = _build_scope_interrupt(plan)
        assert out["node"] == "human_review_scope"
        assert out["data"]["subject"] == "Nova"
        assert len(out["data"]["candidates"]) == 1
        assert len(out["data"]["plan"]) == 2

    @pytest.mark.asyncio
    async def test_gate_interrupts_without_feedback(self) -> None:
        from src.orchestrator.nodes.human_nodes import human_review_scope
        state = {"scope_plan": {"subject": "Nova", "entities": {}}}
        out = await human_review_scope(state)
        assert "interrupt_data" in out
        assert out["interrupt_data"]["node"] == "human_review_scope"

    @pytest.mark.asyncio
    async def test_gate_applies_edited_plan_on_resume(self) -> None:
        from src.orchestrator.nodes.human_nodes import human_review_scope
        edited = {"subject": "Nova", "entities": {"company": []}, "candidates": []}
        state = {
            "scope_plan": {"subject": "Nova", "entities": {}},
            "human_feedback": {"decision": "edit", "scope_plan": edited},
        }
        out = await human_review_scope(state)
        assert out["scope_plan"] == edited
