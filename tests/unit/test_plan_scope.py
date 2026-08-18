"""Unit tests for the scope-planning stage (P.2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_server.tools.join_graph import build_join_graph
from src.mcp_server.tools.plan_scope import _heuristic_plan, _sanitize_plan, plan_scope_impl
from src.orchestrator.nodes.plan_scope import plan_scope


def _hop(from_table: str, from_column: str, to_table: str, to_column: str) -> dict[str, str]:
    return {
        "from_table": from_table,
        "from_column": from_column,
        "to_table": to_table,
        "to_column": to_column,
    }


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
                    "name": "companies",
                    "full_name": "public.companies",
                    "columns": [{"name": "id"}, {"name": "name"}, {"name": "industry"}],
                    "foreign_keys": [],
                    "primary_key": ["id"],
                },
                {
                    "name": "employees",
                    "full_name": "public.employees",
                    "columns": [{"name": "id"}, {"name": "full_name"}, {"name": "company_id"}],
                    "foreign_keys": [
                        {"column": "company_id", "ref_table": "companies", "ref_column": "id"}
                    ],
                    "primary_key": ["id"],
                },
                {
                    "name": "products",
                    "full_name": "public.products",
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
                    "name": "organizations",
                    "full_name": "organizations",
                    "columns": [{"name": "org_id"}, {"name": "org_name"}],
                    "foreign_keys": [],
                    "primary_key": ["org_id"],
                },
                {
                    "name": "people",
                    "full_name": "people",
                    "columns": [{"name": "person_id"}, {"name": "name"}, {"name": "org_id"}],
                    "foreign_keys": [],
                    "primary_key": ["person_id"],
                },
            ],
        },
    }


class TestHeuristicPlan:
    @pytest.mark.asyncio
    async def test_subject_entity_matches_on_name(self) -> None:
        schemas = _schemas()
        graph = await build_join_graph(schemas, origins=[])
        plan = _heuristic_plan("company", ["company", "person", "product"], schemas, graph)
        company = plan["company"]
        # Both origins' company tables are matched on a name-like column.
        crm = next(t for t in company if t["origin_id"] == "crm_origin")
        assert crm["mode"] == "match"
        assert "name" in crm["columns"]
        hr = next(t for t in company if t["origin_id"] == "hr_origin")
        assert hr["mode"] == "match"
        assert "org_name" in hr["columns"]

    @pytest.mark.asyncio
    async def test_dependent_links_via_declared_fk(self) -> None:
        schemas = _schemas()
        graph = await build_join_graph(schemas, origins=[])
        plan = _heuristic_plan("company", ["company", "person", "product"], schemas, graph)
        emp = next(t for t in plan["person"] if t["origin_id"] == "crm_origin")
        assert emp["mode"] == "link_path"
        assert emp["hops"] == 1
        assert emp["path"] == [_hop("companies", "id", "employees", "company_id")]
        assert emp["confidence"] == "declared"
        assert emp["link_entity"] == "company"
        assert emp["columns"] == ["company_id"]

    @pytest.mark.asyncio
    async def test_dependent_links_via_name_hint_without_fk(self) -> None:
        # hr_origin.people has no FK; no origin config is supplied either,
        # so the name-matched edge lands in the graph unverified.
        schemas = _schemas()
        graph = await build_join_graph(schemas, origins=[])
        plan = _heuristic_plan("company", ["company", "person"], schemas, graph)
        ppl = next(t for t in plan["person"] if t["origin_id"] == "hr_origin")
        assert ppl["mode"] == "link_path"
        assert ppl["path"] == [_hop("organizations", "org_id", "people", "org_id")]
        assert ppl["confidence"] == "heuristic"

    @pytest.mark.asyncio
    async def test_product_links_in_crm(self) -> None:
        schemas = _schemas()
        graph = await build_join_graph(schemas, origins=[])
        plan = _heuristic_plan("company", ["company", "product"], schemas, graph)
        prod = next(t for t in plan["product"] if t["origin_id"] == "crm_origin")
        assert prod["mode"] == "link_path"
        assert prod["path"] == [_hop("companies", "id", "products", "company_id")]

    @pytest.mark.asyncio
    async def test_deep_chain_produces_multi_hop_link_path(self) -> None:
        # people has no company column; jobs (person_id, company_id) links them
        # -- exactly the 2-hop case _find_junction used to special-case, now
        # just another find_paths result.
        schema = {
            "crm": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "companies",
                        "full_name": "public.companies",
                        "columns": [{"name": "id"}, {"name": "name"}],
                        "primary_key": ["id"],
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
                        "name": "jobs",
                        "full_name": "public.jobs",
                        "columns": [
                            {"name": "job_id"},
                            {"name": "person_id"},
                            {"name": "company_id"},
                        ],
                        "primary_key": ["job_id"],
                        "foreign_keys": [],
                    },
                ],
            },
        }
        graph = await build_join_graph(schema, origins=[])
        plan = _heuristic_plan("company", ["company", "person"], schema, graph)
        person = next(t for t in plan["person"] if t["table"] == "public.people")
        assert person["mode"] == "link_path"
        assert person["hops"] == 2
        assert person["path"] == [
            _hop("companies", "id", "jobs", "company_id"),
            _hop("jobs", "person_id", "people", "person_id"),
        ]

        # max_hops (config.yaml's scope.max_hops, plumbed through
        # plan_scope_impl's scope_config) bounds the same BFS: a cap of 1
        # cannot see a 2-hop route at all, so the dependent table is
        # correctly left out rather than extracted unfiltered.
        capped_plan = _heuristic_plan("company", ["company", "person"], schema, graph, max_hops=1)
        assert not [t for t in capped_plan["person"] if t["table"] == "public.people"]

    @pytest.mark.asyncio
    async def test_plan_scope_impl_threads_max_hops_from_scope_config(self) -> None:
        """End-to-end: plan_scope_impl's scope_config param (state's
        scope_config, sourced from config.yaml's scope.max_hops) must
        actually reach the BFS, not just be accepted and ignored."""
        schema = {
            "crm": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "companies",
                        "full_name": "public.companies",
                        "columns": [{"name": "id"}, {"name": "name"}],
                        "primary_key": ["id"],
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
                        "name": "jobs",
                        "full_name": "public.jobs",
                        "columns": [
                            {"name": "job_id"},
                            {"name": "person_id"},
                            {"name": "company_id"},
                        ],
                        "primary_key": ["job_id"],
                        "foreign_keys": [],
                    },
                ],
            },
        }
        out = await plan_scope_impl(
            subject="Nova",
            subject_entity="company",
            target_entities=["company", "person"],
            schemas=schema,
            llm_config=None,
            origins=None,
            scope_config={"max_hops": 1},
        )
        person = out["scope_plan"]["entities"]["person"]
        # With max_hops=1 the 2-hop companies->jobs->people route is
        # unreachable, so "person" degrades to a not_found row rather than
        # silently including the 2-hop entry a wider search would find.
        assert all(e["mode"] == "not_found" for e in person)


class TestSanitizePlan:
    """The LLM can still describe a join in the familiar match/link/
    link_via/link_path shapes, but _sanitize_plan must validate every hop
    against the real join graph -- it can no longer invent one."""

    @pytest.mark.asyncio
    async def test_valid_link_is_normalised_to_link_path(self) -> None:
        schemas = _schemas()
        graph = await build_join_graph(schemas, origins=[])
        proposed = {
            "person": [
                {
                    "origin_id": "crm_origin",
                    "table": "employees",
                    "mode": "link",
                    "columns": ["company_id"],
                    "link_entity": "company",
                    "reason": "works there",
                }
            ]
        }
        clean = _sanitize_plan(
            proposed, ["company", "person", "product"], "company", schemas, graph
        )
        assert len(clean["person"]) == 1
        entry = clean["person"][0]
        assert entry["mode"] == "link_path"
        assert entry["path"] == [_hop("companies", "id", "employees", "company_id")]
        assert entry["confidence"] == "declared"

    @pytest.mark.asyncio
    async def test_hallucinated_link_column_is_dropped(self) -> None:
        """A link column that doesn't correspond to any real edge -- an
        invented join, not a name-inference miss -- must be rejected, not
        silently trusted."""
        schemas = _schemas()
        graph = await build_join_graph(schemas, origins=[])
        proposed = {
            "person": [
                {
                    "origin_id": "crm_origin",
                    "table": "employees",
                    "mode": "link",
                    "columns": ["totally_made_up_column"],
                    "link_entity": "company",
                }
            ]
        }
        clean = _sanitize_plan(
            proposed, ["company", "person", "product"], "company", schemas, graph
        )
        assert clean["person"] == []

    @pytest.mark.asyncio
    async def test_valid_link_via_is_normalised_to_2hop_link_path(self) -> None:
        schema = {
            "crm": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "companies",
                        "full_name": "public.companies",
                        "columns": [{"name": "id"}, {"name": "name"}],
                        "primary_key": ["id"],
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
                        "name": "jobs",
                        "full_name": "public.jobs",
                        "columns": [
                            {"name": "job_id"},
                            {"name": "person_id"},
                            {"name": "company_id"},
                        ],
                        "primary_key": ["job_id"],
                        "foreign_keys": [],
                    },
                ],
            },
        }
        graph = await build_join_graph(schema, origins=[])
        proposed = {
            "person": [
                {
                    "origin_id": "crm",
                    "table": "people",
                    "mode": "link_via",
                    "columns": ["person_id"],
                    "link_entity": "company",
                    "via_table": "jobs",
                    "via_from_column": "company_id",
                    "via_to_column": "person_id",
                    "reason": "people are linked via their jobs",
                }
            ]
        }
        clean = _sanitize_plan(proposed, ["company", "person"], "company", schema, graph)
        assert len(clean["person"]) == 1
        entry = clean["person"][0]
        assert entry["mode"] == "link_path"
        assert entry["hops"] == 2
        assert entry["path"] == [
            _hop("companies", "id", "jobs", "company_id"),
            _hop("jobs", "person_id", "people", "person_id"),
        ]

    @pytest.mark.asyncio
    async def test_fabricated_link_via_junction_is_dropped(self) -> None:
        schema = {
            "crm": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "companies",
                        "full_name": "public.companies",
                        "columns": [{"name": "id"}, {"name": "name"}],
                        "primary_key": ["id"],
                        "foreign_keys": [],
                    },
                    {
                        "name": "people",
                        "full_name": "public.people",
                        "columns": [{"name": "person_id"}, {"name": "full_name"}],
                        "primary_key": ["person_id"],
                        "foreign_keys": [],
                    },
                ],
            },
        }
        graph = await build_join_graph(schema, origins=[])
        proposed = {
            "person": [
                {
                    "origin_id": "crm",
                    "table": "people",
                    "mode": "link_via",
                    "columns": ["person_id"],
                    "link_entity": "company",
                    "via_table": "nonexistent_jobs_table",
                    "via_from_column": "company_id",
                    "via_to_column": "person_id",
                }
            ]
        }
        clean = _sanitize_plan(proposed, ["company", "person"], "company", schema, graph)
        assert clean["person"] == []

    @pytest.mark.asyncio
    async def test_explicit_link_path_validated_hop_by_hop(self) -> None:
        schema = {
            "crm": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "companies",
                        "full_name": "public.companies",
                        "columns": [{"name": "id"}, {"name": "name"}],
                        "primary_key": ["id"],
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
                        "name": "jobs",
                        "full_name": "public.jobs",
                        "columns": [
                            {"name": "job_id"},
                            {"name": "person_id"},
                            {"name": "company_id"},
                        ],
                        "primary_key": ["job_id"],
                        "foreign_keys": [],
                    },
                ],
            },
        }
        graph = await build_join_graph(schema, origins=[])
        proposed = {
            "person": [
                {
                    "origin_id": "crm",
                    "table": "people",
                    "mode": "link_path",
                    "path": [
                        _hop("companies", "id", "jobs", "company_id"),
                        _hop("jobs", "person_id", "people", "person_id"),
                    ],
                    "reason": "explicit two-hop path",
                }
            ]
        }
        clean = _sanitize_plan(proposed, ["company", "person"], "company", schema, graph)
        assert len(clean["person"]) == 1
        assert clean["person"][0]["mode"] == "link_path"
        assert clean["person"][0]["hops"] == 2

    @pytest.mark.asyncio
    async def test_explicit_link_path_with_a_fabricated_hop_is_dropped(self) -> None:
        schema = {
            "crm": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "companies",
                        "full_name": "public.companies",
                        "columns": [{"name": "id"}, {"name": "name"}],
                        "primary_key": ["id"],
                        "foreign_keys": [],
                    },
                    {
                        "name": "people",
                        "full_name": "public.people",
                        "columns": [{"name": "person_id"}, {"name": "full_name"}],
                        "primary_key": ["person_id"],
                        "foreign_keys": [],
                    },
                ],
            },
        }
        graph = await build_join_graph(schema, origins=[])
        proposed = {
            "person": [
                {
                    "origin_id": "crm",
                    "table": "people",
                    "mode": "link_path",
                    # A real hop that doesn't exist: no jobs table, no edge.
                    "path": [_hop("companies", "id", "jobs", "company_id")],
                }
            ]
        }
        clean = _sanitize_plan(proposed, ["company", "person"], "company", schema, graph)
        assert clean["person"] == []

    @pytest.mark.asyncio
    async def test_unknown_table_is_dropped(self) -> None:
        schemas = _schemas()
        graph = await build_join_graph(schemas, origins=[])
        proposed = {
            "person": [
                {
                    "origin_id": "crm_origin",
                    "table": "ghost_table",
                    "mode": "link",
                    "columns": ["x"],
                }
            ]
        }
        clean = _sanitize_plan(proposed, ["company", "person"], "company", schemas, graph)
        assert clean["person"] == []


class TestPlanScopeImpl:
    @pytest.mark.asyncio
    async def test_llm_disabled_uses_heuristic(self) -> None:
        out = await plan_scope_impl(
            subject="Nova",
            subject_entity="company",
            target_entities=["company", "person", "product"],
            schemas=_schemas(),
            llm_config=None,
        )
        assert out["llm_used"] is False
        assert out["scope_plan"]["subject"] == "Nova"
        assert out["scope_plan"]["entities"]["company"]

    @pytest.mark.asyncio
    async def test_llm_plan_is_used_and_sanitized(self) -> None:
        llm_reply = {
            "entities": {
                "company": [
                    {
                        "origin_id": "crm_origin",
                        "table": "companies",
                        "mode": "match",
                        "columns": ["name"],
                        "reason": "own record",
                    },
                ],
                "person": [
                    {
                        "origin_id": "crm_origin",
                        "table": "employees",
                        "mode": "link",
                        "columns": ["company_id"],
                        "link_entity": "company",
                        "reason": "works there",
                    },
                    # A hallucinated table that must be dropped.
                    {
                        "origin_id": "crm_origin",
                        "table": "ghost_table",
                        "mode": "link",
                        "columns": ["company_id"],
                    },
                ],
            },
            "notes": "looks clean",
        }
        client = AsyncMock()
        client.call_json = AsyncMock(return_value=llm_reply)

        prompts = {"scope_planning": {"system": "sys {subject}", "human": "h {table_catalog}"}}
        with (
            patch("src.llm.LLMClient", return_value=client),
            patch("src.llm.PROMPT_TEMPLATES", prompts),
        ):
            out = await plan_scope_impl(
                subject="Nova",
                subject_entity="company",
                target_entities=["company", "person", "product"],
                schemas=_schemas(),
                llm_config={"model": "x", "scope_planning_enabled": True},
            )

        assert out["llm_used"] is True
        person = out["scope_plan"]["entities"]["person"]
        # The hallucinated table was dropped; the real one kept, from the model.
        by_table = {e["table"]: e for e in person}
        assert "crm_origin.ghost_table" not in by_table
        assert by_table["public.employees"]["origin_id"] == "crm_origin"
        # hr_origin's "person" table (people) was never mentioned by the
        # model -- its floor entry must still be present. The floor merges
        # per (origin, table), not per entity: a partial model answer for
        # one origin must not silently drop a DIFFERENT origin's table (see
        # test_partial_llm_answer_does_not_drop_other_origins_floor_entry).
        assert by_table["people"]["origin_id"] == "hr_origin"
        assert len(person) == 2
        # product was omitted by the model, so the heuristic filled it in.
        assert out["scope_plan"]["entities"]["product"]

    @pytest.mark.asyncio
    async def test_high_fanout_and_est_rows_are_on_the_wire(self) -> None:
        """Both fields were computed (JoinPath.high_fanout, the new
        est_rows probe) but high_fanout was never actually attached to the
        entry dict -- Gates.tsx read a field that was always undefined and
        silently fell back to a `hops >= 3` guess. Both must be present on
        every link_path entry, and est_rows must be None (not a guess, not
        a KeyError) when no origin connection is available to probe."""
        out = await plan_scope_impl(
            subject="Nova",
            subject_entity="company",
            target_entities=["company", "person"],
            schemas=_schemas(),
            llm_config=None,
            origins=None,
        )
        person = out["scope_plan"]["entities"]["person"]
        for entry in person:
            assert "high_fanout" in entry
            assert isinstance(entry["high_fanout"], bool)
            assert entry["enabled"] == (not entry["high_fanout"])
            # No origin connection info was supplied -- the probe must
            # degrade to None, never raise and never fabricate a number.
            assert entry["est_rows"] is None

    @pytest.mark.asyncio
    async def test_partial_llm_answer_does_not_drop_other_origins_floor_entry(self) -> None:
        """Regression: the floor backfill used to merge per ENTITY, not per
        (origin, table) -- `if not clean.get(entity)`. The scope-planning
        prompt explicitly tells the model it may omit a table and let "the
        automatic path search find and verify the full chain" (Phase 4.3's
        intended contract), so a routine partial answer -- the model
        returning only crm_origin's `employees` for "person" -- discarded
        hr_origin's whole floor entry for "person" outright. Live-observed
        on the Airbus run as seven "does not correspond to a real
        relationship" drops and three zero-row entities. The fix merges
        per (origin_id, table): a partial answer can only ADD tables the
        model named beyond the floor, never remove one the floor already
        covers for a DIFFERENT origin."""
        llm_reply = {
            "entities": {
                "company": [
                    {
                        "origin_id": "crm_origin",
                        "table": "companies",
                        "mode": "match",
                        "columns": ["name"],
                        "reason": "own record",
                    },
                ],
                # Only crm_origin's employees -- hr_origin's people table is
                # omitted, exactly as the prompt instructs for a table the
                # model doesn't want to hand-describe a join for.
                "person": [
                    {
                        "origin_id": "crm_origin",
                        "table": "employees",
                        "mode": "link",
                        "columns": ["company_id"],
                        "link_entity": "company",
                        "reason": "works there",
                    },
                ],
            },
            "notes": "",
        }
        client = AsyncMock()
        client.call_json = AsyncMock(return_value=llm_reply)
        prompts = {"scope_planning": {"system": "sys {subject}", "human": "h {table_catalog}"}}
        with (
            patch("src.llm.LLMClient", return_value=client),
            patch("src.llm.PROMPT_TEMPLATES", prompts),
        ):
            out = await plan_scope_impl(
                subject="Nova",
                subject_entity="company",
                target_entities=["company", "person"],
                schemas=_schemas(),
                llm_config={"model": "x", "scope_planning_enabled": True},
            )

        person = out["scope_plan"]["entities"]["person"]
        by_origin = {(e["origin_id"], e["table"]) for e in person}
        # The model's own entry survives...
        assert ("crm_origin", "public.employees") in by_origin
        # ...and hr_origin's floor entry, which the model never mentioned,
        # must NOT have been silently discarded.
        assert ("hr_origin", "people") in by_origin
        hr_entry = next(e for e in person if e["origin_id"] == "hr_origin")
        assert hr_entry["mode"] == "link_path"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_heuristic(self) -> None:
        client = AsyncMock()
        client.call_json = AsyncMock(side_effect=Exception("boom"))
        prompts = {"scope_planning": {"system": "s", "human": "h"}}
        with (
            patch("src.llm.LLMClient", return_value=client),
            patch("src.llm.PROMPT_TEMPLATES", prompts),
        ):
            out = await plan_scope_impl(
                subject="Nova",
                subject_entity="company",
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
        # join_graph is cached in state for a retry / profile-matched
        # repeat run to reuse (Phase A2's join-graph state-caching gap).
        assert result["join_graph"] is not None

    @pytest.mark.asyncio
    async def test_reuses_a_join_graph_already_in_state(self) -> None:
        """A join_graph already in state (a retry within the same run, or a
        profile match) must be reused, not rebuilt -- rebuilding re-runs
        every candidate edge's inclusion-dependency sampling."""
        schemas = _schemas()
        prebuilt = await build_join_graph(schemas, origins=[])
        state = {
            "merge_scope": {"active": True, "subject": "Nova", "scope_type": "company"},
            "target_entities": ["company", "person"],
            "schemas": schemas,
            "llm_config": {},
            "origins": [],
            "join_graph": prebuilt,
        }
        with patch(
            "src.mcp_server.tools.plan_scope.build_join_graph",
            new=AsyncMock(side_effect=AssertionError("must not rebuild when cached")),
        ):
            result = await plan_scope(state)
        assert result["join_graph"] is prebuilt

    @pytest.mark.asyncio
    async def test_reuses_a_profile_cached_join_graph_on_fingerprint_match(self) -> None:
        """Phase A4: no join_graph in THIS run's state yet, but a saved
        profile's cached graph matches the current schema shape exactly --
        must be reused, not rebuilt."""
        schemas = _schemas()
        prebuilt = await build_join_graph(schemas, origins=[])

        # Compute the fingerprint the SAME way the node does, so the cache
        # actually matches -- not just present under some key.
        from src.mcp_server.tools.join_graph import _DEFAULT_SAMPLE_SIZE
        from src.orchestrator.fingerprint import origins_fingerprint, stage_fingerprint

        fp = stage_fingerprint(
            "join_graph",
            origins_fp=origins_fingerprint([], schemas),
            sample_size=_DEFAULT_SAMPLE_SIZE,
        )
        state = {
            "merge_scope": {"active": True, "subject": "Nova", "scope_type": "company"},
            "target_entities": ["company", "person"],
            "schemas": schemas,
            "llm_config": {},
            "origins": [],
            "_profile_cache": {
                "fingerprints": {"join_graph": fp},
                "artefacts": {"join_graph": {"join_graph": prebuilt}},
            },
        }
        with patch(
            "src.mcp_server.tools.plan_scope.build_join_graph",
            new=AsyncMock(side_effect=AssertionError("must not rebuild when cached")),
        ):
            result = await plan_scope(state)
        assert result["join_graph"] is prebuilt
        assert result["reused_stages"] == ["join_graph"]

    @pytest.mark.asyncio
    async def test_a_schema_drift_falls_through_to_rebuilding_the_graph(self) -> None:
        """The cache miss path must still work correctly -- a stale-shaped
        cached graph is never used."""
        schemas = _schemas()
        prebuilt = await build_join_graph(schemas, origins=[])
        state = {
            "merge_scope": {"active": True, "subject": "Nova", "scope_type": "company"},
            "target_entities": ["company", "person"],
            "schemas": schemas,
            "llm_config": {},
            "origins": [],
            "_profile_cache": {
                "fingerprints": {"join_graph": "fp-from-a-different-schema-shape"},
                "artefacts": {"join_graph": {"join_graph": prebuilt}},
            },
        }
        result = await plan_scope(state)
        assert result["join_graph"] is not prebuilt
        assert "reused_stages" not in result


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
            "subject": "Nova",
            "subject_entity": "company",
            "candidates": [{"origin_id": "crm", "fields": {"name": "Nova Labs"}}],
            "entities": {
                "company": [
                    {
                        "origin_id": "crm",
                        "table": "companies",
                        "mode": "match",
                        "columns": ["name"],
                        "reason": "own",
                    }
                ],
                "person": [
                    {
                        "origin_id": "crm",
                        "table": "employees",
                        "mode": "link",
                        "columns": ["company_id"],
                        "reason": "fk",
                    }
                ],
            },
        }
        out = _build_scope_interrupt(plan)
        assert out["node"] == "human_review_scope"
        assert out["data"]["subject"] == "Nova"
        assert len(out["data"]["candidates"]) == 1
        assert len(out["data"]["plan"]) == 2

    def test_forwards_every_field_including_link_path_hops(self) -> None:
        """Phase 5.1/5.2: the checkbox gate round-trips whatever it's shown
        back on approve, so every field an entry has (path/hops/confidence,
        not just the hand-picked subset the old builder forwarded) must
        survive into the payload -- dropping one here would silently lose
        it the moment a human approves the plan unchanged."""
        from src.orchestrator.nodes.human_nodes import _build_scope_interrupt

        plan = {
            "subject": "Nova",
            "subject_entity": "company",
            "candidates": [],
            "entities": {
                "person": [
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
                        "confidence": "declared",
                        "enabled": False,
                        "link_entity": "company",
                        "reason": "2-hop join",
                    }
                ]
            },
        }
        out = _build_scope_interrupt(plan)
        row = out["data"]["plan"][0]
        assert row["entity_type"] == "person"
        assert row["hops"] == 2
        assert row["confidence"] == "declared"
        assert row["enabled"] is False
        assert len(row["path"]) == 2
        assert row["link_entity"] == "company"

    def test_enabled_defaults_true_when_entry_omits_it(self) -> None:
        from src.orchestrator.nodes.human_nodes import _build_scope_interrupt

        plan = {
            "entities": {
                "company": [
                    {"origin_id": "crm", "table": "companies", "mode": "match", "columns": ["name"]}
                ]
            }
        }
        out = _build_scope_interrupt(plan)
        assert out["data"]["plan"][0]["enabled"] is True

    def test_not_found_rows_are_not_doubled(self) -> None:
        """A real entry covers only origin_1; plan_scope.py's own coverage
        guarantee (_ensure_complete_origin_coverage) only backfills when an
        entity's list is entirely empty, so origin_2's not_found placeholder
        here comes from _build_scope_interrupt's own coverage loop alone --
        verifying it appears exactly once, not duplicated by the two
        coverage passes double-counting the same gap."""
        from src.orchestrator.nodes.human_nodes import _build_scope_interrupt

        plan = {
            "entities": {
                "company": [
                    {
                        "origin_id": "origin_1",
                        "table": "Company",
                        "mode": "match",
                        "columns": ["name"],
                    },
                    {
                        "origin_id": "origin_2",
                        "table": "Company",
                        "mode": "match",
                        "columns": ["name"],
                    },
                ],
                "person": [
                    {"origin_id": "origin_1", "table": "Person", "mode": "link_path", "path": []},
                ],
            }
        }
        out = _build_scope_interrupt(plan)
        keys = [(r["entity_type"], r["origin_id"]) for r in out["data"]["plan"]]
        assert len(keys) == len(set(keys)), f"duplicate rows: {keys}"
        assert ("person", "origin_2") in keys

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
