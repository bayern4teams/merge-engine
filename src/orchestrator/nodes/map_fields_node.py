"""
map_fields node: Maps unified fields to destination columns.

Calls map_fields_impl for each entity type, collects schema changes
and unmapped fields for human review.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.map_fields import _map_type_generic, map_fields_impl
from src.orchestrator.spool import unified_for
from src.mcp_server.connectors import create_connector
from src.orchestrator.utils.retry import TransientError
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
    errors: list[str] = list(state.get("errors", []))
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
        logger.debug("Discovered %d destination tables: %s", len(available_dest_tables), available_dest_tables)
    except Exception as exc:
        logger.warning("Could not discover destination tables: %s", exc)

    for entity_type in target_entities:
        records = unified_for(state, entity_type)
        logger.debug("Processing entity '%s': %d unified records", entity_type, len(records))
        if not records:
            logger.info("No unified records for entity '%s', skipping field mapping.", entity_type)
            field_mappings[entity_type] = {}
            continue

        # Get destination table schema
        dest_table = dest_tables.get(entity_type, entity_type) if isinstance(dest_tables, dict) else entity_type
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
                info = unified_schema.setdefault(key, {
                    "type": "NoneType",
                    "sample_value": None,
                    "sample_values": [],
                })
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
            # The mapping says which destination column each field goes to,
            # but says nothing about whether that column EXISTS. Against a
            # fresh destination it does not, and skipping this left a table
            # with one column instead of nine — the pinned mapping produced a
            # *worse* schema than deriving it. So the columns still have to be
            # reconciled against the live destination on every run.
            existing = {
                str(c.get("name", "")).lower() for c in dest_columns
            }
            schema_changes = []
            for source_field, target in pinned_mapping.items():
                column = (
                    target.get("dest_column")
                    if isinstance(target, dict) else target
                )
                if not column or str(column).lower() in existing:
                    continue
                schema_changes.append({
                    "table": dest_table,
                    "column": column,
                    "data_type": _map_type_generic(
                        unified_schema.get(source_field, {}).get(
                            "type", "STRING"
                        )
                    ),
                })
            pending_schema_changes.extend(schema_changes)
            field_mappings[entity_type] = pinned_mapping
            logger.info(
                "Reusing pinned field mapping for '%s' (%d fields, %d column(s) "
                "to create) from profile %r",
                entity_type, len(pinned_mapping), len(schema_changes),
                state.get("profile_name"),
            )
            continue

        try:
            logger.debug("Calling map_fields_impl for entity '%s'", entity_type)
            result = await map_fields_impl(
                entity_type=entity_type,
                unified_schema=unified_schema,
                destination_schema=dest_schema_info,
                llm_config=with_retry_hint(
                    state.get("llm_config") or None, state, "map_fields"
                ),
            )
            logger.debug("map_fields_impl result keys: %s", list(result.keys()))

            mapping = result.get("field_mapping", {})
            field_mappings[entity_type] = mapping
            logger.debug("Field mapping for '%s': %d mappings", entity_type, len(mapping))
            for mname, mval in mapping.items():
                logger.debug("  Mapping '%s': requires_schema_change=%s", mname, mval.get("requires_schema_change", False))

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

        except Exception as exc:
            msg = f"Field mapping failed for '{entity_type}': {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)
            field_mappings[entity_type] = {}

    logger.debug("Total field_mappings: %d", len(field_mappings))
    logger.debug("Total pending_schema_changes: %d", len(pending_schema_changes))
    logger.debug("Total unmapped_fields entities: %d", len(unmapped_fields))
    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT map_fields")

    return {
        "field_mappings": field_mappings,
        "pending_schema_changes": pending_schema_changes,
        "unmapped_fields": unmapped_fields,
        "errors": errors,
        "origins": state.get("origins", []),
        "destination": state.get("destination", {}),
        "llm_config": state.get("llm_config", {}),
        "extraction_config": state.get("extraction_config", {}),
        "target_entities": target_entities,
        "schemas": state.get("schemas", {}),
        "dependency_tree": state.get("dependency_tree", {}),
        "extraction_order": state.get("extraction_order", []),
        "extracted_data": state.get("extracted_data", {}),
        "entity_clusters": state.get("entity_clusters", {}),
        "unified_entities": unified_entities,
        "human_feedback": state.get("human_feedback"),
        "load_approval": state.get("load_approval"),
        "retry_counts": state.get("retry_counts", {}),
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
    if any(unmapped_fields.get(entity_type) for entity_type in unmapped_fields):
        return True

    return False