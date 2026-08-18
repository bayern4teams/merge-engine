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
from datetime import UTC
from typing import Any

from src.mcp_server.connectors.clickhouse_destination import ClickHouseDestinationConnector
from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector
from src.orchestrator.utils.retry import PermanentError
from src.utils.id_utils import deterministic_uuid, generate_uuid

logger = logging.getLogger("merge-engine.tools.load_data")


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
        logger.debug(
            "Calling connector.load_batch: table=%s, records=%d",
            dest_table,
            len(transformed_records),
        )
        result = connector.load_batch(
            table_name=dest_table,
            records=transformed_records,
            mapping=parent_mapping,
            upsert_key="id",
        )
        logger.debug("load_batch completed: %s", result)

        child_results: dict[str, dict[str, Any]] = {}
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
        result = connector.load_batch(
            table_name=child_table,
            records=child_records,
            mapping=field_mapping,
            upsert_key="id",
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
