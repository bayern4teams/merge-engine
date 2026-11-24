"""P21g/P21k — the ER hooks inside the batch pipeline (D-46, D-49, D-64).

The batch pipeline and the online ER service share one destination DB, and
the report (P21g) names three hooks. Without hook 1 the next load rewrites
every enriched row from the source origins only and silently drops every
enrichment value — the most likely way P21 fails in production.

1. ``inject_enrichment_records`` (resolve phase, candidate pool): reads
   ``merge_engine.er_enrichment_record`` as one more origin named
   ``enrichment_engine``, each record pre-seeded in the entity map with the
   id ER issued, so a cluster containing it adopts that id and the load
   updates the row in place. ``ensure_enrichment_origin`` gives the origin
   survivorship priority -1 - strictly below every configured origin - so
   source-DB facts beat extracted text (D-46) even when the sources never
   set their ``priority`` (default 0 makes priority a no-op tiebreak).
   Best-effort by design: a broken hook may not stop a merge.

2. ``hold_load_write_lock`` (first statement of the load transaction):
   ``pg_advisory_xact_lock(WRITE_LOCK_KEY)`` for the whole transaction (C7).
   The ER service answers 503 + Retry-After while it is held (D-49) — the
   same key ``er_service.acquire_write_locks`` tries.

3. ``write_load_side_effects`` (same transaction, after the entity map is
   saved): ``er_redirect`` for every cluster id that lost all its records to
   another cluster, an ``er_entity_version`` bump for every row the run
   loaded, and — P21k — an ``er_field_history`` row per survivorship loss
   (reason ``HIGHER_PRIORITY_SOURCE``) from the ``_conflicts`` the unify node
   attached to each unified record. Transactional on purpose, like the
   entity map itself: redirect rows for a load that rolled back would send
   clients to ids that were never written.

``persist_er_models`` runs after the load commits: the model each entity
actually fitted this run becomes ``er_model`` — "the bars the last run
used" (P21b). Best-effort: a stale model is provisional-but-scored, not a
broken run.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg2.extras  # type: ignore[import-untyped]

logger = logging.getLogger("merge-engine.er_hooks")

ENRICHMENT_ORIGIN = "enrichment_engine"


def _is_er_capable(connector: Any) -> bool:
    """The ER side tables + advisory locks are a Postgres feature; the batch
    also supports ClickHouse destinations and mocked connectors. Only the
    real PostgreSQL connector gets hooks."""
    from src.mcp_server.connectors.postgresql_destination import (
        PostgreSQLDestinationConnector,
    )

    return isinstance(connector, PostgreSQLDestinationConnector)


def _connector_for_state(state: dict[str, Any]) -> Any:
    destination = state.get("destination")
    if not isinstance(destination, dict) or not destination:
        return None
    try:
        from src.mcp_server.tools.load_data import _get_destination_connector

        connector = _get_destination_connector(destination)
    except Exception as exc:
        logger.warning("ER hook: no destination connector (%s)", exc)
        return None
    if not _is_er_capable(connector):
        return None
    return connector


# ── hook 1: enrichment records as one more origin (P21g-1) ────────────────


def inject_enrichment_records(
    state: dict[str, Any],
    spool: Any,
    entity_map: dict[str, dict[str, str]],
    extracted_data: dict[str, Any],
    run_id: str,
) -> int:
    """Add every ``er_enrichment_record`` to the run's candidate pool.

    Called in the resolve node right after the entity map loads, beside the
    scoped-merge destination injection (same ethos: best-effort, and any
    failure leaves the run exactly as it was without enrichment dedup — a
    broken read of ER's table must never be what stops a merge).

    Each record is tagged with the ``enrichment_engine`` origin triple
    ``origin:table:row`` the spool's record-id convention expects, and
    pre-seeded in ``entity_map`` with the id ER issued (that is also the
    destination row's id), so ``_assign_cluster_id`` propagates it: a
    cluster that contains the enrichment record adopts ER's id and the load
    updates ER's row in place instead of minting a second one.

    Book columns (``extra``/``source_text``/``provenance``/inferred lists)
    stay in ``er_enrichment_record`` where they already live: only data +
    reference fields enter the pool, so they cannot surface as phantom
    conflicts or phantom destination columns.
    """
    connector = _connector_for_state(state)
    if connector is None:
        return 0
    try:
        connector.ensure_er_side_tables()
        s = connector._er_schema
        rows = connector.execute_sql(
            f'SELECT record_id, entity_type, attributes, "references" '
            f'FROM "{s}".er_enrichment_record'
        )
    except Exception as exc:
        logger.warning("ER hook 1: could not read er_enrichment_record (%s) — run continues.", exc)
        return 0
    if not rows:
        return 0

    # Registry types are destination TABLE names ("companies"); the batch
    # resolves abstract concepts ("company") whose rows were pooled under the
    # concept name. Map each enrichment type onto the concept this run is
    # resolving; a type nobody resolves this run is left untouched.
    from src.orchestrator.spool import make_handle
    from src.orchestrator.utils.entity_aliases import concept_matches

    target_entities = [str(t) for t in (state.get("target_entities") or [])]

    def concept_for(entity_type: str) -> str | None:
        if entity_type in target_entities:
            return entity_type
        return next((t for t in target_entities if concept_matches(t, entity_type)), None)

    by_concept: dict[str, list[dict[str, Any]]] = {}
    injected = 0
    for record_id, entity_type, attributes, references in rows:
        concept = concept_for(str(entity_type))
        if concept is None:
            continue
        record = {
            k: v for k, v in {**(attributes or {}), **(references or {})}.items() if v is not None
        }
        if not record:
            continue
        record["_origin_id"] = ENRICHMENT_ORIGIN
        record["_origin_table"] = str(entity_type)
        record["_origin_id_row"] = str(record_id)
        by_concept.setdefault(concept, []).append(record)
        # The id ER issued IS the destination row id (er_service._create_unified
        # wrote it); pre-seeding the map makes clustering adopt it. The map
        # is keyed by the concept name — same name `entity_map.get(entity_type)`
        # is read with at dispatch.
        id_map = dict(entity_map.get(concept, {}))
        id_map[f"{ENRICHMENT_ORIGIN}:{entity_type}:{record_id}"] = _cluster_of(
            connector, str(entity_type), str(record_id)
        ) or str(record_id)
        entity_map[concept] = id_map
        injected += 1

    for concept, records in by_concept.items():
        try:
            spool.append(concept, records)
            # Refresh the handle so entity_rows() for the concept includes the
            # injected rows (same as the destination-dedup injection does).
            extracted_data[concept] = make_handle(run_id, concept, spool.row_count(concept))
        except Exception as exc:
            logger.warning("ER hook 1: spool append failed for '%s' (%s)", concept, exc)

    if injected:
        logger.info(
            "ER hook 1 (P21g-1): injected %d enrichment record(s) across %d type(s) "
            "as origin '%s'.",
            injected,
            len(by_concept),
            ENRICHMENT_ORIGIN,
        )
    return injected


def _cluster_of(connector: Any, entity_type: str, record_id: str) -> str | None:
    """The cluster id ER's own entity_map row carries for this record (ER
    writes ``record_id`` verbatim; older rows keyed the ``origin:table:row``
    form — accept both)."""
    try:
        rows = connector.execute_sql(
            f'SELECT cluster_id FROM "{connector._schema}"."merge_engine_entity_map" '
            "WHERE entity_type = %s AND record_id IN (%s, %s)",
            (entity_type, record_id, f"{ENRICHMENT_ORIGIN}:{entity_type}:{record_id}"),
        )
    except Exception:
        return None
    return str(rows[0][0]) if rows else None


def ensure_enrichment_origin(origins: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """Add the ER origin (lowest priority, D-46) when a run's records
    actually carry it. -1 beats every unset origin's 0 under the priority
    rule; a configured origin (>=1) also beats it. No-op otherwise, so runs
    without enrichment data are byte-identical to before."""
    if any(o.get("id") == ENRICHMENT_ORIGIN for o in origins):
        return
    if any(r.get("_origin_id") == ENRICHMENT_ORIGIN for r in records):
        origins.append({"id": ENRICHMENT_ORIGIN, "priority": -1})
        logger.debug("ER hook 1: origin '%s' added at priority -1 (D-46).", ENRICHMENT_ORIGIN)


# ── hook 2: the load holds the write lock for its whole transaction ───────


def hold_load_write_lock(connector: Any) -> bool:
    """C7/D-49: take the ER write lock as an xact advisory lock. Every ER
    write attempt ``pg_try_advisory_lock``s the same key and gets its 503
    while this transaction runs. Only called on the real (non-dry-run) load
    transaction path; failure to take it does not stop the load — it removes
    the pause guarantee, so it is an ERROR, not a shrug."""
    if not _is_er_capable(connector):
        return False
    try:
        from src.orchestrator.er_service import WRITE_LOCK_KEY

        conn = connector._ensure_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,))
        return True
    except Exception as exc:
        logger.error(
            "ER hook 2: could not take the load write lock (%s) — ER writes are "
            "NOT paused for this load. Concurrent /v1 writes may see a "
            "half-loaded destination.",
            exc,
        )
        return False


# ── hook 3: after the entity map saved (same transaction) ─────────────────


def snapshot_entity_map(connector: Any) -> dict[tuple[str, str], str]:
    """(entity_type, record_id) -> cluster_id before this run's map save.

    The redirect question is 'which cluster ids LOST all their records to
    another cluster', and the only way to answer it truthfully is before/
    after: the map is upserted in place, so after the save the old rows are
    gone. Snapshot the whole map (one pass, read-only); volume path is a
    COPY-out if maps ever get big enough to matter (P21k's batching note).
    """
    if not _is_er_capable(connector):
        return {}
    try:
        rows = connector.execute_sql(
            f"SELECT entity_type, record_id, cluster_id "
            f'FROM "{connector._schema}"."merge_engine_entity_map"'
        )
    except Exception as exc:
        logger.warning("ER hook 3: entity-map snapshot failed (%s)", exc)
        return {}
    return {(str(et), str(rid)): str(cid) for et, rid, cid in rows if cid}


def write_load_side_effects(
    connector: Any,
    run_id: str,
    snapshot: dict[tuple[str, str], str],
    loaded_by_type: dict[str, list[str]],
    conflicts_by_type: dict[str, list[tuple[str, list[dict[str, Any]]]]],
) -> dict[str, int]:
    """Redirects + version bumps + survivorship history, inside the load's
    transaction (see module docstring — propagating failures is the point).

    ``loaded_by_type``: destination cluster ids this run wrote, per batch
    entity type. ``conflicts_by_type``: per cluster, the survivorship
    conflict records the unify node attached (P21k evidence).
    """
    if not _is_er_capable(connector):
        return {"redirects": 0, "versions": 0, "history": 0, "splits": 0}
    s = connector._er_schema
    conn = connector._ensure_connection()
    stats = {"redirects": 0, "versions": 0, "history": 0, "splits": 0}

    # -- er_redirect: ids that lost all their records to one other cluster.
    # The map is upserted in place, so the truth is before-vs-after: the
    # snapshot was read before this run's save; read it again now (same
    # transaction — sees this run's writes, nothing else).
    # Concept-vs-table naming: the map's entity_type is the abstract concept
    # (batch), ER's registry key is the destination table name; ids are
    # global and GET resolves through er_entity_version by table name.
    after = snapshot_entity_map(connector)
    alive_after: set[tuple[str, str]] = {(et, cid) for (et, _rid), cid in after.items()}

    redirect_rows: list[tuple[str, str, str, str]] = []
    split_rows: list[tuple[str, str, str]] = []
    if snapshot:
        moved: dict[tuple[str, str], set[str]] = {}
        for key, old_cid in snapshot.items():
            new_cid = after.get(key)
            if new_cid and new_cid != old_cid:
                moved.setdefault((key[0], old_cid), set()).add(new_cid)
        for (et, old_cid), targets in moved.items():
            if (et, old_cid) in alive_after:
                continue  # the id still holds at least one record
            table_name = _table_for_type(connector, et)
            if len(targets) == 1:
                redirect_rows.append((table_name, old_cid, next(iter(targets)), run_id))
                continue
            # Several targets = the human split this cluster at the matches
            # gate (P21l). GET old must keep resolving (acceptance 19), so
            # old redirects to the SURVIVING part — the smallest id, the
            # batch's own deterministic tie-break (_assign_cluster_id) — and
            # er_split records every part, survivors and new ids alike.
            parts = sorted(targets)
            redirect_rows.append((table_name, old_cid, parts[0], run_id))
            for new_id in parts:
                split_rows.append((table_name, old_cid, new_id))
        if split_rows:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    f'INSERT INTO "{s}".er_split (entity_type, id, new_id, at) '
                    "VALUES (%s, %s, %s, now()) ON CONFLICT (entity_type, id, new_id) DO NOTHING",
                    split_rows,
                )
            stats["splits"] = len(split_rows)
        if redirect_rows:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    f'INSERT INTO "{s}".er_redirect (entity_type, old_id, new_id, run_id, at) '
                    "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (entity_type, old_id) "
                    "DO UPDATE SET new_id = EXCLUDED.new_id, run_id = EXCLUDED.run_id, "
                    "at = now()",
                    redirect_rows,
                )
            stats["redirects"] = len(redirect_rows)

    # -- er_entity_version: every row this run wrote is a new version.
    for entity_type, ids in loaded_by_type.items():
        if not ids:
            continue
        table_name = _table_for_type(connector, entity_type)
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                f'INSERT INTO "{s}".er_entity_version (entity_type, id, version, updated_at) '
                "VALUES (%s, %s, 1, now()) ON CONFLICT (entity_type, id) DO UPDATE "
                "SET version = er_entity_version.version + 1, updated_at = now()",
                [(table_name, rid) for rid in ids],
            )
        stats["versions"] += len(ids)

    # -- er_field_history: survivorship losses (P21k, D-64). One row per
    # value that lost the conflict, reason HIGHER_PRIORITY_SOURCE, with the
    # surviving value as `new` and the loser as `attempted`. Conflicts are
    # only the contested fields, so volume is bounded by disagreement, not
    # by rows x columns.
    history_rows: list[tuple[Any, ...]] = []
    for entity_type, clusters in conflicts_by_type.items():
        table_name = _table_for_type(connector, entity_type)
        for cluster_id, conflicts in clusters:
            for c in conflicts:
                winner = c.get("selected_value")
                for loser in c.get("values") or []:
                    if loser.get("origin") == c.get("selected_origin"):
                        continue
                    history_rows.append(
                        (
                            table_name,
                            cluster_id,
                            c.get("field"),
                            json.dumps(winner, default=str),
                            loser.get("origin"),
                            "HIGHER_PRIORITY_SOURCE",
                            json.dumps(loser.get("value"), default=str),
                            run_id,
                        )
                    )
    if history_rows:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                f'INSERT INTO "{s}".er_field_history '
                "(entity_type, id, version, field, old, new, source, reason, attempted, run_id) "
                "VALUES (%s, %s, NULL, %s, NULL, %s::jsonb, %s, %s, %s::jsonb, %s)",
                history_rows,
            )
        stats["history"] = len(history_rows)

    if any(stats.values()):
        logger.info("ER hook 3 (P21g-3/P21k): %s", stats)
    return stats


def _table_for_type(connector: Any, entity_type: str) -> str:
    """ER's key for a batch entity type: the destination table name (what
    er_service's catalog is keyed by), falling back to the concept name."""
    try:
        from src.orchestrator.utils.entity_aliases import resolve_dest_table

        tables = connector.list_tables() if hasattr(connector, "list_tables") else []
        resolved = resolve_dest_table(entity_type, tables) if tables else None
        return resolved or entity_type
    except Exception:
        return entity_type


# ── er_model refresh (P21g-3, after commit) ───────────────────────────────


def persist_er_models(state: dict[str, Any], models: dict[str, dict[str, Any]]) -> int:
    """'The bars the last run used' (P21b): store each entity's fitted model
    so the online scorer reproduces this run's scoring. After-commit,
    best-effort: an un-persisted model leaves ER on the previous (or
    provisional) bars, which /v1/entity-types already labels honestly."""
    if not models or state.get("dry_run"):
        return 0
    connector = _connector_for_state(state)
    if connector is None:
        return 0
    run_id = str(state.get("run_id") or "")
    saved = 0
    try:
        connector.ensure_er_side_tables()
        s = connector._er_schema
        for entity_type, params in models.items():
            if not params:
                continue
            table_name = _table_for_type(connector, entity_type)
            connector.execute_sql(
                f'INSERT INTO "{s}".er_model (entity_type, run_id, params, updated_at) '
                "VALUES (%s, %s, %s::jsonb, now()) ON CONFLICT (entity_type) DO UPDATE "
                "SET run_id = EXCLUDED.run_id, params = EXCLUDED.params, updated_at = now()",
                (table_name, run_id, json.dumps(params, default=str)),
            )
            saved += 1
    except Exception as exc:
        logger.warning("ER hook: could not persist er_model (%s) — ER keeps previous bars.", exc)
        return 0
    if saved:
        logger.info("ER hook (P21g-3): persisted er_model for %d type(s).", saved)
    return saved


__all__ = [
    "ENRICHMENT_ORIGIN",
    "ensure_enrichment_origin",
    "hold_load_write_lock",
    "inject_enrichment_records",
    "persist_er_models",
    "snapshot_entity_map",
    "write_load_side_effects",
]
