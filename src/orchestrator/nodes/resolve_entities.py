"""
resolve_entities node: Clusters matching records across origins.

Calls resolve_entities_impl for each extracted entity type to produce
clusters of matching records.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from src.mcp_server.tools.metadata_classification import metadata_columns_by_table
from src.mcp_server.tools.resolve_entities import (
    _MIN_COHESION,
    _PROCESS_POOL_MIN_RECORDS,
    CandidatePairBudgetExceeded,
    build_relationship_index,
    rebuild_band_clusters,
    resolve_entities_impl,
)
from src.orchestrator.decision_memory import DecisionMemory
from src.orchestrator.spool import (
    RunSpool,
    clusters_for,
    entity_records_by_id,
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


def _matching_connector_and_config(state: dict[str, Any]) -> tuple[Any | None, dict[str, Any]]:
    """The destination connector + ``matching.*`` config for B.1.3's pg_trgm
    trigram blocking, best-effort (never what stops a merge — same ethos as
    ``_inject_subject_destination_rows``). ``resolve_entities_impl`` itself
    stays disabled (``trigram_blocking_enabled`` defaults False) unless
    config.yaml turns it on, so a connector failure here just means that
    flag has no effect, not a broken run.
    """
    matching_config: dict[str, Any] = {}
    try:
        from src.utils.config_loader import load_config

        cfg = load_config()
        matching_config = cfg.matching.model_dump()
    except Exception as exc:
        logger.debug("Could not load matching config (%s); trigram blocking stays off.", exc)

    connector = None
    destination = state.get("destination")
    if isinstance(destination, dict) and matching_config.get("trigram_blocking_enabled"):
        try:
            from src.mcp_server.tools.load_data import _get_destination_connector

            candidate = _get_destination_connector(destination)
            if hasattr(candidate, "execute_sql"):
                connector = candidate
        except Exception as exc:
            logger.warning(
                "Could not open destination connector for trigram blocking (%s); "
                "falling back to the existing blocking pass alone.",
                exc,
            )
    return connector, matching_config


def _relationship_index_for_companies(
    state: dict[str, Any], extracted_data: dict[str, Any]
) -> dict[str, set[str]]:
    """B.5.1: the relational-agreement signal's index, built once per run
    from every junction the dependency analysis found (P4's
    ``state["table_roles"]["junction"]``) whose two FK columns reference the
    same key — an id -> same-kind ids map (P7: structure, never a table or
    concept name). The junction's rows are the extracted entity of that
    name, else any extracted rows whose ``_origin_table`` is the junction —
    best-effort, never what stops a merge if this data isn't present.
    """
    try:
        index: dict[str, set[str]] = {}
        relation_records: list[dict[str, Any]] = []
        for role in (state.get("table_roles") or {}).get("junction") or []:
            cols = list(role.get("fk_columns") or [])
            targets = {str(r).split("->", 1)[-1].strip() for r in role.get("references") or []}
            if len(cols) != 2 or len(targets) != 1:
                continue  # two kinds of thing, or not a pair: not a same-kind index
            table = role.get("table")
            if table in extracted_data:
                rows = entity_rows(state, table)
            else:
                rows = [
                    row
                    for key in list(extracted_data)
                    for row in entity_rows(state, key)
                    if row.get("_origin_table") == table
                ]
            origin = role.get("origin")
            rows = [
                r
                for r in rows
                if r.get("_origin_table", table) == table
                and str(r.get("_origin_id", origin)) == str(origin)
            ]
            relation_records.extend(rows)
            for k, v in build_relationship_index(rows, (cols[0], cols[1])).items():
                index.setdefault(k, set()).update(v)
        if not index:
            return {}
        logger.info(
            "Relationship index built from %d relation row(s): %d compan(y/ies) indexed.",
            len(relation_records),
            len(index),
        )
        return index
    except Exception as exc:
        logger.warning(
            "Could not build relationship index (%s); relational-agreement signal off "
            "for this run.",
            exc,
        )
        return {}


def _without_metadata(
    records: list[dict[str, Any]], metadata: dict[tuple[str, str], frozenset[str]]
) -> list[dict[str, Any]]:
    """P12: the records matching sees. ETL metadata columns (per origin and table, from discovery)
    are left out of shallow COPIES; the corpus rows themselves are untouched, so the columns stay
    in the run output as provenance. Rows with nothing to drop are passed through as-is."""
    if not metadata:
        return records
    out: list[dict[str, Any]] = []
    for rec in records:
        cols = metadata.get((str(rec.get("_origin_id")), str(rec.get("_origin_table", "")).lower()))
        if cols and any(k.lower() in cols for k in rec):
            rec = {k: v for k, v in rec.items() if k.lower() not in cols}
        out.append(rec)
    return out


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
    metadata = metadata_columns_by_table(state.get("schemas") or {})
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
        prepared.append((entity_type, _without_metadata(records, metadata)))

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

    connector, matching_config = _matching_connector_and_config(state)

    # B.5.1: only worth building when "company" is actually being
    # resolved this run -- the relational-agreement signal is scoped to
    # company_relation data, not every entity type.
    relationship_index: dict[str, set[str]] = {}
    if any(concept_matches("company", et) for et, _records in prepared):
        relationship_index = _relationship_index_for_companies(state, extracted_data)

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
                    connector=connector,
                    matching_config=matching_config,
                    relationship_index=(
                        relationship_index if concept_matches("company", entity_type) else None
                    ),
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
        if isinstance(outcome, CandidatePairBudgetExceeded):
            # P2: the out-of-memory guard bound. Failing one entity and
            # carrying on would end the run `completed` with that entity
            # unresolved — the silent loss the guard exists to prevent.
            raise outcome
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
            # P2: pairs excluded by purging / meta-blocking (computed, not
            # budgeted) — the reviewer sees what blocking chose not to compare.
            "pairs_pruned_by_purging": result.get("pairs_pruned_by_purging", 0),
            "pairs_pruned_by_meta_blocking": result.get("pairs_pruned_by_meta_blocking", 0),
            # Uncertain pairs left unmerged. They form no multi-record
            # cluster, so without this the matches gate would auto-skip
            # and the human would never learn they existed.
            "pending_merge_candidates": result.get("pending_merge_candidates", []),
            # P15: thresholds, predicted counts per band and the score
            # distribution shown at the gate, plus the pairs a gate decision
            # re-clusters from (apply_band_decisions).
            "band_summary": result.get("band_summary", {}),
            "retained_pairs": result.get("retained_pairs", []),
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
    update = {
        "entity_clusters": entity_clusters,
        "resolution_stats": resolution_stats,
        "errors": errors,
        "extracted_data": extracted_data,
        "interrupt_data": None,
        **quality_update,
    }
    # P9: remembered band decisions are applied HERE, before the matches gate --
    # the gate's own body never runs on resume (plan 2.6) -- so a band the
    # memory fully answers never pauses the run.
    update.update(apply_remembered_decisions({**state, **update}))
    return update


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


_MERGE_ACTIONS = {"merge", "accept", "approve", "match", "yes"}
_KEEP_ACTIONS = {"keep_separate", "keep", "reject", "separate", "split", "no"}


def resolves_band(feedback: dict[str, Any]) -> bool:
    """Does this matches-gate feedback carry a band decision (P15)?"""
    return bool(feedback.get("pair_decisions") or feedback.get("band_thresholds"))


def _memory(state: dict[str, Any]) -> DecisionMemory | None:
    """The run's decision memory (P9), or None when none is configured: the
    ``decision_memory_path`` state key, else ``MERGE_ENGINE_DECISION_MEMORY``.
    Off by default, so a run never silently replays another run's decisions."""
    path = state.get("decision_memory_path") or os.environ.get("MERGE_ENGINE_DECISION_MEMORY")
    return DecisionMemory(path) if path else None


def _band_question(state: dict[str, Any], entity_type: str, pair: frozenset[str]) -> dict[str, Any]:
    """The matches-gate question for one band pair, as P9 keys it: the pair and
    both records' content. A changed attribute is a new question, asked again
    (report section 3.7: a resolution is revisited when new attributes arrive)."""
    a, b = sorted(pair)
    recs = entity_records_by_id(state, entity_type, [a, b])

    def content(rec: dict[str, Any] | None) -> dict[str, Any]:
        return {k: v for k, v in (rec or {}).items() if not str(k).startswith("_")}

    question = {
        "gate": "human_review_matches",
        "entity_type": entity_type,
        "record_ids": [a, b],
        "records": [content(recs.get(a)), content(recs.get(b))],
    }
    # JSON-safe and stable whatever the column types (dates, decimals).
    loaded: dict[str, Any] = json.loads(json.dumps(question, default=str))
    return loaded


def apply_remembered_decisions(state: dict[str, Any]) -> dict[str, Any]:
    """Resolve band pairs a previous run's human already decided (P9).

    Each pending pair is looked up by its question; hits are applied exactly
    as a live decision (``apply_band_decisions``), misses stay pending for the
    gate. The count lands in ``resolution_stats[e]["decisions_from_memory"]``.
    Returns {} when no memory is configured or nothing matched.
    """
    memory = _memory(state)
    if memory is None:
        return {}
    hits: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for entity_type, stats in (state.get("resolution_stats") or {}).items():
        for p in stats.get("pending_merge_candidates") or []:
            pair = frozenset(str(r) for r in p.get("record_ids") or [])
            if len(pair) != 2:
                continue
            action = memory.lookup(_band_question(state, entity_type, pair))
            if action in ("merge", "keep_separate"):
                hits.append(
                    {"entity_type": entity_type, "record_ids": sorted(pair), "action": action}
                )
                counts[entity_type] = counts.get(entity_type, 0) + 1
    if not hits:
        return {}
    update = apply_band_decisions(state, {"pair_decisions": hits}, keep_undecided=True)
    for entity_type, n in counts.items():
        update["resolution_stats"][entity_type]["decisions_from_memory"] = n
    logger.info("Decision memory: %d band decision(s) applied without asking.", len(hits))
    return update


def _remember(
    state: dict[str, Any],
    accepted: set[frozenset[str]],
    rejected: set[frozenset[str]],
    entity_type: str,
) -> None:
    """Persist this gate's per-pair answers (P9). A failing write never fails the run."""
    memory = _memory(state)
    if memory is None:
        return
    try:
        for pair in accepted:
            memory.record(_band_question(state, entity_type, pair), "merge")
        for pair in rejected:
            memory.record(_band_question(state, entity_type, pair), "keep_separate")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Decision memory: could not record the gate's decisions: %s", exc)


def apply_band_decisions(
    state: dict[str, Any], feedback: dict[str, Any], keep_undecided: bool = False
) -> dict[str, Any]:
    """Resolve the review band within the run (report P15): the state update
    the matches gate's decision produces.

    ``feedback["pair_decisions"]``: ``[{entity_type, record_ids: [a, b],
    action: "merge" | "keep_separate"}]``; ``feedback["band_thresholds"]``:
    ``{entity: {auto_match, auto_exclude}}``, the bar the operator commits
    after seeing the distribution. Thresholds that are not two numbers with
    ``0 <= auto_exclude < auto_match <= 1`` are refused (the run's own stay).
    An undecided pair or an unrecognised action never merges -- an unintended
    merge is not recoverable. Once applied nothing in the band is pending:
    approving the gate keeps every undecided pair apart.

    Shared by ``WorkflowManager.resume_workflow`` (the live path: the gate
    body never runs on resume) and ``human_review_matches`` (direct callers),
    so the two cannot drift. Pure in ``state``; returns {} when there is
    nothing to resolve.
    """
    decisions = [d for d in feedback.get("pair_decisions") or [] if isinstance(d, dict)]
    committed = feedback.get("band_thresholds") or {}
    all_stats = state.get("resolution_stats") or {}
    run_id = str(state.get("run_id") or "unknown")
    new_stats = dict(all_stats)
    new_clusters = dict(state.get("entity_clusters") or {})
    changed = False
    for entity_type, stats in all_stats.items():
        mine = [d for d in decisions if d.get("entity_type", entity_type) == entity_type]
        if not (stats.get("pending_merge_candidates") or mine or entity_type in committed):
            continue
        changed = True
        shown = (stats.get("band_summary") or {}).get("thresholds") or {}
        auto_match = shown.get("auto_match")
        requested = committed.get(entity_type) if isinstance(committed, dict) else None
        if isinstance(requested, dict):
            m, x = requested.get("auto_match"), requested.get("auto_exclude")
            if (
                isinstance(m, (int, float))
                and isinstance(x, (int, float))
                and not isinstance(m, bool)
                and not isinstance(x, bool)
                and 0 <= x < m <= 1
            ):
                auto_match = m
                logger.info(
                    "Entity '%s': operator committed thresholds match=%s exclude=%s",
                    entity_type,
                    m,
                    x,
                )
                shown = {"auto_match": m, "auto_exclude": x}
            else:
                logger.warning(
                    "Entity '%s': committed thresholds %r refused (need 0 <= exclude < match <= 1)",
                    entity_type,
                    requested,
                )
        accepted: set[frozenset[str]] = set()
        decided_now: list[frozenset[str]] = []  # answered with a recognised action
        # An earlier resume's "keep apart" still holds (a duplicate or late resume).
        rejected: set[frozenset[str]] = {
            frozenset(str(r) for r in p) for p in stats.get("band_kept_apart") or []
        }
        for d in mine:
            pair = frozenset(str(r) for r in d.get("record_ids") or [])
            action = str(d.get("action") or "").strip().lower()
            if len(pair) != 2:
                continue
            if action in _MERGE_ACTIONS:
                accepted.add(pair)
                decided_now.append(pair)
            elif action in _KEEP_ACTIONS:
                rejected.add(pair)
                decided_now.append(pair)
            else:  # kept apart for this run, but not an answer to remember
                logger.warning("Unknown band action %r for %s: kept apart.", action, sorted(pair))
                rejected.add(pair)
        retained = stats.get("retained_pairs")
        applied = retained is not None and auto_match is not None
        if retained is not None and auto_match is not None:
            # A pair a human decided outranks its score; persisted below so a
            # later resume cannot re-derive it away.
            retained = [p for p in retained if frozenset(map(str, p[:2])) not in accepted]
            retained += [[*sorted(p), 1] for p in accepted]
            bar_moved = auto_match != (
                ((stats.get("band_summary") or {}).get("thresholds") or {}).get("auto_match")
            )
            clusters = clusters_for(state, entity_type)
            needed = sorted(
                {str(r) for p in retained for r in p[:2]} | {r for p in accepted for r in p}
            )
            rebuilt = rebuild_band_clusters(
                entity_type,
                clusters,
                retained,
                accepted,
                rejected,
                float(auto_match),
                entity_records_by_id(state, entity_type, needed),
                # a committed bar under the cohesion bar must not be cut again by it
                min(_MIN_COHESION, float(auto_match)) if bar_moved else _MIN_COHESION,
            )
            new_clusters[entity_type] = store_clusters(
                run_id,
                entity_type,
                rebuilt,
                needs_review=sum(1 for c in rebuilt if c.get("required_human_review")),
                multi_record=sum(1 for c in rebuilt if len(c.get("record_ids", [])) > 1),
            )
        abandoned = feedback.get("approved") is False or str(
            feedback.get("decision") or ""
        ).strip().lower() in {"abort", "reject"}
        if not (keep_undecided or abandoned):  # a human answered at the gate: remember it (P9)
            _remember(
                state,
                {p for p in accepted if p in decided_now},
                {p for p in rejected if p in decided_now},
                entity_type,
            )
        new_stats[entity_type] = {
            **stats,
            **(
                {
                    "retained_pairs": retained,
                    "band_kept_apart": sorted(sorted(p) for p in rejected if len(p) == 2),
                }
                if applied
                else {}
            ),
            # Approving the gate keeps every undecided pair apart; memory (P9)
            # resolves only what it remembers and leaves the rest for the gate.
            "pending_merge_candidates": [
                p
                for p in stats.get("pending_merge_candidates") or []
                if keep_undecided
                and frozenset(str(r) for r in p.get("record_ids") or []) not in set(decided_now)
            ],
            "band_resolution": {
                # a pre-P15 checkpoint has no retained pairs: nothing was merged
                "accepted": len(accepted) if applied else 0,
                "kept_separate": len(rejected),
                "thresholds": shown,
            },
        }
    if not changed:
        return {}
    return {"entity_clusters": new_clusters, "resolution_stats": new_stats}
