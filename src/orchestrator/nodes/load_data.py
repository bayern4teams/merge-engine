"""
load_data node: Upserts unified records into the configured destination.

Applies field mappings, transforms records, and calls load_data_impl
for each entity type in dependency order.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import statistics
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.mcp_server.tools.cluster_quality import quality_signals
from src.mcp_server.tools.load_data import _get_destination_connector, load_data_impl
from src.mcp_server.tools.resolve_entities import _NAME_FIELD_HINTS, _best_name_field, _record_id
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

    run_id = state.get("run_id", "")
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
        numeric_disagreements: dict[str, dict[str, Any]] = {}
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
                # P18-5: numeric_max/min resolved, but the sources disagreed.
                nd = conflict.get("numeric_disagreement")
                if isinstance(nd, dict):
                    agg = numeric_disagreements.setdefault(
                        f, {"clusters": 0, "max_spread": 0, "max_relative_spread": 0}
                    )
                    agg["clusters"] += 1
                    agg["max_spread"] = max(agg["max_spread"], nd["spread"])
                    agg["max_relative_spread"] = max(
                        agg["max_relative_spread"], nd["relative_spread"]
                    )

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
            u.get("field")
            for u in (unmapped_fields.get(entity) or [])
            if isinstance(u, dict) and u.get("field") not in mapping_for_entity
        ]

        # P16: label-free quality signals (a flag, never a cut); B-cubed needs the
        # previous run's clustering, kept in the file named by the
        # ``quality_history_path`` state key, else ``MERGE_ENGINE_QUALITY_HISTORY``
        # (same convention as P9's decision memory).
        records = collection_records(state.get("extracted_data"), entity)
        record_names = {_record_id(r): r.get(_best_name_field(r) or "") for r in records}
        history = state.get("quality_history_path") or os.environ.get(
            "MERGE_ENGINE_QUALITY_HISTORY"
        )
        quality = quality_signals(clusters, record_names, history, entity, str(run_id or ""))

        entity_report: dict[str, Any] = {
            "quality": quality,
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
        if numeric_disagreements:
            entity_report["numeric_disagreements"] = numeric_disagreements
        if entity == subject_entity and extracted_total:
            entity_report["subject_unification_ratio"] = round(len(unified) / extracted_total, 4)
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
            ratio is not None
            and ratio >= 0.95
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

    families_requested = len(state.get("target_entities") or entity_names)
    # Regression guard: a target entity family that loaded zero rows used
    # to be invisible to this grading function entirely -- Grade A could
    # print ("no red flags. All target entity families extracted and
    # loaded cleanly") in the same run where the final-approval gate's own
    # zero-row-families banner and validate_data's "fail" severity were
    # both showing, because neither fed into red_flags or the grade rule
    # table below. Live-observed: 3 zero-row families, Grade A.
    zero_row_families = sorted(
        e
        for e in entity_names
        if entities[e].get("rows_inserted", 0) + entities[e].get("rows_updated", 0) == 0
    )
    if zero_row_families:
        red_flags.append(
            f"{len(zero_row_families)} entity famil{'y' if len(zero_row_families) == 1 else 'ies'} "
            f"loaded zero rows: {', '.join(zero_row_families)}"
        )
    # validate_data's own severity (Phase 4.5) -- computed independently of
    # everything above, and previously never consulted by the grade at all.
    # quality_report's shape is {status, severity, entities: {entity: {...}}}
    # (data_quality.run_data_quality_checks) -- per-entity severities live
    # under "entities", not at the top level.
    quality_report: dict[str, Any] = state.get("quality_report") or {}
    has_quality_failure = bool(state.get("has_quality_failure"))
    if has_quality_failure:
        failing = sorted(
            e
            for e, r in (quality_report.get("entities") or {}).items()
            if isinstance(r, dict) and r.get("severity") == "fail"
        )
        red_flags.append(
            "data-quality check failed for: "
            + (", ".join(failing) if failing else "one or more entities")
        )

    if rolled_back or has_quality_failure or any("zero auto-merges" in f for f in red_flags):
        grade = "F"
    elif zero_row_families or any(
        "could not be coerced" in f or "dropped from mapping" in f for f in red_flags
    ):
        grade = "C"
    elif red_flags:
        grade = "B"
    else:
        grade = "A"

    return {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
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
            round(100 * families_loaded / families_requested, 1) if families_requested else 0.0
        ),
        "data_quality": {"grade": grade, "red_flags": red_flags},
        # Per-node wall-clock seconds, merged in by the _timed() wrapper in
        # graph.py's _build_builder() (Phase 0 Sec 0.6). Compared against
        # docs/verification/BUDGETS.yaml by drive_app.py --assert-budgets.
        "timings": dict(state.get("timings") or {}),
    }


def _resolve_dest_table(destination: Any, entity_type: str, available: list[str]) -> str:
    """The destination table an entity loads into (configured name, then alias match)."""
    dest_tables = (
        getattr(destination, "tables", {})
        if hasattr(destination, "tables")
        else (destination.get("tables", {}) if isinstance(destination, dict) else {})
    )
    table = (
        dest_tables.get(entity_type, entity_type) if isinstance(dest_tables, dict) else entity_type
    )
    return (resolve_dest_table(entity_type, available) if available else None) or table


def read_destination_schemas(connector: Any) -> dict[str, dict[str, Any]]:
    """``{table: get_table_schema(table)}`` for every destination table (the I/O half of
    :func:`destination_schema_defects`; a connector that cannot introspect yields ``{}``)."""
    try:
        return {t: connector.get_table_schema(t) for t in connector.list_tables()}
    except Exception as exc:
        logger.warning("Could not read destination schemas for the defect check: %s", exc)
        return {}


def preview_destination_defects(state: dict[str, Any]) -> list[dict[str, Any]]:
    """:func:`destination_schema_defects` for the pre-load gate payload (P17): opens the
    destination read-only, introspects, closes. ``held_links`` is left out (it grows with the
    row count; the load's own report keeps it). An unreachable destination yields ``[]``: the
    load itself then reports the connection failure."""
    destination = state.get("destination")
    try:
        connector = _get_destination_connector(
            destination if isinstance(destination, dict) else None
        )
    except Exception as exc:
        logger.warning("Pre-load defect check: no destination connector (%s)", exc)
        return []
    try:
        defects = destination_schema_defects(state, read_destination_schemas(connector))
    finally:
        with contextlib.suppress(Exception):
            connector.close()
    return [{k: v for k, v in d.items() if k != "held_links"} for d in defects]


# P10: the pre-load gate's invariants, by role. Coverage is a floor, the rest are ceilings.
_COVERAGE = "mapping_coverage"
_NULL_IDENTITY = "null_identity_share"
_LARGEST_CLUSTER = "largest_cluster_share"
_FLAGGED = "flagged_clusters"


def _entity_measures(state: dict[str, Any], entity: str) -> dict[str, float]:
    """One entity's raw numbers for the pre-load invariants (absent = not measurable)."""
    out: dict[str, float] = {}
    records = collection_records(state.get("extracted_data") or {}, entity)
    fields = {k for r in records for k in r if not k.startswith("_")}
    mapped = len((state.get("field_mappings") or {}).get(entity) or {})
    unmapped = len((state.get("unmapped_fields") or {}).get(entity) or [])
    # source fields = those the mapper listed, or those the records carry if it listed fewer
    # (an entity the mapper skipped entirely still has fields, and maps none of them)
    width = max(mapped + unmapped, len(fields))
    if width:
        out["fields"] = width
        out[_COVERAGE] = mapped / width
    # ONE identity field per entity: the best-ranked name hint, an id-like column last
    # (``company_id``/``org_type`` populated must not hide a NULL ``name``)
    identity = min(
        (k for k in fields if any(h in k.lower() for h in _NAME_FIELD_HINTS)),
        key=lambda k: (
            min(i for i, h in enumerate(_NAME_FIELD_HINTS) if h in k.lower()),
            k.lower().endswith("id"),
            len(k),
            k,
        ),
        default=None,
    )
    if records:
        out["records"] = len(records)
    if records and identity:
        out[_NULL_IDENTITY] = sum(not _has_value(r.get(identity)) for r in records) / len(records)
    clusters = clusters_for(state, entity)
    n = sum(len(c.get("record_ids", [])) for c in clusters)
    if n:
        # merges absorbed by the largest cluster, of all merges possible: a lone record is 0
        largest = max(len(c.get("record_ids", [])) for c in clusters)
        out["largest"] = largest
        out["clustered"] = n
        out[_LARGEST_CLUSTER] = (largest - 1) / (n - 1) if n > 1 else 0.0
        names = {_record_id(r): r.get(_best_name_field(r) or "") for r in records}
        out[_FLAGGED] = quality_signals(clusters, names)["flagged_count"]
    return out


def pre_load_gate(state: dict[str, Any], confirmed: Any = None) -> dict[str, Any]:
    """The pre-load invariants (report P10): measured over the run, each with a
    threshold the run proposes from its own entities, and -- when the operator
    confirmed or edited thresholds at the final gate (``confirmed``: {name: number})
    -- judged against those instead. Read-only; the same call builds the gate
    payload and decides the load.

    Proposals (one per invariant per run; m = the median across entities):
    coverage floor = m / 2, never below half of one mapped field of the widest
    entity (a run where nothing maps still fails); NULL-identity and largest-
    cluster ceilings = 2 m, never below the slack of the smallest entity (one
    NULL record; a cluster twice the median entity's largest), never a majority;
    flagged ceiling = 2 m of the per-entity flagged counts.

    ponytail: refusing is all it can do -- it cannot roll back a partial write
    (report P10 Ceiling); the proposals are rules of thumb relative to the run's
    own entities, so a run whose entities are ALL bad alike is caught only by the
    floors above or by the operator. Deferred: thresholds learnt from past runs
    (report P10 Deferred; trigger: enough confirmed runs in P16's history)."""
    per = {
        e: _entity_measures(state, e)
        for e in sorted(
            set(state.get("extracted_data") or {})
            | set(state.get("entity_clusters") or {})
            | set(state.get("unified_entities") or {})
            | set(state.get("field_mappings") or {})
        )
    }

    def values(key: str) -> list[float]:
        return [m[key] for m in per.values() if key in m]

    # slack: one record of the SMALLEST entity (NULLs), and a cluster twice the median
    # entity's largest in the smallest entity (merges) -- a small healthy lookup beside
    # large tables must not be refused for being small
    sizes = values("records")
    one_record = 1 / min(sizes) if sizes else 0.0
    clustered = values("clustered")
    typical = 2 * statistics.median(values("largest")) - 1 if clustered else 0.0
    small = min(clustered, default=0.0)
    one_cluster = min(typical / (small - 1), 1.0) if small > 1 else 0.0
    widest = max(values("fields"), default=0.0)

    def ceiling(xs: list[float], slack: float) -> float:
        return min(max(2 * statistics.median(xs), slack), 1 / 2) if xs else 1.0

    cov, nul, big, flg = (
        values(k) for k in (_COVERAGE, _NULL_IDENTITY, _LARGEST_CLUSTER, _FLAGGED)
    )
    proposed = {
        _COVERAGE: max(statistics.median(cov) / 2, 1 / (2 * widest)) if cov else 0.0,
        _NULL_IDENTITY: ceiling(nul, one_record),
        _LARGEST_CLUSTER: ceiling(big, one_cluster),
        _FLAGGED: 2 * statistics.median(flg) if flg else 0.0,
    }
    measured = {
        _COVERAGE: min(cov, default=1.0),
        _NULL_IDENTITY: max(nul, default=0.0),
        _LARGEST_CLUSTER: max(big, default=0.0),
        _FLAGGED: max(flg, default=0.0),
    }
    edits = confirmed if isinstance(confirmed, dict) else {}
    invariants: dict[str, Any] = {}
    unusable: list[str] = []
    for name, threshold in proposed.items():
        used = edits.get(name, threshold)
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
            unusable.append(name)  # garbage never disables an invariant: fail closed
            used = threshold
        invariants[name] = {
            "measured": measured[name],
            "threshold": threshold,
            "violated": measured[name] < used if name == _COVERAGE else measured[name] > used,
        }
        if name in edits and name not in unusable:
            invariants[name]["confirmed"] = used
    worst = {
        name: sorted(e for e, m in per.items() if name in m and m[name] == measured[name])
        for name in proposed
    }
    return {
        "invariants": invariants,
        "entities_at_worst": worst,
        "unusable_edits": unusable,
        "would_fail": any(v["violated"] for v in invariants.values()) or bool(unusable),
    }


def _confirmed_thresholds(state: dict[str, Any]) -> Any:
    """The thresholds the operator confirmed at the final gate (``thresholds`` on the
    approve feedback), or None. Read here, not in the gate body: with interrupt_before
    the gate body never runs on resume (plan 2.6)."""
    feedback = state.get("human_feedback") or {}
    if feedback.get("node") in (None, "human_final_approval") and "thresholds" in feedback:
        return feedback["thresholds"]
    # kept in state by load_data, so a retry through the error gate keeps the confirmation
    return state.get("pre_load_thresholds")


def _pre_load_failure(gate: dict[str, Any]) -> dict[str, Any] | None:
    """A ``fail`` disposition (no error-gate "skip" that would walk on as if loaded)
    naming every violated invariant, or None when the load may proceed."""
    if not gate["would_fail"]:
        return None
    bad = [
        f"{name} = {v['measured']:.3g} (threshold {v.get('confirmed', v['threshold']):.3g}; "
        f"worst: {', '.join(gate['entities_at_worst'][name]) or '-'})"
        for name, v in gate["invariants"].items()
        if v["violated"]
    ]
    bad += [f"{name}: confirmed threshold is not a number" for name in gate["unusable_edits"]]
    return {
        "node": "load_data",
        "error": "Pre-load invariants violated, nothing was written: " + "; ".join(bad),
        "error_class": "DataQualityError",
        "disposition": "fail",
        "context": {"pre_load_gate": gate},
    }


def _has_value(v: Any) -> bool:
    return v is not None and bool(str(v).strip())


def destination_schema_defects(
    state: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Destination tables that cannot hold the links this run would write (report P17).

    PURE: no connector, no DDL. ``schemas`` is ``{table: {"columns": [{"name": ...}, ...],
    ...}}`` for every destination table (see :func:`read_destination_schemas`);
    the keys double as the available-table list. Callable from a gate payload builder, which
    cannot rely on this node's body running.

    A table is defective when it cannot hold what this run maps into it: a mapped
    ``dest_column`` it does not declare, or (rows that would be pure key) none of the columns
    it declares beyond its primary key (``schema["primary_key"]``, else ``id``) and ``_``
    provenance columns is fed by the mapping while the run carries values for some column the
    table lacks. A table that does not exist while the destination has others is the same
    case with no declared columns (``ensure_table`` would create a bare key table). An
    unmapped field on a table that receives real payload is the normal ``unmapped_fields``
    path, not a defect. Needed columns come from the run itself (mapping ``dest_column`` for
    this table, plus the ``unmapped_fields`` the records carry); missing is needed minus
    declared. Returns one JSON-safe dict per defective entity, whose whole entity is withheld::

        {"entity", "table", "missing_columns": [...], "unprojectable_rows": int,
         "held_links": [{"id": ..., "values": {missing column: value}}, ...]}

    ``unprojectable_rows`` counts the withheld rows that carry a value in any needed column
    (a row with no value anywhere holds no data to lose); ``held_links`` lists those with a
    value in a missing column.

    ponytail: Ceiling - a mapped column the table lacks empties the whole entity rather than
    landing it partially (no repair, by spec); with no destination table at all (``schemas`` empty,
    indistinguishable from a failed read) nothing is reported; a composite key is judged by
    its first column. Deferred: hold the links in the corpus and re-project once the schema's
    owner fixes the DDL (report §3.4); trigger = the corpus lands. ``held_links`` grows with
    the row count until then.
    """
    destination = state.get("destination")
    unified = state.get("unified_entities") or {}
    available = list(schemas)
    defects: list[dict[str, Any]] = []
    for entity in unified:
        table = _resolve_dest_table(destination, entity, available)
        schema = schemas.get(table)
        declared = {c["name"] for c in (schema or {}).get("columns") or []}
        if not schemas or (schema is not None and not declared):
            continue  # nothing introspected, or a table the connector reports with no columns
        schema = schema or {}
        # needed destination column -> the source field that feeds it
        mapped: dict[str, str] = {}
        for src, info in ((state.get("field_mappings") or {}).get(entity) or {}).items():
            if isinstance(info, dict) and (info.get("dest_table") or table) == table:
                mapped.setdefault(info.get("dest_column") or src, src)
        needed = dict(mapped)
        for u in (state.get("unmapped_fields") or {}).get(entity) or []:
            if isinstance(u, dict) and u.get("field"):
                needed.setdefault(u["field"], u["field"])
        key = schema.get("primary_key") or "id"
        fed = {c for c in declared if c != key and not c.startswith("_")} & set(mapped)
        # a mapped column the table lacks always counts; unmapped fields only when no payload
        # column is fed (an unmapped field on a healthy table is the normal unmapped_fields path)
        missing = sorted(set(mapped if fed else needed) - declared)
        if not missing:
            continue
        held, rows = [], 0
        for rec in collection_records(unified, entity, kind="unified"):
            rows += any(_has_value(rec.get(needed[c])) for c in needed)
            if any(_has_value(rec.get(needed[c])) for c in missing):
                held.append(
                    {
                        "id": rec.get("_cluster_id") or rec.get("id"),
                        "values": {c: rec.get(needed[c]) for c in missing},
                    }
                )
        if held:
            defects.append(
                {
                    "entity": entity,
                    "table": table,
                    "missing_columns": missing,
                    "unprojectable_rows": rows,
                    "held_links": held,
                }
            )
    safe: list[dict[str, Any]] = json.loads(json.dumps(defects, default=str))  # JSON-safe values
    return safe


def _write_run_report(report: dict[str, Any]) -> str | None:
    """Write the reconciliation report to logs/run_reports/<run_id>.json."""
    run_id = report.get("run_id") or "unknown_run"
    try:
        _RUN_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _RUN_REPORTS_DIR / f"{run_id}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info("Reconciliation report written to %s", path)
        return str(path)
    except Exception as exc:
        logger.warning("Could not write run report: %s", exc)
        return None


async def load_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Load unified data into the configured destination.

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
            "Load NOT approved (load_approval=%r) — writing nothing to the destination.%s",
            state.get("load_approval"),
            f" Reason: {reason}" if reason else "",
        )
        report = build_reconciliation_report(state, {}, list(state.get("errors", [])))
        report["load_rejected"] = True
        report["load_rejected_reason"] = reason
        _write_run_report(report)
        logger.debug("EXIT load_data_node (rejected)")
        # No new error here - a rejected load isn't a failure, it's an
        # explicit human decision. The old `errors: state.get("errors", [])`
        # re-appended the entire error history through operator.add's
        # reducer on every rejection (Phase 0 Sec 0.8).
        return {
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
    # P10: the run's own invariants, at the thresholds the operator confirmed.
    invariant_failure = None
    confirmed: Any = None
    if not guardrail_failure:
        confirmed = _confirmed_thresholds(state)
        invariant_failure = _pre_load_failure(pre_load_gate(state, confirmed))
        guardrail_failure = invariant_failure
    if guardrail_failure:
        logger.error(
            "Pre-load guardrail fired — refusing to load: %s",
            guardrail_failure["error"][:400],
        )
        report = build_reconciliation_report(state, {}, list(state.get("errors", [])))
        report["guardrail_fired"] = True
        refusal: dict[str, Any] = {"reconciliation_report": report, "failure": guardrail_failure}
        if invariant_failure and confirmed is not None:
            refusal["pre_load_thresholds"] = confirmed
        # P17's defect report survives a P10 refusal: the reviewer fixing the run needs both.
        defects = preview_destination_defects(state) if invariant_failure else []
        if defects:
            report["destination_defects"] = refusal["destination_defects"] = defects
        _write_run_report(report)
        logger.debug("EXIT load_data_node (guardrail)")
        # The guardrail's own detail already lands in `failure` and the
        # report - no separate new error to add here (see the rejected-load
        # branch above for why the old passthrough was a bug).
        return refusal

    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {})
    field_mappings: dict[str, dict] = state.get("field_mappings", {})
    destination = state.get("destination")
    extraction_order = state.get("extraction_order", [])
    # Only THIS call's own errors - seeding with state's existing history
    # used to mean the returned "errors" list already contained the old
    # history, which operator.add's reducer then appended a SECOND time on
    # top of the existing state, duplicating it every single call (Phase 0
    # Sec 0.8). `errors[-3:]` below only ever needs errors raised during
    # this call's own per-entity load attempts.
    errors: list[str] = []

    logger.debug("extraction_order: %s", extraction_order)
    logger.debug("unified_entities keys: %s", list(unified_entities.keys()))
    for ue in unified_entities:
        logger.debug(
            "  Entity '%s': %d unified records", ue, collection_count(unified_entities, ue)
        )

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
            "back to per-entity connections, which are not atomic.",
            exc,
        )
        load_connector = None
        atomic = _null_transaction

    # A dry run does the entire load and then rolls it back, so the report
    # describes exactly what would have been written — real counts against
    # the real schema, with the destination untouched.
    dry_run = bool(state.get("dry_run"))
    if dry_run:
        logger.info("DRY RUN: the load will be rolled back, not committed.")

    # Report, never repair (P17): a table that cannot hold its links is left empty and named;
    # no DDL is issued. Introspection is read-only and happens before the transaction.
    defects = (
        destination_schema_defects(state, read_destination_schemas(load_connector))
        if load_connector is not None
        else []
    )
    for d in defects:
        errors.append(
            f"Destination table '{d['table']}' cannot hold its linkage: missing column(s) "
            f"{', '.join(d['missing_columns'])}; {d['unprojectable_rows']} row(s) cannot be "
            f"projected and were not written."
        )

    rolled_back = False
    try:
        with atomic(dry_run=dry_run) if dry_run else atomic():
            if pending_schema_changes:
                await _apply_schema_changes(
                    destination,
                    pending_schema_changes,
                    errors,
                    connector=load_connector,
                )

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
                skip_entities=frozenset(d["entity"] for d in defects),
            )

            # Record which entity each source record ended up in. Inside the
            # transaction on purpose: if the load rolls back the map must roll
            # back with it, or the next run reuses ids for rows that were
            # never written and silently duplicates them.
            if not failed_entities:
                _persist_entity_map(state, destination, set(load_results), connector=load_connector)
                # Phase 7.2: durable per-pair merge lineage, same transaction
                # for the same reason as the entity map above -- a rolled
                # back load must not leave audit rows for a merge that never
                # actually landed.
                _persist_merge_audit(
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
        logger.error("Load rolled back — the destination is unchanged. %s", exc)
    except Exception as exc:
        rolled_back = True
        msg = f"Load transaction failed and was rolled back: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
        if not failed_entities:
            failed_entities = list(entity_order)
    finally:
        if load_connector is not None:
            with contextlib.suppress(Exception):
                load_connector.close()

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
        state,
        load_results,
        errors,
        failed_entities,
        rolled_back=rolled_back,
    )
    report["load_rolled_back"] = rolled_back
    if defects:
        report["destination_defects"] = defects
        report["data_quality"]["red_flags"].extend(
            f"{d['table']}: destination table cannot hold its linkage "
            f"(missing {', '.join(d['missing_columns'])}); {d['unprojectable_rows']} row(s) "
            f"left unwritten"
            for d in defects
        )
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

    update: dict[str, Any] = {"errors": errors, "reconciliation_report": report, "failure": failure}
    if confirmed is not None:
        update["pre_load_thresholds"] = confirmed
    if defects:
        update["destination_defects"] = defects
    return update


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
    skip_entities: frozenset[str] = frozenset(),
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
        candidate_table = _resolve_dest_table(destination, entity_type, available_dest_tables)
        entity_type_by_dest_table[candidate_table] = entity_type

    for entity_type in entity_order:
        records = collection_records(unified_entities, entity_type, kind="unified")
        logger.debug("Processing entity '%s': %d records", entity_type, len(records))
        if not records:
            logger.info("No records to load for entity '%s', skipping.", entity_type)
            continue
        if entity_type in skip_entities:
            logger.error(
                "Entity '%s' not loaded: its destination table cannot hold its links.", entity_type
            )
            continue

        mapping = field_mappings.get(entity_type, {})
        logger.debug("Field mapping for '%s': %d mappings", entity_type, len(mapping))
        dest_table = _resolve_dest_table(destination, entity_type, available_dest_tables)
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
                other_primary_tables=set(entity_type_by_dest_table) - {dest_table},
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
            # Surfaced beyond the log line: a cross-table mapping approved
            # at the gate that then had no reverse FK to write through at
            # load time used to be visible nowhere a human reviewing the
            # run would look. errors is already rendered in the final
            # report a reviewer sees.
            for child_table, child_result in (result.get("child_tables") or {}).items():
                dropped = child_result.get("dropped_fields")
                if dropped:
                    errors.append(
                        f"Entity '{entity_type}': cross-table mapping into '{child_table}' "
                        f"had no foreign key back to its parent table — {len(dropped)} "
                        f"field(s) did not reach the destination: {', '.join(dropped)}"
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
                "back, because without it a re-run would duplicate rows.",
                exc,
            )
            raise
        # No transaction to roll back: the rows are already committed.
        # Claiming a rollback here would be a straight lie about the state
        # of the user's database.
        logger.error(
            "Could not persist the entity map (%s). The load is already "
            "committed, so it stands — but a re-run may insert duplicate "
            "rows instead of updating.",
            exc,
        )


def _persist_merge_audit(
    state: dict[str, Any],
    destination: Any,
    entity_types: set[str],
    connector: Any = None,
) -> None:
    """Persist durable per-pair merge evidence (Phase 7.1's ``match_evidence``).

    One row per matched pair, covering every multi-member cluster's internal
    edges — the field-level breakdown or LLM rationale that justified the
    merge, queryable long after the run's own spool is cleaned up. Best
    effort and non-fatal: unlike ``_persist_entity_map``, losing an audit
    row does not corrupt a future run (no duplicate destination rows result
    from it) — it only means that one merge's "why" is missing from the
    audit table, which is a real but recoverable loss, not one worth
    rolling an otherwise-successful load back over.
    """
    if not entity_types or not isinstance(destination, dict):
        return
    try:
        owns_connector = connector is None
        if connector is None:
            connector = _get_destination_connector(destination)
        if not hasattr(connector, "save_merge_audit"):
            return
        run_id = str(state.get("run_id", ""))
        saved = 0
        for entity_type in entity_types:
            rows: list[dict[str, Any]] = []
            for cluster in clusters_for(state, entity_type):
                evidence = cluster.get("match_evidence")
                if not evidence:
                    continue
                cluster_id = cluster.get("cluster_id", "")
                # A human who confirmed this cluster at the matches gate
                # (cluster_edits.apply_cluster_decisions' "keep" path) leaves
                # match_evidence untouched but sets reviewed_by_human -- that
                # explicit confirmation outranks the original auto/llm
                # decision as the audit trail's "decided_by".
                human_confirmed = bool(cluster.get("reviewed_by_human"))
                for pair in evidence.get("pairs", []):
                    record_a = pair.get("a")
                    record_b = pair.get("b")
                    if not record_a or not record_b:
                        continue
                    method = pair.get("method", "")
                    rows.append(
                        {
                            "cluster_id": cluster_id,
                            "record_a": record_a,
                            "record_b": record_b,
                            "confidence": pair.get("confidence"),
                            "method": method,
                            "evidence": {
                                "matched_fields": pair.get("matched_fields", []),
                                "mismatched_fields": pair.get("mismatched_fields", []),
                                "reason": pair.get("reason"),
                            },
                            "decided_by": "human"
                            if human_confirmed
                            else ("llm" if method == "llm" else "auto"),
                            "human_override": "merge" if human_confirmed else None,
                        }
                    )
            if rows:
                saved += connector.save_merge_audit(entity_type, run_id, rows)

        # Phase 7.2: pairs a human split apart (cluster_edits.
        # apply_cluster_decisions, via workflow_manager's resume handler).
        # These never appear in the per-cluster loop above -- the new
        # per-partition clusters carry no match_evidence of their own -- so
        # without this, a human's "these are NOT the same entity" decision
        # would be applied to entity_clusters but leave no audit trail at
        # all, unlike every other decided_by value.
        split_by_entity: dict[str, list[dict[str, Any]]] = {}
        for pair in state.get("human_split_pairs") or []:
            entity_type = pair.get("entity_type")
            record_a, record_b = pair.get("record_a"), pair.get("record_b")
            if not entity_type or entity_type not in entity_types or not record_a or not record_b:
                continue
            split_by_entity.setdefault(entity_type, []).append(
                {
                    "cluster_id": pair.get("cluster_id", ""),
                    "record_a": record_a,
                    "record_b": record_b,
                    "confidence": pair.get("confidence"),
                    "method": pair.get("method", ""),
                    "evidence": {
                        "matched_fields": pair.get("matched_fields", []),
                        "mismatched_fields": pair.get("mismatched_fields", []),
                        "reason": pair.get("reason"),
                    },
                    "decided_by": "human",
                    "human_override": "split",
                }
            )
        for entity_type, rows in split_by_entity.items():
            saved += connector.save_merge_audit(entity_type, run_id, rows)

        if saved:
            logger.info("Persisted %d merge-audit row(s)", saved)
        if owns_connector and hasattr(connector, "close"):
            connector.close()
    except Exception as exc:
        logger.warning(
            "Could not persist merge audit rows (%s) — the load itself is "
            "unaffected, but this merge's per-pair evidence will not be "
            "queryable afterward.",
            exc,
        )


async def _apply_schema_changes(
    destination: Any,
    changes: list[dict],
    errors: list[str],
    connector: Any = None,
) -> None:
    """Refuse pending schema changes — the destination schema is a contract.

    This is the last of four layers keeping the destination immutable. The
    mapper no longer proposes new columns and the mappings gate no longer
    accepts them, so reaching here at all means a stale checkpoint or a
    regression upstream. Either way it is recorded as an error and surfaced
    at the final gate rather than executed: a run that mutated the schema
    used to leave the ALTERs committed even when every row rolled back,
    which is how ``company`` grew from 14 columns to 43.
    """
    summary = ", ".join(f"{c.get('table')}.{c.get('column')}" for c in changes[:10])
    if len(changes) > 10:
        summary += f", … (+{len(changes) - 10} more)"
    msg = (
        f"Refused {len(changes)} destination schema change(s) — the destination "
        f"schema is a fixed contract and no column may be added: {summary}. "
        f"Map these fields onto existing columns or leave them unmapped."
    )
    logger.error(msg)
    errors.append(msg)


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
    # Only THIS call's own errors - see load_data_node's identical fix
    # above for why seeding with the existing history double-counted it
    # through operator.add's reducer (Phase 0 Sec 0.8).
    errors: list[str] = []

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
        connector = _get_destination_connector(
            destination if isinstance(destination, dict) else None
        )
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
                tracking_key,
                entry.get("watermark_column"),
                entry.get("last_value"),
                entry.get("rows_extracted", 0),
            )
        except Exception as exc:
            msg = f"Failed to update extraction state for '{tracking_key}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT update_extraction_state_node")
    return {"errors": errors}
