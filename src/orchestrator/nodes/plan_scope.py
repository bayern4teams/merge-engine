"""plan_scope node: build the per-table extraction plan for a scoped merge.

Runs after analyze_dependencies. When the request is scoped to one organisation
(``merge_scope.active``), it asks plan_scope_impl how to restrict each entity's
tables to the subject and writes the resulting ``scope_plan`` into state, where
extract_data reads it. When the request is not scoped, it does nothing and the
pipeline extracts whole tables exactly as before.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.plan_scope import plan_scope_impl, probe_subject_candidates

logger = logging.getLogger("merge-engine.orchestrator.plan_scope")


async def plan_scope(state: dict[str, Any]) -> dict[str, Any]:
    """Populate ``scope_plan`` when the merge is scoped to one subject."""
    logger.debug("ENTER plan_scope")
    merge_scope = state.get("merge_scope") or {}
    target_entities = state.get("target_entities", []) or []

    planning_cfg = state.get("planning") or state.get("config", {}).get("planning") or {}
    engine = planning_cfg.get("engine") if isinstance(planning_cfg, dict) else None
    if not engine:
        engine = (
            state.get("llm_config", {}).get("planning.engine")
            or state.get("planning.engine")
            or "sequential"
        )

    if str(engine).lower() == "swarm" and state.get("scope_plan"):
        logger.debug("EXIT plan_scope (swarm scope_plan already populated)")
        return {
            "scope_plan": state.get("scope_plan", {}),
            "table_selection_notes": state.get("table_selection_notes", ""),
        }

    if not merge_scope.get("active") or not target_entities:
        logger.debug("EXIT plan_scope (not a scoped merge)")
        return {"scope_plan": {}, "table_selection_notes": state.get("table_selection_notes", "")}

    subject = merge_scope.get("subject", "")
    subject_entity = merge_scope.get("scope_type", "company")
    schemas = state.get("schemas", {})
    llm_config: dict[str, Any] = state.get("llm_config", {})

    logger.info("Planning scope for subject %r (%s)", subject, subject_entity)
    result = await plan_scope_impl(
        subject=subject,
        subject_entity=subject_entity,
        target_entities=target_entities,
        schemas=schemas,
        llm_config=llm_config,
    )
    scope_plan = result.get("scope_plan", {})
    entities = scope_plan.get("entities", {})
    logger.info(
        "Scope plan ready (llm_used=%s): %s",
        result.get("llm_used"),
        {e: len(v) for e, v in entities.items()},
    )

    # Preview which subject rows the plan will match, so the confirmation gate
    # can show "did you mean Nova Labs or Novatech?". Best-effort — a probe
    # failure leaves candidates empty but never stops the run.
    origins = state.get("origins", []) or []
    scope_plan["candidates"] = await probe_subject_candidates(subject, scope_plan, origins)

    return {
        "scope_plan": scope_plan,
        "table_selection_notes": (
            (state.get("table_selection_notes") or "")
            + (f"\nScope: {result.get('notes')}" if result.get("notes") else "")
        ).strip(),
    }
