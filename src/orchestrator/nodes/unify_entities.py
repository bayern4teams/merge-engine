"""
unify_entities node: Merges matched record clusters into unified records.

Calls unify_entity_group_impl for each cluster to produce one unified
record per cluster with conflict details.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.unify_entity_group import unify_entity_group_impl
from src.orchestrator.spool import (
    clusters_for,
    collection_count,
    entity_rows,
    store_unified,
)

logger = logging.getLogger("merge-engine.orchestrator.unify_entities")


async def unify_entity_groups(state: dict[str, Any]) -> dict[str, Any]:
    """Unify all entity clusters.

    Parameters
    ----------
    state : dict
        MergeState with ``entity_clusters``, ``extracted_data``, ``target_entities``.

    Returns
    -------
    dict
        Update dict with ``unified_entities`` and ``errors`` keys.
    """
    logger.debug("ENTER unify_entity_groups")
    logger.debug("State keys: %s", list(state.keys()))
    entity_clusters: dict[str, list[dict]] = state.get("entity_clusters", {})
    target_entities: list[str] = state.get("target_entities", [])
    origins: list[dict] = state.get("origins", [])
    # Per-field survivorship rules (Phase O.3). Absent means the historical
    # priority -> completeness -> recency chain, so an unconfigured run
    # behaves exactly as it did before profiles existed.
    conflict_policy: dict = state.get("conflict_policy") or {}
    # Only THIS call's own errors (Phase 0 Sec 0.8) - see load_data_node's
    # identical fix for why seeding with the existing history double-counted
    # it through operator.add's reducer.
    errors: list[str] = []
    unified_entities: dict[str, list[dict]] = dict(state.get("unified_entities", {}))

    logger.debug("target_entities: %s", target_entities)
    logger.debug("entity_clusters keys: %s", list(entity_clusters.keys()))

    run_id = str(state.get("run_id") or "unknown")
    total_clusters = sum(collection_count(entity_clusters, e) for e in entity_clusters)
    logger.debug("Total clusters across all entities: %d", total_clusters)

    for entity_type in target_entities:
        clusters = clusters_for(state, entity_type)
        all_records = entity_rows(state, entity_type)
        logger.debug(
            "Processing entity '%s': %d clusters, %d records",
            entity_type,
            len(clusters),
            len(all_records),
        )

        # Build record lookup by origin_id
        record_lookup: dict[str, dict] = {}
        for rec in all_records:
            rid = f"{rec.get('_origin_id', '?')}:{rec.get('_origin_table', '?')}:{rec.get('_origin_id_row', '')}"
            record_lookup[rid] = rec

        unified_records: list[dict] = []
        total_conflicts = 0

        for cluster_idx, cluster in enumerate(clusters):
            cluster_id = cluster.get("cluster_id", "")
            record_ids = cluster.get("record_ids", [])
            logger.debug(
                "  Cluster %d (%s): %d records",
                cluster_idx,
                cluster_id[:12] if cluster_id else "?",
                len(record_ids),
            )

            # Look up actual records
            cluster_records = []
            for rid in record_ids:
                rec = record_lookup.get(rid)
                if rec:
                    cluster_records.append(rec)

            if not cluster_records:
                logger.warning("Cluster '%s' has no matching records, skipping.", cluster_id)
                continue

            try:
                logger.debug(
                    "    Calling unify_entity_group_impl for cluster '%s'", cluster_id[:12]
                )
                result = await unify_entity_group_impl(
                    cluster_id=cluster_id,
                    records=cluster_records,
                    origins=origins,
                    conflict_policy=conflict_policy,
                )
                logger.debug("    unify_entity_group_impl result keys: %s", list(result.keys()))

                unified_record = result.get("unified_record", {})
                conflicts = result.get("conflicts", [])
                null_filled = result.get("null_filled_fields", [])

                total_conflicts += len(conflicts)
                logger.debug("    Conflicts: %d, Null-filled: %d", len(conflicts), len(null_filled))

                # Assign universal UUID to unified record
                unified_record["id"] = cluster_id
                unified_record["_entity_type"] = entity_type

                # Add metadata
                if conflicts:
                    unified_record["_conflicts"] = conflicts
                if null_filled:
                    unified_record["_null_filled"] = null_filled

                unified_records.append(unified_record)

                logger.info(
                    "Unified entity '%s': %d records → 1 record, %d conflicts, %d null-filled",
                    cluster_id,
                    len(cluster_records),
                    len(conflicts),
                    len(null_filled),
                )

            except Exception as exc:
                msg = f"Unify failed for cluster '{cluster_id}': {exc}"
                logger.error(msg, exc_info=True)
                errors.append(msg)

        # The merged records go to the spool. Measured on a 2,284-record
        # merge, this channel alone was 69.6% of every checkpoint written.
        unified_entities[entity_type] = store_unified(run_id, entity_type, unified_records)
        logger.debug("Entity '%s': %d unified records", entity_type, len(unified_records))

        logger.info(
            "Entity unification for '%s': %d clusters → %d unified records, %d total conflicts",
            entity_type,
            len(clusters),
            len(unified_records),
            total_conflicts,
        )

    logger.debug("Total unified_entities keys: %d", len(unified_entities))
    for ue, records in unified_entities.items():
        logger.debug("  Entity '%s': %d unified records", ue, len(records))
    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT unify_entity_groups")

    # Only the keys this node actually computes (Phase 0 Sec 0.8) - a key it
    # omits is left untouched by LangGraph's per-key TypedDict state update,
    # not cleared.
    return {
        "unified_entities": unified_entities,
        "errors": errors,
        "interrupt_data": None,
    }
