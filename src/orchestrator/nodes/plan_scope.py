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

from src.mcp_server.tools.join_graph import _DEFAULT_SAMPLE_SIZE
from src.mcp_server.tools.plan_scope import plan_scope_impl, probe_subject_candidates
from src.orchestrator.fingerprint import origins_fingerprint, stage_fingerprint
from src.orchestrator.profiles import stage_cache_get

logger = logging.getLogger("merge-engine.orchestrator.plan_scope")


async def plan_scope(state: dict[str, Any]) -> dict[str, Any]:
    """Populate ``scope_plan`` when the merge is scoped to one subject."""
    logger.debug("ENTER plan_scope")
    merge_scope = state.get("merge_scope") or {}
    target_entities = state.get("target_entities", []) or []

    if not merge_scope.get("active") or not target_entities:
        logger.debug("EXIT plan_scope (not a scoped merge)")
        return {"scope_plan": {}, "table_selection_notes": state.get("table_selection_notes", "")}

    subject = merge_scope.get("subject", "")
    subject_entity = merge_scope.get("scope_type", "company")
    schemas = state.get("schemas", {})
    llm_config: dict[str, Any] = state.get("llm_config", {})
    origins = state.get("origins", []) or []
    scope_config: dict[str, Any] = state.get("scope_config", {}) or {}

    # Phase A4: a graph already in THIS run's state (a retry after this same
    # node failed downstream) wins first -- it is guaranteed fresh, no
    # fingerprint check needed. Failing that, a profile-cached graph is
    # reused only if its fingerprint (origins' schema shape + sample size)
    # still matches; a schema drift correctly falls through to rebuilding.
    join_graph_fp = stage_fingerprint(
        "join_graph",
        origins_fp=origins_fingerprint(origins, schemas),
        sample_size=scope_config.get("join_graph_sample_size") or _DEFAULT_SAMPLE_SIZE,
    )
    cached_graph = state.get("join_graph")
    reused_join_graph = False
    if cached_graph is None:
        profile_cache = state.get("_profile_cache") or {}
        cached_bundle = stage_cache_get(profile_cache, "join_graph", join_graph_fp)
        if cached_bundle and cached_bundle.get("join_graph") is not None:
            cached_graph = cached_bundle["join_graph"]
            reused_join_graph = True
            logger.info(
                "Reusing join_graph from profile %r (fingerprint match)",
                state.get("profile_name"),
            )

    logger.info("Planning scope for subject %r (%s)", subject, subject_entity)
    result = await plan_scope_impl(
        subject=subject,
        subject_entity=subject_entity,
        target_entities=target_entities,
        schemas=schemas,
        llm_config=llm_config,
        origins=origins,
        scope_config=scope_config,
        cached_graph=cached_graph,
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
    scope_plan["candidates"] = await probe_subject_candidates(subject, scope_plan, origins)

    return {
        "scope_plan": scope_plan,
        "table_selection_notes": (
            (state.get("table_selection_notes") or "")
            + (f"\nScope: {result.get('notes')}" if result.get("notes") else "")
        ).strip(),
        "join_graph": result.get("join_graph"),
        # Recorded even when reused, so a completed run's capture_from_state
        # can cache this (schema-shape-derived, request-independent) graph
        # for the NEXT run -- the cache check above is what consumes it.
        "_join_graph_fingerprint": join_graph_fp,
        **({"reused_stages": ["join_graph"]} if reused_join_graph else {}),
    }
