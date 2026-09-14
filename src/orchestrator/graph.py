"""
LangGraph state graph builder and compiled graph for merge-engine.

Wires all orchestrator nodes, edges, conditional edges, and interrupt points
into a single compiled LangGraph workflow.

Supports two modes:
1. In-process mode: build_graph() with MemorySaver (for direct WorkflowManager use)
2. Server mode: graph module-level export for LangGraph CLI server
"""

from __future__ import annotations

import functools
import logging
import operator
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from src.orchestrator.nodes import (
    analyze_dependencies,
    discover_schemas,
    extract_data,
    handle_error,
    has_low_confidence_clusters,
    has_unmapped_fields,
    human_final_approval,
    human_review_deps,
    human_review_mappings,
    human_review_matches,
    human_review_scope,
    load_data_node,
    map_fields,
    parse_request,
    plan_scope,
    resolve_entities,
    unify_entity_groups,
    update_extraction_state_node,
    validate_data_node,
    validate_graph_consistency,
)
from src.orchestrator.nodes.preflight import preflight
from src.orchestrator.utils.retry import TransientError, classify_exception

logger = logging.getLogger("merge-engine.orchestrator.graph")

# Node names for interrupt configuration. handle_error is listed last and
# kept out of GATE_PRODUCER: it is a gate the pipeline hopes never to reach,
# not a step in the normal review sequence.
HITL_NODES = [
    "human_review_deps",
    "human_review_mappings",
    "human_review_scope",
    "human_review_matches",
    "human_final_approval",
    "handle_error",
]

# The four gates a healthy run passes through, for UI stepper purposes.
REVIEW_GATES = HITL_NODES[:-1]


def _is_transient(exc: Exception) -> bool:
    """Retry predicate for LangGraph's RetryPolicy.

    Classification happens here rather than at each raise site so a raw
    driver exception that nobody thought to wrap still gets judged on its
    merits instead of defaulting to "retry forever" or "never retry".
    """
    return isinstance(classify_exception(exc), TransientError)


# Anything touching a database or the LLM API can fail for reasons that have
# nothing to do with the request — a dropped connection, a rate limit, a
# failover. Those get retried with exponential backoff and jitter; every
# other failure class falls straight through to the error gate, because
# retrying a syntax error three times only delays the message.
NETWORK_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    max_interval=30.0,
    jitter=True,
    retry_on=_is_transient,
)

# Where each fallible node goes when it succeeds. The error gate reads this
# to offer "skip": continue from the failure as if that node had produced
# nothing, rather than forcing the whole run to be abandoned.
NODE_SUCCESSOR = {
    "preflight": "discover_schemas",
    "discover_schemas": "parse_request",
    "parse_request": "analyze_dependencies",
    "analyze_dependencies": "human_review_deps",
    "map_fields": "plan_scope",
    # plan_scope_impl currently catches every exception itself and falls back
    # to a heuristic plan (never raises, never sets state["failure"]) — but
    # _error_gate_destinations() must cover every node that COULD reach the
    # error gate, not just the ones that do today. Without this entry, a
    # future hard failure in plan_scope would hit `if decision == "skip" and
    # failed_node in NODE_SUCCESSOR` (false), silently downgrading "skip" to
    # "abort" with no warning. Skipping a failed plan_scope means treating
    # the merge as unscoped, same as _scope_router's own "continue" path
    # when there is no plan.
    "plan_scope": "extract_data",
    "extract_data": "validate_data",
    "validate_data": "resolve_entities",
    "resolve_entities": "unify_entities",
    "unify_entities": "human_final_approval",
    "load_data": "validate_graph_consistency",
}

# Which node produced the data a gate reviews. "retry" at a gate re-runs that
# node with the user's written reason injected into its prompt.
GATE_PRODUCER = {
    "human_review_deps": "analyze_dependencies",
    "human_review_mappings": "map_fields",
    "human_review_scope": "plan_scope",
    "human_review_matches": "resolve_entities",
}

# A user can send the same gate back this many times before the graph moves on
# regardless — otherwise a retry loop never terminates.
MAX_GATE_RETRIES = 3


def _gate_router(gate: str, forward: str):
    """Build the conditional-edge router for one HITL gate.

    The four decisions a gate accepts:

    ``approve`` / ``edit``
        Continue to ``forward``. (Edits are already merged into state by
        ``WorkflowManager.resume_workflow`` before the graph resumes.)
    ``retry``
        Go back to the node that produced this gate's data and run it again
        with the user's reason as extra prompt context.
    ``reject`` / ``abort``
        End the run. Nothing further executes and nothing is written.

    Absent or unrecognised feedback continues forward, preserving the
    behaviour of runs that predate the richer decision vocabulary.
    """
    producer = GATE_PRODUCER.get(gate)

    def route(state: dict[str, Any]) -> str:
        feedback = state.get("human_feedback") or {}
        decision = str(feedback.get("decision") or "").strip().lower()

        # Legacy shape: {"approved": bool} with no decision verb.
        if not decision and "approved" in feedback:
            decision = "approve" if feedback.get("approved") else "reject"

        if decision in ("reject", "abort"):
            logger.info("Gate '%s' rejected by user — ending run.", gate)
            return "abort"

        if decision == "retry" and producer:
            attempts = (state.get("gate_retry_counts") or {}).get(gate, 0)
            if attempts > MAX_GATE_RETRIES:
                logger.warning(
                    "Gate '%s' retried %d times (limit %d) — continuing "
                    "forward instead of looping again.",
                    gate,
                    attempts,
                    MAX_GATE_RETRIES,
                )
                return "forward"
            logger.info(
                "Gate '%s' sent back to '%s' (attempt %d).",
                gate,
                producer,
                attempts,
            )
            return "retry"

        return "forward"

    route.__name__ = f"route_{gate}"
    return route


def _has_open_failure(state: dict[str, Any], node: str) -> bool:
    """Did `node` just record a failure that no human has dispositioned yet?"""
    failure = state.get("failure") or {}
    return bool(failure) and failure.get("node") == node


def _failure_route(state: dict[str, Any], node: str) -> str | None:
    """Decide what an open failure on `node` means for the run.

    The failure's ``disposition`` is what distinguishes the two cases:

    ``escalate``
        The run *could* continue without this step, so a human gets to
        decide — retry it, skip it knowing the output is incomplete, or
        abandon the run.
    ``fail``
        The run cannot work at all: an unreachable database, refused
        credentials, a destination we may not write to. There is nothing to
        retry from inside the run and nothing safe to skip, so it ends here
        rather than offering a gate whose only honest answer is "abort".
    """
    if not _has_open_failure(state, node):
        return None
    failure = state.get("failure") or {}
    detail = str(failure.get("error", ""))[:200]
    if str(failure.get("disposition", "escalate")).lower() == "fail":
        logger.error("Node '%s' failed unrecoverably: %s", node, detail)
        return "abort"
    logger.error("Node '%s' failed: %s — routing to the error gate.", node, detail)
    return "error"


def _fallible_router(node: str, forward: str):
    """Route a node onward, to the error gate, or straight to the end.

    A node that cannot finish its work records a structured ``failure`` and
    returns normally; this router turns that into a stop. The alternative —
    letting the node log and continue — is what allowed a half-extracted
    table to be merged as though it were complete.
    """

    def route(state: dict[str, Any]) -> str:
        return _failure_route(state, node) or "continue"

    route.__name__ = f"route_after_{node}"
    return route


def _error_gate_router(state: dict[str, Any]) -> str:
    """Route out of the error gate on the user's decision.

    ``retry`` re-runs the node that failed, ``skip`` continues from its
    successor without its output, and anything else ends the run. Absent
    feedback aborts rather than continues: the run is already known to be
    incomplete, so guessing "carry on" is the one option that can silently
    produce a wrong merge.

    Reads the *handled* failure rather than ``state["failure"]``: this runs
    after ``handle_error``, which deliberately clears ``failure`` so that
    re-running the failing node is not immediately re-caught by its own
    router. The decision and the node it applies to travel together in
    ``handled_failures`` instead.
    """
    handled = state.get("handled_failures") or []
    latest = handled[-1] if handled else {}
    failed_node = latest.get("node") or ""
    decision = str(latest.get("decision") or "").strip().lower()

    if decision == "retry" and failed_node:
        attempts = (state.get("retry_counts") or {}).get(failed_node, 0)
        if attempts > MAX_GATE_RETRIES:
            logger.warning(
                "Node '%s' retried %d times (limit %d) — aborting instead of looping.",
                failed_node,
                attempts,
                MAX_GATE_RETRIES,
            )
            return "abort"
        logger.info("Error gate: retrying '%s' (attempt %d).", failed_node, attempts)
        return failed_node

    if decision == "skip" and failed_node in NODE_SUCCESSOR:
        successor = NODE_SUCCESSOR[failed_node]
        logger.warning(
            "Error gate: skipping failed node '%s' and continuing at '%s'. "
            "The run is knowingly incomplete.",
            failed_node,
            successor,
        )
        return successor

    logger.info("Error gate: aborting run after failure in '%s'.", failed_node)
    return "abort"


def _scope_router(state: dict[str, Any]) -> str:
    """Show the scope gate only for a scoped merge with a plan to confirm.

    An unscoped run, or one whose planning produced nothing to restrict, goes
    straight to extraction — the gate is never entered, so it never interrupts.
    """
    merge_scope = state.get("merge_scope") or {}
    scope_plan = state.get("scope_plan") or {}
    has_plan = bool(scope_plan.get("entities") or {})
    if merge_scope.get("active") and has_plan:
        return "review"
    return "continue"


def _resolve_entities_router(state: dict[str, Any]) -> str:
    """Error check first, then the existing low-confidence review decision."""
    failed = _failure_route(state, "resolve_entities")
    if failed:
        return failed
    return "review" if has_low_confidence_clusters(state) else "continue"


def _map_fields_router(state: dict[str, Any]) -> str:
    """Error check first, then the existing unmapped-fields review decision."""
    failed = _failure_route(state, "map_fields")
    if failed:
        return failed
    return "review" if has_unmapped_fields(state) else "continue"


def _error_gate_destinations() -> dict[str, str]:
    """Every node the error gate can hand control back to."""
    destinations: dict[str, str] = {"abort": END}
    for node, successor in NODE_SUCCESSOR.items():
        destinations[node] = node
        destinations[successor] = successor
    return destinations


NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _timed(name: str, fn: NodeFn) -> NodeFn:
    """Wrap a node so its wall-clock seconds land in MergeState.timings.

    Centralised here rather than added to each node file, so that adding a
    new node (or a branch's own node) never requires touching every node's
    body just to keep the timing budget (Phase 0 Sec 0.6) accurate. A node
    that never actually runs its body on a given step (a HITL gate resumed
    via `interrupt_before`) correctly contributes no timing entry for that
    step - there is nothing to time.
    """

    @functools.wraps(fn)
    async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic()
        result = await fn(state)
        elapsed = time.monotonic() - start
        result = dict(result) if result else {}
        result["timings"] = {name: elapsed}
        return result

    return wrapper


def _build_builder() -> StateGraph:
    """Build the raw StateGraph (nodes + edges) without compiling."""
    builder = StateGraph(MergeState)

    # ── Add all nodes ───────────────────────────────────────────────
    # Every node that reaches out to a database or the LLM carries the
    # network retry policy. Pure-compute nodes (unify_entities) and the
    # HITL gates deliberately do not: retrying them cannot help.
    builder.add_node("preflight", _timed("preflight", preflight), retry_policy=NETWORK_RETRY)
    builder.add_node(
        "parse_request", _timed("parse_request", parse_request), retry_policy=NETWORK_RETRY
    )
    builder.add_node(
        "discover_schemas",
        _timed("discover_schemas", discover_schemas),
        retry_policy=NETWORK_RETRY,
    )
    builder.add_node(
        "analyze_dependencies",
        _timed("analyze_dependencies", analyze_dependencies),
        retry_policy=NETWORK_RETRY,
    )
    builder.add_node("human_review_deps", _timed("human_review_deps", human_review_deps))
    # Scope planning for a "merge everything about Nova" request. Carries the
    # network retry policy because it probes the origins for candidate subject
    # rows to show at the confirmation gate.
    builder.add_node("plan_scope", _timed("plan_scope", plan_scope), retry_policy=NETWORK_RETRY)
    builder.add_node("human_review_scope", _timed("human_review_scope", human_review_scope))
    builder.add_node(
        "extract_data", _timed("extract_data", extract_data), retry_policy=NETWORK_RETRY
    )
    # Data quality gate (Phase 4.5): never sets state["failure"] itself --
    # a hard `fail` severity is surfaced at human_final_approval for a human
    # to decide, not auto-aborted here -- so it needs no retry policy or
    # error-gate routing of its own, just a plain edge to resolve_entities.
    builder.add_node("validate_data", _timed("validate_data", validate_data_node))
    builder.add_node(
        "resolve_entities", _timed("resolve_entities", resolve_entities), retry_policy=NETWORK_RETRY
    )
    builder.add_node("human_review_matches", _timed("human_review_matches", human_review_matches))
    builder.add_node("unify_entities", _timed("unify_entities", unify_entity_groups))
    builder.add_node("map_fields", _timed("map_fields", map_fields), retry_policy=NETWORK_RETRY)
    builder.add_node(
        "human_review_mappings", _timed("human_review_mappings", human_review_mappings)
    )
    builder.add_node("human_final_approval", _timed("human_final_approval", human_final_approval))
    builder.add_node("load_data", _timed("load_data", load_data_node), retry_policy=NETWORK_RETRY)
    # B.4.2: post-merge consistency rules over just-written destination
    # facts. Never sets state["failure"] itself (same discipline as
    # validate_data) -- a rule violation is a red flag for a human to see
    # at the run report, never an auto-abort -- so it needs no
    # _fallible_router error routing, just a plain edge onward.
    builder.add_node(
        "validate_graph_consistency",
        _timed("validate_graph_consistency", validate_graph_consistency),
    )
    builder.add_node(
        "update_extraction_state",
        _timed("update_extraction_state", update_extraction_state_node),
    )
    builder.add_node("handle_error", _timed("handle_error", handle_error))

    # ── Main flow edges ─────────────────────────────────────────────
    # Preflight runs before anything else so an unreachable database or a
    # read-only destination fails in seconds, not five minutes into an
    # extraction that was never going to be loadable.
    builder.set_entry_point("preflight")

    # Every fallible node routes through its own error check. A node that
    # records a `failure` stops the run at the error gate instead of
    # continuing with partial output.
    for node in (
        "preflight",
        "parse_request",
        "discover_schemas",
        "analyze_dependencies",
        "extract_data",
        "unify_entities",
        "load_data",
    ):
        forward = NODE_SUCCESSOR[node]
        builder.add_conditional_edges(
            node,
            _fallible_router(node, forward),
            {"continue": forward, "error": "handle_error", "abort": END},
        )

    # validate_data never sets state["failure"] itself (a hard `fail`
    # severity is surfaced at human_final_approval for a human to decide,
    # never auto-aborted), so it needs no _fallible_router error routing --
    # a plain edge is the honest representation of "this step cannot fail
    # the run by itself".
    builder.add_edge("validate_data", "resolve_entities")

    # validate_graph_consistency: same "cannot fail the run by itself"
    # discipline as validate_data above -- a plain edge, no error-gate
    # routing of its own.
    builder.add_edge("validate_graph_consistency", "update_extraction_state")

    # Conditional: low-confidence entity matches -> human review
    builder.add_conditional_edges(
        "resolve_entities",
        _resolve_entities_router,
        {
            "error": "handle_error",
            "abort": END,
            "review": "human_review_matches",
            "continue": "unify_entities",
        },
    )

    # Conditional: unmapped fields -> human review
    builder.add_conditional_edges(
        "map_fields",
        _map_fields_router,
        {
            "error": "handle_error",
            "abort": END,
            "review": "human_review_mappings",
            "continue": "plan_scope",
        },
    )

    # ── Gate routing ────────────────────────────────────────────────
    # Every HITL gate is a real decision point: approve/edit continues,
    # retry sends the work back to the node that produced it, and
    # reject ends the run without writing anything.
    # Dependency review forwards into field mapping rather than scope planning.
    builder.add_conditional_edges(
        "human_review_deps",
        _gate_router("human_review_deps", "map_fields"),
        {"forward": "map_fields", "retry": "analyze_dependencies", "abort": END},
    )
    # Field mapping review forwards into scope planning.
    builder.add_conditional_edges(
        "human_review_mappings",
        _gate_router("human_review_mappings", "plan_scope"),
        {"forward": "plan_scope", "retry": "map_fields", "abort": END},
    )
    # After planning, a scoped merge stops at the scope-confirmation gate; an
    # unscoped one skips straight to extraction (the gate is never entered, so
    # it never interrupts — the same auto-skip the match/mapping gates use).
    builder.add_conditional_edges(
        "plan_scope",
        _scope_router,
        {"review": "human_review_scope", "continue": "extract_data"},
    )
    builder.add_conditional_edges(
        "human_review_scope",
        _gate_router("human_review_scope", "extract_data"),
        {"forward": "extract_data", "retry": "plan_scope", "abort": END},
    )
    builder.add_conditional_edges(
        "human_review_matches",
        _gate_router("human_review_matches", "unify_entities"),
        {"forward": "unify_entities", "retry": "resolve_entities", "abort": END},
    )
    # Final approval has no producer to retry — it either loads or it doesn't.
    # load_data_node independently refuses to write unless load_approval is
    # True, so a mis-route can never turn a rejection into a write.
    builder.add_conditional_edges(
        "human_final_approval",
        _gate_router("human_final_approval", "load_data"),
        {"forward": "load_data", "retry": "load_data", "abort": END},
    )

    builder.add_edge("update_extraction_state", END)

    # ── Error routing ───────────────────────────────────────────────
    # The error gate is a real HITL stop. It shows what failed, where, and
    # how many attempts were made, then routes on the user's decision:
    # retry that node, skip it and continue knowingly incomplete, or abort.
    builder.add_conditional_edges(
        "handle_error",
        _error_gate_router,
        _error_gate_destinations(),
    )

    return builder


class MergeState(TypedDict):
    user_request: str
    run_id: str

    target_entities: list[Any]
    # Subset of target_entities the user actually named/asked for, as opposed to
    # types a wildcard rule (e.g. "Company Wildcards") merely inferred. Added
    # 2026-07-30 (remediation #1, merge-engine-remediation-1-to-6 in project
    # memory) so extract_data can escalate a missing EXPLICIT entity (a real gap)
    # while only warning on a missing INFERRED one (a wildcard guessed wrong).
    explicit_target_entities: list[Any]
    # Inferred (non-explicit) entity types that had no source table anywhere and
    # were dropped without escalating -- set by extract_data.py, surfaced in the
    # reconciliation report / data-quality scorecard (remediation #6).
    dropped_inferred_entities: list[Any]
    # Entities the scope plan named (real candidate tables existed) that ended
    # up with 0 rows purely because every candidate was scoped out (no plan
    # entry, no subject ids, or an empty join-path result) -- distinct from
    # dropped_inferred_entities (no table existed at all) and from a real
    # extraction failure (failed_tables, in the failure context). Set by
    # extract_data.py (Phase 4.4 of the fix-bugs follow-up plan); before this
    # such an entity was invisible beyond an unexplained "extracted_total: 0".
    scoped_out_entities: list[Any]
    # Set by parse_request when the request targets one named organisation
    # ("all information about Nova"): {active, subject, scope_type}. When
    # active, scope planning, extraction and dedup all restrict to the subject.
    merge_scope: dict[str, Any]
    origins: list[dict[str, Any]]
    destination: dict[str, Any]

    llm_config: dict[str, Any]
    extraction_config: dict[str, Any]
    scope_config: dict[str, Any]

    schemas: dict[str, Any]
    dependency_tree: dict[str, Any]
    column_dependencies: list[Any]
    # Set by analyze_dependencies when the LLM call fails and it falls back
    # to a flat (unordered) dependency tree, so a human sees the tree is
    # unverified rather than mistaking it for a real analysis.
    dependency_analysis_warning: str
    extraction_order: list[Any]

    # Which source tables hold each entity, chosen by the LLM over the FULL
    # table catalog and confirmed by the human at the dependency gate:
    # {entity: [{origin_id, table, confidence, reason, source}]}. Empty means
    # extraction falls back to the alias-name heuristic.
    entity_tables: dict[str, Any]
    table_selection_notes: str
    # Phase A4: the input fingerprint entity_tables was computed (or reused)
    # from -- capture_from_state reads this to decide whether to cache the
    # result into the profile. Undeclared here, it is silently dropped by
    # LangGraph before the run ends (only schema-declared keys survive a
    # node's return dict), which is exactly what made stage caching a no-op
    # end-to-end despite parse_request.py setting it on every return path.
    _entity_tables_fingerprint: str

    # Built by plan_scope for a scoped merge: how to restrict each entity's
    # tables to the subject — {subject, subject_entity, entities: {entity:
    # [{origin_id, table, mode: match|link, columns, ...}]}}. Empty for an
    # unscoped merge, in which case extraction reads whole tables as before.
    scope_plan: dict[str, Any]
    # The JoinGraph plan_scope built (or reused) while producing scope_plan
    # -- cached so a node retry within the same run, or a profile-matched
    # repeat run (fingerprint.py), can skip re-sampling every candidate
    # edge's inclusion dependency. None for an unscoped merge.
    join_graph: Any
    # Phase A4: the input fingerprint join_graph was computed (or reused)
    # from -- same rationale as _entity_tables_fingerprint above.
    _join_graph_fingerprint: str

    # Phase O — decisions a saved profile pins so a re-run is a re-run.
    # {entity: {blocking_keys, unique_id_fields, deterministic_id_fields,
    #           strictness}} (O.2; deterministic_id_fields added in Phase 3.1
    # of merge-engine-remediation-1-to-6 — operator-declared identity keys
    # like a customer_ref that settle a match/non-match directly)
    matching_rules: dict[str, Any]
    # {field: [rule, ...]} survivorship, "_default" covers the rest (O.3)
    conflict_policy: dict[str, Any]
    # Name of the profile this run was started from, "" when ad-hoc (O.4)
    profile_name: str
    # Phase A4: the applied profile's per-stage fingerprints/artefacts
    # ({"fingerprints": {stage: fp}, "artefacts": {stage: value}}) --
    # separate from the PINNED_KEYS values above, which apply_profile seeds
    # unconditionally. A node consults this via profiles.stage_cache_get to
    # decide whether ITS OWN cached artefact still matches the current
    # run's input shape, never trusting a pin blindly. Set once by
    # apply_profile before the graph starts; no node ever returns this key,
    # so it needs no reducer.
    _profile_cache: dict[str, Any]
    # Which cacheable stages this run actually reused (Phase A4) -- surfaced
    # at the gates so a human sees "reused from profile X" rather than a
    # silent skip. operator.add for the same reason errors/handled_failures
    # is: more than one node may report a reuse across the run.
    reused_stages: Annotated[list[Any], operator.add]
    # Part 1.6.A: gate names (from REVIEW_GATES) this run should resume
    # through automatically instead of pausing for a human. Opt-in per run
    # via the Advanced panel; empty means every gate pauses as before.
    # Checked in WorkflowManager._finalize_stream_status, never in a node
    # body (a HITL node's body never runs on resume — see the module
    # docstring's "HITL resume trap").
    auto_approve_gates: list[str]

    # When True, extraction ignores any tracked watermark and re-reads every
    # table in full. Set for a fresh run (a new/uncompleted profile) or the
    # explicit "Full re-merge" toggle — the answer to "I deleted rows from the
    # destination and want them back", which an incremental run cannot give.
    force_full_extraction: bool

    extracted_data: dict[str, Any]
    # Watermarks observed during extraction this run, keyed by
    # "{origin_id}:{table_name}" -> {watermark_column, watermark_kind,
    # last_value, rows_extracted}. Written to merge_engine_extraction_state
    # by update_extraction_state_node after a successful load.
    extraction_watermarks: Annotated[dict[str, Any], operator.or_]
    # Set by validate_data_node (Phase 4.5 of the fix-bugs follow-up plan):
    # {status, severity, entities: {entity: {severity, row_count,
    # sample_size, sampled, checks}}}. Never used to auto-abort the run --
    # surfaced at human_final_approval so a human decides. workflow_manager.py
    # plumbed the read side of these two keys ahead of this node existing
    # (Phase 2.1); this is what actually populates them.
    quality_report: dict[str, Any]
    has_quality_failure: bool

    # ⚠️ IMPORTANT: these must be merge-safe
    entity_clusters: Annotated[dict[str, Any], operator.or_]
    # Phase 7.2: per-pair match_evidence salvaged from a cluster at the
    # moment a human splits it (cluster_edits.apply_cluster_decisions) --
    # the new per-partition clusters carry no evidence of their own, so this
    # is the only record of "a human looked at this specific pair and said
    # these are NOT the same entity". load_data._persist_merge_audit writes
    # these with human_override="split". Same accumulate-across-resumes
    # shape as errors/handled_failures below.
    human_split_pairs: Annotated[list[Any], operator.add]
    # Per-entity resolution audit numbers ({entity: {auto_matched,
    # auto_excluded, llm_scored, total_records, clusters_formed}}) so the
    # HITL gates can show what the system decided without review.
    resolution_stats: Annotated[dict[str, Any], operator.or_]
    unified_entities: Annotated[dict[str, Any], operator.or_]
    field_mappings: Annotated[dict[str, Any], operator.or_]
    unmapped_fields: Annotated[dict[str, Any], operator.or_]

    # Replace semantics (no reducer): the only writer (map_fields) already
    # copies-and-extends the prior list, and the mapping review gate must be
    # able to REMOVE cancelled proposals on resume — an add reducer would
    # duplicate and never delete.
    pending_schema_changes: list[Any]

    human_feedback: Any
    load_approval: Any

    # How many times the user has sent each gate back for rework, keyed by
    # gate node name. Bounded by MAX_GATE_RETRIES so a retry can't loop forever.
    gate_retry_counts: dict[str, int]
    # Free-text reason the user gave when rejecting a gate's output, keyed by
    # the PRODUCING node name. That node injects it into its LLM prompt on the
    # rerun so the second attempt actually differs from the first.
    retry_hints: dict[str, str]

    errors: Annotated[list[Any], operator.add]

    # The single open failure that stopped the pipeline, if any:
    # {node, error, error_class, disposition, context, attempt}. A node that
    # cannot finish its work sets this and returns; the router turns it into
    # a stop at the error gate. Cleared when a human dispositions it, with
    # the original appended to `handled_failures` for the audit trail.
    # Replace semantics (no reducer) — it must be clearable.
    failure: dict[str, Any]
    handled_failures: Annotated[list[Any], operator.add]

    # What preflight checked and what it found, so a run that stopped before
    # reading any data can say exactly which check failed.
    preflight_report: dict[str, Any]

    retry_counts: dict[str, int]
    interrupt_data: Any

    # Per-entity audit numbers built by load_data_node; also written to
    # logs/run_reports/<run_id>.json
    reconciliation_report: dict[str, Any]

    # Per-node wall-clock seconds, merged across every node invocation this
    # run (Phase 0 Sec 0.6 - observable-completion budgets, see
    # docs/verification/BUDGETS.yaml and drive_app.py --assert-budgets).
    # Written centrally by _timed() in _build_builder(), not by each node, so
    # adding a node never requires touching this concern.
    timings: Annotated[dict[str, float], operator.or_]


def build_graph(checkpointer: Any = None) -> Any:
    """Build and compile the LangGraph state graph.

    Parameters
    ----------
    checkpointer : Any, optional
        LangGraph checkpointer. If None, uses MemorySaver.
        For production use, pass SqliteSaver or PostgresSaver.

    Returns
    -------
    Any
        A compiled LangGraph workflow ready for execution.
    """
    builder = _build_builder()

    # Compile with checkpointing (enables resume after interrupts)
    if checkpointer is not None:
        try:
            graph = builder.compile(
                checkpointer=checkpointer,
                interrupt_before=HITL_NODES,
                interrupt_after=[],
            )
            logger.info(
                "Compiled merge workflow graph with %d HITL interrupt points", len(HITL_NODES)
            )
        except Exception:
            logger.warning("Failed to compile with checkpointer, compiling without")
            graph = builder.compile()
    else:
        try:
            from langgraph.checkpoint.memory import MemorySaver

            memory = MemorySaver()
            graph = builder.compile(
                checkpointer=memory,
                interrupt_before=HITL_NODES,
                interrupt_after=[],
            )
            logger.info(
                "Compiled merge workflow graph with MemorySaver and %d HITL interrupt points",
                len(HITL_NODES),
            )
        except ImportError:
            logger.warning("langgraph-checkpoint not installed; compiling without checkpointer")
            graph = builder.compile()

    return graph


def _apply_run_config_overrides(
    llm_config: dict[str, Any],
    extraction_config: dict[str, Any],
    run_config: dict[str, Any] | None,
) -> None:
    """Part 1.6.A: apply this run's UI-set overrides in place.

    ``run_config`` only ever carries keys the caller explicitly set (the API
    layer omits unset fields) — anything absent leaves the file-loaded value
    untouched, so a partial override (e.g. only ``temperature``) never
    silently resets everything else to a default.
    """
    if not run_config:
        return
    for key in (
        "model",
        "fallback_models",
        "temperature",
        "entity_resolution_enabled",
        "field_mapping_enabled",
    ):
        if run_config.get(key) is not None:
            llm_config[key] = run_config[key]
    if run_config.get("batch_size") is not None:
        extraction_config["default_batch_size"] = run_config["batch_size"]


def get_initial_state(
    user_request: str,
    config_path: str | None = None,
    config: Any = None,
    origins: list[dict[str, Any]] | None = None,
    destination: dict[str, Any] | None = None,
    run_id: str = "",
    conflict_policy: dict[str, Any] | None = None,
    matching_rules: dict[str, Any] | None = None,
    run_config: dict[str, Any] | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    """Create the initial state dict for a new workflow.

    Parameters can be provided either via a loaded Config object
    (legacy) or directly from the UI (origins + destination dicts).

    Parameters
    ----------
    user_request : str
        User's natural language prompt.
    config_path : str, optional
        Path to config.yaml (used when config is None).
    config : Config, optional
        Validated application config from config_loader (legacy).
        If provided, origins/destination are extracted from it.
    origins : list[dict], optional
        Origin database configurations from the UI.
        Each dict: {id, type, connection: {host, port, user, password, database}, priority}
    destination : dict, optional
        Destination database configuration from the UI.
        {type, host, port, user, password, database, schema}
    conflict_policy : dict, optional
        Per-field survivorship override (Phase 5, O.3) supplied directly by
        the caller for this run — e.g. from ``WorkflowStart.conflict_policy``.
        Takes effect ahead of a saved profile's pinned ``conflict_policy``,
        since ``apply_profile`` only fills a key the state hasn't already set.
    matching_rules : dict, optional
        Per-entity matching overrides (O.2) keyed by entity type, plus an
        optional ``_default`` entry applied when no per-entity type is known
        yet (Part 1.6.A — the Advanced panel submits this before
        ``target_entities`` exists). Same precedence as ``conflict_policy``.
    run_config : dict, optional
        Part 1.6.A run-by-run overrides from the UI's Advanced panel: any of
        ``model``, ``fallback_models``, ``temperature``,
        ``entity_resolution_enabled``, ``field_mapping_enabled``,
        ``batch_size``, ``auto_approve_gates``. Only keys actually present
        override the file-loaded config; everything else is left alone.
    subject : str, optional
        Subject-first mode (Phase 3.3): when set (UI-originated workflows
        only), ``merge_scope`` is set directly to an active company-scoped
        merge for this name, rather than left inactive for parse_request's
        LLM call to derive from ``user_request``'s free text.

    Returns
    -------
    dict
        Initial state ready for graph.stream().
    """
    # Handle UI-originated workflows (origins + destination dicts)
    if origins is not None and destination is not None:
        llm_config = _load_llm_config(config_path)
        extraction_config: dict[str, Any] = {"default_batch_size": 1000}
        _apply_run_config_overrides(llm_config, extraction_config, run_config)
        scope_config = _load_scope_config(config_path)
        subject_clean = (subject or "").strip()
        merge_scope = (
            {"active": True, "subject": subject_clean, "scope_type": "company"}
            if subject_clean
            else {"active": False, "subject": "", "scope_type": "none"}
        )
        return {
            "user_request": user_request,
            "run_id": run_id,
            "target_entities": [],
            "explicit_target_entities": [],
            "merge_scope": merge_scope,
            "origins": origins,
            "destination": destination,
            # run_id rides along so every LLM call can be attributed to this
            # run in the cost ledger (O.5) — without it every run reports zero.
            "llm_config": {**llm_config, "run_id": run_id},
            "extraction_config": extraction_config,
            "scope_config": scope_config,
            "schemas": {},
            "dependency_tree": {},
            "column_dependencies": [],
            "dependency_analysis_warning": "",
            "extraction_order": [],
            "entity_tables": {},
            "table_selection_notes": "",
            "_entity_tables_fingerprint": "",
            "scope_plan": {},
            "join_graph": None,
            "_join_graph_fingerprint": "",
            "matching_rules": matching_rules or {},
            "conflict_policy": conflict_policy or {},
            "profile_name": "",
            "_profile_cache": {},
            "reused_stages": [],
            "auto_approve_gates": list((run_config or {}).get("auto_approve_gates") or []),
            "force_full_extraction": False,
            "extracted_data": {},
            "extraction_watermarks": {},
            "entity_clusters": {},
            "human_split_pairs": [],
            "resolution_stats": {},
            "unified_entities": {},
            "field_mappings": {},
            "unmapped_fields": {},
            "pending_schema_changes": [],
            "human_feedback": None,
            "load_approval": None,
            "gate_retry_counts": {},
            "retry_hints": {},
            "errors": [],
            "failure": {},
            "handled_failures": [],
            "preflight_report": {},
            "retry_counts": {},
            "interrupt_data": None,
        }

    # Legacy path: extract from Config object
    if config is not None:
        origins_list = []
        for o in config.origins:
            origin_dict = {
                "id": o.id,
                "type": o.type,
                "connection": o.connection.model_dump(),
                "priority": o.priority,
            }
            origins_list.append(origin_dict)

        destination_dict = {
            "type": config.destination.type,
            "host": config.destination.host,
            "port": config.destination.port or 5432,
            "user": config.destination.user or "",
            "password": config.destination.password or "",
            "database": config.destination.database or "",
            "schema": config.destination.schema_name or "public",
        }

        llm_config = {
            "provider": config.llm.provider,
            "model": config.llm.model,
            # Without this, a run started via this legacy Config-object path
            # gets no quota-fallback chain at all - the first model failure
            # is fatal instead of walking config.yaml's llm.fallback_models
            # (Phase 0 Sec 0.8, found stale relative to _load_llm_config,
            # the UI path's equivalent helper, which already includes it).
            "fallback_models": config.llm.fallback_models,
            "api_key": config.llm.api_key,
            "base_url": config.llm.base_url,
            "temperature": config.llm.temperature,
            "structured_output": config.llm.structured_output,
            "entity_resolution_enabled": config.llm.entity_resolution_enabled,
            "field_mapping_enabled": config.llm.field_mapping_enabled,
            "persistent_cache_enabled": config.llm.persistent_cache_enabled,
            "persistent_cache_ttl_days": config.llm.persistent_cache_ttl_days,
            "persistent_cache_max_rows": config.llm.persistent_cache_max_rows,
        }

        return {
            "user_request": user_request,
            "run_id": run_id,
            "target_entities": [],
            "explicit_target_entities": [],
            "merge_scope": {"active": False, "subject": "", "scope_type": "none"},
            "origins": origins_list,
            "destination": destination_dict,
            "llm_config": {**llm_config, "run_id": run_id},
            "extraction_config": {
                "default_batch_size": config.extraction.default_batch_size,
            },
            "scope_config": {
                "max_hops": config.scope.max_hops,
                "join_graph_sample_size": config.scope.join_graph_sample_size,
            },
            "schemas": {},
            "dependency_tree": {},
            "column_dependencies": [],
            "dependency_analysis_warning": "",
            "extraction_order": [],
            "entity_tables": {},
            "table_selection_notes": "",
            "_entity_tables_fingerprint": "",
            "scope_plan": {},
            "join_graph": None,
            "_join_graph_fingerprint": "",
            "matching_rules": matching_rules or {},
            # Was hardcoded to {}, silently ignoring the caller's
            # conflict_policy parameter entirely - the UI path (above)
            # already respects it via `conflict_policy or {}` (Phase 0 Sec
            # 0.8, found stale relative to that path).
            "conflict_policy": conflict_policy or {},
            "auto_approve_gates": list((run_config or {}).get("auto_approve_gates") or []),
            "profile_name": "",
            "_profile_cache": {},
            "reused_stages": [],
            "force_full_extraction": False,
            "extracted_data": {},
            "extraction_watermarks": {},
            "entity_clusters": {},
            "human_split_pairs": [],
            "resolution_stats": {},
            "unified_entities": {},
            "field_mappings": {},
            "unmapped_fields": {},
            "pending_schema_changes": [],
            "human_feedback": None,
            "load_approval": None,
            "gate_retry_counts": {},
            "retry_hints": {},
            "errors": [],
            "failure": {},
            "handled_failures": [],
            "preflight_report": {},
            "retry_counts": {},
            "interrupt_data": None,
        }

    raise ValueError("Must provide either (config) or (origins and destination)")


def _load_llm_config(config_path: str | None) -> dict[str, Any]:
    """Load LLM configuration from config file.

    Parameters
    ----------
    config_path : str | None
        Path to config.yaml. Uses default if None.

    Returns
    -------
    dict
        LLM configuration dict.
    """
    try:
        from src.utils.config_loader import load_config

        cfg = load_config(config_path)
        return {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "fallback_models": cfg.llm.fallback_models,
            "api_key": cfg.llm.api_key,
            "base_url": cfg.llm.base_url,
            "temperature": cfg.llm.temperature,
            "structured_output": cfg.llm.structured_output,
            "entity_resolution_enabled": cfg.llm.entity_resolution_enabled,
            "field_mapping_enabled": cfg.llm.field_mapping_enabled,
            "persistent_cache_enabled": cfg.llm.persistent_cache_enabled,
            "persistent_cache_ttl_days": cfg.llm.persistent_cache_ttl_days,
            "persistent_cache_max_rows": cfg.llm.persistent_cache_max_rows,
        }
    except Exception:
        # Return placeholder -- LLM will be configured via env vars
        return {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "",
            "base_url": None,
            "temperature": 0.0,
            "structured_output": True,
            "entity_resolution_enabled": True,
            "field_mapping_enabled": True,
            "persistent_cache_enabled": True,
            "persistent_cache_ttl_days": 30,
            "persistent_cache_max_rows": 50_000,
        }


def _load_scope_config(config_path: str | None) -> dict[str, Any]:
    """Load scope-planning defaults (join-graph BFS max_hops etc.) from
    config file. Falls back to join_graph.py's own module-level defaults
    (_DEFAULT_MAX_HOPS / the sample-size default already baked into
    build_join_graph) so a config-load failure degrades to the same
    behaviour the tool already had before this was made configurable.
    """
    try:
        from src.utils.config_loader import load_config

        cfg = load_config(config_path)
        return {
            "max_hops": cfg.scope.max_hops,
            "join_graph_sample_size": cfg.scope.join_graph_sample_size,
            "engine": cfg.scope.engine,
        }
    except Exception:
        return {"max_hops": 6, "join_graph_sample_size": 500, "engine": "v3_discovery"}


# ── Server Mode Export ──────────────────────────────────────────────
# LangGraph CLI server expects a compiled `graph` at module level.
# The server handles checkpointer/persistence automatically — it does NOT
# allow custom checkpointer in the compiled graph. We compile WITHOUT
# a checkpointer for the CLI server path. The server adds its own
# persistence layer at runtime.


def _get_server_graph():
    """Create a compiled graph for LangGraph CLI server mode.

    The LangGraph API handles persistence automatically. Custom
    checkpointer (MemorySaver/SqliteSaver/etc.) are NOT allowed in
    server mode — the API overrides them with its own persistence.
    """
    builder = _build_builder()
    graph = builder.compile(
        interrupt_before=HITL_NODES,
        interrupt_after=[],
    )
    logger.info("Server graph compiled (no custom checkpointer — API handles persistence)")
    return graph


# Lazy module-level export for LangGraph CLI (Phase 0 Sec 0.8): compiling a
# StateGraph is not free, and `graph = _get_server_graph()` at module scope
# used to pay that cost on EVERY import of this module, including from
# WorkflowManager's in-process mode, which never touches this export at all
# and calls build_graph() instead. PEP 562 module __getattr__ defers the
# compile until something actually accesses `graph` (the LangGraph CLI
# server's expected entry point, `langgraph.json`'s "./graph.py:graph"),
# and caches the result so a second access doesn't recompile.
_server_graph_cache: Any = None


def __getattr__(name: str) -> Any:
    global _server_graph_cache
    if name == "graph":
        if _server_graph_cache is None:
            _server_graph_cache = _get_server_graph()
        return _server_graph_cache
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
