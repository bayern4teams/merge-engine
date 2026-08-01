"""
map_fields tool implementation.

Maps unified fields to destination columns with a hybrid pipeline:

1. Exact / normalized heuristic matching handles the cheap, confident
   cases (score >= 0.85).
2. Everything the heuristic can't confidently place (weak substring
   matches, aliases, unknowns) goes to the LLM in one call per entity
   type, with sample values and the full destination schema. The LLM
   decides per field: map onto an existing column, propose a new
   column, or skip.
3. On LLM failure (or when disabled via ``llm.field_mapping_enabled``),
   the original heuristic fallback tiers apply — mapping still works,
   just with less semantic insight.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("merge-engine.tools.map_fields")


# Heuristic scores >= this are accepted without consulting the LLM.
_HEURISTIC_ACCEPT = 0.85

# Allowed generic SQL types for LLM-proposed new columns (anything else
# falls back to TEXT).
_ALLOWED_NEW_COLUMN_TYPES = {
    "TEXT", "BIGINT", "INTEGER", "DOUBLE PRECISION", "BOOLEAN",
    "TIMESTAMP", "DATE",
}

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


async def map_fields_impl(
    entity_type: str,
    unified_schema: dict[str, Any],
    destination_schema: dict[str, Any],
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map unified fields to destination columns.

    Parameters
    ----------
    entity_type : str
        The entity type being mapped.
    unified_schema : dict
        {field_name: {"type": str, "sample_value": any,
                      "sample_values": [any, ...], ...}, ...}
        Field names and sample values from unified records.
    destination_schema : dict
        {table: str, columns: [{"name": str, "type": str, ...}, ...]}
        Destination table column definitions.
    llm_config : dict, optional
        LLM configuration. When present (and ``field_mapping_enabled``
        is not False), fields the heuristic can't confidently place are
        decided by the LLM.

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
    logger.debug("ENTER map_fields_impl: entity_type=%s, source_fields=%d, dest_columns=%d",
                  entity_type, len(unified_schema), len(destination_schema.get("columns", [])))

    source_fields = list(unified_schema.keys())
    dest_columns = {col["name"]: col for col in destination_schema.get("columns", [])}
    dest_table = destination_schema.get("table", entity_type)
    logger.debug("Source fields: %d, Destination columns: %d, Dest table: %s",
                  len(source_fields), len(dest_columns), dest_table)

    field_mapping: dict[str, dict[str, Any]] = {}
    unmapped_fields: list[dict[str, Any]] = []
    schema_changes: list[dict[str, str]] = []

    # ── Phase 1: Exact name matching ─────────────────────────────────
    logger.debug("Phase 1: Exact name matching")
    exact_matches = 0
    for field in source_fields:
        if field in dest_columns:
            incompatible, reason = _incompatible_with_column(
                field, field, dest_columns, unified_schema
            )
            if incompatible:
                _propose_incompatible_fallback(
                    field, reason, dest_table, dest_columns,
                    field_mapping, unmapped_fields, schema_changes,
                )
                continue
            field_mapping[field] = {
                "dest_column": field,
                "confidence": 1.0,
            }
            exact_matches += 1
    logger.debug("Phase 1 complete: %d exact matches", exact_matches)

    # ── Phase 2: Confident heuristic matching (>= _HEURISTIC_ACCEPT) ──
    remaining_fields = [f for f in source_fields if f not in field_mapping]
    taken_columns = {m["dest_column"] for m in field_mapping.values()}
    heuristic_scores: dict[str, tuple[str | None, float]] = {}

    heuristic_matches = 0
    for field in remaining_fields:
        best_match = None
        best_score = 0.0
        for dest_col in dest_columns:
            if dest_col in taken_columns:
                continue
            score = _heuristic_match(field, dest_col)
            if score > best_score:
                best_score = score
                best_match = dest_col
        heuristic_scores[field] = (best_match, best_score)
        if best_match and best_score >= _HEURISTIC_ACCEPT:
            incompatible, reason = _incompatible_with_column(
                field, best_match, dest_columns, unified_schema
            )
            if incompatible:
                _propose_incompatible_fallback(
                    field, reason, dest_table, dest_columns,
                    field_mapping, unmapped_fields, schema_changes,
                )
                continue
            field_mapping[field] = {
                "dest_column": best_match,
                "confidence": best_score,
            }
            taken_columns.add(best_match)
            heuristic_matches += 1
    logger.debug("Phase 2 complete: %d confident heuristic matches", heuristic_matches)

    # ── Phase 3: LLM decision for everything the heuristic couldn't place ──
    undecided = [f for f in remaining_fields if f not in field_mapping]
    llm_enabled = bool(llm_config) and llm_config.get("field_mapping_enabled", True)
    llm_decisions: dict[str, dict[str, Any]] | None = None
    if undecided and llm_enabled:
        logger.info(
            "Sending %d undecided fields for entity '%s' to LLM mapping",
            len(undecided), entity_type,
        )
        llm_decisions = await _decide_fields_with_llm(
            entity_type=entity_type,
            fields=undecided,
            unified_schema=unified_schema,
            destination_schema=destination_schema,
            llm_config=llm_config,
        )
    elif undecided:
        logger.info(
            "LLM field mapping disabled/unavailable; using heuristic tiers "
            "for %d fields", len(undecided),
        )

    llm_mapped = 0
    for field in undecided:
        decision = (llm_decisions or {}).get(field)
        if decision is not None:
            applied = _apply_llm_decision(
                field, decision, dest_columns, taken_columns, dest_table,
                unified_schema, field_mapping, unmapped_fields, schema_changes,
            )
            if applied:
                llm_mapped += 1
                continue
            # Invalid decision — fall through to heuristic tiers below
        _apply_heuristic_tier(
            field, heuristic_scores.get(field, (None, 0.0)),
            dest_table, unified_schema, field_mapping, unmapped_fields,
            schema_changes, taken_columns, dest_columns,
        )

    logger.debug(
        "Phase 3 complete: llm_decided=%d, unmapped=%d, schema_changes=%d",
        llm_mapped, len(unmapped_fields), len(schema_changes),
    )
    logger.debug(
        "EXIT map_fields_impl: total_mappings=%d, unmapped=%d, schema_changes=%d",
        len(field_mapping), len(unmapped_fields), len(schema_changes),
    )
    return {
        "entity_type": entity_type,
        "field_mapping": field_mapping,
        "unmapped_fields": unmapped_fields,
        "schema_changes": schema_changes,
    }


def _incompatible_with_column(
    field: str,
    dest_col: str,
    dest_columns: dict[str, Any],
    unified_schema: dict[str, Any],
) -> tuple[bool, str]:
    """Would mapping ``field`` onto ``dest_col`` crash the load?

    Checked before EVERY existing-column mapping is accepted (exact-name,
    confident-heuristic, low-confidence-heuristic, and LLM "map") — the
    fastest of the three crash classes fixed here (Fix #3) is a single bad
    row aborting the whole atomic load with no warning until then.
    """
    from src.mcp_server.tools.value_coercion import is_compatible

    col_type = str((dest_columns.get(dest_col) or {}).get("type", ""))
    if not col_type:
        return False, ""
    info = unified_schema.get(field, {})
    samples = info.get("sample_values") or []
    if not samples:
        sv = info.get("sample_value")
        samples = [sv] if sv is not None else []
    ok, reason = is_compatible(samples, col_type)
    return (not ok), reason


def _propose_incompatible_fallback(
    field: str,
    reason: str,
    dest_table: str,
    dest_columns: dict[str, Any],
    field_mapping: dict[str, dict[str, Any]],
    unmapped_fields: list[dict[str, Any]],
    schema_changes: list[dict[str, str]],
) -> None:
    """Refuse an incompatible existing-column mapping; preserve the value in
    a new TEXT column instead of letting the load crash on it, and flag the
    redirect at the ``human_review_mappings`` gate so a reviewer sees why.
    """
    base = re.sub(r"[^a-z0-9_]", "_", field.strip().lower()) or "field"
    name = f"{base}_raw"
    suffix = 2
    while name in dest_columns:
        name = f"{base}_raw{suffix}"
        suffix += 1
    schema_changes.append({"table": dest_table, "column": name, "data_type": "TEXT"})
    field_mapping[field] = {
        "dest_column": name,
        "confidence": 0.0,
        "requires_schema_change": True,
        "reason": reason,
    }
    unmapped_fields.append({
        "field": field,
        "confidence": 0.0,
        "suggested_column": name,
        "reason": reason,
    })
    logger.info(
        "Field '%s' redirected to new column '%s' (TEXT): %s", field, name, reason,
    )


def _apply_llm_decision(
    field: str,
    decision: dict[str, Any],
    dest_columns: dict[str, Any],
    taken_columns: set[str],
    dest_table: str,
    unified_schema: dict[str, Any],
    field_mapping: dict[str, dict[str, Any]],
    unmapped_fields: list[dict[str, Any]],
    schema_changes: list[dict[str, str]],
) -> bool:
    """Apply one validated LLM decision. Returns False if invalid."""
    action = decision.get("action")

    if action == "map":
        dest_col = decision.get("dest_column")
        if dest_col not in dest_columns:
            logger.warning(
                "LLM mapped '%s' to nonexistent column '%s'; ignoring",
                field, dest_col,
            )
            return False
        incompatible, reason = _incompatible_with_column(
            field, dest_col, dest_columns, unified_schema
        )
        if incompatible:
            logger.warning(
                "LLM mapped '%s' -> '%s' but the sample values are "
                "incompatible with that column's type (%s); redirecting to "
                "a new column instead of letting the load crash on it.",
                field, dest_col, reason,
            )
            _propose_incompatible_fallback(
                field, reason, dest_table, dest_columns,
                field_mapping, unmapped_fields, schema_changes,
            )
            return True
        field_mapping[field] = {
            "dest_column": dest_col,
            "confidence": 0.9,
            "mapped_by": "llm",
        }
        taken_columns.add(dest_col)
        logger.info("LLM mapped field '%s' -> existing column '%s'", field, dest_col)
        return True

    if action == "new_column":
        name = str(decision.get("name") or field).strip().lower()
        if not _IDENTIFIER_RE.match(name):
            logger.warning("LLM proposed invalid column name '%s'; ignoring", name)
            return False
        if name in dest_columns:
            logger.warning(
                "LLM proposed new column '%s' colliding with existing column; ignoring",
                name,
            )
            return False
        col_type = str(decision.get("type", "TEXT")).upper()
        if col_type not in _ALLOWED_NEW_COLUMN_TYPES:
            col_type = "TEXT"
        schema_changes.append({
            "table": dest_table,
            "column": name,
            "data_type": col_type,
        })
        field_mapping[field] = {
            "dest_column": name,
            "confidence": 0.9,
            "requires_schema_change": True,
            "mapped_by": "llm",
        }
        logger.info(
            "LLM proposed new column '%s' (%s) for field '%s'", name, col_type, field,
        )
        return True

    if action == "skip":
        # Skips still surface at the review gate so a human can override.
        unmapped_fields.append({
            "field": field,
            "confidence": 0.0,
            "suggested_column": None,
            "reason": f"LLM proposed skipping: {decision.get('reason', 'no reason given')}",
        })
        logger.info("LLM proposed skipping field '%s': %s", field, decision.get("reason"))
        return True

    logger.warning("LLM returned unknown action %r for field '%s'", action, field)
    return False


def _apply_heuristic_tier(
    field: str,
    best: tuple[str | None, float],
    dest_table: str,
    unified_schema: dict[str, Any],
    field_mapping: dict[str, dict[str, Any]],
    unmapped_fields: list[dict[str, Any]],
    schema_changes: list[dict[str, str]],
    taken_columns: set[str],
    dest_columns: dict[str, Any] | None = None,
) -> None:
    """Original heuristic fallback tiers for a single field."""
    best_match, best_score = best
    if best_match and best_match in taken_columns:
        best_match, best_score = None, 0.0

    if best_match and best_score >= 0.5:
        incompatible, reason = _incompatible_with_column(
            field, best_match, dest_columns or {}, unified_schema
        )
        if incompatible:
            _propose_incompatible_fallback(
                field, reason, dest_table, dest_columns or {},
                field_mapping, unmapped_fields, schema_changes,
            )
            return
        field_mapping[field] = {
            "dest_column": best_match,
            "confidence": best_score,
        }
        taken_columns.add(best_match)
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
        dest_type = _map_type_generic(source_type)
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


# ── LLM decision call ────────────────────────────────────────────────


def _format_source_fields_block(
    fields: list[str],
    unified_schema: dict[str, Any],
) -> str:
    """Format the undecided source fields (with samples) for the prompt."""
    lines: list[str] = []
    for field in fields:
        info = unified_schema.get(field, {})
        samples = info.get("sample_values")
        if not samples:
            sample = info.get("sample_value")
            samples = [sample] if sample is not None else []
        samples_str = ", ".join(
            json.dumps(s, ensure_ascii=False, default=str) for s in samples[:3]
        )
        lines.append(
            f"- {field} (type: {info.get('type', 'unknown')}; "
            f"sample values: {samples_str or 'none'})"
        )
    return "\n".join(lines)


async def _decide_fields_with_llm(
    entity_type: str,
    fields: list[str],
    unified_schema: dict[str, Any],
    destination_schema: dict[str, Any],
    llm_config: dict[str, Any],
) -> dict[str, dict[str, Any]] | None:
    """One batched LLM call deciding all undecided fields.

    Returns {field: decision} or None on failure (caller falls back to
    heuristic tiers — the run never blocks on the LLM).
    """
    from src.llm import LLMClient, PROMPT_TEMPLATES

    try:
        prompts = PROMPT_TEMPLATES.get("field_mapping", {})
        system_prompt = prompts.get("system", "")
        human_template = prompts.get("human", "")
        if not system_prompt or not human_template:
            logger.warning("field_mapping prompts missing; using heuristic tiers")
            return None

        dest_table = destination_schema.get("table", entity_type)
        dest_cols_str = "\n".join(
            f"- {col['name']} ({col.get('type', 'unknown')})"
            for col in destination_schema.get("columns", [])
        )

        user_prompt = (
            human_template
            .replace("{entity_type}", entity_type)
            .replace("{dest_table}", str(dest_table))
            .replace("{destination_schema}", dest_cols_str)
            .replace("{source_fields_block}",
                     _format_source_fields_block(fields, unified_schema))
        )
        # Set when a reviewer sent the mappings gate back for rework.
        hint = str((llm_config or {}).get("retry_hint") or "").strip()
        if hint:
            from src.orchestrator.utils.retry_hints import apply_retry_hint
            user_prompt = apply_retry_hint(
                user_prompt, {"retry_hints": {"map_fields": hint}}, "map_fields"
            )

        client = LLMClient(llm_config)
        result = await client.call_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        if not isinstance(result, dict):
            logger.warning("LLM field-mapping returned non-dict; using heuristics")
            return None

        decisions = {
            field: dec for field, dec in result.items()
            if field in fields and isinstance(dec, dict)
        }
        logger.info(
            "LLM decided %d/%d undecided fields for entity '%s'",
            len(decisions), len(fields), entity_type,
        )
        return decisions

    except Exception as exc:
        logger.warning(
            "LLM field-mapping failed (%s); using heuristic tiers for %d fields",
            exc, len(fields),
        )
        return None


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


def _map_type_generic(source_type: str) -> str:
    """Map a generic source type to a portable SQL type.

    Output feeds the destination connectors' DDL translators
    (``_pg_ddl_type`` / ``_clickhouse_type_from_string``).
    """
    type_map: dict[str, str] = {
        "STRING": "TEXT",
        "STR": "TEXT",
        "VARCHAR": "TEXT",
        "TEXT": "TEXT",
        "INT": "BIGINT",
        "INTEGER": "INTEGER",
        "BIGINT": "BIGINT",
        "SMALLINT": "INTEGER",
        "FLOAT": "DOUBLE PRECISION",
        "DECIMAL": "DOUBLE PRECISION",
        "REAL": "DOUBLE PRECISION",
        "DOUBLE": "DOUBLE PRECISION",
        "BOOL": "BOOLEAN",
        "BOOLEAN": "BOOLEAN",
        "TIMESTAMP": "TIMESTAMP",
        "TIMESTAMPTZ": "TIMESTAMP",
        "DATETIME": "TIMESTAMP",
        "DATE": "DATE",
        "TIME": "TEXT",
        "JSON": "TEXT",
        "JSONB": "TEXT",
        "UUID": "TEXT",
        "BYTEA": "TEXT",
        "OBJECT": "TEXT",
        "ARRAY": "TEXT",
        "LIST": "TEXT",
        "DICT": "TEXT",
        "NONETYPE": "TEXT",
        "UNKNOWN": "TEXT",
    }
    return type_map.get(source_type.upper(), "TEXT")


async def map_fields_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    logger.debug("ENTER map_fields_handler: arguments_keys=%s", list(arguments.keys()))
    return await map_fields_impl(**arguments)
