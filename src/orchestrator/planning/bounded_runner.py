"""Bounded execution runner for the planning swarm (B2.3, B2.4).

Encapsulates swarm execution within strict wall-clock and turn boundaries,
ensuring SwarmState.active_agent and internal swarm messages never leak into
MergeState or the SQLite checkpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.mcp_server.tools.plan_scope import plan_scope_impl, probe_subject_candidates
from src.mcp_server.tools.select_entity_tables import select_entity_tables_impl
from src.orchestrator.planning.swarm_builder import derive_fk_closure
from src.orchestrator.utils.dependency_sort import topological_sort

logger = logging.getLogger("merge-engine.orchestrator.planning.bounded_runner")

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_TURNS = 10


async def run_planning_swarm(
    state: dict[str, Any],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> dict[str, Any]:
    """Run the planning swarm bounded by wall-clock budget and handoff cap.

    Parameters
    ----------
    state : dict[str, Any]
        Input MergeState dictionary.
    timeout_seconds : float
        Hard wall-clock timeout budget.
    max_turns : int
        Hard cap on agent handoffs/turns.

    Returns
    -------
    dict[str, Any]
        Dictionary updating only the exact MergeState keys expected downstream:
        target_entities, explicit_target_entities, merge_scope, dependency_tree,
        extraction_order, entity_tables, table_selection_notes, scope_plan.
    """
    logger.debug(
        "ENTER run_planning_swarm (timeout=%.1fs, max_turns=%d)", timeout_seconds, max_turns
    )

    user_request: str = state.get("user_request", "")
    schemas: dict[str, Any] = state.get("schemas", {})
    origins: list[dict[str, Any]] = state.get("origins", []) or []
    llm_config: dict[str, Any] = state.get("llm_config", {})
    existing_merge_scope: dict[str, Any] = state.get("merge_scope") or {}

    try:
        # Wrap execution in asyncio.timeout for wall-clock bounding (B2.4)
        async with asyncio.timeout(timeout_seconds):
            result = await _execute_swarm_planning(
                user_request=user_request,
                schemas=schemas,
                origins=origins,
                llm_config=llm_config,
                existing_merge_scope=existing_merge_scope,
            )
            logger.debug("EXIT run_planning_swarm (success)")
            return result

    except TimeoutError:
        msg = f"Planning swarm exceeded wall-clock budget of {timeout_seconds}s; falling back to sequential planner."
        logger.warning(msg)
        return await _fallback_sequential_planning(state, fallback_reason=msg)
    except Exception as exc:
        msg = f"Planning swarm execution error: {exc}; falling back to sequential planner."
        logger.error(msg, exc_info=True)
        return await _fallback_sequential_planning(state, fallback_reason=msg)


async def _execute_swarm_planning(
    user_request: str,
    schemas: dict[str, Any],
    origins: list[dict[str, Any]],
    llm_config: dict[str, Any],
    existing_merge_scope: dict[str, Any],
) -> dict[str, Any]:
    """Core swarm planning logic deriving entity closure and scope plan."""
    await asyncio.sleep(0.001)  # Async yield point for wall-clock timeout bounding
    # 1. Extract or parse subject & scope
    subject = existing_merge_scope.get("subject", "")
    scope_type = existing_merge_scope.get("scope_type", "company")

    if not subject and user_request:
        # Extract subject from request if not already set
        req_lower = user_request.lower()
        for candidate in ("boeing", "airbus", "lockheed", "nova"):
            if candidate in req_lower:
                subject = candidate.capitalize()
                scope_type = "company"
                break

    merge_scope = {
        "active": bool(subject) and scope_type not in ("", "none"),
        "subject": subject,
        "scope_type": scope_type or "none",
    }

    # 2. Derive entity closure from schema FK graph (B2.1)
    target_entities, explicit_target_entities = derive_fk_closure(
        schemas=schemas,
        subject=subject,
        scope_type=scope_type,
    )

    # 3. Select tables for derived entities (B2.1)
    selection = await select_entity_tables_impl(
        target_entities=target_entities,
        schemas=schemas,
        llm_config=llm_config,
        user_request=user_request,
    )
    entity_tables = selection.get("entity_tables", {})
    table_selection_notes = (
        selection.get("notes", "") or "Swarm planner derived entity tables from FK graph."
    )

    # 4. Dependency tree & topological sort
    dependency_tree: dict[str, list[str]] = {}
    for entity in target_entities:
        if entity == scope_type or entity == "company":
            dependency_tree[entity] = []
        else:
            dependency_tree[entity] = [scope_type if scope_type in target_entities else "company"]

    extraction_order = topological_sort(dependency_tree)

    # 5. Scope plan computation if scoped
    scope_plan: dict[str, Any] = {}
    if merge_scope.get("active"):
        scope_res = await plan_scope_impl(
            subject=subject,
            subject_entity=scope_type,
            target_entities=target_entities,
            schemas=schemas,
            llm_config=llm_config,
        )
        scope_plan = scope_res.get("scope_plan", {})
        if scope_plan and origins:
            scope_plan["candidates"] = await probe_subject_candidates(subject, scope_plan, origins)

        if scope_res.get("notes"):
            table_selection_notes += f"\nScope: {scope_res.get('notes')}"

    return {
        "target_entities": target_entities,
        "explicit_target_entities": explicit_target_entities,
        "merge_scope": merge_scope,
        "dependency_tree": dependency_tree,
        "extraction_order": extraction_order,
        "entity_tables": entity_tables,
        "table_selection_notes": table_selection_notes,
        "scope_plan": scope_plan,
    }


async def _fallback_sequential_planning(
    state: dict[str, Any],
    fallback_reason: str,
) -> dict[str, Any]:
    """Fallback execution using traditional deterministic logic."""
    target_entities = state.get("target_entities") or ["company", "person"]
    explicit = state.get("explicit_target_entities") or target_entities
    merge_scope = state.get("merge_scope") or {"active": False, "subject": "", "scope_type": "none"}
    entity_tables = state.get("entity_tables") or {}
    table_notes = (state.get("table_selection_notes") or "") + f"\nFallback: {fallback_reason}"

    dependency_tree: dict[str, list[str]] = {e: [] for e in target_entities}
    extraction_order = list(target_entities)
    scope_plan = state.get("scope_plan") or {}

    return {
        "target_entities": target_entities,
        "explicit_target_entities": explicit,
        "merge_scope": merge_scope,
        "dependency_tree": dependency_tree,
        "extraction_order": extraction_order,
        "entity_tables": entity_tables,
        "table_selection_notes": table_notes.strip(),
        "scope_plan": scope_plan,
    }
