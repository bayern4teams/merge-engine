"""
Human-in-the-loop interrupt nodes for merge-engine.

These nodes pause the graph and collect user feedback via the Gradio UI.
They use LangGraph's interrupt mechanism.
"""

from __future__ import annotations

import logging
from typing import Any

from src.orchestrator.spool import (
    clusters_for,
    entity_records_by_id,
    store_clusters,
    unified_for,
)
from src.orchestrator.utils.cluster_edits import apply_cluster_decisions

# Imported lazily inside the function that needs it — graph.py imports this
# module, so a module-level import would be circular.

logger = logging.getLogger("merge-engine.orchestrator.human_nodes")


def _build_origin_schemas(schemas: dict[str, Any]) -> dict[str, Any]:
    """Restructure per-origin discovery results into {origin: {table: [cols]}}.

    The ``schemas`` state key holds ``{origin_id: {"tables": [{"name", "columns":
    [{"name", ...}]}]}}`` from the ``discover_schemas`` node. The dependency-gate
    UI needs a table->columns map per origin to power cascading dropdowns for the
    Add-Custom-Dependency-Link form, so we flatten it here.
    """
    out: dict[str, Any] = {}
    for origin_id, schema_data in (schemas or {}).items():
        tables: dict[str, Any] = {}
        for table in (schema_data or {}).get("tables", []) or []:
            tname = (table or {}).get("name", "")
            if not tname:
                continue
            tables[tname] = [c.get("name") for c in (table.get("columns") or []) if c.get("name")]
        if tables:
            out[origin_id] = tables
    return out


def _build_deps_interrupt(state: dict[str, Any]) -> dict[str, Any]:
    """The payload the dependency gate shows. Shared with the workflow
    manager, since interrupt_before means this node's body has not run at
    the pause — see _build_scope_interrupt / build_error_interrupt for the
    same pattern, and Phase 2.1 of the fix-bugs follow-up plan for why this
    one had to be split out: the workflow manager's own copy omitted
    origin_schemas and column_dependencies entirely, so the "Add Custom
    Dependency Link" cascading dropdowns were always empty and the button
    permanently disabled at the live gate."""
    dependency_tree = state.get("dependency_tree", {})
    extraction_order = state.get("extraction_order", [])
    column_dependencies = state.get("column_dependencies", [])

    interrupt_data: dict[str, Any] = {
        "node": "human_review_deps",
        "title": "Review Dependency Tree",
        "data": {
            "dependency_tree": dependency_tree,
            "extraction_order": extraction_order,
            "column_dependencies": column_dependencies,
            "entity_tables": state.get("entity_tables", {}),
            "origin_schemas": _build_origin_schemas(state.get("schemas", {})),
            "table_selection_notes": state.get("table_selection_notes", ""),
        },
    }
    warning = state.get("dependency_analysis_warning", "")
    if warning:
        interrupt_data["data"]["warning"] = warning
    return interrupt_data


async def human_review_deps(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Show dependency tree for human review.

    The graph pauses here. The UI renders the tree and collects
    user modifications. On resume, human_feedback contains the
    user's approved/modified dependency tree.
    """
    logger.debug("ENTER human_review_deps")
    logger.debug("State keys: %s", list(state.keys()))
    logger.debug("dependency_tree: %s", state.get("dependency_tree", {}))
    logger.debug("extraction_order: %s", state.get("extraction_order", []))

    interrupt_data = _build_deps_interrupt(state)

    # Check if user has already provided feedback (on resume)
    human_feedback = state.get("human_feedback")
    if human_feedback and "dependency_tree" in human_feedback:
        logger.info("Human approved/modified dependency tree")
        logger.debug("Feedback keys: %s", list(human_feedback.keys()))
        res: dict[str, Any] = {
            "dependency_tree": human_feedback["dependency_tree"],
        }
        if "column_dependencies" in human_feedback:
            res["column_dependencies"] = human_feedback["column_dependencies"]
        return res

    # Interrupt the graph
    logger.debug("Interrupting at human_review_deps checkpoint")
    logger.debug("EXIT human_review_deps (interrupt)")
    return {"interrupt_data": interrupt_data}


async def human_review_scope(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: confirm which subject the scoped merge targets, and how.

    Only reached for a scoped merge ("all information about Nova"). Shows the
    subject, the candidate rows the match found (so a human can tell Nova Labs
    from Novatech), and the per-entity join plan that will pull in that
    subject's people and products. On resume the user may replace the plan
    (edit-then-continue) — e.g. after narrowing to one specific company.
    """
    logger.debug("ENTER human_review_scope")
    scope_plan = state.get("scope_plan", {}) or {}

    interrupt_data = _build_scope_interrupt(scope_plan)

    human_feedback = state.get("human_feedback")
    if human_feedback and "scope_plan" in human_feedback:
        logger.info("Human confirmed/edited the merge scope")
        return {
            "scope_plan": human_feedback["scope_plan"],
        }

    logger.debug("Interrupting at human_review_scope checkpoint")
    logger.debug("EXIT human_review_scope (interrupt)")
    return {"interrupt_data": interrupt_data}


def _build_scope_interrupt(scope_plan: dict[str, Any]) -> dict[str, Any]:
    """The payload the scope gate shows. Shared with the workflow manager,
    since interrupt_before means this node's body has not run at the pause."""
    entities = scope_plan.get("entities", {}) or {}
    all_origins = sorted(
        list(
            {
                e.get("origin_id")
                for entries in entities.values()
                for e in entries
                if e.get("origin_id")
            }
        )
    )
    plan_rows: list[dict[str, Any]] = []
    for entity, entries in entities.items():
        covered = {e.get("origin_id") for e in entries if e.get("origin_id")}
        for e in entries:
            plan_rows.append(
                {
                    "entity_type": entity,
                    "origin_id": e.get("origin_id"),
                    "table": e.get("table"),
                    "mode": e.get("mode"),
                    "columns": e.get("columns", []),
                    "via_table": e.get("via_table"),
                    "via_from_column": e.get("via_from_column"),
                    "via_to_column": e.get("via_to_column"),
                    "reason": e.get("reason", ""),
                }
            )
        for oid in all_origins:
            if oid not in covered:
                plan_rows.append(
                    {
                        "entity_type": entity,
                        "origin_id": oid,
                        "table": "—",
                        "mode": "not_found",
                        "columns": [],
                        "reason": f"No matching or linked {entity} table found in {oid}",
                    }
                )
    return {
        "node": "human_review_scope",
        "title": "Confirm merge scope",
        "data": {
            "subject": scope_plan.get("subject", ""),
            "subject_entity": scope_plan.get("subject_entity", "company"),
            "candidates": scope_plan.get("candidates", []),
            "plan": plan_rows,
        },
    }


def _auto_match_stats(state: dict[str, Any]) -> dict[str, int]:
    """Aggregate per-entity resolution stats into pipeline-wide counts.

    Shared with the workflow manager (Phase 2.1) — the two independent
    copies were identical in logic but kept as separate functions, which is
    exactly the setup that let the match gate's payload drift in the first
    place (see _build_matches_interrupt below).
    """
    stats = state.get("resolution_stats") or {}
    return {
        "auto_matched_pairs": sum(s.get("auto_matched", 0) for s in stats.values()),
        "auto_excluded_pairs": sum(s.get("auto_excluded", 0) for s in stats.values()),
        "llm_scored_pairs": sum(s.get("llm_scored", 0) for s in stats.values()),
    }


def _build_matches_interrupt(state: dict[str, Any]) -> dict[str, Any]:
    """The payload the match gate shows. Shared with the workflow manager
    (Phase 2.1 of the fix-bugs follow-up plan) — this node's own body used
    to build only {low_confidence_clusters, total_clustering_decisions},
    while the workflow manager's independent copy additionally computed
    per-record field previews, auto_match_stats, and pending_merge_candidates
    (uncertain-band pairs that were scored but left unmerged, which would
    otherwise be invisible to the reviewer). Since interrupt_before means
    this function's caller's body never runs before the pause, the live gate
    only ever showed the richer workflow-manager version — but a caller that
    resumes without ever hitting the manager's synth path (a direct
    resume_workflow call, as some tests do) would have seen the poorer one.
    One function now, so there is only one payload to drift.
    """
    entity_clusters = state.get("entity_clusters", {})
    low_conf: list[dict[str, Any]] = []
    for entity_type in entity_clusters:
        clusters = clusters_for(state, entity_type)
        # Fetch only the records the gate actually shows, not every
        # extracted row — see the identical comment on the workflow
        # manager's history of this function for why that matters at scale.
        needed = [
            rid
            for cluster in clusters
            if cluster.get("required_human_review", False)
            for rid in cluster.get("record_ids", [])
        ]
        records_by_id = entity_records_by_id(state, entity_type, needed)
        for cluster in clusters:
            if cluster.get("required_human_review", False):
                record_ids = cluster.get("record_ids", [])
                # Surface the actual field values (not just the opaque
                # internal record key) so a human reviewer can tell WHY two
                # records look alike instead of guessing from an id like
                # "origin_1:companies:42".
                records_preview = []
                for rid in record_ids:
                    rec = records_by_id.get(rid, {})
                    fields = {
                        k: v
                        for k, v in rec.items()
                        if not k.startswith("_") and v not in (None, "")
                    }
                    records_preview.append({"record_id": rid, "fields": fields})
                low_conf.append(
                    {
                        "entity_type": entity_type,
                        "cluster_id": cluster.get("cluster_id", ""),
                        "record_ids": record_ids,
                        "records": records_preview,
                        "confidence": cluster.get("match_confidence", 0),
                    }
                )

    # Pairs scored in the uncertain band and therefore NOT merged. They form
    # no cluster, so they would otherwise be invisible — the reviewer would
    # never learn the system had hesitated.
    pending: list[dict[str, Any]] = []
    for entity_type, stats in (state.get("resolution_stats") or {}).items():
        candidates = stats.get("pending_merge_candidates") or []
        records_by_id = entity_records_by_id(
            state, entity_type, [rid for c in candidates for rid in c.get("record_ids", [])]
        )
        for candidate in candidates:
            preview = []
            for rid in candidate.get("record_ids", []):
                rec = records_by_id.get(rid, {})
                preview.append(
                    {
                        "record_id": rid,
                        "fields": {
                            k: v
                            for k, v in rec.items()
                            if not k.startswith("_") and v not in (None, "")
                        },
                    }
                )
            pending.append(
                {
                    "entity_type": entity_type,
                    "record_ids": candidate.get("record_ids", []),
                    "records": preview,
                    "confidence": candidate.get("confidence", 0),
                }
            )

    return {
        "node": "human_review_matches",
        "title": "Review Entity Matches",
        "data": {
            "low_confidence_clusters": low_conf,
            "total_clustering_decisions": len(low_conf),
            "auto_match_stats": _auto_match_stats(state),
            "pending_merge_candidates": pending,
            "pending_merge_note": (
                f"{len(pending)} pair(s) looked similar but not similar enough to merge "
                "automatically. They were left as separate records — approve to keep them separate."
            )
            if pending
            else "",
        },
    }


async def human_review_matches(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Show low-confidence entity matches for review.

    The UI displays ambiguous clusters side-by-side. User confirms,
    rejects, or splits clusters.
    """
    logger.debug("ENTER human_review_matches")
    logger.debug("State keys: %s", list(state.keys()))

    interrupt_data = _build_matches_interrupt(state)
    logger.debug(
        "Found %d low-confidence clusters", len(interrupt_data["data"]["low_confidence_clusters"])
    )

    human_feedback = state.get("human_feedback")
    if human_feedback and "cluster_decisions" in human_feedback:
        logger.info("Human reviewed entity matches")
        logger.debug("Feedback keys: %s", list(human_feedback.keys()))
        # Clusters live in the spool, so materialise them, apply the
        # user's splits, and write the result back as a handle.
        run_id = str(state.get("run_id") or "unknown")
        materialised = {e: clusters_for(state, e) for e in state.get("entity_clusters", {})}
        rebuilt, stats = apply_cluster_decisions(materialised, human_feedback["cluster_decisions"])
        rebuilt = {
            e: store_clusters(
                run_id,
                e,
                cs,
                needs_review=sum(1 for c in cs if c.get("required_human_review")),
                multi_record=sum(1 for c in cs if len(c.get("record_ids", [])) > 1),
            )
            for e, cs in rebuilt.items()
        }
        logger.info("Cluster decisions applied: %s", stats)
        return {
            "entity_clusters": rebuilt,
        }

    logger.debug("Interrupting at human_review_matches checkpoint")
    logger.debug("EXIT human_review_matches (interrupt)")
    return {"interrupt_data": interrupt_data}


def _build_field_provenance(state: dict[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
    """Map each source field to its (origin_id, table) for mapping-row display.

    ``map_fields_impl`` attaches ``origin_id``/``table``/``source_field`` to a
    mapping row only when the field reached mapping via the discovered-schema
    path. When fields come straight off unified records (no per-field source
    tag), those keys are empty, so the UI's Origin / Source Table columns render
    "—". Backfill here from the discovered schemas restricted to the entity's
    chosen tables, keyed by column name. Returns
    ``{entity_type: {column_name: {"origin_id", "table"}}}``.
    """
    schemas: dict[str, Any] = state.get("schemas", {}) or {}
    entity_tables: dict[str, Any] = state.get("entity_tables", {}) or {}
    entity_names = set(entity_tables) | set(state.get("field_mappings", {}) or {})
    provenance: dict[str, dict[str, dict[str, str]]] = {}
    for entity in sorted(entity_names):
        chosen = entity_tables.get(entity, [])
        chosen_by_origin: dict[str, set[str]] = {}
        for et in chosen or []:
            oid = str(et.get("origin_id", "")).strip()
            tbl = str(et.get("table", "")).strip()
            if oid and tbl:
                chosen_by_origin.setdefault(oid, set()).add(tbl.lower())
        field_map: dict[str, dict[str, str]] = {}
        for origin_id, schema_data in schemas.items():
            for table in (schema_data or {}).get("tables", []) or []:
                tname = (table or {}).get("name", "")
                full = (table or {}).get("full_name") or tname
                allowed = chosen_by_origin.get(origin_id)
                if (
                    allowed is not None
                    and tname.lower() not in allowed
                    and str(full).lower() not in allowed
                ):
                    continue
                for col in (table.get("columns") or []) or []:
                    cname = (col or {}).get("name", "")
                    if not cname or cname.startswith("_"):
                        continue
                    field_map.setdefault(cname, {"origin_id": origin_id, "table": full or tname})
        provenance[entity] = field_map
    return provenance


def _backfill_mapping_provenance(
    mapping_rows: list[dict[str, Any]],
    provenance: dict[str, dict[str, dict[str, str]]],
) -> None:
    """Fill empty origin_id/table on mapping rows from discovered provenance."""
    for row in mapping_rows:
        if row.get("origin_id") and row.get("table"):
            continue
        src = (provenance.get(row.get("entity_type", "")) or {}).get(row.get("source_field", ""))
        if src:
            if not row.get("origin_id"):
                row["origin_id"] = src["origin_id"]
            if not row.get("table"):
                row["table"] = src["table"]


def _build_mapping_rows(state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the complete per-field mapping table, with provenance backfilled.

    Shared with the workflow manager (Phase 2.1 of the fix-bugs follow-up
    plan). The workflow manager used to keep an independent copy of this
    logic that never set ``origin_id``/``table``/``is_coalesced`` at all, so
    the Origin / Source Table columns rendered "—" at the live gate
    regardless of the backfill logic below — that logic ran, but only on
    this function's body, which ``interrupt_before`` means never executes
    before the pause. Emits one row for every field in field_mappings
    (auto-mapped fields included — the reviewer must see everything, not
    just the subset needing attention) plus one skip-row per unmapped field,
    with the richer ``unmapped_fields`` list (confidence/suggested_column/
    reason) that a plain filter over ``mapping_rows`` alone cannot
    reconstruct, since a skip-row never carries those fields itself.
    """
    field_mappings = state.get("field_mappings", {})
    unmapped_fields_by_entity: dict[str, list[dict[str, Any]]] = state.get("unmapped_fields", {})

    mapping_rows: list[dict[str, Any]] = []
    for entity_type, mappings in field_mappings.items():
        for source_field, info in mappings.items():
            dest_col = (
                info.get("dest_column", source_field) if isinstance(info, dict) else str(info)
            )
            conf = info.get("confidence", 1.0) if isinstance(info, dict) else 1.0
            oid = info.get("origin_id", "") if isinstance(info, dict) else ""
            tbl = info.get("table", "") if isinstance(info, dict) else ""
            sfield = (
                info.get("source_field", source_field) if isinstance(info, dict) else source_field
            )
            coalesced = info.get("is_coalesced", False) if isinstance(info, dict) else False

            mapping_rows.append(
                {
                    "entity_type": entity_type,
                    "source_field": sfield,
                    "action": "map",
                    "dest_column": dest_col,
                    "confidence": conf,
                    "origin_id": oid,
                    "table": tbl,
                    "is_coalesced": coalesced,
                }
            )

    unmapped: list[dict[str, Any]] = []
    for entity_type, fields in unmapped_fields_by_entity.items():
        for field_info in fields:
            sfield = field_info.get("source_field") or field_info.get("field", "")
            oid = field_info.get("origin_id", "")
            tbl = field_info.get("table", "")
            unmapped.append(
                {
                    "entity_type": entity_type,
                    "source_field": sfield,
                    "confidence": field_info.get("confidence"),
                    "suggested_column": field_info.get("suggested_column"),
                    "reason": field_info.get("reason"),
                }
            )
            mapping_rows.append(
                {
                    "entity_type": entity_type,
                    "source_field": sfield,
                    "action": "skip",
                    # Not actually used (action is skip), but shown as a
                    # preview of what the mapper considered before giving up
                    # on it — useful context, not a live value.
                    "dest_column": field_info.get("suggested_column") or "",
                    "confidence": field_info.get("confidence", 0.0),
                    "origin_id": oid,
                    "table": tbl,
                    "is_coalesced": False,
                    # Phase 2.3: carried onto the row itself (not just the
                    # separate unmapped_fields list) so the gate's Reason
                    # column can explain a skip without a second lookup.
                    "reason": field_info.get("reason") or "",
                }
            )

    # Bug #4: mapping rows built from unified records carry no origin_id/table
    # (only the discovered-schema upfront path tags them) — backfill both the
    # mapped rows and the skip rows from the origin schemas so the UI's Origin
    # / Source Table columns stop showing "—".
    _backfill_mapping_provenance(mapping_rows, _build_field_provenance(state))

    return mapping_rows, unmapped


async def human_review_mappings(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Show field mappings and schema changes for review."""
    logger.debug("ENTER human_review_mappings")
    logger.debug("State keys: %s", list(state.keys()))
    pending_schema_changes = state.get("pending_schema_changes", [])

    mapping_rows, unmapped = _build_mapping_rows(state)
    logger.debug("Mapping rows: %d, Unmapped fields: %d", len(mapping_rows), len(unmapped))

    interrupt_data = {
        "node": "human_review_mappings",
        "title": "Review Field Mappings",
        "data": {
            "mapping_rows": mapping_rows,
            "unmapped_fields": unmapped,
            "pending_schema_changes": pending_schema_changes,
        },
    }

    human_feedback = state.get("human_feedback")
    if human_feedback and ("mapping_decisions" in human_feedback or "approved" in human_feedback):
        logger.info("Human reviewed field mappings")
        return {
            "pending_schema_changes": human_feedback.get("schema_changes", pending_schema_changes),
        }

    logger.debug("Interrupting at human_review_mappings checkpoint")
    logger.debug("EXIT human_review_mappings (interrupt)")
    return {"interrupt_data": interrupt_data}


async def human_final_approval(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Final summary and approval gate.

    Shows a summary of all work done and awaits explicit user approval
    before proceeding with data loading.
    """
    logger.debug("ENTER human_final_approval")
    logger.debug("State keys: %s", list(state.keys()))
    unified_entities = state.get("unified_entities", {})
    pending_schema_changes = state.get("pending_schema_changes", [])
    errors = state.get("errors", [])

    # Build summary
    summary: dict[str, Any] = {
        "total_entities": len(unified_entities),
        "entity_counts": {},
        "total_conflicts": 0,
        "schema_changes": len(pending_schema_changes),
        "errors": len(errors),
    }

    for entity_type in unified_entities:
        records = unified_for(state, entity_type)
        summary["entity_counts"][entity_type] = len(records)
        for rec in records:
            conflicts = rec.get("_conflicts", rec.get("_conflicts", []))
            if isinstance(conflicts, list):
                summary["total_conflicts"] += len(conflicts)

    logger.debug("Summary: %s", summary)

    interrupt_data = {
        "node": "human_final_approval",
        "title": "Final Approval",
        "data": {
            "summary": summary,
            "pending_schema_changes": pending_schema_changes,
        },
    }

    # Only honor feedback addressed to this node — feedback left over from
    # an earlier checkpoint (e.g. approving mappings) must not auto-approve
    # the final load. Feedback without a "node" key is accepted for
    # backward compatibility.
    human_feedback = state.get("human_feedback")
    feedback_node = (human_feedback or {}).get("node")
    if (
        human_feedback
        and human_feedback.get("approved")
        and feedback_node in (None, "human_final_approval")
    ):
        logger.info("Human approved final load")
        logger.debug("EXIT human_final_approval (approved)")
        return {"load_approval": True}

    logger.debug("Interrupting at human_final_approval checkpoint")
    logger.debug("EXIT human_final_approval (interrupt)")
    return {"interrupt_data": interrupt_data}


def build_error_interrupt(state: dict[str, Any]) -> dict[str, Any]:
    """The payload the error gate shows: what failed, where, and how often.

    Shared with ``WorkflowManager._build_interrupt_data`` because with
    ``interrupt_before`` the node body has not run when the graph pauses.
    """
    from src.orchestrator.graph import NODE_SUCCESSOR

    failure = state.get("failure") or {}
    failed_node = failure.get("node") or "unknown"
    attempts = (state.get("retry_counts") or {}).get(failed_node, 0)
    successor = NODE_SUCCESSOR.get(failed_node, "")

    return {
        "node": "handle_error",
        "title": f"Problem in step '{failed_node}'",
        "data": {
            "failed_node": failed_node,
            "error": failure.get("error", ""),
            "error_class": failure.get("error_class", ""),
            "disposition": failure.get("disposition", "escalate"),
            "context": failure.get("context", {}),
            "attempts": attempts,
            # Spelled out because "skip" sounds harmless and is not: it
            # continues the merge with this step's output missing.
            "options": [
                {
                    "decision": "retry",
                    "label": f"Retry '{failed_node}'",
                    "detail": "Run the failed step again from the same state.",
                },
                {
                    "decision": "skip",
                    "label": "Skip and continue",
                    "detail": (
                        f"Continue at '{successor}' without this step's "
                        "output. The merge will be knowingly incomplete."
                    )
                    if successor
                    else "Continue without this step's output.",
                },
                {
                    "decision": "abort",
                    "label": "Abort the run",
                    "detail": "Stop here. Nothing further is written.",
                },
            ],
            "recent_errors": [str(e) for e in (state.get("errors") or [])[-5:]],
        },
    }


async def handle_error(state: dict[str, Any]) -> dict[str, Any]:
    """Error escalation gate: stop, show a human what broke, take a decision.

    In the normal resume path this body does NOT run: ``resume_workflow``
    calls ``update_state(as_node="handle_error")``, which tells LangGraph the
    node has already executed, so the decision is recorded there instead.
    This is the fallback for any path that reaches the node directly, and it
    is written to be idempotent with that one — an already-cleared
    ``failure`` makes it a no-op rather than a second audit entry.
    """
    logger.debug("ENTER handle_error")
    failure = state.get("failure") or {}
    if not failure:
        logger.debug("EXIT handle_error (nothing to handle)")
        return {}

    feedback = state.get("human_feedback") or {}
    decision = str(feedback.get("decision") or "abort").strip().lower()
    failed_node = failure.get("node") or "unknown"

    retry_counts = dict(state.get("retry_counts") or {})
    if decision == "retry":
        retry_counts[failed_node] = retry_counts.get(failed_node, 0) + 1

    logger.warning(
        "Error gate: user chose '%s' for failure in '%s' (%s)",
        decision,
        failed_node,
        str(failure.get("error", ""))[:200],
    )
    logger.debug("EXIT handle_error (decision=%s)", decision)

    return {
        # Cleared so the failing node's router lets it through on a retry,
        # and so a skipped failure does not re-trigger the gate.
        "failure": {},
        "handled_failures": [{**failure, "decision": decision}],
        "retry_counts": retry_counts,
    }
