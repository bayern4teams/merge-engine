"""
map_fields node: Maps unified fields to destination columns.

Calls map_fields_impl for each entity type, collects schema changes
and unmapped fields for human review.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

from src.mcp_server.tools.dest_family import build_family_map
from src.mcp_server.tools.map_fields import map_fields_impl
from src.orchestrator.spool import unified_for
from src.orchestrator.utils.retry_hints import with_retry_hint

logger = logging.getLogger("merge-engine.orchestrator.map_fields")

from src.orchestrator.utils.entity_aliases import resolve_dest_table  # noqa: E402

# How many records to scan for field discovery/samples. 5 was too narrow: a
# field only present on a minority of records (Boeing origin2's
# firstName/lastName/middleName) was never seen at all, and the FIRST record
# to introduce a key set its "type" even when that record's value was None —
# both silently produced empty/mistyped columns. Scanning further and typing
# from the first NON-null value fixes both (Fix #3, Phase 4.3).
_MAX_SAMPLE_RECORDS = 500
_MAX_SAMPLE_VALUES = 10


def _entity_tables(state: dict[str, Any], entity_type: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """(origin_id, table) for every discovered table that belongs to ``entity_type``."""
    schemas = state.get("schemas", {})
    entity_tables = (state.get("entity_tables") or {}).get(entity_type, [])

    target_tables_by_origin: dict[str, set[str]] = {}
    if entity_tables:
        for et in entity_tables:
            oid = str(et.get("origin_id", "")).strip()
            tbl = str(et.get("table", "")).strip()
            if oid and tbl:
                target_tables_by_origin.setdefault(oid, set()).add(tbl.lower())

    for origin_id, schema_data in schemas.items():
        matched_tables = target_tables_by_origin.get(origin_id)
        for table in schema_data.get("tables", []):
            table_name = str(table.get("name", "")).strip()
            full_name = str(table.get("full_name") or table_name).strip()

            if matched_tables is not None:
                if (
                    table_name.lower() not in matched_tables
                    and full_name.lower() not in matched_tables
                ):
                    continue
            else:
                from src.orchestrator.utils.entity_aliases import concept_matches

                if not (
                    concept_matches(entity_type, table_name)
                    or concept_matches(entity_type, full_name)
                ):
                    continue
            yield origin_id, table


def _metadata_only_fields(state: dict[str, Any], entity_type: str) -> set[str]:
    """P12: lower-cased field names that are ETL metadata in every discovered table of the entity
    that carries them. A field any origin treats as business stays mappable; a unified record has
    no single origin, so that is the only consistent reading."""
    meta: set[str] = set()
    business: set[str] = set()
    for _origin_id, table in _entity_tables(state, entity_type):
        flagged = {str(c).lower() for c in table.get("metadata_columns") or []}
        for col in table.get("columns", []):
            cname = str(col.get("name", "")).strip().lower()
            (meta if cname in flagged else business).add(cname)
    return meta - business


def _build_source_schema_from_discovered(state: dict[str, Any], entity_type: str) -> dict[str, Any]:
    """Collect source fields across origins for entity_type when run before extraction.

    ETL metadata columns (P12) are skipped: an unmapped column is never projected, and the
    column itself stays in the corpus.
    """
    source_schema: dict[str, Any] = {}
    for origin_id, table in _entity_tables(state, entity_type):
        table_name = str(table.get("name", "")).strip()
        full_name = str(table.get("full_name") or table_name).strip()
        metadata = {str(c).lower() for c in table.get("metadata_columns") or []}
        sample_row = table.get("sample_row", {})
        for col in table.get("columns", []):
            cname = str(col.get("name", "")).strip()
            if not cname or cname.startswith("_") or cname.lower() in metadata:
                continue
            col_type = str(col.get("type", "STRING"))
            sample_val = sample_row.get(cname)

            info = {
                "type": col_type,
                "sample_value": sample_val,
                "sample_values": [sample_val] if sample_val is not None else [],
                "origin_id": origin_id,
                "table": full_name or table_name,
                "source_field": cname,
            }
            source_schema[cname] = info

    return source_schema


async def map_fields(state: dict[str, Any]) -> dict[str, Any]:
    """Map unified entity fields to destination columns.

    Parameters
    ----------
    state : dict
        MergeState with ``unified_entities``, ``destination``, ``target_entities``.

    Returns
    -------
    dict
        Update dict with ``field_mappings``, ``pending_schema_changes``, ``errors``.
    """
    logger.debug("ENTER map_fields")
    logger.debug("State keys: %s", list(state.keys()))
    unified_entities: dict[str, list[dict]] = state.get("unified_entities", {})
    destination = state.get("destination")
    target_entities: list[str] = state.get("target_entities", [])
    errors: list[str] = []
    field_mappings: dict[str, dict] = dict(state.get("field_mappings", {}))
    pending_schema_changes: list[dict] = list(state.get("pending_schema_changes", []))
    unmapped_fields: dict[str, list] = dict(state.get("unmapped_fields", {}))

    logger.debug("target_entities: %s", target_entities)
    logger.debug("unified_entities keys: %s", list(unified_entities.keys()))

    # Get destination type (handles both dict and Config object)
    if isinstance(destination, dict):
        dest_type = destination.get("type", "postgresql")
        dest_tables = destination.get("tables", {})
    else:
        dest_type = getattr(destination, "type", "postgresql")
        dest_tables = getattr(destination, "tables", {})
    logger.debug("destination type: %s", dest_type)

    # Discover the real destination tables/columns so mapping targets
    # actual schema instead of a stub.
    dest_connector = None
    available_dest_tables: list[str] = []
    try:
        from src.mcp_server.tools.load_data import _get_destination_connector

        dest_connector = _get_destination_connector(
            destination if isinstance(destination, dict) else None
        )
        available_dest_tables = dest_connector.list_tables()
        logger.debug(
            "Discovered %d destination tables: %s",
            len(available_dest_tables),
            available_dest_tables,
        )
    except Exception as exc:
        logger.warning("Could not discover destination tables: %s", exc)

    # Phase 6.2: the real destination FK graph, so a source field with no
    # home on an entity's own table (Company.totalInvestment) can still be
    # offered `finance.amount` instead of being dropped. Empty for a
    # connector without FK introspection (ClickHouse) or if this fails --
    # mapping still works, just scoped to one table per entity as before.
    family_map: dict[str, set[str]] = {}
    if dest_connector is not None:
        try:
            family_map = build_family_map(dest_connector)
        except Exception as exc:
            logger.warning("Could not build destination family map: %s", exc)
    # Schema lookups are cached across entities -- several entities can
    # share the same family table (e.g. both `company` and `person` may
    # each border `location`), and get_table_schema is a real DB call.
    dest_schema_cache: dict[str, list[dict[str, Any]]] = {}

    def _schema_for_table(table: str) -> list[dict[str, Any]]:
        if table in dest_schema_cache:
            return dest_schema_cache[table]
        cols: list[dict[str, Any]] = []
        if dest_connector is not None:
            try:
                cols = dest_connector.get_table_schema(table).get("columns", [])
            except Exception as exc:
                logger.warning("Could not read schema for family table '%s': %s", table, exc)
        dest_schema_cache[table] = cols
        return cols

    llm_tasks: list[tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    for entity_type in target_entities:
        records = unified_for(state, entity_type)
        logger.debug("Processing entity '%s': %d unified records", entity_type, len(records))

        # Get destination table schema
        dest_table = (
            dest_tables.get(entity_type, entity_type)
            if isinstance(dest_tables, dict)
            else entity_type
        )
        if available_dest_tables:
            resolved = resolve_dest_table(entity_type, available_dest_tables)
            if resolved:
                dest_table = resolved
        logger.debug("Destination table: %s", dest_table)

        # Build destination schema info from the real table when possible
        dest_columns: list[dict[str, Any]] = _schema_for_table(dest_table)
        if not dest_columns:
            dest_columns = [
                {"name": "id", "type": "STRING"},
                {"name": "_source_ids", "type": "STRING"},
            ]
        dest_schema_info = {
            "table": dest_table,
            "columns": dest_columns,
        }

        # Phase 6.2: sibling tables this entity's own table has a real FK
        # relationship with, so map_fields_impl can widen its candidate
        # search past a single table when nothing on the primary table fits.
        family_schemas: list[dict[str, Any]] = [
            {"table": fam_table, "columns": cols}
            for fam_table in sorted(family_map.get(dest_table, set()))
            if (cols := _schema_for_table(fam_table))
        ]

        unified_schema: dict[str, Any] = {}
        if records:
            metadata_only = _metadata_only_fields(state, entity_type)
            for rec in records[:_MAX_SAMPLE_RECORDS]:
                for key, value in rec.items():
                    if key.startswith("_") or key.lower() in metadata_only:
                        continue
                    info = unified_schema.setdefault(
                        key,
                        {
                            "type": "NoneType",
                            "sample_value": None,
                            "sample_values": [],
                        },
                    )
                    if value is None:
                        continue
                    if info["type"] == "NoneType":
                        info["type"] = type(value).__name__
                        info["sample_value"] = value
                    if (
                        len(info["sample_values"]) < _MAX_SAMPLE_VALUES
                        and value not in info["sample_values"]
                    ):
                        info["sample_values"].append(value)
        else:
            # Build schema from discovered origin schemas (upfront mapping before extraction)
            unified_schema = _build_source_schema_from_discovered(state, entity_type)

        if not unified_schema:
            logger.info(
                "No fields discovered for entity '%s', skipping field mapping.", entity_type
            )
            field_mappings[entity_type] = {}
            continue

        logger.debug("Unified/source schema fields: %d", len(unified_schema))
        for fname, finfo in unified_schema.items():
            logger.debug("  Field '%s': type=%s", fname, finfo.get("type"))

        # A pinned mapping is reused verbatim (Phase O.4). This is the
        # decision that made re-runs irreproducible: two runs of identical
        # data mapped customer_id differently and built destination schemas
        # of 15 and 13 columns. Re-asking would reintroduce exactly that.
        pinned_mapping = (state.get("field_mappings") or {}).get(entity_type)
        if pinned_mapping and state.get("profile_name"):
            # A pinned mapping says which destination column each field goes
            # to, but says nothing about whether that column EXISTS. The
            # destination schema is a fixed contract, so a pinned target that
            # is not there is dropped to unmapped rather than created — a
            # profile pinned before that rule existed must not smuggle columns
            # back in.
            existing = {(dest_table.lower(), str(c.get("name", "")).lower()) for c in dest_columns}
            for fam in family_schemas:
                fam_table = str(fam.get("table", "")).lower()
                existing |= {
                    (fam_table, str(c.get("name", "")).lower()) for c in fam.get("columns", [])
                }
            reconciled: dict[str, Any] = {}
            dropped: list[dict[str, Any]] = []
            for source_field, target in pinned_mapping.items():
                column = target.get("dest_column") if isinstance(target, dict) else target
                target_table = (
                    (target.get("dest_table") or dest_table)
                    if isinstance(target, dict)
                    else dest_table
                )
                if column and (str(target_table).lower(), str(column).lower()) in existing:
                    reconciled[source_field] = target
                    continue
                dropped.append(
                    {
                        "field": source_field,
                        "confidence": 0.0,
                        "suggested_column": column or None,
                        "reason": (
                            f"Pinned mapping targets column '{column}', which does "
                            f"not exist in '{dest_table}'; destination schema is "
                            f"authoritative"
                        ),
                    }
                )
            field_mappings[entity_type] = reconciled
            unmapped_fields[entity_type] = dropped
            logger.info(
                "Reusing pinned field mapping for '%s' (%d fields kept, %d "
                "dropped as missing destination columns) from profile %r",
                entity_type,
                len(reconciled),
                len(dropped),
                state.get("profile_name"),
            )
            continue

        llm_tasks.append((entity_type, unified_schema, dest_schema_info, family_schemas))

    # ── Phase 2: the actual LLM round-trips, concurrent (Part 1.5 B.1) ─
    # These are independent per-entity LLM calls with no shared state left
    # to touch for its own concurrent-Phase-1 reasons (schema/family-map
    # discovery, all synchronous) — wall time was sum(latency), now
    # max(latency). B.3.1: map_fields_impl's own Phase 0 (Valentine) still
    # calls dest_connector.sample_distinct_values per undecided field, so
    # it stays passed through here; that's async I/O, not a repeat of the
    # synchronous Phase 1 work this comment originally described.
    # `return_exceptions=True` so one entity's failure degrades to that
    # entity's error entry, matching the per-entity try/except semantics
    # this replaces, rather than cancelling its siblings.
    logger.debug("Dispatching %d field-mapping LLM call(s) concurrently", len(llm_tasks))
    llm_results = await asyncio.gather(
        *(
            map_fields_impl(
                entity_type=entity_type,
                unified_schema=unified_schema,
                destination_schema=dest_schema_info,
                llm_config=with_retry_hint(state.get("llm_config") or None, state, "map_fields"),
                family_schemas=task_family_schemas,
                connector=dest_connector,
            )
            for entity_type, unified_schema, dest_schema_info, task_family_schemas in llm_tasks
        ),
        return_exceptions=True,
    )

    # ── Phase 3: sequential merge, in target_entities order ────────────
    # `asyncio.gather` preserves input order (same pattern as
    # discover_schemas.py's D.4.1 parallelization), so llm_results lines up
    # 1:1 with llm_tasks. Merging here — not inside the gather — keeps
    # pending_schema_changes/errors ordering deterministic across runs,
    # exactly as it was when this loop ran strictly sequentially.
    for (entity_type, _unified_schema, _dest_schema_info, _family_schemas), outcome in zip(
        llm_tasks, llm_results, strict=True
    ):
        # isinstance(..., Exception), not BaseException: matches the original
        # bare `except Exception` this replaces — a genuine BaseException
        # (e.g. CancelledError) must still propagate, not be swallowed as
        # "this entity's mapping failed".
        if isinstance(outcome, Exception):
            msg = f"Field mapping failed for '{entity_type}': {outcome}"
            logger.error(msg, exc_info=outcome)
            errors.append(msg)
            field_mappings[entity_type] = {}
            continue
        if isinstance(outcome, BaseException):
            raise outcome

        result = outcome
        logger.debug("map_fields_impl result keys: %s", list(result.keys()))

        mapping = result.get("field_mapping", {})
        field_mappings[entity_type] = mapping
        logger.debug("Field mapping for '%s': %d mappings", entity_type, len(mapping))
        for mname, mval in mapping.items():
            logger.debug(
                "  Mapping '%s': requires_schema_change=%s",
                mname,
                mval.get("requires_schema_change", False),
            )

        schema_changes = result.get("schema_changes", [])
        pending_schema_changes.extend(schema_changes)
        logger.debug("Schema changes: %d", len(schema_changes))

        unmapped = result.get("unmapped_fields", [])
        # P1: ambiguous splits were left unsplit on purpose; the operator must see them (a field
        # the mapper already reported unmapped just gains its candidates).
        for amb in result.get("ambiguous_splits", []):
            known = next((u for u in unmapped if u.get("field") == amb["field"]), None)
            if known is None:
                unmapped.append(amb)
            else:
                known["candidates"] = amb["candidates"]
        unmapped_fields[entity_type] = unmapped
        logger.debug("Unmapped fields: %d", len(unmapped))

        logger.info(
            "Field mapping for '%s': %d mapped, %d unmapped, %d schema changes",
            entity_type,
            len([m for m in mapping.values() if not m.get("requires_schema_change")]),
            len(unmapped),
            len(schema_changes),
        )

    logger.debug("Total field_mappings: %d", len(field_mappings))
    logger.debug("Total pending_schema_changes: %d", len(pending_schema_changes))
    logger.debug("Total unmapped_fields entities: %d", len(unmapped_fields))
    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT map_fields")

    # Only the keys this node actually computes (Phase 0 Sec 0.8) - a key it
    # omits is left untouched by LangGraph's per-key TypedDict state update,
    # not cleared.
    return {
        "field_mappings": field_mappings,
        "pending_schema_changes": pending_schema_changes,
        "unmapped_fields": unmapped_fields,
        "errors": errors,
        "interrupt_data": None,
    }


def has_unmapped_fields(state: dict[str, Any]) -> bool:
    """Conditional edge: True if any entity has unmapped or low-confidence fields."""
    pending_schema_changes: list[dict] = state.get("pending_schema_changes", [])
    unmapped_fields: dict[str, list] = state.get("unmapped_fields", {})

    # If there are schema changes, human review is needed
    if pending_schema_changes:
        return True

    # If any entity has low-confidence unmapped fields, human review is needed
    return any(unmapped_fields.get(entity_type) for entity_type in unmapped_fields)
