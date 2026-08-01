"""
resolve_entities node: Clusters matching records across origins.

Calls resolve_entities_impl for each extracted entity type to produce
clusters of matching records.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.resolve_entities import resolve_entities_impl
from src.orchestrator.utils.entity_aliases import concept_matches
from src.orchestrator.spool import (
    RunSpool,
    clusters_for,
    entity_row_count,
    entity_rows,
    is_handle,
    make_handle,
    store_clusters,
)
from src.orchestrator.utils.retry import TransientError
from src.orchestrator.utils.retry_hints import with_retry_hint

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
    resolution_stats: dict[str, dict] = dict(state.get("resolution_stats", {}))

    run_id = str(state.get("run_id") or "unknown")
    spool = RunSpool(run_id)

    logger.debug("target_entities: %s", target_entities)
    logger.debug("extracted_data keys: %s", list(extracted_data.keys()))
    for et in extracted_data:
        logger.debug("  Entity '%s': %d records", et, entity_row_count(state, et))

    # Entity ids already assigned to source records by earlier runs. Without
    # these, an incremental run (which sees only the origins lacking a usable
    # watermark) re-derives ids from a smaller membership and inserts new
    # destination rows instead of updating the existing ones.
    entity_map = _load_entity_map(state)

    # Destination dedup (scoped merge): pull the subject rows the destination
    # already holds and add them to the candidate set, so an incoming record
    # that IS one already present clusters with it and adopts its id — updating
    # the row instead of inserting a second copy. Guarded to the subject entity:
    # reading every destination person unfiltered would both be unbounded and
    # pollute clustering with unrelated rows.
    _inject_subject_destination_rows(state, spool, entity_map, extracted_data)

    for entity_type in target_entities:
        records = entity_rows(state, entity_type)
        if not records:
            # extracted_data is keyed by table-level names (e.g. "companies",
            # "organizations") while target entities are abstract concepts
            # (e.g. "company") — pool all matching tables across origins so
            # cross-source resolution sees them together.
            pooled: list[dict] = []
            for key in list(extracted_data):
                if key != entity_type and concept_matches(entity_type, key):
                    rows = entity_rows(state, key)
                    logger.debug("Pooling %d rows from '%s' into concept '%s'", len(rows), key, entity_type)
                    pooled.extend(rows)
            if pooled:
                records = pooled
                # Write the pooled set back to the spool under the concept
                # name so unify/map/load see it the same way, without the
                # rows travelling through checkpointed state.
                spool.reset_entity(entity_type)
                spool.append(entity_type, pooled)
                extracted_data[entity_type] = make_handle(
                    run_id, entity_type, len(pooled)
                )
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
                llm_config=with_retry_hint(
                    state.get("llm_config") or None, state, "resolve_entities"
                ),
                known_cluster_ids=entity_map.get(entity_type),
                matching_rules=(state.get("matching_rules") or {}).get(
                    entity_type
                ),
                subject=(state.get("merge_scope") or {}).get("subject") or None,
            )
            logger.debug("resolve_entities_impl result keys: %s", list(result.keys()))

            clusters = result.get("clusters", [])

            # Stats
            total_records = result.get("total_records", 0)
            auto_matched = result.get("auto_matched", 0)
            auto_excluded = result.get("auto_excluded", 0)
            llm_scored = result.get("llm_scored", 0)
            multi_record_clusters = [c for c in clusters if len(c.get("record_ids", [])) > 1]
            low_conf_clusters = [c for c in clusters if c.get("required_human_review", False)]

            # Clusters go to the spool; state keeps a handle carrying the
            # summary the routers and gates need, so neither has to read
            # every cluster back just to decide what to do.
            entity_clusters[entity_type] = store_clusters(
                run_id, entity_type, clusters,
                needs_review=len(low_conf_clusters),
                multi_record=len(multi_record_clusters),
            )

            uncompared = result.get("uncompared_pairs", 0)
            resolution_stats[entity_type] = {
                "total_records": total_records,
                "auto_matched": auto_matched,
                "auto_excluded": auto_excluded,
                "llm_scored": llm_scored,
                "clusters_formed": len(clusters),
                "low_confidence": len(low_conf_clusters),
                # Pairs the comparison budget could not reach. A reviewer
                # approving this run needs to see that some records were
                # never compared, rather than have it sit in a log file.
                "uncompared_pairs": uncompared,
                "candidate_pairs": result.get("candidate_pairs", 0),
                # Uncertain pairs left unmerged. They form no multi-record
                # cluster, so without this the matches gate would auto-skip
                # and the human would never learn they existed.
                "pending_merge_candidates": result.get(
                    "pending_merge_candidates", []
                ),
            }
            if uncompared:
                logger.warning(
                    "Entity '%s': %d candidate pair(s) exceeded the comparison "
                    "budget and were NOT compared. Duplicates among them will "
                    "not have been detected.",
                    entity_type, uncompared,
                )

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
        "resolution_stats": resolution_stats,
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


_DEST_ORIGIN = "__destination__"
_DEST_READ_LIMIT = 2000


def _inject_subject_destination_rows(
    state: dict[str, Any],
    spool: Any,
    entity_map: dict[str, dict[str, str]],
    extracted_data: dict[str, Any],
) -> None:
    """Add existing destination rows for the subject entity to the candidate set.

    Best-effort and heavily guarded: only for a scoped merge, only the subject
    entity, only rows whose text matches the subject, capped. Each injected row
    is tagged as coming from ``__destination__`` and pre-seeded in the entity
    map with its existing destination id, so a cluster containing it adopts that
    id (see ``_assign_cluster_id``) and the load updates the row in place.

    Any failure leaves the run exactly as it was without dedup — a destination
    read must never be what stops a merge.
    """
    merge_scope = state.get("merge_scope") or {}
    if not merge_scope.get("active"):
        return
    subject = str(merge_scope.get("subject") or "").strip()
    subject_entity = str(merge_scope.get("scope_type") or "").strip()
    if not subject or not subject_entity:
        return
    destination = state.get("destination")
    if not isinstance(destination, dict):
        return

    try:
        from src.mcp_server.tools.load_data import _get_destination_connector
        from src.orchestrator.utils.entity_aliases import resolve_dest_table

        connector = _get_destination_connector(destination)
        if not hasattr(connector, "read_entity_rows"):
            return
        available = connector.list_tables() if hasattr(connector, "list_tables") else []
        dest_table = resolve_dest_table(subject_entity, available) if available else subject_entity
        if not dest_table:
            return

        rows = connector.read_entity_rows(dest_table, limit=_DEST_READ_LIMIT)
        if not rows:
            return

        # Keep only rows that actually mention the subject, matched in Python so
        # no assumption is made about which column holds the name.
        needle = subject.lower()
        matched = [
            r for r in rows
            if any(
                isinstance(v, str) and needle in v.lower()
                for v in r.values()
            )
        ]
        if not matched:
            logger.info(
                "Destination dedup: no existing %r rows in %s matched %r.",
                subject_entity, dest_table, subject,
            )
            return

        prepared: list[dict[str, Any]] = []
        id_map: dict[str, str] = dict(entity_map.get(subject_entity, {}))
        for r in matched:
            dest_id = r.get("id")
            if dest_id in (None, ""):
                continue
            row = dict(r)
            row["_origin_id"] = _DEST_ORIGIN
            row["_origin_table"] = dest_table
            row["_origin_id_row"] = str(dest_id)
            record_key = f"{_DEST_ORIGIN}:{dest_table}:{dest_id}"
            id_map[record_key] = str(dest_id)
            prepared.append(row)

        if not prepared:
            return

        spool.append(subject_entity, prepared)
        entity_map[subject_entity] = id_map
        # Refresh the handle's count so downstream readers see the injected rows.
        extracted_data[subject_entity] = make_handle(
            str(state.get("run_id") or "unknown"),
            subject_entity,
            spool.row_count(subject_entity),
        )
        logger.info(
            "Destination dedup: injected %d existing %r row(s) from %s so a "
            "re-merge updates them instead of duplicating.",
            len(prepared), subject_entity, dest_table,
        )
    except Exception as exc:
        logger.warning(
            "Destination dedup skipped (%s); incoming subject rows may be "
            "inserted as new instead of updating existing ones.", exc,
        )


def _load_entity_map(state: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Previously assigned cluster ids, per entity type, from the destination.

    Best-effort: a destination that can't be reached, or one that has never
    been loaded, simply yields no prior assignments and every cluster derives
    a fresh id — the behaviour before the map existed.
    """
    destination = state.get("destination")
    if not isinstance(destination, dict):
        return {}
    try:
        from src.mcp_server.tools.load_data import _get_destination_connector

        connector = _get_destination_connector(destination)
        if not hasattr(connector, "get_entity_map"):
            return {}
        loaded = {
            entity_type: connector.get_entity_map(entity_type)
            for entity_type in state.get("target_entities", [])
        }
        total = sum(len(v) for v in loaded.values())
        if total:
            logger.info(
                "Loaded %d previously assigned entity id(s) from the "
                "destination — re-runs will update those rows, not duplicate them.",
                total,
            )
        return loaded
    except Exception as exc:
        logger.warning(
            "Could not read the entity map (%s); cluster ids will be derived "
            "from membership alone, which can duplicate rows on an "
            "incremental re-run.", exc,
        )
        return {}


def has_low_confidence_clusters(state: dict[str, Any]) -> bool:
    """Conditional edge: does anything need a human's judgement on matching?

    Two reasons to stop, not one. The obvious case is a merged cluster the
    system isn't sure about. The subtler case is a pair scored in the
    uncertain band and therefore NOT merged — it forms no multi-record
    cluster, so before this check the gate auto-skipped and the human never
    learned those pairs existed.
    """
    for entity_type, stats in (state.get("resolution_stats") or {}).items():
        pending = stats.get("pending_merge_candidates") or []
        if pending:
            logger.info(
                "Entity '%s': %d uncertain pair(s) were left unmerged and "
                "need a human decision.", entity_type, len(pending),
            )
            return True

    entity_clusters: dict[str, Any] = state.get("entity_clusters", {})

    for entity_type, value in entity_clusters.items():
        # The handle carries the review count, so this router — which the
        # graph evaluates on every pass — does not have to open the spool
        # and read every cluster just to answer yes or no.
        if is_handle(value):
            if int(value.get("needs_review", 0)) > 0:
                logger.info(
                    "Entity '%s': %s cluster(s) need review.",
                    entity_type, value.get("needs_review"),
                )
                return True
            continue
        for cluster in value or []:
            record_ids = cluster.get("record_ids", [])
            if len(record_ids) > 1 and cluster.get("required_human_review", False):
                logger.info(
                    "Low-confidence cluster found for entity '%s': cluster %s",
                    entity_type,
                    cluster.get("cluster_id", "?"),
                )
                return True

    return False