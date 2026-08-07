"""Multi-agent planning swarm builder for merge-engine (B2.1).

Constructs the langgraph-swarm planning subgraph consisting of four specialized agents:
1. SchemaExplorer: Queries origin database schemas and foreign key graph.
2. EntityPlanner: Derives entity closure from actual FK connections rather than a fixed enum.
3. TableSelector: Resolves target entities to concrete (origin_id, table) source pairs.
4. ScopePlanner: Builds subject-scoped extraction and join filtering plans.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langgraph.graph import StateGraph
from langgraph_swarm import SwarmState, create_handoff_tool, create_swarm

from src.orchestrator.utils.entity_aliases import _group_of, concept_matches
from src.orchestrator.utils.entity_types import CANONICAL_ENTITY_TYPES

logger = logging.getLogger("merge-engine.orchestrator.planning.swarm_builder")


class SwarmPlannerState(SwarmState, total=False):
    """Extended state schema for the planning swarm."""

    user_request: str
    subject: str
    scope_type: str
    schemas: dict[str, Any]
    origins: list[dict[str, Any]]
    target_entities: list[str]
    explicit_target_entities: list[str]
    merge_scope: dict[str, Any]
    dependency_tree: dict[str, list[str]]
    extraction_order: list[str]
    entity_tables: dict[str, Any]
    table_selection_notes: str
    scope_plan: dict[str, Any]


def derive_fk_closure(
    schemas: dict[str, Any], subject: str = "", scope_type: str = "company"
) -> tuple[list[str], list[str]]:
    """Derive entity closure from schema foreign key graph.

    Starting from tables matching subject/scope_type (e.g. company), traverses
    all foreign key links to find all reachable entity families in the actual catalog.

    Returns
    -------
    tuple[list[str], list[str]]
        (target_entities, explicit_target_entities)
    """
    if not schemas:
        return ["company", "person"], ["company"]

    # Collect all table names and their FK links
    all_tables: dict[str, dict[str, Any]] = {}
    fk_edges: list[tuple[str, str]] = []  # (from_table, to_table)

    for _origin_id, schema_data in schemas.items():
        for table in schema_data.get("tables", []):
            tname = table.get("name", "")
            if not tname:
                continue
            all_tables[tname] = table
            for fk in table.get("foreign_keys", []):
                ref_t = fk.get("ref_table", "")
                if ref_t:
                    fk_edges.append((tname, ref_t))

    # Identify seed tables matching subject/scope_type
    seed_type = scope_type if scope_type and scope_type != "none" else "company"
    seed_tables = [t for t in all_tables if concept_matches(seed_type, t)]
    if not seed_tables:
        seed_tables = list(all_tables.keys())

    visited_tables: set[str] = set(seed_tables)
    queue: list[str] = list(seed_tables)

    # Traverse FK connections (both directions)
    while queue:
        curr = queue.pop(0)
        for t_from, t_to in fk_edges:
            if t_from == curr and t_to not in visited_tables:
                visited_tables.add(t_to)
                queue.append(t_to)
            elif t_to == curr and t_from not in visited_tables:
                visited_tables.add(t_from)
                queue.append(t_from)

    # Map connected tables back to canonical entity types or alias group concepts
    discovered_entities: set[str] = set()
    for tname in visited_tables:
        matched = False
        for ctype in CANONICAL_ENTITY_TYPES:
            if concept_matches(ctype, tname):
                discovered_entities.add(ctype)
                matched = True
        if not matched:
            grp = _group_of(tname)
            if grp:
                for item in grp:
                    if item in CANONICAL_ENTITY_TYPES:
                        discovered_entities.add(item)
                        matched = True
                        break
            if not matched:
                # Use clean table name token as entity concept if missing
                clean_name = tname.lower().replace("company_", "").replace("person_", "")
                if clean_name in CANONICAL_ENTITY_TYPES:
                    discovered_entities.add(clean_name)

    # Always ensure primary subject type is included
    discovered_entities.add(seed_type)

    # Order target entities logically (subject first, then others sorted)
    target_entities = [seed_type, *sorted(e for e in discovered_entities if e != seed_type)]

    # Explicit target entities are types explicitly mentioned in subject/request
    explicit_target_entities = [seed_type]

    logger.info(
        "FK-derived entity closure for subject %r (%s): %d entities found: %s",
        subject,
        seed_type,
        len(target_entities),
        target_entities,
    )
    return target_entities, explicit_target_entities


def create_planning_swarm(
    model_or_client: Any,
) -> StateGraph[SwarmPlannerState]:
    """Build compiled langgraph-swarm planning graph.

    Returns
    -------
    StateGraph
        The swarm workflow builder ready to compile.
    """
    # Build handoff tools
    schema_to_planner = create_handoff_tool(
        agent_name="EntityPlanner",
        description="Hand off to EntityPlanner to derive entity closure from FK graph",
    )
    schema_to_selector = create_handoff_tool(
        agent_name="TableSelector",
        description="Hand off to TableSelector to map entities to source tables",
    )
    planner_to_schema = create_handoff_tool(
        agent_name="SchemaExplorer",
        description="Hand off to SchemaExplorer to verify table existence in origin schemas",
    )
    planner_to_selector = create_handoff_tool(
        agent_name="TableSelector",
        description="Hand off to TableSelector once target entity list is derived",
    )
    selector_to_scope = create_handoff_tool(
        agent_name="ScopePlanner",
        description="Hand off to ScopePlanner to compute table filtering plan",
    )
    selector_to_planner = create_handoff_tool(
        agent_name="EntityPlanner",
        description="Hand off back to EntityPlanner to revise target entity list",
    )
    scope_to_selector = create_handoff_tool(
        agent_name="TableSelector",
        description="Hand off back to TableSelector if scope planning requires table revisions",
    )

    schema_explorer = create_agent(
        model_or_client,
        tools=[schema_to_planner, schema_to_selector],
        system_prompt=(
            "You are SchemaExplorer. Your job is to inspect origin database catalogs, "
            "verify table existence, and check foreign key graphs. Hand off to EntityPlanner "
            "or TableSelector when ready."
        ),
        name="SchemaExplorer",
    )

    entity_planner = create_agent(
        model_or_client,
        tools=[planner_to_schema, planner_to_selector],
        system_prompt=(
            "You are EntityPlanner. Your job is to derive the entity closure for the user "
            "request from the actual schema FK graph. Verify tables with SchemaExplorer and "
            "hand off to TableSelector."
        ),
        name="EntityPlanner",
    )

    table_selector = create_agent(
        model_or_client,
        tools=[selector_to_scope, selector_to_planner],
        system_prompt=(
            "You are TableSelector. Your job is to resolve each entity concept to concrete "
            "(origin_id, table) pairs in the catalog. Hand off to ScopePlanner when done."
        ),
        name="TableSelector",
    )

    scope_planner = create_agent(
        model_or_client,
        tools=[scope_to_selector],
        system_prompt=(
            "You are ScopePlanner. Your job is to produce match/link/link_via scope plans "
            "restricting extractions to the request subject."
        ),
        name="ScopePlanner",
    )

    workflow = create_swarm(
        [schema_explorer, entity_planner, table_selector, scope_planner],
        default_active_agent="SchemaExplorer",
        state_schema=SwarmPlannerState,
    )
    return workflow
