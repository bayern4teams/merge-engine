"""
map_fields node: Maps unified fields to destination columns.

Calls map_fields_impl for each entity type, collects schema changes
and unmapped fields for human review.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
    # Only THIS call's own errors (Phase 0 Sec 0.8) - see load_data_node's
    # identical fix for why seeding with the existing history double-counted
    # it through operator.add's reducer.
    errors: list[str] = []
    field_mappings: dict[str, dict] = dict(state.get("field_mappings", {}))
    pending_schema_changes: list[dict] = list(state.get("pending_schema_changes", []))
    unmapped_fields: dict[str, list] = dict(state.get("unmapped_fields", {}))

    logger.debug("target_entities: %s", target_entities)
    logger.debug("unified_entities keys: %s", list(unified_entities.keys()))
    for ue in unified_entities:
        recs = unified_for(state, ue)
        logger.debug("  Entity '%s': %d unified records", ue, len(recs))

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

    # ── Phase 1: sequential prep (Part 1.5 B.1) ────────────────────────
    # Everything here is either pure CPU (the unified-schema scan) or the
    # ONE shared resource that must not be touched concurrently:
    # dest_connector.get_table_schema(). A psycopg2/databricks connection
    # is not safe for concurrent use, so every schema read happens here,
    # up front, before any `await map_fields_impl(...)`. Once this loop
    # finishes, nothing below touches dest_connector again.
    llm_tasks: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for entity_type in target_entities:
        records = unified_for(state, entity_type)
        logger.debug("Processing entity '%s': %d unified records", entity_type, len(records))
        if not records:
            logger.info("No unified records for entity '%s', skipping field mapping.", entity_type)
            field_mappings[entity_type] = {}
            continue

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
        dest_columns: list[dict[str, Any]] = []
        if dest_connector is not None:
            try:
                dest_columns = dest_connector.get_table_schema(dest_table).get("columns", [])
            except Exception as exc:
                logger.warning("Could not read schema for dest table '%s': %s", dest_table, exc)
        if not dest_columns:
            dest_columns = [
                {"name": "id", "type": "STRING"},
                {"name": "_source_ids", "type": "STRING"},
            ]
        dest_schema_info = {
            "table": dest_table,
            "columns": dest_columns,
        }

        # Build unified schema by scanning up to _MAX_SAMPLE_RECORDS records,
        # collecting up to _MAX_SAMPLE_VALUES distinct non-null sample values
        # per field. Every record's keys are unioned in, not just the first
        # few, and a field's "type" is set from the first NON-null value it
        # has — a field whose first-seen record happened to be null used to
        # get stuck typed as NoneType forever, even once real values showed up.
        unified_schema: dict[str, Any] = {}
        for rec in records[:_MAX_SAMPLE_RECORDS]:
            for key, value in rec.items():
                if key.startswith("_"):
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
        logger.debug("Unified schema fields: %d", len(unified_schema))
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
            existing = {str(c.get("name", "")).lower() for c in dest_columns}
            reconciled: dict[str, Any] = {}
            dropped: list[dict[str, Any]] = []
            for source_field, target in pinned_mapping.items():
                column = target.get("dest_column") if isinstance(target, dict) else target
                if column and str(column).lower() in existing:
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

        llm_tasks.append((entity_type, unified_schema, dest_schema_info))

    # ── Phase 2: the actual LLM round-trips, concurrent (Part 1.5 B.1) ─
    # These are independent per-entity LLM calls with no shared state left
    # to touch (dest_connector was fully drained in Phase 1) — wall time
    # was sum(latency), now max(latency). `return_exceptions=True` so one
    # entity's failure degrades to that entity's error entry, matching the
    # per-entity try/except semantics this replaces, rather than cancelling
    # its siblings.
    logger.debug("Dispatching %d field-mapping LLM call(s) concurrently", len(llm_tasks))
    llm_results = await asyncio.gather(
        *(
            map_fields_impl(
                entity_type=entity_type,
                unified_schema=unified_schema,
                destination_schema=dest_schema_info,
                llm_config=with_retry_hint(state.get("llm_config") or None, state, "map_fields"),
            )
            for entity_type, unified_schema, dest_schema_info in llm_tasks
        ),
        return_exceptions=True,
    )

    # ── Phase 3: sequential merge, in target_entities order ────────────
    # `asyncio.gather` preserves input order (same pattern as
    # discover_schemas.py's D.4.1 parallelization), so llm_results lines up
    # 1:1 with llm_tasks. Merging here — not inside the gather — keeps
    # pending_schema_changes/errors ordering deterministic across runs,
    # exactly as it was when this loop ran strictly sequentially.
    for (entity_type, _unified_schema, _dest_schema_info), outcome in zip(
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
