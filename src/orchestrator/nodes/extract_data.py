"""
extract_data node: Extracts data for all entities in dependency order.

Parses the dependency tree and target schemas to determine which tables
from which origins contain the target entity, then calls extract_data
in batches until all rows are fetched.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.extract_data import extract_data_impl
from src.mcp_server.tools.load_data import _get_destination_connector
from src.orchestrator.spool import RunSpool, is_handle, make_handle
from src.orchestrator.utils.dependency_sort import find_execution_groups
from src.orchestrator.utils.retry import TransientError, classify_exception

logger = logging.getLogger("merge-engine.orchestrator.extract_data")

# A scoped merge's linked-entity filter is an IN (...) over every id the
# subject links to (e.g. every person who ever worked at a large company) --
# for a real company this can be tens of thousands of ids. One placeholder
# per id in the query text, repeated on every paginated batch call, is what
# blew past ClickHouse's HTTP field-size limit on a live Boeing run ("HTML
# Form Exception: Field value too long") even though each individual id is
# short. Splitting the id list into several independently-paginated queries
# keeps every request small regardless of how many ids there are.
_MAX_IN_FILTER_VALUES = 500


def _chunked_filters(
    scope_filter: dict[str, Any] | None, chunk_size: int = _MAX_IN_FILTER_VALUES
) -> list[dict[str, Any] | None]:
    """Split a single-condition ``{column: {"op": "in", "value": [...]}}``
    filter into several once its value list is large. Any other filter shape
    (``ilike``, a small ``in``, or ``None``) is returned unchanged as the only
    element — chunking only ever applies to the "many linked ids" case.
    """
    if not scope_filter:
        return [scope_filter]
    (column, cond), = scope_filter.items()
    values = cond.get("value")
    if cond.get("op") != "in" or not isinstance(values, list) or len(values) <= chunk_size:
        return [scope_filter]
    return [
        {column: {"op": "in", "value": values[i:i + chunk_size]}}
        for i in range(0, len(values), chunk_size)
    ]


def _handle_count(value: Any) -> int:
    """Row count from either a spool handle or an inline list."""
    if is_handle(value):
        return int(value.get("row_count", 0))
    return len(value or [])


async def extract_data(state: dict[str, Any]) -> dict[str, Any]:
    """Extract data for all entities in dependency order.

    Parameters
    ----------
    state : dict
        MergeState with ``extraction_order``, ``schemas``, ``origins``.

    Returns
    -------
    dict
        Update dict with ``extracted_data`` key.
    """
    logger.debug("ENTER extract_data")
    logger.debug("State keys: %s", list(state.keys()))
    extraction_order = state.get("extraction_order", [])
    schemas = state.get("schemas", {})
    origins = state.get("origins", [])
    destination = state.get("destination", {})
    extraction_config = state.get("extraction_config", {})
    entity_tables: dict[str, Any] = state.get("entity_tables", {}) or {}
    # A fresh run (new profile) or the explicit "Full re-merge" toggle: ignore
    # every tracked watermark and re-read each table in full. This is what makes
    # "delete rows from the destination, then re-merge" actually bring them back
    # — the watermark lives in the destination's tracking table, not the entity
    # tables, so an incremental run would otherwise see nothing new and load 0.
    force_full_extraction: bool = bool(state.get("force_full_extraction"))
    errors: list[str] = list(state.get("errors", []))
    extracted_data: dict[str, list[dict]] = dict(state.get("extracted_data", {}))
    extraction_watermarks: dict[str, Any] = dict(state.get("extraction_watermarks", {}))

    # Tables that could not be read in full. Any entry here means the merge
    # would be built on incomplete input, so the run stops at the error gate
    # rather than producing a confident partial answer.
    failed_tables: list[dict[str, Any]] = []
    # Entities with no source table at all. Split explicit-vs-inferred (2026-07-30,
    # remediation #1 in merge-engine-remediation-1-to-6, project memory): a type
    # the user actually asked for and can't be supplied is a real, reportable data
    # gap and must escalate; a type a wildcard rule merely guessed (e.g. "product"
    # for a company-only corpus) should not abort a run over a guess that didn't
    # pan out. explicit_target_entities defaults to target_entities only when the
    # KEY IS ABSENT from state (old profile/model that never set it) -- so old
    # profiles keep today's strict "everything escalates" behaviour. A present-
    # but-EMPTY list is a legitimate, distinct value ("nothing was explicit, it
    # was all wildcard-inferred") and must not be treated the same as "absent" --
    # `state.get(...) or fallback` would wrongly collapse `[]` into the fallback
    # since an empty list is falsy in Python.
    _explicit_raw = state.get("explicit_target_entities", None)
    explicit_target_entities: set[str] = set(
        _explicit_raw if _explicit_raw is not None else state.get("target_entities", [])
    )
    missing_entities: list[str] = []
    missing_explicit: list[str] = []
    missing_inferred: list[str] = []

    # Rows go to a per-run DuckDB file; state keeps only a handle. Holding
    # them in state meant every checkpoint — one per step, plus one per HITL
    # pause — serialised the entire source data set.
    run_id = str(state.get("run_id") or "unknown")
    spool = RunSpool(run_id)

    default_batch_size = extraction_config.get("default_batch_size", 1000)
    logger.debug("extraction_order: %s", extraction_order)
    logger.debug("default_batch_size: %d", default_batch_size)

    # Origins are read-only: incremental state lives in the destination's
    # merge_engine_extraction_state tracking table, looked up once per table.
    dest_connector = None
    try:
        dest_connector = _get_destination_connector(destination if isinstance(destination, dict) else None)
    except Exception as exc:
        logger.warning("Could not create destination connector for extraction-state lookup: %s", exc)

    # Scoped merge ("everything about Nova"): the plan says how to restrict
    # each table. Two-phase — the subject entity (company) is matched by name
    # first, its ids collected per origin, then dependent entities (person,
    # product) are restricted to those ids. The dependency order guarantees the
    # subject entity is extracted before anything that links to it.
    scope_plan = state.get("scope_plan") or {}
    scope_entities: dict[str, Any] = scope_plan.get("entities") or {}
    scope_active = bool((state.get("merge_scope") or {}).get("active")) and bool(scope_entities)
    subject = scope_plan.get("subject", "")
    subject_entity_name = scope_plan.get("subject_entity", "")
    # PK values of the matched subject rows, per origin — the set each dependent
    # table's link column is filtered against.
    subject_ids: dict[str, list[Any]] = {}
    if scope_active:
        logger.info(
            "Scoped extraction for subject %r (entity %r).",
            subject, subject_entity_name,
        )

    # Group entities that can be extracted in parallel
    dep_tree = {e: state.get("dependency_tree", {}).get(e, []) for e in extraction_order}
    execution_groups = find_execution_groups(dep_tree)
    logger.debug("Execution groups: %d groups", len(execution_groups))

    for group_idx, group in enumerate(execution_groups):
        logger.debug("Processing execution group %d: %s", group_idx, group)
        for entity_type in group:
            logger.info("Extracting entity type: %s", entity_type)

            # Determine which origin tables contain this entity. The
            # LLM-chosen, human-confirmed selection wins; the alias-name
            # heuristic is the fallback when selection produced nothing.
            table_candidates = _candidates_from_selection(
                entity_type, entity_tables, schemas
            )
            if not table_candidates:
                table_candidates = _find_entity_tables(entity_type, schemas)
            logger.debug("Found %d table candidates for entity '%s'", len(table_candidates), entity_type)
            for tc in table_candidates:
                logger.debug("  Candidate: %s (%s)", tc["table_name"], tc["origin_id"])

            if not table_candidates:
                missing_entities.append(entity_type)
                if entity_type in explicit_target_entities:
                    # The user asked for this entity and no origin can supply
                    # it. Continuing would merge everything *except* what was
                    # asked for, and still report success.
                    msg = f"No tables found for entity '{entity_type}' in any origin."
                    logger.error(msg)
                    errors.append(msg)
                    missing_explicit.append(entity_type)
                else:
                    # A wildcard rule (e.g. "Company Wildcards") inferred this
                    # type; the user never asked for it by name, so a guess
                    # that doesn't pan out is a warning, not a run-stopping gap.
                    msg = f"Inferred entity '{entity_type}' has no source table in any origin -- skipping it, not escalating (not explicitly requested)."
                    logger.warning(msg)
                    missing_inferred.append(entity_type)
                continue

            # A fresh table per entity, so a retry after a failure does not
            # append to the rows the failed attempt already wrote.
            spool.reset_entity(entity_type)
            rows_written = 0

            for candidate in table_candidates:
                origin_id = candidate["origin_id"]
                table_name = candidate["table_name"]
                columns = [c["name"] for c in candidate.get("columns", [])]
                pk_columns = [c for c in (candidate.get("primary_key") or []) if c]

                if not columns:
                    columns = ["*"]

                origin_config = next(
                    (o for o in origins if o.get("id") == origin_id), None
                )
                if origin_config is None:
                    msg = f"No origin config found for '{origin_id}'; skipping {table_name}."
                    logger.warning(msg)
                    errors.append(msg)
                    continue

                # Scope filter for this (entity, origin, table). A scoped merge
                # extracts ONLY planned tables — a table with no plan entry is
                # skipped rather than read in full, because reading it whole
                # would pull in every other organisation's rows.
                scope_filter = None
                if scope_active and entity_type in scope_entities:
                    entry = _scope_entry_for(
                        scope_entities[entity_type], origin_id, table_name
                    )
                    if entry is None:
                        logger.info(
                            "Scoped merge: %s:%s is not in the plan for '%s' — skipping.",
                            origin_id, table_name, entity_type,
                        )
                        continue
                    ids_here = subject_ids.get(origin_id, [])
                    if entry.get("mode") == "link_via":
                        # Two-hop: "people who work at Nova" — resolve the
                        # junction (jobs.company_id IN nova ids -> person_ids)
                        # first, then restrict this table to those keys.
                        if not ids_here:
                            logger.info(
                                "Scoped merge: no subject ids in %s — %s skipped.",
                                origin_id, table_name,
                            )
                            continue
                        via_keys = await _resolve_link_via(
                            origin_config, entry, ids_here
                        )
                        if not via_keys:
                            logger.info(
                                "Scoped merge: junction %s linked no %s to the "
                                "subject — %s skipped.",
                                entry.get("via_table"), entity_type, table_name,
                            )
                            continue
                        scope_filter = {
                            entry["columns"][0]: {"op": "in", "value": via_keys}
                        }
                    else:
                        scope_filter, skip = _scope_filter_for(
                            entry, subject, ids_here
                        )
                        if skip:
                            logger.info(
                                "Scoped merge: no subject ids for %r in origin %s "
                                "— %s contributes nothing.",
                                subject, origin_id, table_name,
                            )
                            continue

                # Origins are read-only: incremental extraction is driven by
                # a watermark column (timestamp or monotonic PK, detected in
                # discover_schema) rather than any per-row marker on the
                # origin. No usable watermark -> full re-extraction every run.
                watermark_column = candidate.get("watermark_column")
                watermark_kind = candidate.get("watermark_kind")
                watermark = None
                tracking_key = f"{origin_id}:{table_name}"
                if force_full_extraction:
                    # Read the whole table, but still record the new high-water
                    # mark below so the NEXT (non-fresh) run can go incremental.
                    logger.info(
                        "Full re-merge: ignoring any tracked watermark for %s.",
                        tracking_key,
                    )
                elif watermark_column and dest_connector is not None:
                    try:
                        tracked_state = dest_connector.get_extraction_state(origin_id, table_name)
                    except Exception as exc:
                        logger.warning("Could not read extraction state for %s: %s", tracking_key, exc)
                        tracked_state = None
                    last_value = tracked_state.get("last_value") if tracked_state else None
                    if last_value is not None:
                        # The tracking table stores last_value as TEXT; numeric
                        # watermarks must be re-typed or drivers with typed
                        # binds (asyncpg) reject the comparison outright.
                        if watermark_kind == "numeric":
                            try:
                                last_value = int(last_value)
                            except (TypeError, ValueError):
                                try:
                                    last_value = float(last_value)
                                except (TypeError, ValueError):
                                    pass
                        watermark = {"column": watermark_column, "since": last_value, "kind": watermark_kind}
                    logger.debug("Watermark for %s: column=%s, kind=%s, since=%s",
                                 tracking_key, watermark_column, watermark_kind, last_value)
                elif not watermark_column:
                    logger.warning(
                        "No usable watermark column for %s:%s (no timestamp or monotonic "
                        "PK found) — extracting the full table every run.",
                        origin_id, table_name,
                    )

                logger.debug("Extracting from %s:%s (columns=%s, batch_size=%d)", origin_id, table_name, columns[:5], default_batch_size)

                # Extract all batches. A scoped merge's linked-entity filter
                # can be a many-thousand-id IN (...) list (see
                # _chunked_filters); each chunk is paginated independently but
                # total_fetched/batch_num stay cumulative across chunks so the
                # stable-row-key ordinal fallback and failure reporting remain
                # correct for the table as a whole, not just one chunk of it.
                total_fetched = 0
                batch_num = 0
                last_watermark_value = None
                table_failed: str | None = None
                # Where this table's rows start, so a failure part-way can
                # roll them back out of the entity's spooled rows.
                rows_before_table = rows_written

                for filter_chunk in _chunked_filters(scope_filter):
                    cursor = None
                    while True:
                        batch_num += 1
                        try:
                            logger.debug("  Batch %d for %s:%s, cursor=%s", batch_num, origin_id, table_name, cursor)
                            result = await extract_data_impl(
                                origin_config=origin_config,
                                table_name=table_name,
                                columns=columns,
                                batch_size=default_batch_size,
                                cursor=cursor,
                                watermark=watermark,
                                pk_columns=pk_columns,
                                filter=filter_chunk,
                            )

                            batch_rows = result.get("rows", [])
                            total_fetched += len(batch_rows)
                            logger.debug("  Batch %d returned %d rows, total=%d, next_cursor=%s", batch_num, len(batch_rows), total_fetched, result.get("next_cursor"))

                            # Tag rows with origin metadata. _origin_id_row must be
                            # stable and unique: resolve/unify use
                            # "{_origin_id}:{_origin_table}:{_origin_id_row}" as the
                            # cross-node record key, and cluster ids are derived
                            # from it — so it must identify the SAME source row on
                            # every run. Prefer the real primary key; the ordinal
                            # fallback only applies to tables that have none, and
                            # is not stable across incremental runs.
                            row_base = total_fetched - len(batch_rows)
                            for row_idx, row in enumerate(batch_rows):
                                row["_origin_id"] = origin_id
                                row["_origin_table"] = table_name
                                row["_origin_id_row"] = _stable_row_key(
                                    row, pk_columns, row_base + row_idx
                                )

                            # Straight to disk. The batch is the only thing this
                            # node ever holds in memory.
                            rows_written += spool.append(entity_type, batch_rows)

                            # Phase 1: collect the subject entity's ids so the
                            # dependent tables can be restricted to them. Uses the
                            # primary key, which is what a foreign key references.
                            if (
                                scope_active
                                and entity_type == subject_entity_name
                                and pk_columns
                            ):
                                pk = pk_columns[0]
                                bucket = subject_ids.setdefault(origin_id, [])
                                for row in batch_rows:
                                    val = row.get(pk)
                                    if val is not None:
                                        bucket.append(val)

                            # Rows are ordered ascending by the watermark column
                            # when one is pushed down, so the last row of the
                            # last non-empty batch carries the new high-water mark
                            # FOR THIS CHUNK. Chunks are independent id subsets with
                            # no ordering relationship between them, so the overall
                            # high-water mark is the max seen across every chunk.
                            if watermark_column and batch_rows:
                                candidate_wm = batch_rows[-1].get(watermark_column)
                                if last_watermark_value is None:
                                    last_watermark_value = candidate_wm
                                else:
                                    try:
                                        if candidate_wm is not None and candidate_wm > last_watermark_value:
                                            last_watermark_value = candidate_wm
                                    except TypeError:
                                        last_watermark_value = candidate_wm

                            cursor = result.get("next_cursor")
                            if cursor is None:
                                break

                        except Exception as exc:
                            # A batch failing part-way through a table used to
                            # `break` and keep whatever had arrived, which then
                            # merged as though the table were complete — a silent,
                            # confident, wrong answer. The partial rows are dropped
                            # and the whole run stops at the error gate instead.
                            classified = classify_exception(exc)
                            table_failed = (
                                f"{origin_id}:{table_name} failed on batch "
                                f"{batch_num} after {total_fetched} row(s): "
                                f"{type(classified).__name__}: {classified}"
                            )
                            logger.error(table_failed, exc_info=True)
                            errors.append(table_failed)
                            break

                    if table_failed:
                        break

                if table_failed:
                    # Roll the partial rows back out. No watermark is recorded
                    # either, so a later re-run re-reads this table from its
                    # last known-good position rather than skipping the rows
                    # this attempt never managed to read.
                    discarded = spool.truncate_after(entity_type, rows_before_table)
                    rows_written = rows_before_table
                    failed_tables.append({
                        "entity_type": entity_type,
                        "origin_id": origin_id,
                        "table_name": table_name,
                        "rows_discarded": discarded,
                        "detail": table_failed,
                    })
                    logger.error(
                        "Discarding %d partially-extracted row(s) from %s:%s — "
                        "a partial table cannot be merged as if it were whole.",
                        discarded, origin_id, table_name,
                    )
                    continue

                if watermark_column and last_watermark_value is not None:
                    extraction_watermarks[tracking_key] = {
                        "origin_id": origin_id,
                        "table_name": table_name,
                        "watermark_column": watermark_column,
                        "watermark_kind": watermark_kind,
                        "last_value": str(last_watermark_value),
                        "rows_extracted": total_fetched,
                    }

                logger.info(
                    "Extracted %d rows from %s:%s for entity '%s'",
                    total_fetched,
                    origin_id,
                    table_name,
                    entity_type,
                )

            # State gets a handle, not the rows.
            extracted_data[entity_type] = make_handle(
                run_id, entity_type, rows_written
            )
            logger.debug("Entity '%s' total rows: %d", entity_type, rows_written)

    logger.debug("Total entities in extracted_data: %d", len(extracted_data))
    for et, handle in extracted_data.items():
        logger.debug("  Entity '%s': %d rows", et, _handle_count(handle))
    logger.debug("Total errors: %d", len(errors))

    # A run built on tables that could not be read in full, or missing an
    # EXPLICITLY requested entity entirely, is not a successful run. Stop and
    # say so. A missing INFERRED entity (a wildcard guess that didn't pan out)
    # is recorded but does not escalate -- see missing_inferred above.
    failure: dict[str, Any] = {}
    if failed_tables or missing_explicit:
        parts: list[str] = []
        if failed_tables:
            parts.append(
                f"{len(failed_tables)} table(s) could not be read completely: "
                + "; ".join(f["detail"] for f in failed_tables)
            )
        if missing_explicit:
            parts.append(
                "no source table could be found for entity(s): "
                + ", ".join(missing_explicit)
            )
        failure = {
            "node": "extract_data",
            "error": (
                "Extraction is incomplete, so merging now would produce a "
                "partial result reported as a complete one. " + " Also, ".join(parts)
            ),
            "error_class": "DataQualityError",
            "disposition": "escalate",
            "context": {
                "failed_tables": failed_tables,
                "missing_entities": missing_explicit,
                "dropped_inferred_entities": missing_inferred,
                "rows_kept": {
                    e: _handle_count(h) for e, h in extracted_data.items()
                },
            },
        }
        logger.error("extract_data failing loudly: %s", failure["error"][:400])
    elif missing_inferred:
        logger.warning(
            "extract_data: %d inferred entity type(s) had no source table and "
            "were silently dropped (not explicitly requested): %s",
            len(missing_inferred), missing_inferred,
        )

    logger.debug("EXIT extract_data")

    return {
        "extracted_data": extracted_data,
        "extraction_watermarks": extraction_watermarks,
        "errors": errors,
        "failure": failure,
        "dropped_inferred_entities": missing_inferred,
        "origins": state.get("origins", []),
        "destination": state.get("destination", {}),
        "llm_config": state.get("llm_config", {}),
        "extraction_config": state.get("extraction_config", {}),
        "target_entities": state.get("target_entities", []),
        "explicit_target_entities": state.get("explicit_target_entities", []),
        "schemas": state.get("schemas", {}),
        "dependency_tree": state.get("dependency_tree", {}),
        "extraction_order": state.get("extraction_order", []),
        "entity_clusters": state.get("entity_clusters", {}),
        "unified_entities": state.get("unified_entities", {}),
        "field_mappings": state.get("field_mappings", {}),
        "pending_schema_changes": state.get("pending_schema_changes", []),
        "human_feedback": state.get("human_feedback"),
        "load_approval": state.get("load_approval"),
        "retry_counts": state.get("retry_counts", {}),
        "interrupt_data": None,
    }


def _scope_entry_for(
    entries: list[dict[str, Any]], origin_id: str, table_name: str
) -> dict[str, Any] | None:
    """Find the scope-plan entry for a specific (origin, table), or None.

    Matches on the bare table name too, since the plan may name ``companies``
    where discovery reports ``public.companies``.
    """
    bare = str(table_name).split(".")[-1]
    for e in entries or []:
        if str(e.get("origin_id")) != str(origin_id):
            continue
        et = str(e.get("table", ""))
        if et == table_name or et.split(".")[-1] == bare:
            return e
    return None


async def _resolve_link_via(
    origin_config: dict[str, Any],
    entry: dict[str, Any],
    subject_ids: list[Any],
) -> list[Any]:
    """Resolve a two-hop link through a junction table.

    "People who work at Nova" is not a column on the people table — it is a row
    in ``jobs`` (or ``employment_history``). This reads that junction, keeping
    the rows whose subject-side column is one of the subject ids, and returns
    the entity-side keys they point at (the person ids), de-duplicated.
    """
    via_table = entry.get("via_table")
    from_col = entry.get("via_from_column")
    to_col = entry.get("via_to_column")
    if not (via_table and from_col and to_col):
        return []
    keys: list[Any] = []
    # A broad scope match can produce many subject ids (e.g. every company
    # matching a common name); chunk here too rather than assume the subject
    # side is always small.
    for filter_chunk in _chunked_filters({from_col: {"op": "in", "value": list(subject_ids)}}):
        cursor = None
        while True:
            result = await extract_data_impl(
                origin_config=origin_config,
                table_name=via_table,
                columns=[to_col],
                batch_size=2000,
                cursor=cursor,
                filter=filter_chunk,
            )
            for row in result.get("rows", []):
                val = row.get(to_col)
                if val is not None:
                    keys.append(val)
            cursor = result.get("next_cursor")
            if cursor is None:
                break
    return list(dict.fromkeys(keys))


def _scope_filter_for(
    entry: dict[str, Any], subject: str, subject_ids: list[Any]
) -> tuple[dict[str, Any] | None, bool]:
    """Turn one scope-plan entry into an extraction filter.

    Returns ``(filter, skip)``. ``skip`` is True when the table must contribute
    nothing — a ``link`` table whose origin produced no subject ids. Passing an
    empty ``IN`` would be dropped and silently extract the whole table, which is
    the one outcome a scoped merge must never produce.
    """
    columns = entry.get("columns") or []
    if not columns:
        return None, True
    if entry.get("mode") == "link":
        if not subject_ids:
            return None, True
        return {columns[0]: {"op": "in", "value": list(subject_ids)}}, False
    # match: case-insensitive substring on the first named column.
    return {columns[0]: {"op": "ilike", "value": subject}}, False


def _candidates_from_selection(
    entity_type: str,
    entity_tables: dict[str, Any],
    schemas: dict[str, Any],
) -> list[dict[str, Any]]:
    """Turn the confirmed entity->table selection into extraction candidates.

    Looks each selected (origin, table) back up in the discovered schema so
    columns, primary key and watermark column come from the real metadata
    rather than from whatever the selection payload happened to carry.
    """
    selected = entity_tables.get(entity_type) or []
    if not selected:
        return []

    # Index the discovered schema once, by both full and bare table name.
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for origin_id, schema_data in (schemas or {}).items():
        for table in schema_data.get("tables", []):
            name = table.get("name", "")
            full_name = table.get("full_name", name)
            for key in {full_name, name, full_name.split(".")[-1]}:
                if key:
                    index.setdefault((origin_id, key), (table, full_name))

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in selected:
        origin_id = str(item.get("origin_id") or "")
        table_name = str(item.get("table") or "")
        found = index.get((origin_id, table_name)) or index.get(
            (origin_id, table_name.split(".")[-1])
        )
        if found is None:
            logger.warning(
                "Selected table %s:%s for entity '%s' is not in the discovered "
                "schema — skipping it.", origin_id, table_name, entity_type,
            )
            continue
        table, full_name = found
        if (origin_id, full_name) in seen:
            continue
        seen.add((origin_id, full_name))
        candidates.append({
            "origin_id": origin_id,
            "table_name": full_name,
            "columns": table.get("columns", []),
            "foreign_keys": table.get("foreign_keys", []),
            "primary_key": table.get("primary_key", []),
            "watermark_column": table.get("watermark_column"),
            "watermark_kind": table.get("watermark_kind"),
            "selection_reason": item.get("reason", ""),
            "selection_source": item.get("source", ""),
        })
    return candidates


def _stable_row_key(
    row: dict[str, Any], pk_columns: list[str], ordinal: int
) -> str:
    """Identify a source row the same way on every run.

    Uses the table's primary key when the extracted columns include it (the
    common case), so a row keeps its identity across incremental runs and
    cluster ids stay stable. Falls back to the batch ordinal for tables with
    no primary key — correct within a run, but not across runs; such tables
    also have no watermark, so they are fully re-extracted each time.
    """
    if pk_columns:
        values = [row.get(c) for c in pk_columns]
        if all(v is not None for v in values):
            return "|".join(str(v) for v in values)
    for fallback in ("id", "_id"):
        if row.get(fallback) is not None:
            return str(row[fallback])
    return f"#{ordinal}"


def _find_entity_tables(
    entity_type: str, schemas: dict[str, Any]
) -> list[dict[str, Any]]:
    """Find tables that plausibly contain the given entity type.

    Returns list of {origin_id, table_name, columns, full_name}.
    """
    candidates: list[dict[str, Any]] = []

    for origin_id, schema_data in schemas.items():
        for table in schema_data.get("tables", []):
            table_name = table.get("name", "")
            full_name = table.get("full_name", table_name)

            if _table_matches_entity(table_name, full_name, entity_type):
                candidates.append({
                    "origin_id": origin_id,
                    "table_name": full_name,
                    "columns": table.get("columns", []),
                    "foreign_keys": table.get("foreign_keys", []),
                    "primary_key": table.get("primary_key", []),
                    "watermark_column": table.get("watermark_column"),
                    "watermark_kind": table.get("watermark_kind"),
                })

    return candidates


def _table_matches_entity(table_name: str, full_name: str, entity_type: str) -> bool:
    """Does a table name match an entity concept?

    Uses the shared alias groups so canonical singular concepts from the
    LLM ("company", "person") match irregular plurals and synonyms across
    origins ("companies", "organizations", "people", "persons").

    Tables whose full name isn't a known concept (e.g. "person_data",
    "customer_addresses") fall back to per-token matching, so entity-prefixed
    satellite tables are still discovered. Tables that ARE a known concept
    (e.g. "org_founders" -> founder group) never token-match, so they can't
    leak into a different entity ("company") via a shared token ("org").
    """
    from src.orchestrator.utils.entity_aliases import _group_of, concept_matches

    for name in (table_name, full_name):
        if concept_matches(entity_type, name):
            return True

    bare = full_name.lower().split(".")[-1].strip() or table_name.lower().strip()
    if _group_of(bare) is not None:
        return False
    return any(concept_matches(entity_type, token) for token in bare.split("_") if token)