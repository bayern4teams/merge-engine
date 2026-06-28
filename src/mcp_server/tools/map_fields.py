"""
map_fields tool implementation.

Maps unified fields to destination columns using LLM.
Returns mapping, confidence scores, and any schema changes needed.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.orchestrator.utils.retry import TransientError, PermanentError

logger = logging.getLogger("merge-engine.tools.map_fields")


async def map_fields_impl(
    entity_type: str,
    unified_schema: dict[str, Any],
    destination_schema: dict[str, Any],
) -> dict[str, Any]:
    """Map unified fields to destination columns.

    Parameters
    ----------
    entity_type : str
        The entity type being mapped.
    unified_schema : dict
        {field_name: {"type": str, "sample_value": any, "conflicts": int, ...}, ...}
        Field names and sample values from unified records.
    destination_schema : dict
        {table: str, columns: [{"name": str, "type": str, ...}, ...]}
        Destination table column definitions.

    Returns
    -------
    dict
        {
            "entity_type": str,
            "field_mapping": {
                "source_field": {"dest_column": str, "confidence": float}
            },
            "unmapped_fields": [{"field": str, "reason": str}],
            "schema_changes": [{"column": str, "data_type": str, "table": str}],
        }
    """
    from src.mcp_server.server import _get_config
    config = _get_config()

    source_fields = list(unified_schema.keys())
    dest_columns = {col["name"]: col for col in destination_schema.get("columns", [])}
    dest_table = destination_schema.get("table", entity_type)

    field_mapping: dict[str, dict[str, Any]] = {}
    unmapped_fields: list[dict[str, Any]] = []
    schema_changes: list[dict[str, str]] = []

    # ── Phase 1: Exact name matching ─────────────────────────────────
    for field in source_fields:
        if field in dest_columns:
            field_mapping[field] = {
                "dest_column": field,
                "confidence": 1.0,
            }

    # ── Phase 2: LLM-assisted mapping for remaining fields ───────────
    # In a full implementation, this would call the LLM with a prompt like:
    # "Map these unified source fields to these destination columns."
    #
    # For now, we use heuristic matching:
    remaining_fields = [f for f in source_fields if f not in field_mapping]
    remaining_columns = set(dest_columns.keys()) - set(field_mapping.keys())

    for field in remaining_fields:
        best_match = None
        best_score = 0.0

        for dest_col in remaining_columns:
            if dest_col in field_mapping:
                continue
            score = _heuristic_match(field, dest_col)
            if score > best_score:
                best_score = score
                best_match = dest_col

        if best_match and best_score >= 0.5:
            field_mapping[field] = {
                "dest_column": best_match,
                "confidence": best_score,
            }
            remaining_columns.discard(best_match)
        elif best_score >= 0.3:
            # Low confidence — flag as unmapped for human review
            unmapped_fields.append({
                "field": field,
                "confidence": best_score,
                "suggested_column": best_match,
                "reason": "Low confidence match; requires human review",
            })
        else:
            # No match found — may need new column
            source_type = unified_schema.get(field, {}).get("type", "STRING")
            dest_type = _map_type_to_databricks(source_type)
            schema_changes.append({
                "table": dest_table,
                "column": field,
                "data_type": dest_type,
            })
            field_mapping[field] = {
                "dest_column": field,  # Will be added via schema change
                "confidence": 0.0,
                "requires_schema_change": True,
            }

    return {
        "entity_type": entity_type,
        "field_mapping": field_mapping,
        "unmapped_fields": unmapped_fields,
        "schema_changes": schema_changes,
    }


def _heuristic_match(source: str, dest: str) -> float:
    """Compute a heuristic match score between a source field and dest column.

    Returns 0.0–1.0 confidence score.
    """
    s = source.lower().replace(" ", "_").replace("-", "_")
    d = dest.lower().replace(" ", "_").replace("-", "_")

    if s == d:
        return 1.0
    if s in d or d in s:
        return 0.8
    if s.replace("_", "") == d.replace("_", ""):
        return 0.85
    # Check common aliases
    aliases: dict[str, list[str]] = {
        "first_name": ["fname", "forename", "given_name"],
        "last_name": ["lname", "surname", "family_name"],
        "email": ["e_mail", "e-mail", "email_address"],
        "phone": ["telephone", "tel", "phone_number", "mobile"],
        "birth_date": ["dob", "date_of_birth", "birthday"],
    }
    # Build reverse lookup: variant -> canonical
    variant_to_canonical: dict[str, str] = {}
    for canonical, variants in aliases.items():
        for v in variants:
            variant_to_canonical[v] = canonical
        variant_to_canonical[canonical] = canonical  # canonical maps to itself
    if s in variant_to_canonical and d in variant_to_canonical:
        if variant_to_canonical[s] == variant_to_canonical[d]:
            return 0.7
    return 0.0


def _map_type_to_databricks(source_type: str) -> str:
    """Map a generic source type to Databricks/Delta type."""
    type_map: dict[str, str] = {
        "STRING": "STRING",
        "VARCHAR": "STRING",
        "TEXT": "STRING",
        "INTEGER": "INTEGER",
        "BIGINT": "BIGINT",
        "SMALLINT": "INTEGER",
        "DECIMAL": "DECIMAL(19,4)",
        "REAL": "FLOAT",
        "DOUBLE": "DOUBLE",
        "BOOLEAN": "BOOLEAN",
        "TIMESTAMP": "TIMESTAMP",
        "TIMESTAMPTZ": "TIMESTAMP",
        "DATE": "DATE",
        "TIME": "STRING",
        "JSON": "STRING",
        "JSONB": "STRING",
        "UUID": "STRING",
        "BYTEA": "STRING",
        "OBJECT": "STRING",
        "ARRAY": "STRING",
        "UNKNOWN": "STRING",
    }
    return type_map.get(source_type.upper(), "STRING")


async def map_fields_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    return await map_fields_impl(**arguments)