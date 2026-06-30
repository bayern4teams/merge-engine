"""
resolve_entities node: Clusters matching records across origins.

Calls resolve_entities_impl for each extracted entity type to produce
clusters of matching records.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.resolve_entities import resolve_entities_impl
from src.orchestrator.utils.retry import TransientError

logger = logging.getLogger("merge-engine.orchestrator.resolve_entities")


async def resolve_entities(state: dict[str, Any]) -> dict[str, Any]:
    """Resolve entities across all extracted entity types.

    Parameters
    ----------
    state : dict
        MergeState with ``extracted_data`` populated.

    Returns
    -------
    dict
        Update dict with ``entity_clusters`` and ``errors`` keys.
    """
    logger.debug("ENTER resolve_entities")
    logger.debug("State keys: %s", list(state.keys()))
    extracted_data: dict[str, list[dict]] = state.get("extracted_data", {})
    target_entities: list[str] = state.get("target_entities", [])
    errors: list[str] = list(state.get("errors", []))
    entity_clusters: dict[str, list[dict]] = dict(state.get("entity_clusters", {}))

    logger.debug("target_entities: %s", target_entities)
    logger.debug("extracted_data keys: %s", list(extracted_data.keys()))
    for et, rows in extracted_data.items():
        logger.debug("  Entity '%s': %d records", et, len(rows))

    for entity_type in target_entities:
        records = extracted_data.get(entity_type, [])
        logger.debug("Processing entity '%s': %d records", entity_type, len(records))
        if not records:
            logger.info("No records to resolve for entity '%s', skipping.", entity_type)
            entity_clusters[entity_type] = []
            continue

        logger.info("Resolving entities for '%s': %d records", entity_type, len(records))

        try:
            logger.debug("Calling resolve_entities_impl for entity '%s'", entity_type)
            result = await resolve_entities_impl(
                entity_type=entity_type,
                records=records,
            )
            logger.debug("resolve_entities_impl result keys: %s", list(result.keys()))

            clusters = result.get("clusters", [])
            entity_clusters[entity_type] = clusters

            # Stats
            total_records = result.get("total_records", 0)
            auto_matched = result.get("auto_matched", 0)
            llm_scored = result.get("llm_scored", 0)
            multi_record_clusters = [c for c in clusters if len(c.get("record_ids", [])) > 1]
            low_conf_clusters = [c for c in clusters if c.get("required_human_review", False)]

            logger.debug(
                "Resolution stats - total=%d, auto=%d, llm=%d, multi=%d, low_conf=%d",
                total_records, auto_matched, llm_scored, len(multi_record_clusters), len(low_conf_clusters),
            )
            for lc in low_conf_clusters:
                logger.debug(
                    "Low-confidence cluster: id=%s, records=%s, score=%.2f",
                    lc.get("cluster_id"), lc.get("record_ids"), lc.get("confidence", 0),
                )

            logger.info(
                "Entity resolution for '%s': %d records → %d clusters "
                "(auto=%d, llm_scored=%d, multi=%d)",
                entity_type,
                total_records,
                len(clusters),
                auto_matched,
                llm_scored,
                len(multi_record_clusters),
            )

        except Exception as exc:
            msg = f"Entity resolution failed for '{entity_type}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)
            entity_clusters[entity_type] = []

    logger.debug("Total entity types in entity_clusters: %d", len(entity_clusters))
    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT resolve_entities")

    return {
        "entity_clusters": entity_clusters,
        "errors": errors,
        "origins": state.get("origins", []),
        "destination": state.get("destination", {}),
        "llm_config": state.get("llm_config", {}),
        "extraction_config": state.get("extraction_config", {}),
        "target_entities": target_entities,
        "schemas": state.get("schemas", {}),
        "dependency_tree": state.get("dependency_tree", {}),
        "extraction_order": state.get("extraction_order", []),
        "extracted_data": extracted_data,
        "unified_entities": state.get("unified_entities", {}),
        "field_mappings": state.get("field_mappings", {}),
        "pending_schema_changes": state.get("pending_schema_changes", []),
        "human_feedback": state.get("human_feedback"),
        "load_approval": state.get("load_approval"),
        "retry_counts": state.get("retry_counts", {}),
        "interrupt_data": None,
    }


def has_low_confidence_clusters(state: dict[str, Any]) -> bool:
    """Conditional edge: True if any entity has low-confidence clusters."""
    entity_clusters: dict[str, list[dict]] = state.get("entity_clusters", {})

    for entity_type, clusters in entity_clusters.items():
        for cluster in clusters:
            record_ids = cluster.get("record_ids", [])
            if len(record_ids) > 1 and cluster.get("required_human_review", False):
                logger.info(
                    "Low-confidence cluster found for entity '%s': cluster %s",
                    entity_type,
                    cluster.get("cluster_id", "?"),
                )
                return True

    return False