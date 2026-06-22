"""
unify_entities node: Merges matched record clusters into unified records.

Calls unify_entity_group_impl for each cluster to produce one unified
record per cluster with conflict details.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.unify_entity_group import unify_entity_group_impl

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
    entity_clusters: dict[str, list[dict]] = state.get("entity_clusters", {})
    extracted_data: dict[str, list[dict]] = state.get("extracted_data", {})
    target_entities: list[str] = state.get("target_entities", [])
    errors: list[str] = list(state.get("errors", []))
    unified_entities: dict[str, list[dict]] = dict(state.get("unified_entities", {}))

    for entity_type in target_entities:
        clusters = entity_clusters.get(entity_type, [])
        all_records = extracted_data.get(entity_type, [])

        # Build record lookup by origin_id
        record_lookup: dict[str, dict] = {}
        for rec in all_records:
            rid = f"{rec.get('_origin_id', '?')}:{rec.get('_origin_table', '?')}:{rec.get('_origin_id_row', '')}"
            record_lookup[rid] = rec

        unified_records: list[dict] = []
        total_conflicts = 0

        for cluster in clusters:
            cluster_id = cluster.get("cluster_id", "")
            record_ids = cluster.get("record_ids", [])

            # Look up actual records
            cluster_records = []
            for rid in record_ids:
                rec = record_lookup.get(rid)
                if rec:
                    cluster_records.append(rec)

            if not cluster_records:
                logger.warning(
                    "Cluster '%s' has no matching records, skipping.", cluster_id
                )
                continue

            try:
                result = await unify_entity_group_impl(
                    cluster_id=cluster_id,
                    records=cluster_records,
                )

                unified_record = result.get("unified_record", {})
                conflicts = result.get("conflicts", [])
                null_filled = result.get("null_filled_fields", [])

                total_conflicts += len(conflicts)

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
                logger.error(msg)
                errors.append(msg)

        unified_entities[entity_type] = unified_records

        logger.info(
            "Entity unification for '%s': %d clusters → %d unified records, %d total conflicts",
            entity_type,
            len(clusters),
            len(unified_records),
            total_conflicts,
        )

    return {
        "unified_entities": unified_entities,
        "errors": errors,
    }