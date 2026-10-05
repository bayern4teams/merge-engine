"""
load_data tool implementation.

Upserts unified records into the destination PostgreSQL table
using INSERT ... ON CONFLICT (MERGE-like behavior).
Handles conflict column and provenance tracking.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

from src.mcp_server.connectors.clickhouse_destination import ClickHouseDestinationConnector
from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
from src.orchestrator.utils.retry import PermanentError
from src.utils.id_utils import deterministic_uuid, generate_uuid

logger = logging.getLogger("merge-engine.tools.load_data")


def _resolve_upsert_key(connector: Any, dest_table: str) -> str:
    """The destination table's real primary-key column, or "id" as a
    fallback when introspection is unavailable or the table has none.

    ``load_batch``'s ``upsert_key`` must name a column that actually exists
    on the destination table. A literal ``"id"`` broke every load into a
    schema whose primary keys are named ``<entity>_id`` (this project's
    real ``kg`` schema: ``kg.company.company_id`` etc.) — the transformed
    record legitimately carried that column (Phase 6's universal-identity
    field, see ``_transform_records``), but ``load_batch`` rejected the
    whole batch before ever reaching Postgres because "id" itself wasn't
    among the table's columns. Asking the table its own primary key,
    instead of assuming a name, is the fix.
    """
    if hasattr(connector, "get_table_schema"):
        try:
            pk = connector.get_table_schema(dest_table).get("primary_key")
            if isinstance(pk, str) and pk:
                return pk
        except Exception:
            logger.debug(
                "Could not introspect primary key for '%s'; falling back to 'id'",
                dest_table,
                exc_info=True,
            )
    return "id"


def _align_identity_column(records: list[dict[str, Any]], upsert_key: str) -> None:
    """Copy each record's canonical identity (``"id"``) onto ``upsert_key``.

    ``_transform_records``/the child-row builder always guarantee a stable
    ``"id"`` value (cluster-derived, so idempotent across re-runs) — that is
    the identity ``load_batch`` must upsert on, regardless of what column
    name the destination table's own primary key happens to have. Mutates
    in place; a no-op when ``upsert_key`` already is ``"id"``.
    """
    if upsert_key == "id":
        return
    for rec in records:
        rec[upsert_key] = rec.get("id")


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normalize_field_name(name: str) -> str:
    """``companyName``/``company_name``/``CompanyName`` all normalize to the
    same ``company_name`` — so they resolve to the same fact_predicate
    instead of three near-duplicate ones."""
    spaced = _CAMEL_BOUNDARY_RE.sub("_", name)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def _looks_like_name_field(field: str) -> bool:
    lowered = field.lower()
    return "name" in lowered and "username" not in lowered


def _infer_fact_value(value: Any) -> tuple[str, Any] | None:
    """(value_kind, coerced_value) for a raw field value, or None to skip it
    (null/empty). Only TEXT/NUMERIC/DATE/BOOLEAN are inferred — anything
    that would need ENTITY/CODE/MONEY's extra reference data (see
    ``write_knowledge_fact``) is coerced to TEXT instead of dropped."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "BOOLEAN", value
    if isinstance(value, (int, float)):
        return "NUMERIC", value
    if isinstance(value, (date, datetime)):
        return "DATE", value
    if isinstance(value, (dict, list)):
        return "TEXT", json.dumps(value, default=str)
    return "TEXT", str(value)


def _write_entity_attributes_as_facts(
    connector: Any,
    dest_table: str,
    raw_records: list[dict[str, Any]],
    transformed_records: list[dict[str, Any]],
    upsert_key: str,
) -> int:
    """Write every non-null attribute of an entity-graph record as a
    ``knowledge_fact`` row (plus one ``entity_name`` row for whichever field
    looks like the record's name).

    This project's real destination shape has nowhere else for these values
    to go: ``kg.company`` etc. carry only their own id, every attribute is a
    fact keyed by a ``fact_predicate`` (see the forensic audit, §7/§11) —
    this is the load path that actually reaches it, rather than everything
    landing in ``unmapped_fields``. Best-effort per field: one field's
    failure is logged and skipped, never fails the whole load.
    """
    if not raw_records:
        return 0
    entity_type = dest_table.upper()
    source_id = connector.get_or_create_source(
        uri=f"merge-engine:{dest_table}",
        title=f"merge-engine automated load ({dest_table})",
    )
    name_type_id = connector.get_or_create_taxonomy_term("name_type", "PREFERRED", "Preferred")
    written = 0
    for raw_rec, t_rec in zip(raw_records, transformed_records, strict=True):
        entity_id = t_rec.get(upsert_key)
        if not entity_id:
            continue
        name_written = False
        for field, value in raw_rec.items():
            # "id" is the universal identity field _transform_records always
            # guarantees (see _align_identity_column) — already represented
            # by the entity row itself, not a real attribute.
            if field.startswith("_") or field in (upsert_key, "id"):
                continue
            inferred = _infer_fact_value(value)
            if inferred is None:
                continue
            value_kind, coerced_value = inferred

            if not name_written and value_kind == "TEXT" and _looks_like_name_field(field):
                try:
                    connector.write_entity_name(
                        item_id=deterministic_uuid("kg_name", entity_id),
                        entity_id=entity_id,
                        name=coerced_value,
                        name_type_term_id=name_type_id,
                    )
                    name_written = True
                    continue
                except Exception:
                    logger.debug(
                        "Could not write entity_name for %s (field %r)",
                        entity_id,
                        field,
                        exc_info=True,
                    )

            predicate_code = f"{dest_table}_{_normalize_field_name(field)}"
            try:
                predicate_id = connector.get_or_create_fact_predicate(
                    predicate_code, field, entity_type, value_kind
                )
                connector.write_knowledge_fact(
                    item_id=deterministic_uuid("kg_fact", entity_id, predicate_code),
                    entity_id=entity_id,
                    predicate_id=predicate_id,
                    value_kind=value_kind,
                    value=coerced_value,
                    source_id=source_id,
                )
                written += 1
            except Exception:
                logger.debug(
                    "Could not write knowledge_fact for %s.%s", dest_table, field, exc_info=True
                )
    return written


def _translate_foreign_keys(
    records: list[dict[str, Any]],
    foreign_keys: dict[str, str],
    cluster_lookup_for_table: dict[str, dict[str, str]],
) -> int:
    """Rewrite FK columns from source ids to the referenced rows' cluster ids.

    Must run on already-transformed records (dest-column keys) — ``foreign_keys``
    names destination columns like ``industry_id``, which do not exist on a raw
    record keyed by its origin field name (``sourceCategory``).

    A parent row is written under its cluster id, not the source id a child
    record's raw field carries, so an untranslated FK points at a row that does
    not exist and Postgres rejects the whole (atomic) load. An id with no known
    cluster is nulled rather than left dangling: the child row is still worth
    keeping, the unresolvable link is not.

    ``referenced_table`` missing from ``cluster_lookup_for_table`` entirely
    means the caller never resolved it to an entity type — that's genuinely
    unknown, so the value is left for the database to judge. But a table
    that *was* resolved and simply has zero clusters (an entity type with no
    records extracted this run, e.g. a source array field like technology's
    "categories" that was never its own extracted entity) means every value
    pointing at it is unconditionally unresolvable — nulling it is not a
    guess, it's the only correct answer, and leaving it as-is guarantees the
    exact FK violation this function exists to prevent.
    """
    translated = 0
    for record in records:
        for column, referenced_table in foreign_keys.items():
            value = record.get(column)
            if value is None or value == "":
                continue
            if referenced_table not in cluster_lookup_for_table:
                continue
            lookup = cluster_lookup_for_table[referenced_table]
            resolved = lookup.get(str(value))
            if resolved is not None:
                if resolved != value:
                    record[column] = resolved
                    translated += 1
            else:
                record[column] = None
                translated += 1
    return translated


async def load_data_impl(
    entity_type: str,
    records: list[dict[str, Any]],
    mapping: dict[str, Any] | None = None,
    destination_config: dict[str, Any] | None = None,
    table_name: str | None = None,
    connector: Any = None,
    foreign_keys: dict[str, str] | None = None,
    cluster_lookup_by_table: dict[str, dict[str, str]] | None = None,
    other_primary_tables: set[str] | None = None,
) -> dict[str, Any]:
    """Upsert unified records into the PostgreSQL destination.

    Parameters
    ----------
    entity_type : str
        The entity type being loaded.
    records : list[dict]
        Unified records to upsert. Each should have ``id`` as the universal UUID.
    mapping : dict, optional
        Field mapping dict from map_fields output:
        {source_field: {"dest_column": str, "confidence": float, ...}, ...}
    destination_config : dict, optional
        Destination connection config. If None, uses the global config.
    connector : optional
        An already-open destination connector to write through. The caller
        passes one when several entities must land in a single transaction —
        creating a fresh connector per entity would give each its own
        connection, and therefore its own independent commit.
    other_primary_tables : set[str], optional
        Destination tables every OTHER entity in this run already loads as
        its own independently-matched, deduplicated primary record. A
        cross-table mapping into one of these is never a genuine child
        write, even when the child table's own FK happens to point back at
        this entity (e.g. `company.industry_id -> industry.id`, discovered
        while processing the `industry` entity): company is not an
        anonymous child row per industry record, it is a full entity
        loaded on its own with its own real name/website/etc, and blindly
        appending a near-empty row for every unrelated industry record
        would duplicate it with ghost rows carrying no identifying data.

    Returns
    -------
    dict
        {
            "success": bool,
            "entity_type": str,
            "total_records": int,
            "inserted": int,
            "updated": int,
            "failed": int,
            "child_tables": {table: {"inserted": int, "updated": int}, ...},
        }
        ``child_tables`` (Phase 6.3) covers rows written into sibling
        destination tables for mapping entries tagged with a ``dest_table``
        other than this entity's own — empty when the mapping is entirely
        single-table, which is every call site before Phase 6.2.
    """
    logger.debug(
        "ENTER load_data_impl: entity_type=%s, records_count=%d", entity_type, len(records)
    )
    if not records:
        logger.debug("No records to load, returning early")
        return {
            "success": True,
            "entity_type": entity_type,
            "total_records": 0,
            "inserted": 0,
            "updated": 0,
            "failed": 0,
        }

    if connector is None:
        try:
            connector = _get_destination_connector(destination_config)
        except Exception as exc:
            raise PermanentError(f"Failed to create destination connector: {exc}") from exc

    # Get the destination table name — use entity_type as default (dynamic)
    dest_table = table_name or entity_type
    logger.debug("Destination table: %s", dest_table)

    # Phase 6.3: a mapping entry may be tagged with a ``dest_table`` other
    # than this entity's own (map_fields_impl's widened family search, Phase
    # 6.2) — those fields belong in a sibling table's child row, not this
    # one. Only the entries destined for THIS table drive the parent
    # transform; everything else is written after, once the parent's ids
    # are known.
    mapping_by_table = _group_mapping_by_dest_table(mapping, dest_table)
    parent_mapping = mapping_by_table.pop(dest_table, {})

    # Transform records using mapping
    logger.debug(
        "Transforming %d records with mapping=%s", len(records), parent_mapping is not None
    )
    transformed_records = _transform_records(records, parent_mapping, dest_table)
    logger.debug("Transformed %d records", len(transformed_records))

    if foreign_keys and cluster_lookup_by_table:
        translated = _translate_foreign_keys(
            transformed_records, foreign_keys, cluster_lookup_by_table
        )
        if translated:
            logger.info(
                "Entity '%s': resolved %d foreign-key value(s) to their "
                "referenced rows' destination ids.",
                entity_type,
                translated,
            )

    # Build provenance tracking
    for rec in transformed_records:
        rec["_loaded_at"] = _get_timestamp()
        rec["_source_count"] = rec.get("_source_count", 1)

    try:
        if hasattr(connector, "ensure_table"):
            connector.ensure_table(dest_table)
        upsert_key = _resolve_upsert_key(connector, dest_table)
        _align_identity_column(transformed_records, upsert_key)

        # This project's real destination is an entity/knowledge-graph
        # schema, not a flat table per entity: a "company" row can't exist
        # until its supertype "entity" (and "organization") rows do, and
        # every attribute beyond the bare id belongs in knowledge_fact, not
        # a destination column (see the forensic audit, §7/§11). Detected
        # generically via FK introspection — a no-op (entity_chain is None)
        # against any other, flat destination schema, so this changes
        # nothing for the existing test fixtures or a non-kg destination.
        entity_chain = None
        if hasattr(connector, "get_entity_supertype_chain"):
            try:
                entity_chain = connector.get_entity_supertype_chain(dest_table)
            except Exception:
                logger.debug(
                    "Could not determine entity-supertype chain for '%s'",
                    dest_table,
                    exc_info=True,
                )
        if entity_chain is not None:
            for rec in transformed_records:
                rec_entity_id = rec.get(upsert_key)
                if rec_entity_id:
                    connector.ensure_entity_chain(rec_entity_id, dest_table, entity_chain)

        logger.debug(
            "Calling connector.load_batch: table=%s, records=%d, upsert_key=%s",
            dest_table,
            len(transformed_records),
            upsert_key,
        )
        # P4: dimension rows go in BEFORE the parent rows that link to them (the
        # link is a real foreign key), and consume their mapping entries.
        dimension_results = _materialise_dimensions(
            connector,
            dest_table,
            records,
            transformed_records,
            mapping_by_table,
            other_primary_tables,
        )
        result = connector.load_batch(
            table_name=dest_table,
            records=transformed_records,
            mapping=parent_mapping,
            upsert_key=upsert_key,
        )
        logger.debug("load_batch completed: %s", result)

        facts_written = 0
        if entity_chain is not None:
            facts_written = _write_entity_attributes_as_facts(
                connector, dest_table, records, transformed_records, upsert_key
            )
            logger.info(
                "Entity '%s': wrote %d knowledge_fact row(s) into the kg schema "
                "(attributes have no destination column beyond the id).",
                entity_type,
                facts_written,
            )

        child_results: dict[str, dict[str, Any]] = dict(dimension_results)
        if mapping_by_table:
            child_results = _write_child_rows(
                connector=connector,
                primary_table=dest_table,
                source_records=records,
                transformed_parent_records=transformed_records,
                mapping_by_table=mapping_by_table,
                other_primary_tables=other_primary_tables,
            )

        return {
            "success": True,
            "entity_type": entity_type,
            "total_records": len(records),
            "inserted": result.get("inserted", len(records)),
            "updated": result.get("updated", 0),
            "failed": 0,
            "type_coercions_attempted": result.get("type_coercions_attempted", 0),
            "type_coercion_failures": result.get("type_coercion_failures", 0),
            "child_tables": child_results,
            "knowledge_facts_written": facts_written,
        }
    except Exception as exc:
        logger.error("Load failed for entity '%s': %s", entity_type, exc)
        raise PermanentError(
            f"Load failed for entity '{entity_type}' into table '{dest_table}': {exc}"
        ) from exc


def _group_mapping_by_dest_table(
    mapping: dict[str, Any] | None,
    primary_table: str,
) -> dict[str, dict[str, Any]]:
    """Split one entity's field mapping by which destination table it targets.

    A plain mapping (no ``dest_table`` on any entry — every caller before
    Phase 6.2, and every entry map_fields_impl placed on the entity's own
    table) collapses entirely into ``{primary_table: mapping}``, so this is
    a no-op for the common case.
    """
    grouped: dict[str, dict[str, Any]] = {primary_table: {}}
    for src_field, info in (mapping or {}).items():
        table = (info.get("dest_table") if isinstance(info, dict) else None) or primary_table
        grouped.setdefault(table, {})[src_field] = info
    return grouped


def _materialise_dimensions(
    connector: Any,
    primary_table: str,
    source_records: list[dict[str, Any]],
    transformed_parent_records: list[dict[str, Any]],
    mapping_by_table: dict[str, dict[str, Any]],
    other_primary_tables: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Repeated text on a parent row -> one dimension row per DISTINCT text, plus a
    link on the parent row (report P4). Zero new destination columns: only columns
    the tables already declare are written, and the link is the parent's own
    existing foreign-key column.

    The direction ``_write_child_rows`` cannot take (it needs the child's FK to
    point back at the primary): here the PRIMARY table holds the FK to the
    dimension. A dimension id is derived from its table and text, so a re-run
    upserts the same row instead of inserting another; a blank text writes no row
    and no link. Handled tables are removed from ``mapping_by_table``; the
    parents' link is set in place, so the caller must run this BEFORE the parent
    load (the link is a real FK).

    ponytail: find-or-create among rows THIS function wrote (deterministic id, no
    lookup); a pre-existing row with the same text under another id is not
    matched, and a dimension that is also its own entity this run is left to
    that entity (report P4 Ceiling / Deferred: human-confirmed structure).
    """
    results: dict[str, dict[str, Any]] = {}
    if (
        not mapping_by_table  # a single-table mapping: nothing cross-table to look up
        or not hasattr(connector, "get_foreign_keys")
        or not hasattr(connector, "load_batch")
    ):
        return results
    try:
        own_fks = connector.get_foreign_keys(primary_table)
    except Exception as exc:
        logger.warning("Could not read foreign keys for '%s': %s", primary_table, exc)
        return results
    for dim_table in list(mapping_by_table):
        field_mapping = mapping_by_table[dim_table]
        links = [col for col, ref in own_fks.items() if ref == dim_table]
        if not field_mapping or len(links) != 1 or dim_table in (other_primary_tables or set()):
            continue
        link = links[0]
        try:
            dim_fks = connector.get_foreign_keys(dim_table)
        except Exception as exc:
            logger.warning("Could not read foreign keys for '%s': %s", dim_table, exc)
            continue
        if primary_table in dim_fks.values():
            continue  # the dimension also points back: an ordinary child table
        # An origin value on one of the dimension's own FK columns is unresolved
        # (same reasoning as _write_child_rows): dropped, reported.
        dropped = sorted(f for f, i in field_mapping.items() if i.get("dest_column") in dim_fks)
        field_mapping = {f: i for f, i in field_mapping.items() if f not in dropped}
        dest_cols = [i.get("dest_column") for i in field_mapping.values()]
        schema_columns = {
            c["name"]: c for c in connector.get_table_schema(dim_table).get("columns", [])
        }
        required = [
            c
            for c in dim_fks
            if c not in dest_cols and not schema_columns.get(c, {}).get("nullable", True)
        ]
        all_fields = sorted(mapping_by_table.pop(dim_table))
        if required or not field_mapping:
            results[dim_table] = {"inserted": 0, "updated": 0, "dropped_fields": all_fields}
            continue

        dim_rows: dict[str, dict[str, Any]] = {}
        for src_rec, parent_rec in zip(source_records, transformed_parent_records, strict=True):
            row = _transform_records([src_rec], field_mapping, dim_table)[0]
            text = {c: row.get(c) for c in dest_cols}
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in text.values()):
                parent_rec.setdefault(link, None)
                continue
            row["id"] = deterministic_uuid(
                "dimension_row", dim_table, json.dumps(text, sort_keys=True, default=str)
            )
            row["_loaded_at"] = _get_timestamp()
            dim_rows.setdefault(row["id"], row)
            parent_rec[link] = row["id"]
        if dim_rows:
            if hasattr(connector, "ensure_table"):
                connector.ensure_table(dim_table)
            upsert_key = _resolve_upsert_key(connector, dim_table)
            _align_identity_column(list(dim_rows.values()), upsert_key)
            written = connector.load_batch(
                table_name=dim_table,
                records=list(dim_rows.values()),
                mapping=field_mapping,
                upsert_key=upsert_key,
            )
            results[dim_table] = {
                "inserted": written.get("inserted", 0),
                "updated": written.get("updated", 0),
            }
            logger.info(
                "Materialised %d dimension row(s) in '%s' via '%s'", len(dim_rows), dim_table, link
            )
        if dropped:
            results.setdefault(dim_table, {"inserted": 0, "updated": 0})["dropped_fields"] = dropped
    return results


def _write_child_rows(
    connector: Any,
    primary_table: str,
    source_records: list[dict[str, Any]],
    transformed_parent_records: list[dict[str, Any]],
    mapping_by_table: dict[str, dict[str, Any]],
    other_primary_tables: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Write one child row per parent record into each cross-mapped sibling table.

    Only the direction the destination family graph can actually resolve
    unambiguously: a sibling table whose OWN foreign key points back at
    ``primary_table`` (e.g. ``finance.company_id -> company.id``) — a
    straightforward one-child-row-per-parent-record write. A field
    cross-mapped onto a sibling table that instead holds the FK the
    *primary* table points at (the parent references the child, e.g. a
    hypothetical ``company.location_id -> location.id``) would need
    find-or-create matching against existing rows in that table, a
    genuinely different feature this does not attempt — it is logged and
    the field is dropped rather than guessed at.

    That "child's own FK points back at primary" test is necessary but,
    live-caught (BP-A2, a real two-origin ClickHouse run), not sufficient:
    ``company.industry_id -> industry.id`` and ``company.location_id ->
    location.id`` both pass it just as cleanly as the genuine
    ``finance.company_id -> company.id`` case, but "company" there is not
    an anonymous many-side child of industry/location — it is itself a
    full, independently-matched, independently-deduplicated primary entity
    elsewhere in the SAME run. Treating it as a child manufactured one
    company row per unrelated industry/location record, each carrying
    nothing but that one FK value and a timestamp — 23 nameless ghost
    company rows out of 25 in the live run that caught this. Any
    ``child_table`` that is itself one of ``other_primary_tables`` is
    therefore never eligible, regardless of what its own FKs point at.

    Reuses the existing atomic-load transaction via the caller-supplied
    ``connector`` (never opens its own), so a partial fan-out cannot commit:
    an exception here propagates out of ``load_data_impl`` exactly like a
    parent-table load failure does, rolling the whole load back.
    """
    results: dict[str, dict[str, Any]] = {}
    other_primary_tables = other_primary_tables or set()
    if not hasattr(connector, "get_foreign_keys") or not hasattr(connector, "load_batch"):
        logger.warning(
            "Destination connector %s cannot resolve foreign keys for "
            "cross-table field mapping(s) into %s; those field(s) will not "
            "reach the destination.",
            type(connector).__name__,
            ", ".join(sorted(mapping_by_table)),
        )
        return results

    for child_table, field_mapping in mapping_by_table.items():
        if not field_mapping:
            continue
        if child_table in other_primary_tables:
            dropped = sorted(field_mapping)
            logger.warning(
                "Cross-table mapping into '%s' targets a table this run "
                "already loads as its own independent entity, not an "
                "anonymous child of '%s' -- skipping %d field(s) rather "
                "than manufacturing a duplicate/ghost row: %s",
                child_table,
                primary_table,
                len(dropped),
                ", ".join(dropped),
            )
            results[child_table] = {"inserted": 0, "updated": 0, "dropped_fields": dropped}
            continue
        try:
            fks = connector.get_foreign_keys(child_table)
        except Exception as exc:
            logger.warning("Could not read foreign keys for '%s': %s", child_table, exc)
            continue
        fk_column = next((col for col, ref in fks.items() if ref == primary_table), None)
        if fk_column is None:
            dropped = sorted(field_mapping)
            logger.warning(
                "Cross-table mapping into '%s' has no foreign key back to "
                "'%s' — skipping %d field(s), which will not reach the "
                "destination: %s",
                child_table,
                primary_table,
                len(dropped),
                ", ".join(dropped),
            )
            # Surfaced beyond the log: this used to be invisible to anyone
            # reviewing the run — a mapping approved at the gate, silently
            # unwritable at load time, with no trace anywhere a human would
            # look. results[child_table] is normally only set for tables
            # that were actually written; a dropped_fields-only entry marks
            # "we tried, and couldn't" distinctly from "nothing to write".
            results[child_table] = {"inserted": 0, "updated": 0, "dropped_fields": dropped}
            continue

        # A field cross-mapped onto one of child_table's OTHER foreign-key
        # columns (e.g. a Location record's `companyID` landing on
        # `experience.company_id` purely because the names normalize the
        # same way) is not safe to write literally: that slot expects an
        # already-resolved DESTINATION row id, but the mapped value is the
        # raw, unresolved ORIGIN id -- writing it either violates the FK
        # constraint outright (the common case, since origin and
        # destination ids live in different id spaces) or, worse, silently
        # links to the wrong row on the rare id collision. Only fk_column
        # itself (set below from the already-resolved parent_id, never
        # from field_mapping) is a legitimate FK write here.
        fk_target_cols = set(fks) - {fk_column}
        suspect = {
            field: info
            for field, info in field_mapping.items()
            if info and info.get("dest_column") in fk_target_cols
        }
        dropped_fk_fields: list[str] = []
        if suspect:
            dropped_fk_fields = sorted(suspect)
            logger.warning(
                "Cross-table mapping into '%s' targets foreign-key column(s) "
                "%s with a raw, unresolved origin value rather than a "
                "matched destination id -- skipping %d field(s), which will "
                "not reach the destination: %s",
                child_table,
                sorted({info.get("dest_column") for info in suspect.values()}),
                len(dropped_fk_fields),
                ", ".join(dropped_fk_fields),
            )
            field_mapping = {k: v for k, v in field_mapping.items() if k not in suspect}
            if not field_mapping:
                results[child_table] = {
                    "inserted": 0,
                    "updated": 0,
                    "dropped_fields": dropped_fk_fields,
                }
                continue

        dest_cols = {info.get("dest_column") for info in field_mapping.values() if info}

        # A genuine relationship table (e.g. company_company_relationship's
        # company_a_id + company_b_id, BOTH required, BOTH pointing back at
        # THIS SAME primary_table) needs a second resolved parent entity per
        # row -- something this one-parent-per-child model has no way to
        # supply. fk_target_cols (computed above) already names every other
        # FK column besides fk_column itself; if any of those is NOT NULL
        # and never got a mapped value, inserting would violate that
        # constraint and roll back the whole batch (live-reproduced: Boeing,
        # a "description" field auto-mapped onto company_company_relationship
        # rolled back an otherwise-successful load). Refuse up front instead.
        if fk_target_cols:
            schema_columns = {
                c["name"]: c for c in connector.get_table_schema(child_table).get("columns", [])
            }
            unresolvable_fk_cols = sorted(
                col
                for col in fk_target_cols
                if col not in dest_cols and not schema_columns.get(col, {}).get("nullable", True)
            )
            if unresolvable_fk_cols:
                dropped = sorted(field_mapping)
                logger.warning(
                    "Cross-table mapping into '%s' would leave required "
                    "foreign-key column(s) %s unset -- this table needs a "
                    "second resolved parent entity per row (e.g. a "
                    "company-to-company relationship), not a single-parent "
                    "child attribute -- skipping %d field(s) rather than a "
                    "doomed insert: %s",
                    child_table,
                    unresolvable_fk_cols,
                    len(dropped),
                    ", ".join(dropped),
                )
                results[child_table] = {
                    "inserted": 0,
                    "updated": 0,
                    "dropped_fields": dropped,
                }
                continue

        child_records: list[dict[str, Any]] = []
        for src_rec, parent_rec in zip(source_records, transformed_parent_records, strict=True):
            parent_id = parent_rec.get("id")
            if parent_id is None:
                continue
            child_rec = _transform_records([src_rec], field_mapping, child_table)[0]
            # A record from an origin that never carried any of this child
            # table's fields transforms to all-None — writing an empty
            # child row for it is noise with nothing in it to show.
            if all(child_rec.get(c) is None for c in dest_cols):
                continue
            # Deterministic, not generate_uuid(): a random id here meant
            # upsert_key="id" could never match an existing row on a
            # repeat run of the same merge (the parent is idempotent via
            # its own _cluster_id-derived id -- _transform_records:441 --
            # but a fresh random id every run made every child-table
            # re-load an INSERT, duplicating child rows on each re-run).
            # This loop pairs each parent record with exactly one child
            # row per child_table (the zip above), so (parent_id,
            # child_table) alone is already a unique, stable key.
            child_rec["id"] = deterministic_uuid("child_row", parent_id, child_table)
            child_rec[fk_column] = parent_id
            child_records.append(child_rec)

        if not child_records:
            if dropped_fk_fields:
                results[child_table] = {
                    "inserted": 0,
                    "updated": 0,
                    "dropped_fields": dropped_fk_fields,
                }
            continue

        if hasattr(connector, "ensure_table"):
            connector.ensure_table(child_table)
        child_upsert_key = _resolve_upsert_key(connector, child_table)
        _align_identity_column(child_records, child_upsert_key)
        result = connector.load_batch(
            table_name=child_table,
            records=child_records,
            mapping=field_mapping,
            upsert_key=child_upsert_key,
        )
        results[child_table] = {
            "inserted": result.get("inserted", 0),
            "updated": result.get("updated", 0),
        }
        if dropped_fk_fields:
            results[child_table]["dropped_fields"] = dropped_fk_fields
        logger.info(
            "Loaded %d child row(s) into '%s' via foreign key '%s' (cross-table mapping from '%s')",
            len(child_records),
            child_table,
            fk_column,
            primary_table,
        )
    return results


def _get_destination_connector(
    destination_config: dict[str, Any] | None,
):
    """Get or create a destination connector (PostgreSQL or ClickHouse).

    Parameters
    ----------
    destination_config : dict | None
        Destination config from the UI, or None to use global config.

    Returns
    -------
    PostgreSQLDestinationConnector | ClickHouseDestinationConnector
    """
    if destination_config:
        dest_type = destination_config.get("type", "postgresql")
        config_dict = {
            "host": destination_config.get("host", "localhost"),
            "port": destination_config.get("port", 5432),
            "user": destination_config.get("user", ""),
            "password": destination_config.get("password", ""),
            "database": destination_config.get("database", ""),
            "schema": destination_config.get("schema", "public"),
        }
    else:
        from src.mcp_server.server import _get_config

        config = _get_config()
        dest = config.destination
        dest_type = getattr(dest, "type", "postgresql") if dest else "postgresql"
        config_dict = {
            "host": dest.host or "localhost",
            "port": dest.port or 5432,
            "user": dest.user or "",
            "password": dest.password or "",
            "database": dest.database or "",
            "schema": dest.schema_name or "public",
        }

    if dest_type == "clickhouse":
        # ClickHouse uses port 8123 by default
        config_dict["port"] = destination_config.get("port", 8123) if destination_config else 8123
        return ClickHouseDestinationConnector(config_dict)

    return PostgreSQLDestinationConnector(config_dict)


def _transform_records(
    records: list[dict[str, Any]],
    mapping: dict[str, Any] | None,
    dest_table: str,
) -> list[dict[str, Any]]:
    """Transform unified records according to field mapping.

    Parameters
    ----------
    records : list[dict]
        Unified records (input).
    mapping : dict | None
        Field mapping from map_fields output.
    dest_table : str
        Destination table name (for provenance).

    Returns
    -------
    list[dict]
        Transformed records ready for INSERT ... ON CONFLICT.
    """
    transformed: list[dict[str, Any]] = []

    for rec in records:
        t_rec: dict[str, Any] = {}

        if mapping:
            for src_field, field_info in mapping.items():
                dest_col = field_info.get("dest_column") or src_field
                value = rec.get(src_field)

                # Fields requiring schema changes are included too — the
                # columns are added by _apply_schema_changes before load,
                # and load_batch drops any column the table still lacks.

                # Serialize conflict data to JSON string
                if isinstance(value, dict) and "conflicts" in value:
                    value = json.dumps(value["conflicts"], default=str)
                elif isinstance(value, dict) and "conflict" in value:
                    value = json.dumps(value["conflict"], default=str)

                # Coalesce many-to-one mappings (e.g. company_name AND
                # org_title both -> name): a record from one origin lacks
                # the other origin's source field, and that absent (None)
                # value must not clobber a value already mapped in.
                if value is not None or dest_col not in t_rec:
                    t_rec[dest_col] = value
        else:
            # No mapping — pass through non-internal fields
            for key, value in rec.items():
                if key.startswith("_") and key not in ("_cluster_id",):
                    continue
                t_rec[key] = value

        # Ensure universal UUID key exists and is non-null. A mapping like
        # person_id -> id leaves id None for records whose origin doesn't
        # have that source field (NOT NULL PK violation on insert).
        if t_rec.get("id") is None:
            t_rec["id"] = rec.get("_cluster_id") or rec.get("id") or generate_uuid()

        # Add provenance metadata
        source_ids = []
        if "_source_ids" in rec:
            try:
                source_ids = json.loads(rec["_source_ids"])
            except (json.JSONDecodeError, TypeError):
                source_ids = []
        if not source_ids:
            source_ids = [{"cluster_id": rec.get("_cluster_id", "")}]

        t_rec["_source_ids"] = json.dumps(source_ids, default=str)

        transformed.append(t_rec)

    return transformed


def _get_timestamp() -> str:
    """Get current UTC timestamp as ISO string."""
    from datetime import datetime

    return datetime.now(UTC).isoformat()


async def load_data_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    logger.debug("ENTER load_data_handler: arguments_keys=%s", list(arguments.keys()))
    return await load_data_impl(**arguments)
