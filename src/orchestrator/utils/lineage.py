"""Column-level data lineage computation module.

Joins field mappings with source origin table provenance from extracted/spool rows,
preserves conflict survivorship resolution details (_conflicts), and optionally
enriches mapping verification using sqlglot.

Known Limitations:
- Destination rows currently store `_source_ids` as `[{"cluster_id": ...}]` rather
  than individual origin row IDs.
- Durable entity map table `merge_engine_entity_map` is supported on PostgreSQL
  destinations only (ClickHouse does not persist entity maps).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("merge-engine.orchestrator.utils.lineage")


def _attempt_sqlglot_verification(
    entity: str,
    field_mappings: dict[str, Any],
) -> dict[str, Any]:
    """Attempt optional sqlglot expression lineage validation."""
    try:
        from sqlglot.lineage import lineage

        results: dict[str, Any] = {}
        for source_field, dest_col in field_mappings.items():
            if isinstance(dest_col, str) and isinstance(source_field, str):
                try:
                    # Parse expression if field mapping contains SQL expression
                    sql_expr = f"SELECT {source_field} AS {dest_col} FROM {entity}"
                    node = lineage(dest_col, sql_expr)
                    results[dest_col] = {
                        "verified": True,
                        "expression": str(node.expression),
                        "source_name": node.name,
                    }
                except Exception:
                    results[dest_col] = {"verified": False, "note": "direct_mapping"}
        return {"status": "success", "verified_columns": results}
    except ImportError:
        return {"status": "skipped", "reason": "sqlglot library not installed"}
    except Exception as exc:
        logger.debug("sqlglot verification skipped due to: %s", exc)
        return {"status": "skipped", "reason": str(exc)}


def build_lineage(
    extracted_data: dict[str, Any] | None = None,
    field_mappings: dict[str, Any] | None = None,
    unified_entities: dict[str, Any] | None = None,
    conflict_records: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute column-level data lineage across origins, fields, and destination columns.

    Args:
        extracted_data: Raw extracted rows containing `_origin_id`, `_origin_table`, `_origin_id_row`.
        field_mappings: Dict mapping entity -> {source_field: dest_column}.
        unified_entities: Final unified entity groups.
        conflict_records: Preserved `_conflicts` data from unification containing:
            `{field, values:[{origin, value}], selected_origin, selected_value, winning_rule}`.

    Returns:
        Dict containing column-level lineage graph per entity, conflict provenance,
        sqlglot enrichment status, and known limitations.
    """
    if extracted_data is None:
        extracted_data = {}
    if field_mappings is None:
        field_mappings = {}
    if unified_entities is None:
        unified_entities = {}
    if conflict_records is None:
        conflict_records = {}

    entity_lineage: dict[str, Any] = {}

    for entity, mappings in field_mappings.items():
        if not isinstance(mappings, dict):
            continue

        # Extract origin provenance for this entity's fields
        data_rows: list[dict[str, Any]] = []
        entity_raw = extracted_data.get(entity)
        if isinstance(entity_raw, list):
            data_rows = entity_raw
        elif isinstance(entity_raw, dict) and "rows" in entity_raw:
            data_rows = entity_raw.get("rows") or []

        # Find all (origin_id, origin_table) that provided each source field
        field_origins: dict[str, list[dict[str, Any]]] = {}
        for row in data_rows:
            if not isinstance(row, dict):
                continue
            origin_id = row.get("_origin_id") or "unknown_origin"
            origin_table = row.get("_origin_table") or "unknown_table"
            origin_row_id = row.get("_origin_id_row")

            for src_field in row:
                if src_field.startswith("_"):
                    continue
                if src_field not in field_origins:
                    field_origins[src_field] = []
                entry = {"origin_id": origin_id, "origin_table": origin_table}
                if origin_row_id is not None:
                    entry["origin_row_id"] = origin_row_id
                if entry not in field_origins[src_field]:
                    field_origins[src_field].append(entry)

        columns_lineage: dict[str, Any] = {}
        for source_field, dest_col in mappings.items():
            if not isinstance(dest_col, str):
                continue

            origins = field_origins.get(source_field, [])
            conflicts_for_field = conflict_records.get(entity, {}).get(source_field) or []

            columns_lineage[dest_col] = {
                "source_field": source_field,
                "origins": origins,
                "conflicts": conflicts_for_field,
                "multi_origin": len(origins) > 1,
            }

        sqlglot_res = _attempt_sqlglot_verification(entity, mappings)

        entity_lineage[entity] = {
            "columns": columns_lineage,
            "sqlglot_enrichment": sqlglot_res,
        }

    return {
        "status": "completed",
        "entities": entity_lineage,
        "limitations": {
            "source_ids_granularity": "_source_ids carries cluster_id; full origin row IDs logged in spool metadata",
            "durable_entity_map": "merge_engine_entity_map persisted on PostgreSQL destinations only",
        },
    }
