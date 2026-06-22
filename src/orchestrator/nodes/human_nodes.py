"""
Human-in-the-loop interrupt nodes for merge-engine.

These nodes pause the graph and collect user feedback via the Gradio UI.
They use LangGraph's interrupt mechanism.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("merge-engine.orchestrator.human_nodes")


async def human_review_deps(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Show dependency tree for human review.

    The graph pauses here. The UI renders the tree and collects
    user modifications. On resume, human_feedback contains the
    user's approved/modified dependency tree.
    """
    dependency_tree = state.get("dependency_tree", {})
    extraction_order = state.get("extraction_order", [])

    interrupt_data = {
        "node": "human_review_deps",
        "title": "Review Dependency Tree",
        "data": {
            "dependency_tree": dependency_tree,
            "extraction_order": extraction_order,
        },
    }

    # Check if user has already provided feedback (on resume)
    human_feedback = state.get("human_feedback")
    if human_feedback and "dependency_tree" in human_feedback:
        logger.info("Human approved/modified dependency tree")
        return {
            "dependency_tree": human_feedback["dependency_tree"],
            "errors": state.get("errors", []),
        }

    # Interrupt the graph
    return {"interrupt_data": interrupt_data}


async def human_review_matches(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Show low-confidence entity matches for review.

    The UI displays ambiguous clusters side-by-side. User confirms,
    rejects, or splits clusters.
    """
    entity_clusters = state.get("entity_clusters", {})
    low_conf_clusters: list[dict[str, Any]] = []

    for entity_type, clusters in entity_clusters.items():
        for cluster in clusters:
            if cluster.get("required_human_review", False):
                low_conf_clusters.append({
                    "entity_type": entity_type,
                    "cluster_id": cluster.get("cluster_id", ""),
                    "record_ids": cluster.get("record_ids", []),
                    "confidence": cluster.get("match_confidence", 0),
                })

    interrupt_data = {
        "node": "human_review_matches",
        "title": "Review Entity Matches",
        "data": {
            "low_confidence_clusters": low_conf_clusters,
            "total_clustering_decisions": len(low_conf_clusters),
        },
    }

    human_feedback = state.get("human_feedback")
    if human_feedback and "cluster_decisions" in human_feedback:
        logger.info("Human reviewed entity matches")
        # Process decisions: keep, split, or merge clusters
        return {"errors": state.get("errors", [])}

    return {"interrupt_data": interrupt_data}


async def human_review_mappings(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Show unmapped fields and schema changes for review.

    The UI displays fields that couldn't be automatically mapped,
    along with proposed schema changes.
    """
    field_mappings = state.get("field_mappings", {})
    pending_schema_changes = state.get("pending_schema_changes", [])

    # Collect unmapped fields
    unmapped: list[dict[str, Any]] = []
    for entity_type, mapping in field_mappings.items():
        for src_field, info in mapping.items():
            if info.get("confidence", 1.0) == 0 and not info.get("requires_schema_change"):
                unmapped.append({
                    "entity_type": entity_type,
                    "source_field": src_field,
                })

    interrupt_data = {
        "node": "human_review_mappings",
        "title": "Review Field Mappings",
        "data": {
            "unmapped_fields": unmapped,
            "pending_schema_changes": pending_schema_changes,
        },
    }

    human_feedback = state.get("human_feedback")
    if human_feedback and "mapping_decisions" in human_feedback:
        logger.info("Human reviewed field mappings")
        return {
            "pending_schema_changes": human_feedback.get("schema_changes", pending_schema_changes),
            "errors": state.get("errors", []),
        }

    return {"interrupt_data": interrupt_data}


async def human_final_approval(state: dict[str, Any]) -> dict[str, Any]:
    """Interrupt: Final summary and approval gate.

    Shows a summary of all work done and awaits explicit user approval
    before proceeding with data loading.
    """
    unified_entities = state.get("unified_entities", {})
    field_mappings = state.get("field_mappings", {})
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

    for entity_type, records in unified_entities.items():
        summary["entity_counts"][entity_type] = len(records)
        for rec in records:
            conflicts = rec.get("_conflicts", rec.get("_conflicts", []))
            if isinstance(conflicts, list):
                summary["total_conflicts"] += len(conflicts)

    interrupt_data = {
        "node": "human_final_approval",
        "title": "Final Approval",
        "data": {
            "summary": summary,
            "pending_schema_changes": pending_schema_changes,
        },
    }

    human_feedback = state.get("human_feedback")
    if human_feedback and human_feedback.get("approved"):
        logger.info("Human approved final load")
        return {
            "load_approval": True,
            "errors": errors,
        }

    return {"interrupt_data": interrupt_data}


async def handle_error(state: dict[str, Any]) -> dict[str, Any]:
    """Error handler node: Inspect errors and decide retry vs escalate.

    Parameters
    ----------
    state : dict
        MergeState with current error information.

    Returns
    -------
    dict
        Update dict. If max retries exceeded, triggers interrupt.
    """
    errors = state.get("errors", [])
    retry_counts = state.get("retry_counts", {})
    current_node = state.get("current_step", "unknown")

    if not errors:
        return {"errors": errors}

    last_error = errors[-1] if errors else ""
    current_count = retry_counts.get(current_node, 0) + 1
    max_attempts = 3  # Configurable

    retry_counts[current_node] = current_count

    if current_count >= max_attempts:
        # Escalate to human
        logger.error("Max retries exceeded for node '%s': %s", current_node, last_error)
        return {
            "errors": errors,
            "retry_counts": retry_counts,
            "interrupt_data": {
                "node": "handle_error",
                "title": "Error: Max Retries Exceeded",
                "data": {
                    "error": last_error,
                    "node_name": current_node,
                    "retry_count": current_count,
                },
            },
        }

    logger.warning("Retry %d/%d for node '%s': %s", current_count, max_attempts, current_node, last_error)
    return {"errors": errors, "retry_counts": retry_counts}