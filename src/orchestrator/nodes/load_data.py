"""
load_data node: Upserts unified records into Databricks destination.

Applies field mappings, transforms records, and calls load_data_impl
for each entity type in dependency order.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from src.mcp_server.tools.load_data import load_data_impl, _get_destination_connector
from src.orchestrator.spool import (
    clusters_for,
    collection_count,
    collection_records,
    entity_origin_counts,
    entity_row_count,
    unified_for,
)
from src.orchestrator.utils.entity_aliases import resolve_dest_table

logger = logging.getLogger("merge-engine.orchestrator.load_data")

_RUN_REPORTS_DIR = Path("logs") / "run_reports"



# Pre-load guardrail (Phase 6.2) threshold: a scoped merge whose subject
# entity forms at least this many clusters with ZERO auto-merges is the
# exact signature of the Airbus 20->20 / Lockheed 9->9 matcher-collapse
# failures Phase 3 fixed — below this, a handful of genuinely-distinct
# records forming their own singleton clusters is normal, not a red flag.
_SCOPED_MERGE_GUARDRAIL_MIN_CLUSTERS = 5


def _scoped_merge_guardrail_failure(state: dict[str, Any]) -> dict[str, Any] | None:
    """None if the run is safe to load; a ``failure`` dict if it is not.

    Only ever fires for a scoped ("everything about X") merge, since an
    unscoped run has no single subject entity to judge unification against.
    If this fires after Phase 3's matcher fixes are in place, duplicates are
    about to ship — pausing here is cheaper than reviewing N rows in the
    destination that should have been one.
    """
    merge_scope = state.get("merge_scope") or {}
    if not merge_scope.get("active"):
        return None
    subject_entity = merge_scope.get("scope_type")
    if not subject_entity:
        return None

    clusters = clusters_for(state, subject_entity)
    if len(clusters) < _SCOPED_MERGE_GUARDRAIL_MIN_CLUSTERS:
        return None
    multi = [c for c in clusters if len(c.get("record_ids", [])) > 1]
    auto_merged = len(multi) - len([c for c in multi if c.get("required_human_review")])
    if auto_merged > 0:
        return None

    return {
        "node": "load_data",
        "error": (
            f"Scoped merge on {merge_scope.get('subject')!r} formed "
            f"{len(clusters)} clusters for '{subject_entity}' with zero "
            f"auto-merges among them — the destination would receive "
            f"{len(clusters)} separate rows for what is very likely the "
            f"same handful of real-world entities. Loading has been paused "
            f"for review rather than shipping the duplicates."
        ),
        "error_class": "DataQualityError",
        "disposition": "escalate",
        "context": {
            "subject_entity": subject_entity,
            "clusters_formed": len(clusters),
            "auto_merged_clusters": 0,
        },
    }


def build_reconciliation_report(
    state: dict[str, Any],
    load_results: dict[str, dict[str, int]],
    errors: list[str],
    failed_entities: list[str] | None = None,
    rolled_back: bool = False,
) -> dict[str, Any]:
    """Per-entity reconciliation numbers for the finished run.

    Counts extraction per origin, clustering outcomes, conflict decisions
    (with per-field breakdown), load results, and new columns — so the
    run can be audited against direct DB counts. Also grades the run's data
    quality (Phase 6.1): a red flag list plus a coarse letter grade, so a
    reviewer scanning the final-approval gate doesn't have to reconstruct
    "is this run actually healthy?" from the raw per-entity numbers by hand.
    """
    extracted_data: dict[str, list[dict]] = state.get("extracted_data", {}) or {}
    entity_clusters: dict[str, list[dict]] = state.get("entity_clusters", {}) or {}
    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {}) or {}
    pending_schema_changes: list[dict] = state.get("pending_schema_changes", []) or []
    field_mappings: dict[str, dict] = state.get("field_mappings", {}) or {}
    unmapped_fields: dict[str, list] = state.get("unmapped_fields", {}) or {}
    merge_scope: dict[str, Any] = state.get("merge_scope") or {}
    subject_entity = merge_scope.get("scope_type") if merge_scope.get("active") else None
    dropped_inferred_entities = list(state.get("dropped_inferred_entities") or [])

    entities: dict[str, Any] = {}
    entity_names = set(extracted_data) | set(entity_clusters) | set(unified_entities)
    families_loaded = 0
    red_flags: list[str] = []
    for entity in sorted(entity_names):
        per_origin = entity_origin_counts(state, entity)
        extracted_total = entity_row_count(state, entity)

        clusters = clusters_for(state, entity)
        multi = [c for c in clusters if len(c.get("record_ids", [])) > 1]
        human_reviewed = [c for c in clusters if c.get("required_human_review")]
        auto_merged = len(multi) - len([c for c in multi if c.get("required_human_review")])

        unified = unified_for(state, entity)
        conflict_fields: dict[str, int] = {}
        winning_rules: dict[str, int] = {}
        total_conflicts = 0
        for rec in unified:
            for conflict in rec.get("_conflicts", []) or []:
                if not isinstance(conflict, dict):
                    continue
                total_conflicts += 1
                f = conflict.get("field", "?")
                conflict_fields[f] = conflict_fields.get(f, 0) + 1
                rule = conflict.get("winning_rule", "priority")
                winning_rules[rule] = winning_rules.get(rule, 0) + 1

        result = load_results.get(entity, {})
        stats = (state.get("resolution_stats") or {}).get(entity, {})
        rows_inserted = result.get("inserted", 0)
        rows_updated = result.get("updated", 0)
        type_coercion_failures = result.get("type_coercion_failures", 0)

        # A field genuinely dropped from the load is one flagged unmapped
        # AND with no entry in field_mappings — the Phase 4.2 incompatible-
        # type redirect (a new *_raw column) already has a mapping entry, so
        # it is NOT dropped, only redirected; this only counts fields that
        # will not reach the destination unless a human intervenes at the
        # mapping-review gate.
        mapping_for_entity = field_mappings.get(entity, {}) or {}
        dropped_fields = [
            u.get("field") for u in (unmapped_fields.get(entity) or [])
            if isinstance(u, dict) and u.get("field") not in mapping_for_entity
        ]

        entity_report: dict[str, Any] = {
            "extracted_per_origin": per_origin,
            "extracted_total": extracted_total,
            "clusters_formed": len(clusters),
            # Candidate pairs the comparison budget could not reach. Non-zero
            # means duplicates may exist that were never even compared —
            # the reviewer approving this load needs to see that.
            "uncompared_pairs": stats.get("uncompared_pairs", 0),
            "candidate_pairs_compared": stats.get("candidate_pairs", 0),
            "auto_merged_clusters": auto_merged,
            "human_reviewed_clusters": len(human_reviewed),
            "unified_records": len(unified),
            "conflicts": total_conflicts,
            "conflict_fields": conflict_fields,
            "conflict_winning_rules": winning_rules,
            "rows_inserted": rows_inserted,
            "rows_updated": rows_updated,
            # Attempted (unified) but never landed — on a rolled-back load
            # every unified record counts as dropped, since load_results is
            # cleared to {} in that case.
            "rows_dropped": max(len(unified) - (rows_inserted + rows_updated), 0),
            "type_coercion_failures": type_coercion_failures,
            "fields_dropped_from_mapping": dropped_fields,
        }
        if entity == subject_entity and extracted_total:
            entity_report["subject_unification_ratio"] = round(
                len(unified) / extracted_total, 4
            )
        entities[entity] = entity_report

        if rows_inserted + rows_updated > 0:
            families_loaded += 1
        if type_coercion_failures:
            red_flags.append(
                f"{entity}: {type_coercion_failures} value(s) could not be "
                f"coerced to their destination column type"
            )
        if dropped_fields:
            red_flags.append(
                f"{entity}: {len(dropped_fields)} field(s) dropped from "
                f"mapping, pending human review"
            )
        ratio = entity_report.get("subject_unification_ratio")
        if (
            ratio is not None and ratio >= 0.95
            and len(clusters) >= _SCOPED_MERGE_GUARDRAIL_MIN_CLUSTERS
            and auto_merged == 0
        ):
            red_flags.append(
                f"{entity}: {len(clusters)} clusters formed with zero "
                f"auto-merges — likely duplicate records that never matched"
            )

    if rolled_back:
        red_flags.insert(0, "load rolled back — nothing was written to the destination")
    if dropped_inferred_entities:
        red_flags.append(
            f"{len(dropped_inferred_entities)} inferred entity type(s) had "
            f"no source table and were silently dropped: "
            f"{', '.join(dropped_inferred_entities)}"
        )

    if rolled_back or any("zero auto-merges" in f for f in red_flags):
        grade = "F"
    elif any("could not be coerced" in f or "dropped from mapping" in f for f in red_flags):
        grade = "C"
    elif red_flags:
        grade = "B"
    else:
        grade = "A"

    families_requested = len(state.get("target_entities") or entity_names)

    return {
        "run_id": state.get("run_id", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entities": entities,
        "new_columns": [
            {"table": c.get("table"), "column": c.get("column"), "data_type": c.get("data_type")}
            for c in pending_schema_changes
        ],
        "errors": list(errors),
        "load_failed_entities": list(failed_entities or []),
        "dropped_inferred_entities": dropped_inferred_entities,
        "families_requested": families_requested,
        "families_loaded": families_loaded,
        "completeness_pct": (
            round(100 * families_loaded / families_requested, 1)
            if families_requested else 0.0
        ),
        "data_quality": {"grade": grade, "red_flags": red_flags},
    }


def _write_run_report(report: dict[str, Any]) -> str | None:
    """Write the reconciliation report to logs/run_reports/<run_id>.json."""
    run_id = report.get("run_id") or "unknown_run"
    try:
        _RUN_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _RUN_REPORTS_DIR / f"{run_id}.json"
        path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        logger.info("Reconciliation report written to %s", path)
        return str(path)
    except Exception as exc:
        logger.warning("Could not write run report: %s", exc)
        return None


async def load_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Load unified data into the Databricks destination.

    Parameters
    ----------
    state : dict
        MergeState with ``unified_entities``, ``field_mappings``, ``destination``.

    Returns
    -------
    dict
        Update dict with ``errors`` key.
    """
    logger.debug("ENTER load_data_node")
    logger.debug("State keys: %s", list(state.keys()))

    # The final-approval gate is the last thing standing between a run and the
    # user's database. Routing alone is not enough: update_state/resume paths
    # can schedule this node with load_approval already False, so refuse here
    # too rather than trusting the edge. Anything other than an explicit True
    # writes nothing.
    if state.get("load_approval") is not True:
        reason = ((state.get("human_feedback") or {}).get("reason") or "").strip()
        logger.warning(
            "Load NOT approved (load_approval=%r) — writing nothing to the "
            "destination.%s",
            state.get("load_approval"),
            f" Reason: {reason}" if reason else "",
        )
        report = build_reconciliation_report(state, {}, list(state.get("errors", [])))
        report["load_rejected"] = True
        report["load_rejected_reason"] = reason
        _write_run_report(report)
        logger.debug("EXIT load_data_node (rejected)")
        return {
            "errors": state.get("errors", []),
            "reconciliation_report": report,
            "failure": {},
        }

    # Pre-load guardrail (Phase 6.2): a scoped merge whose subject entity
    # never auto-merged anything, despite forming enough clusters that it
    # almost certainly should have, is the exact failure signature Phase 3
    # was written to fix (Airbus 20->20, Lockheed 9->9). Checked before any
    # connector is opened — there is nothing to roll back here, just N
    # duplicate rows that should not ship.
    guardrail_failure = _scoped_merge_guardrail_failure(state)
    if guardrail_failure:
        logger.error(
            "Pre-load guardrail fired — refusing to load: %s",
            guardrail_failure["error"][:400],
        )
        report = build_reconciliation_report(state, {}, list(state.get("errors", [])))
        report["guardrail_fired"] = True
        _write_run_report(report)
        logger.debug("EXIT load_data_node (guardrail)")
        return {
            "errors": state.get("errors", []),
            "reconciliation_report": report,
            "failure": guardrail_failure,
        }

    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {})
    field_mappings: dict[str, dict] = state.get("field_mappings", {})
    destination = state.get("destination")
    extraction_order = state.get("extraction_order", [])
    errors: list[str] = list(state.get("errors", []))

    logger.debug("extraction_order: %s", extraction_order)
    logger.debug("unified_entities keys: %s", list(unified_entities.keys()))
    for ue in unified_entities:
        logger.debug("  Entity '%s': %d unified records",
                     ue, collection_count(unified_entities, ue))

    # Schema changes are applied INSIDE the load transaction below (Fix #4,
    # Phase 4.5) — not here. Applying them on their own connector, ahead of
    # the transaction, is exactly why a failed load used to leave the
    # destination schema permanently altered (+N columns, a new table) even
    # though every row of data rolled back: the ALTERs had already committed
    # on a connection the transaction never owned.
    pending_schema_changes = state.get("pending_schema_changes", [])

    # Process every unified entity, dependency-ordered entities first.
    # unified_entities is keyed by concept names, which may not appear in
    # extraction_order (that holds table-level names).
    entity_order = [e for e in extraction_order if e in unified_entities]
    entity_order += [e for e in unified_entities if e not in entity_order]
    load_results: dict[str, dict[str, int]] = {}
    failed_entities: list[str] = []

    # One connector, one transaction, for every entity. A merge that writes
    # `person` and then fails on `company` used to leave `person` committed:
    # a destination that matches no origin and that the next run has to
    # reconcile against. Either the whole merge lands or none of it does.
    #
    # list_tables() doubles as the connection probe. Connectors construct
    # lazily, so without actually talking to the database here a dead
    # destination would sail past this block and only fail once we were
    # already inside the transaction.
    available_dest_tables: list[str] = []
    load_connector = None
    atomic = _null_transaction
    try:
        candidate = _get_destination_connector(
            destination if isinstance(destination, dict) else None
        )
        available_dest_tables = candidate.list_tables()
        logger.debug("Destination tables available: %s", available_dest_tables)
        load_connector = candidate
        if hasattr(load_connector, "atomic"):
            atomic = load_connector.atomic
        else:
            logger.warning(
                "Destination connector %s has no transaction support — a "
                "mid-merge failure can leave it partially written.",
                type(load_connector).__name__,
            )
    except Exception as exc:
        logger.warning(
            "Could not open a shared destination connector (%s); falling "
            "back to per-entity connections, which are not atomic.", exc,
        )
        load_connector = None
        atomic = _null_transaction

    # A dry run does the entire load and then rolls it back, so the report
    # describes exactly what would have been written — real counts against
    # the real schema, with the destination untouched.
    dry_run = bool(state.get("dry_run"))
    if dry_run:
        logger.info("DRY RUN: the load will be rolled back, not committed.")

    rolled_back = False
    try:
        with atomic(dry_run=dry_run) if dry_run else atomic():
            if pending_schema_changes:
                logger.info(
                    "Applying %d pending schema change(s) inside the load "
                    "transaction...", len(pending_schema_changes),
                )
                logger.debug(
                    "Schema changes: %s",
                    json.dumps(pending_schema_changes, default=str)[:500],
                )
                await _apply_schema_changes(
                    destination, pending_schema_changes, errors,
                    connector=load_connector,
                )
                if load_connector is not None:
                    # A schema change may have created a brand-new table
                    # (ensure_table) that the earlier connectivity-probe
                    # list_tables() call could not have seen yet.
                    available_dest_tables = load_connector.list_tables()

            await _load_all_entities(
                entity_order=entity_order,
                unified_entities=unified_entities,
                field_mappings=field_mappings,
                destination=destination,
                available_dest_tables=available_dest_tables,
                connector=load_connector,
                load_results=load_results,
                failed_entities=failed_entities,
                errors=errors,
                state=state,
            )

            # Record which entity each source record ended up in. Inside the
            # transaction on purpose: if the load rolls back the map must roll
            # back with it, or the next run reuses ids for rows that were
            # never written and silently duplicates them.
            if not failed_entities:
                _persist_entity_map(
                    state, destination, set(load_results), connector=load_connector
                )
            else:
                # Raising is what triggers the rollback: everything written
                # in this transaction, including earlier entities, is undone.
                raise _LoadAborted(
                    f"{len(failed_entities)} entity/entities failed to load: "
                    f"{', '.join(failed_entities)}"
                )
    except _LoadAborted as exc:
        rolled_back = True
        logger.error(
            "Load rolled back — the destination is unchanged. %s", exc
        )
    except Exception as exc:
        rolled_back = True
        msg = f"Load transaction failed and was rolled back: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
        if not failed_entities:
            failed_entities = list(entity_order)
    finally:
        if load_connector is not None:
            try:
                load_connector.close()
            except Exception:
                pass

    if rolled_back:
        # Nothing landed, so no entity may claim inserted/updated counts.
        # Every attempted entity is reported as not loaded, not just the one
        # that raised: the earlier entities were rolled back with it, and
        # listing only the culprit would imply the others are safely in the
        # database. That is exactly the confident-but-wrong summary Phase M
        # exists to stop.
        load_results = {}
        failed_entities = [
            e for e in entity_order if collection_count(unified_entities, e)
        ] or list(dict.fromkeys(failed_entities))

    report = build_reconciliation_report(
        state, load_results, errors, failed_entities, rolled_back=rolled_back,
    )
    report["load_rolled_back"] = rolled_back
    if dry_run:
        # Distinguished from a failure rollback: the numbers are real and
        # the destination is untouched *by design*.
        report["dry_run"] = True
    _write_run_report(report)

    # Fix #4/#6 (Phase 4.6): a rolled-back load used to flow straight to
    # update_extraction_state as if nothing had happened — NODE_SUCCESSOR
    # already routes load_data through the same _fallible_router every other
    # node uses, but this node never populated `failure`, so that router had
    # nothing to catch. "escalate" (not "fail") because the run COULD still
    # be salvaged — a human can retry the load or knowingly skip it — the
    # destination itself is not unreachable, just this attempt failed.
    failure: dict[str, Any] = {}
    if rolled_back:
        failure = {
            "node": "load_data",
            "error": (
                "The load transaction failed and was rolled back — the "
                "destination is unchanged. " + "; ".join(errors[-3:])
            ),
            "error_class": "DataQualityError",
            "disposition": "escalate",
            "context": {
                "failed_entities": failed_entities,
                "pending_schema_changes": len(pending_schema_changes),
            },
        }
        logger.error("load_data failing loudly: %s", failure["error"][:400])

    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT load_data_node")

    return {"errors": errors, "reconciliation_report": report, "failure": failure}


class _LoadAborted(RuntimeError):
    """Raised to roll the load transaction back after a failed entity."""


@contextmanager
def _null_transaction():
    """Stand-in when the destination cannot give us a real transaction."""
    yield


def _source_id_to_cluster_id(state: dict[str, Any], entity_type: str) -> dict[str, str]:
    """Raw source-record id -> the destination id that record's cluster got.

    A record id is spooled as ``origin:table:row_id``; a foreign-key column in
    another entity's record holds only the bare ``row_id``, so both forms are
    indexed here.
    """
    lookup: dict[str, str] = {}
    for cluster in clusters_for(state, entity_type):
        cluster_id = cluster.get("cluster_id")
        if not cluster_id:
            continue
        for record_id in cluster.get("record_ids", []):
            lookup[record_id] = cluster_id
            bare = str(record_id).rsplit(":", 1)[-1]
            lookup.setdefault(bare, cluster_id)
    return lookup


async def _load_all_entities(
    *,
    entity_order: list[str],
    unified_entities: dict[str, list[dict]],
    field_mappings: dict[str, dict],
    destination: Any,
    available_dest_tables: list[str],
    connector: Any,
    load_results: dict[str, dict[str, int]],
    failed_entities: list[str],
    errors: list[str],
    state: dict[str, Any] | None = None,
) -> None:
    """Load every entity through the shared connector/transaction.

    Records are pulled from the spool one entity at a time, so peak memory
    is one entity's merged output rather than every entity at once.
    """
    # Destination table -> {source id: cluster id}, built lazily per referenced
    # table and reused across entities that point at the same parent.
    cluster_lookup_by_table: dict[str, dict[str, str]] = {}

    # Resolved once for every entity up front, not as each is loaded — a
    # child can be loaded before its parent (company before industry, per
    # extraction order), so FK resolution must be able to look up ANY
    # entity's table regardless of where the loop currently is.
    entity_type_by_dest_table: dict[str, str] = {}
    for entity_type in entity_order:
        dest_tables = getattr(destination, 'tables', {}) if hasattr(destination, 'tables') else (destination.get('tables', {}) if isinstance(destination, dict) else {})
        candidate_table = dest_tables.get(entity_type, entity_type) if isinstance(dest_tables, dict) else entity_type
        if available_dest_tables:
            resolved = resolve_dest_table(entity_type, available_dest_tables)
            if resolved:
                candidate_table = resolved
        entity_type_by_dest_table[candidate_table] = entity_type

    for entity_type in entity_order:
        records = collection_records(unified_entities, entity_type, kind="unified")
        logger.debug("Processing entity '%s': %d records", entity_type, len(records))
        if not records:
            logger.info("No records to load for entity '%s', skipping.", entity_type)
            continue

        mapping = field_mappings.get(entity_type, {})
        logger.debug("Field mapping for '%s': %d mappings", entity_type, len(mapping))
        dest_tables = getattr(destination, 'tables', {}) if hasattr(destination, 'tables') else (destination.get('tables', {}) if isinstance(destination, dict) else {})
        dest_table = dest_tables.get(entity_type, entity_type) if isinstance(dest_tables, dict) else entity_type
        if available_dest_tables:
            resolved = resolve_dest_table(entity_type, available_dest_tables)
            if resolved:
                dest_table = resolved
        logger.debug("Destination table: %s", dest_table)

        # Foreign keys are resolved by load_data_impl AFTER it applies the
        # field mapping — only then do records carry destination column names
        # like "industry_id" that a raw source record (keyed "sourceCategory")
        # does not have yet.
        foreign_keys: dict[str, str] = {}
        if state is not None and connector is not None and hasattr(connector, "get_foreign_keys"):
            try:
                foreign_keys = connector.get_foreign_keys(dest_table)
            except Exception as exc:
                logger.warning("Could not read foreign keys for '%s': %s", dest_table, exc)
                foreign_keys = {}
            for referenced_table in set(foreign_keys.values()):
                if referenced_table in cluster_lookup_by_table:
                    continue
                referenced_entity = entity_type_by_dest_table.get(
                    referenced_table, referenced_table
                )
                cluster_lookup_by_table[referenced_table] = _source_id_to_cluster_id(
                    state, referenced_entity
                )

        # Pass raw unified records through — load_data_impl applies the
        # field mapping itself (transforming here too would rename fields
        # twice, nulling every renamed column).
        logger.debug("Prepared %d load records for entity '%s'", len(records), entity_type)

        try:
            logger.debug("Calling load_data_impl for entity '%s'", entity_type)
            result = await load_data_impl(
                entity_type=entity_type,
                records=records,
                mapping=mapping,
                destination_config=destination,
                table_name=dest_table,
                connector=connector,
                foreign_keys=foreign_keys,
                cluster_lookup_by_table=cluster_lookup_by_table,
            )
            logger.debug("load_data_impl result: %s", json.dumps(result, default=str)[:300])

            load_results[entity_type] = {
                "total": result.get("total_records", 0),
                "inserted": result.get("inserted", 0),
                "updated": result.get("updated", 0),
                "type_coercions_attempted": result.get("type_coercions_attempted", 0),
                "type_coercion_failures": result.get("type_coercion_failures", 0),
            }
            logger.info(
                "Loaded entity '%s': %d total, %d inserted, %d updated",
                entity_type,
                result.get("total_records", 0),
                result.get("inserted", 0),
                result.get("updated", 0),
            )

        except Exception as exc:
            msg = f"Load failed for entity '{entity_type}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)
            failed_entities.append(entity_type)
            # Stop at the first failure. Inside a transaction the connection
            # is already in an aborted state, so every later statement would
            # fail anyway with a misleading "current transaction is aborted"
            # rather than the error that actually caused this.
            return


def _persist_entity_map(
    state: dict[str, Any],
    destination: Any,
    entity_types: set[str],
    connector: Any = None,
) -> None:
    """Save each source record's cluster assignment for future runs.

    This is what makes a re-run idempotent under incremental extraction: the
    next run reuses these ids instead of deriving new ones from whatever
    subset of records it happens to see, so existing destination rows are
    updated rather than duplicated.
    """
    if not entity_types or not isinstance(destination, dict):
        return
    # A caller-supplied connector means we are inside the load's transaction,
    # so a failure here can (and must) roll the load back. Without one there
    # is nothing to undo.
    in_transaction = connector is not None
    try:
        if connector is None:
            connector = _get_destination_connector(destination)
        if not hasattr(connector, "save_entity_map"):
            return
        run_id = str(state.get("run_id", ""))
        saved = 0
        for entity_type in entity_types:
            assignments = {
                record_id: cluster.get("cluster_id", "")
                for cluster in clusters_for(state, entity_type)
                for record_id in cluster.get("record_ids", [])
                if cluster.get("cluster_id")
            }
            saved += connector.save_entity_map(entity_type, assignments, run_id)
        if saved:
            logger.info("Persisted %d source-record -> entity assignments", saved)
    except Exception as exc:
        if in_transaction:
            # Inside the load's transaction there is something to undo, and
            # undoing it is right: without the map the next run derives fresh
            # cluster ids and duplicates every row, so a load whose map did
            # not persist is not a load worth keeping.
            logger.error(
                "Could not persist the entity map (%s) — rolling the load "
                "back, because without it a re-run would duplicate rows.", exc,
            )
            raise
        # No transaction to roll back: the rows are already committed.
        # Claiming a rollback here would be a straight lie about the state
        # of the user's database.
        logger.error(
            "Could not persist the entity map (%s). The load is already "
            "committed, so it stands — but a re-run may insert duplicate "
            "rows instead of updating.", exc,
        )


async def _apply_schema_changes(
    destination: Any,
    changes: list[dict],
    errors: list[str],
    connector: Any = None,
) -> None:
    """Apply pending ALTER TABLE ADD COLUMN statements to the destination.

    Fix #4 (Phase 4.5, merge-engine-remediation-1-to-6): when ``connector``
    is given, it is the SAME connector the caller is about to load through —
    reusing it, rather than opening a second connection, is what lets these
    ALTERs join the caller's `atomic()` transaction and roll back with the
    data if the load fails afterward. PostgreSQL DDL is transactional, and
    the connector's `_commit` is already a no-op while an enclosing `atomic()`
    block owns the transaction, so no connector changes were needed — only
    calling it with the load's own connector instead of a fresh one.

    Without a connector (the shared-connector-unavailable fallback), a fresh
    one is opened here and closed after use; those ALTERs commit immediately
    and will NOT roll back if the load fails later — logged explicitly so
    that gap is visible rather than assumed away.
    """
    dest_type = destination.type if hasattr(destination, "type") else (destination.get("type", "postgresql") if isinstance(destination, dict) else "postgresql")
    logger.debug("Applying schema changes to destination type '%s'", dest_type)
    logger.debug("Schema changes count: %d", len(changes))

    owns_connector = connector is None
    try:
        if owns_connector:
            dest_config = destination.model_dump() if hasattr(destination, "model_dump") else (destination.connection.model_dump() if hasattr(destination, "connection") else destination)
            logger.debug("Creating a standalone destination connector for schema extension")
            from src.mcp_server.tools.load_data import _get_destination_connector
            connector = _get_destination_connector(
                dest_config if isinstance(dest_config, dict) else None
            )
            logger.warning(
                "Schema changes are being applied on a standalone connector, "
                "outside the load transaction — they will NOT roll back if "
                "the load fails afterward."
            )
        if hasattr(connector, "ensure_table"):
            for table in {c["table"] for c in changes}:
                connector.ensure_table(table)
        logger.debug("Calling connector.extend_schema_batch with %d changes", len(changes))
        result = connector.extend_schema_batch(changes)
        logger.debug("extend_schema_batch result: %s", json.dumps(result, default=str))
        logger.info(
            "Schema extension: %d added, %d failed",
            result.get("added", 0),
            result.get("failed", 0),
        )
        for err in range(result.get("failed", 0)):
            errors.append("Schema change failed")
    except Exception as exc:
        msg = f"Schema extension failed: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
    finally:
        if owns_connector and connector is not None:
            try:
                connector.close()
            except Exception:
                pass


async def update_extraction_state_node(state: dict[str, Any]) -> dict[str, Any]:
    """Persist the watermarks observed during extraction this run.

    Origins are read-only — nothing is written back to them. Incremental
    state lives entirely in the destination's merge_engine_extraction_state
    tracking table (one row per origin/table, not one per extracted row),
    so this is O(number of tables) regardless of how large the origins are.

    Parameters
    ----------
    state : dict
        MergeState with ``extraction_watermarks``, ``destination``, ``run_id``.

    Returns
    -------
    dict
        Update dict with ``errors`` key.
    """
    logger.debug("ENTER update_extraction_state_node")
    logger.debug("State keys: %s", list(state.keys()))
    extraction_watermarks: dict[str, Any] = state.get("extraction_watermarks", {})
    destination = state.get("destination", {})
    run_id = state.get("run_id", "")
    errors: list[str] = list(state.get("errors", []))

    logger.debug("Tables with observed watermarks: %d", len(extraction_watermarks))

    if not extraction_watermarks:
        logger.debug("EXIT update_extraction_state_node (nothing to persist)")
        return {"errors": errors}

    # Advancing the watermark after a failed load would make the next run skip
    # those rows entirely — the data would never reach the destination.
    failed_entities = (state.get("reconciliation_report") or {}).get("load_failed_entities", [])
    if failed_entities:
        logger.warning(
            "Not advancing extraction watermarks — load failed for: %s. "
            "The next run will re-extract these rows.",
            ", ".join(failed_entities),
        )
        return {"errors": errors}

    try:
        connector = _get_destination_connector(destination if isinstance(destination, dict) else None)
    except Exception as exc:
        msg = f"Could not create destination connector for extraction-state update: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
        logger.debug("EXIT update_extraction_state_node (connector creation failed)")
        return {"errors": errors}

    for tracking_key, entry in extraction_watermarks.items():
        try:
            connector.update_extraction_state(
                origin_id=entry["origin_id"],
                table_name=entry["table_name"],
                watermark_column=entry.get("watermark_column"),
                watermark_kind=entry.get("watermark_kind"),
                last_value=entry.get("last_value"),
                run_id=run_id,
                rows_extracted=entry.get("rows_extracted", 0),
            )
            logger.info(
                "Updated extraction state for '%s': watermark %s=%s (%d rows)",
                tracking_key, entry.get("watermark_column"), entry.get("last_value"),
                entry.get("rows_extracted", 0),
            )
        except Exception as exc:
            msg = f"Failed to update extraction state for '{tracking_key}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT update_extraction_state_node")
    return {"errors": errors}