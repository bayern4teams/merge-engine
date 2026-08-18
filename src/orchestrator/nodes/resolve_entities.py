"""
resolve_entities node: Clusters matching records across origins.

Calls resolve_entities_impl for each extracted entity type to produce
clusters of matching records.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from src.mcp_server.tools.resolve_entities import (
    _PROCESS_POOL_MIN_RECORDS,
    resolve_entities_impl,
)
from src.orchestrator.spool import (
    RunSpool,
    entity_row_count,
    entity_rows,
    is_handle,
    make_handle,
    store_clusters,
)
from src.orchestrator.utils.entity_aliases import concept_matches
from src.orchestrator.utils.retry_hints import with_retry_hint

logger = logging.getLogger("merge-engine.orchestrator.resolve_entities")

# Bounded rather than os.cpu_count(): each worker holds a full copy of one
# entity's records (pickled across the process boundary), and ClickHouse/
# Postgres connections plus the API server itself are already competing for
# this machine's cores during a run. 4 is the same conservative default
# Part 1.5 B.2's design discussion used for a comparable fan-out.
_MAX_POOL_WORKERS = 4


def _matching_rules_for(
    matching_rules: dict[str, Any] | None, entity_type: str
) -> dict[str, Any] | None:
    """Per-entity rules, falling back to a ``_default`` entry (Part 1.6.A).

    ``matching_rules`` is keyed per entity type
    (``{entity: {blocking_keys, unique_id_fields, strictness, ...}}``, O.2).
    A run-by-run override set in the UI's Advanced panel is submitted before
    ``target_entities`` is even known (parse_request hasn't run yet), so it
    can only be written under a synthetic ``_default`` key — the same
    convention ``conflict_policy`` already uses (see ``WorkflowStart``'s
    docstring). An explicit per-entity entry still wins over ``_default``.
    """
    rules = matching_rules or {}
    return rules.get(entity_type) or rules.get("_default")


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
    # Only THIS call's own errors (Phase 0 Sec 0.8) - see load_data_node's
    # identical fix for why seeding with the existing history double-counted
    # it through operator.add's reducer.
    errors: list[str] = []
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

    # ── Phase 1: sequential prep (Part 1.5 B.3, same shape as B.1's
    # map_fields split) ──────────────────────────────────────────────
    # Record pooling writes to this run's spool (reset_entity/append) --
    # not safe to run concurrently with other entities' spool writes, so
    # every entity's records are resolved here, strictly in order, before
    # any resolve_entities_impl call is dispatched.
    prepared: list[tuple[str, list[dict[str, Any]]]] = []
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
                    logger.debug(
                        "Pooling %d rows from '%s' into concept '%s'", len(rows), key, entity_type
                    )
                    pooled.extend(rows)
            if pooled:
                records = pooled
                # Write the pooled set back to the spool under the concept
                # name so unify/map/load see it the same way, without the
                # rows travelling through checkpointed state.
                spool.reset_entity(entity_type)
                spool.append(entity_type, pooled)
                extracted_data[entity_type] = make_handle(run_id, entity_type, len(pooled))
        logger.debug("Processing entity '%s': %d records", entity_type, len(records))
        if not records:
            logger.info("No records to resolve for entity '%s', skipping.", entity_type)
            entity_clusters[entity_type] = []
            continue
        prepared.append((entity_type, records))

    # ── Phase 2: concurrent dispatch ────────────────────────────────────
    # Part 1.5 B.3: resolve_entities_impl's CPU-bound core (blocking, model
    # fit, scoring) is pure-Python and GIL-bound -- asyncio.gather alone
    # would not parallelise it (unlike B.1's map_fields, which is I/O-bound
    # LLM round-trips). A process pool gives real parallelism across
    # entities instead.
    #
    # Requires at least TWO qualifying entities, not one: measured live
    # (docs/verification/PROCESS_POOL_PROFILE.md), dispatching a single
    # 1,503-record entity alone to the pool was slightly SLOWER than inline
    # (23.67s vs 22.52s) -- pickling/IPC overhead with nothing to overlap it
    # against. Two same-sized entities dispatched concurrently measured
    # 1.53x faster than sequential, correctness verified byte-identical.
    # One qualifying entity therefore runs inline exactly as before; the
    # pool only stands up when there is real work to parallelise.
    #
    # Any pool-creation failure degrades to running every entity inline
    # (process_pool=None) -- this must never be what breaks a merge run.
    process_pool: ProcessPoolExecutor | None = None
    qualifying = sum(1 for _et, records in prepared if len(records) >= _PROCESS_POOL_MIN_RECORDS)
    if qualifying >= 2:
        try:
            process_pool = ProcessPoolExecutor(
                max_workers=min(_MAX_POOL_WORKERS, os.cpu_count() or 1)
            )
        except Exception as exc:
            logger.warning(
                "Could not create resolve_entities process pool (%s); "
                "every entity will run inline instead.",
                exc,
            )
            process_pool = None

    try:
        logger.debug("Dispatching %d entity resolution(s) concurrently", len(prepared))
        results = await asyncio.gather(
            *(
                resolve_entities_impl(
                    entity_type=entity_type,
                    records=records,
                    llm_config=with_retry_hint(
                        state.get("llm_config") or None, state, "resolve_entities"
                    ),
                    known_cluster_ids=entity_map.get(entity_type),
                    matching_rules=_matching_rules_for(state.get("matching_rules"), entity_type),
                    subject=(state.get("merge_scope") or {}).get("subject") or None,
                    process_pool=process_pool,
                )
                for entity_type, records in prepared
            ),
            return_exceptions=True,
        )
    finally:
        if process_pool is not None:
            process_pool.shutdown(wait=True)

    # ── Phase 3: sequential merge, in target_entities order ─────────────
    # asyncio.gather preserves input order, so results lines up 1:1 with
    # prepared. Merging here keeps entity_clusters/resolution_stats/errors
    # ordering deterministic across runs, exactly as when this loop ran
    # strictly sequentially (same pattern as B.1's map_fields_node.py).
    for (entity_type, _records), outcome in zip(prepared, results, strict=True):
        if isinstance(outcome, Exception):
            msg = f"Entity resolution failed for '{entity_type}': {outcome}"
            logger.error(msg, exc_info=outcome)
            errors.append(msg)
            entity_clusters[entity_type] = []
            continue
        if isinstance(outcome, BaseException):
            raise outcome

        result = outcome
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
            run_id,
            entity_type,
            clusters,
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
            "pending_merge_candidates": result.get("pending_merge_candidates", []),
        }
        if uncompared:
            logger.warning(
                "Entity '%s': %d candidate pair(s) exceeded the comparison "
                "budget and were NOT compared. Duplicates among them will "
                "not have been detected.",
                entity_type,
                uncompared,
            )

        logger.debug(
            "Resolution stats - total=%d, auto=%d, llm=%d, multi=%d, low_conf=%d",
            total_records,
            auto_matched,
            llm_scored,
            len(multi_record_clusters),
            len(low_conf_clusters),
        )
        for lc in low_conf_clusters:
            logger.debug(
                "Low-confidence cluster: id=%s, records=%s, score=%.2f",
                lc.get("cluster_id"),
                lc.get("record_ids"),
                lc.get("confidence", 0),
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

    logger.debug("Total entity types in entity_clusters: %d", len(entity_clusters))
    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT resolve_entities")

    # Interim fix for a comparison-budget truncation being visible nowhere
    # but a log line and a JSON field no UI ever rendered: duplicates among
    # the unscanned pairs are a real, reportable risk, not something that
    # should be invisible at the final gate. Folds into whatever
    # validate_data already found — never overwrites it, since
    # quality_report/has_quality_failure have no LangGraph reducer and a
    # plain return here would silently discard validate_data's own findings
    # from the node that ran immediately before this one.
    budget_truncated = {
        entity_type: stats["uncompared_pairs"]
        for entity_type, stats in resolution_stats.items()
        if stats.get("uncompared_pairs")
    }
    quality_update: dict[str, Any] = {}
    if budget_truncated:
        existing_report = dict(state.get("quality_report") or {})
        entities_report = dict(existing_report.get("entities") or {})
        overall_severity = existing_report.get("severity", "pass")
        for entity_type, uncompared in budget_truncated.items():
            prior = dict(entities_report.get(entity_type) or {})
            checks = list(prior.get("checks") or [])
            checks.append(
                {
                    "check": "comparison_budget",
                    "severity": "warn",
                    "success": False,
                    "details": (
                        f"{uncompared} candidate pair(s) exceeded the comparison "
                        "budget and were never compared — duplicates among them "
                        "would not have been detected."
                    ),
                }
            )
            # A "warn" here never downgrades an entity validate_data already
            # marked "fail" — this adds risk information, it doesn't soften
            # a harder finding.
            entity_severity = "fail" if prior.get("severity") == "fail" else "warn"
            entities_report[entity_type] = {**prior, "severity": entity_severity, "checks": checks}
            if entity_severity == "warn" and overall_severity != "fail":
                overall_severity = "warn"
        quality_update = {
            "quality_report": {
                **existing_report,
                "status": existing_report.get("status", "completed"),
                "severity": overall_severity,
                "entities": entities_report,
            },
            # Unchanged: a "warn" never forces has_quality_failure -- only
            # validate_data's own hard "fail" findings do that, exactly as
            # data_quality.run_data_quality_checks' own severity->has_failure
            # rule already works.
            "has_quality_failure": bool(state.get("has_quality_failure")),
        }

    # Only the keys this node actually computes (Phase 0 Sec 0.8) - a key it
    # omits is left untouched by LangGraph's per-key TypedDict state update,
    # not cleared. extracted_data is kept explicit since
    # _inject_subject_destination_rows above may have added subject rows to
    # it for a scoped merge.
    return {
        "entity_clusters": entity_clusters,
        "resolution_stats": resolution_stats,
        "errors": errors,
        "extracted_data": extracted_data,
        "interrupt_data": None,
        **quality_update,
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
            r for r in rows if any(isinstance(v, str) and needle in v.lower() for v in r.values())
        ]
        if not matched:
            logger.info(
                "Destination dedup: no existing %r rows in %s matched %r.",
                subject_entity,
                dest_table,
                subject,
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
            len(prepared),
            subject_entity,
            dest_table,
        )
    except Exception as exc:
        logger.warning(
            "Destination dedup skipped (%s); incoming subject rows may be "
            "inserted as new instead of updating existing ones.",
            exc,
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
            "incremental re-run.",
            exc,
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
                "Entity '%s': %d uncertain pair(s) were left unmerged and need a human decision.",
                entity_type,
                len(pending),
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
                    entity_type,
                    value.get("needs_review"),
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
