"""
unify_entity_group tool implementation.

Merges attributes within a cluster: complement nulls, flag conflicts,
produce unified record with conflict details.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.utils.id_utils import generate_uuid
from src.orchestrator.utils.retry import TransientError, PermanentError

logger = logging.getLogger("merge-engine.tools.unify_entity_group")


async def unify_entity_group_impl(
    cluster_id: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Unify a cluster of matching records into one record.

    Parameters
    ----------
    cluster_id : str
        Unique identifier for the cluster.
    records : list[dict]
        All records in this cluster.

    Returns
    -------
    dict
        {
            "cluster_id": str,
            "unified_record": dict,
            "conflicts": list[dict],
            "null_filled_fields": list[str],
            "source_count": int,
        }
    """
    logger.debug("ENTER unify_entity_group_impl: cluster_id=%s, records_count=%d",
                  cluster_id, len(records))
    from src.mcp_server.server import _get_config
    config = _get_config()

    if not records:
        logger.debug("No records to unify, raising ValueError")
        raise ValueError("Cannot unify empty record list")

    conflicts: list[dict[str, Any]] = []
    null_filled: list[str] = []
    unified: dict[str, Any] = {}
    sources: set[str] = set()

    # Collect all field names across all records
    all_fields: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for rec in records:
        origin = rec.get("_origin_id", rec.get("origin_id", "unknown"))
        sources.add(origin)
        for key, value in rec.items():
            if key.startswith("_"):
                continue
            all_fields.setdefault(key, []).append((rec, origin))

    logger.debug("Collected %d fields from %d records across %d sources",
                  len(all_fields), len(records), len(sources))

    # Priority order: higher priority origins preferred for conflicts
    origin_priorities: dict[str, int] = {}
    for o in config.origins:
        origin_priorities[o.id] = o.priority
    logger.debug("Origin priorities: %s", origin_priorities)

    fields_processed = 0
    fields_with_conflicts = 0
    fields_complemented = 0
    for field, origin_values in all_fields.items():
        non_null_values = [(rec, orig) for rec, orig in origin_values if rec.get(field) is not None]
        null_count = len(origin_values) - len(non_null_values)
        fields_processed += 1

        if len(non_null_values) == 1:
            # Only one non-null value — complement: fill null with this
            rec, orig = non_null_values[0]
            unified[field] = rec[field]
            if null_count > 0:
                null_filled.append(field)
                fields_complemented += 1
            logger.debug("Field '%s': single non-null value from %s", field, orig)
        elif len(non_null_values) == 0:
            # All null — skip this field
            logger.debug("Field '%s': all null, skipping", field)
            continue
        else:
            # Multiple non-null values
            values = [v[0][field] for v in non_null_values]
            origins = [v[1] for v in non_null_values]

            # Check if all values agree
            if all(v == values[0] for v in values):
                unified[field] = values[0]
                logger.debug("Field '%s': all %d values agree on '%s'", field, len(values), values[0])
            else:
                # Conflict! Pick highest-priority origin's value
                fields_with_conflicts += 1
                best = max(non_null_values, key=lambda x: origin_priorities.get(x[1], 0))
                unified[field] = best[0][field]

                conflicts.append({
                    "field": field,
                    "values": [
                        {"origin": orig, "value": rec[field]}
                        for rec, orig in non_null_values
                    ],
                    "selected_origin": best[1],
                    "selected_value": best[0][field],
                })
                logger.debug("Field '%s': CONFLICT! %d values, selected %s from %s",
                              field, len(values), best[0][field], best[1])

    # Preserve cluster-level metadata
    unified["_cluster_id"] = cluster_id
    unified["_source_count"] = len(sources)

    logger.debug(
        "EXIT unify_entity_group_impl: cluster_id=%s, unified_fields=%d, conflicts=%d, null_filled=%d, sources=%d",
        cluster_id, len(unified), len(conflicts), len(null_filled), len(sources),
    )
    return {
        "cluster_id": cluster_id,
        "unified_record": unified,
        "conflicts": conflicts,
        "null_filled_fields": null_filled,
        "source_count": len(sources),
    }


async def unify_entity_group_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    logger.debug("ENTER unify_entity_group_handler: arguments_keys=%s", list(arguments.keys()))
    return await unify_entity_group_impl(**arguments)
