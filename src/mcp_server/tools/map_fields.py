"""
map_fields tool implementation.

Maps unified fields to destination columns with a hybrid pipeline:

1. Exact / normalized heuristic matching handles the cheap, confident
   cases (score >= 0.85).
2. Everything the heuristic can't confidently place (weak substring
   matches, aliases, unknowns) goes to the LLM in one call per entity
   type, with sample values and the full destination schema. The LLM
   decides per field: map onto an existing column or skip.
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

# Destination columns the merge engine populates itself (the generated
# identity and the provenance list) — never a mapping target. Without this,
# an origin-local surrogate key like `companyID` heuristically matches `id`
# via plain substring containment (`"id" in "companyid"`) at 0.8 confidence,
# and the low-confidence fallback tier (>=0.5) accepts it: an origin's
# random hex string would land directly in the destination's own primary
# key, bypassing `_transform_records`' `id is None` autogeneration and
# corrupting the upsert key. Found live while verifying Bug A/B.
_RESERVED_DEST_COLUMNS = {"id", "_source_ids"}


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
    logger.debug(
        "ENTER map_fields_impl: entity_type=%s, source_fields=%d, dest_columns=%d",
        entity_type,
        len(unified_schema),
        len(destination_schema.get("columns", [])),
    )

    source_fields = list(unified_schema.keys())
    dest_columns = {
        col["name"]: col
        for col in destination_schema.get("columns", [])
        if col["name"] not in _RESERVED_DEST_COLUMNS
    }
    dest_table = destination_schema.get("table", entity_type)
    logger.debug(
        "Source fields: %d, Destination columns: %d, Dest table: %s",
        len(source_fields),
        len(dest_columns),
        dest_table,
    )

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
                _refuse_incompatible(field, reason, unmapped_fields)
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
    # Best match ignoring `taken_columns` — feeds the Phase 3 coalesce check
    # below (a field whose only strong candidate is a column another field
    # already claimed, e.g. `entityName` once `name` is taken).
    heuristic_scores_any: dict[str, tuple[str | None, float]] = {}

    heuristic_matches = 0
    for field in remaining_fields:
        best_match = None
        best_score = 0.0
        best_match_any = None
        best_score_any = 0.0
        for dest_col in dest_columns:
            score = _heuristic_match(field, dest_col)
            if score > best_score_any:
                best_score_any = score
                best_match_any = dest_col
            if dest_col in taken_columns:
                continue
            if score > best_score:
                best_score = score
                best_match = dest_col
        heuristic_scores[field] = (best_match, best_score)
        heuristic_scores_any[field] = (best_match_any, best_score_any)
        if best_match and best_score >= _HEURISTIC_ACCEPT:
            incompatible, reason = _incompatible_with_column(
                field, best_match, dest_columns, unified_schema
            )
            if incompatible:
                _refuse_incompatible(field, reason, unmapped_fields)
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
            len(undecided),
            entity_type,
        )
        llm_decisions = await _decide_fields_with_llm(
            entity_type=entity_type,
            fields=undecided,
            unified_schema=unified_schema,
            destination_schema={"table": dest_table, "columns": list(dest_columns.values())},
            llm_config=llm_config,
        )
    elif undecided:
        logger.info(
            "LLM field mapping disabled/unavailable; using heuristic tiers for %d fields",
            len(undecided),
        )

    llm_mapped = 0
    for field in undecided:
        decision = (llm_decisions or {}).get(field)
        if decision is not None:
            applied = _apply_llm_decision(
                field,
                decision,
                dest_columns,
                taken_columns,
                unified_schema,
                field_mapping,
                unmapped_fields,
            )
            if applied:
                llm_mapped += 1
                continue
            # Invalid decision — fall through to heuristic tiers below
        _apply_heuristic_tier(
            field,
            heuristic_scores.get(field, (None, 0.0)),
            heuristic_scores_any.get(field, (None, 0.0)),
            unified_schema,
            field_mapping,
            unmapped_fields,
            taken_columns,
            dest_columns,
        )

    # Enrich mapping results with origin metadata and coalesced flag
    dest_counts: dict[str, int] = {}
    for entry in field_mapping.values():
        dcol = entry.get("dest_column")
        if dcol:
            dest_counts[dcol] = dest_counts.get(dcol, 0) + 1

    for field, entry in field_mapping.items():
        meta = unified_schema.get(field, {})
        if meta.get("origin_id"):
            entry["origin_id"] = meta.get("origin_id")
        if meta.get("table"):
            entry["table"] = meta.get("table")
        if meta.get("source_field"):
            entry["source_field"] = meta.get("source_field")
        dcol = entry.get("dest_column")
        entry["is_coalesced"] = bool(dcol and dest_counts.get(dcol, 0) > 1)

    for item in unmapped_fields:
        field = item.get("field", "")
        meta = unified_schema.get(field, {})
        if meta.get("origin_id"):
            item["origin_id"] = meta.get("origin_id")
        if meta.get("table"):
            item["table"] = meta.get("table")
        if meta.get("source_field"):
            item["source_field"] = meta.get("source_field")

    logger.debug(
        "Phase 3 complete: llm_decided=%d, unmapped=%d, schema_changes=%d",
        llm_mapped,
        len(unmapped_fields),
        len(schema_changes),
    )
    logger.debug(
        "EXIT map_fields_impl: total_mappings=%d, unmapped=%d, schema_changes=%d",
        len(field_mapping),
        len(unmapped_fields),
        len(schema_changes),
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
    from src.utils.value_coercion import is_compatible

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


def _refuse_incompatible(
    field: str,
    reason: str,
    unmapped_fields: list[dict[str, Any]],
) -> None:
    """Refuse a mapping whose sample values would crash the load.

    Previously the value was preserved by inventing a ``<field>_raw`` TEXT
    column. The destination schema is a fixed contract, so the field is left
    unmapped and surfaced at the ``human_review_mappings`` gate instead.
    """
    unmapped_fields.append(
        {
            "field": field,
            "confidence": 0.0,
            "suggested_column": None,
            "reason": reason,
        }
    )
    logger.info(
        "Field '%s' incompatible with destination column type; left unmapped (%s)",
        field,
        reason,
    )


def _apply_llm_decision(
    field: str,
    decision: dict[str, Any],
    dest_columns: dict[str, Any],
    taken_columns: set[str],
    unified_schema: dict[str, Any],
    field_mapping: dict[str, dict[str, Any]],
    unmapped_fields: list[dict[str, Any]],
) -> bool:
    """Apply one validated LLM decision. Returns False if invalid."""
    action = decision.get("action")

    if action == "map":
        dest_col = decision.get("dest_column")
        if dest_col not in dest_columns:
            logger.warning(
                "LLM mapped '%s' to nonexistent column '%s'; ignoring",
                field,
                dest_col,
            )
            return False
        incompatible, reason = _incompatible_with_column(
            field, dest_col, dest_columns, unified_schema
        )
        if incompatible:
            logger.warning(
                "LLM mapped '%s' -> '%s' but the sample values are "
                "incompatible with that column's type (%s); leaving it "
                "unmapped instead of letting the load crash on it.",
                field,
                dest_col,
                reason,
            )
            _refuse_incompatible(field, reason, unmapped_fields)
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
        # Phase 0.0 BUG A FIX: destination schema is authoritative.
        # LLM should never propose new columns — log and reject.
        logger.warning(
            "LLM proposed new_column for field '%s' but destination schema is authoritative; "
            "rejecting (this field will fall through to heuristic tiers or be unmapped)",
            field,
        )
        return False

    if action == "skip":
        # Skips still surface at the review gate so a human can override.
        unmapped_fields.append(
            {
                "field": field,
                "confidence": 0.0,
                "suggested_column": None,
                "reason": f"LLM proposed skipping: {decision.get('reason', 'no reason given')}",
            }
        )
        logger.info("LLM proposed skipping field '%s': %s", field, decision.get("reason"))
        return True

    logger.warning("LLM returned unknown action %r for field '%s'", action, field)
    return False


def _apply_heuristic_tier(
    field: str,
    best: tuple[str | None, float],
    best_any: tuple[str | None, float],
    unified_schema: dict[str, Any],
    field_mapping: dict[str, dict[str, Any]],
    unmapped_fields: list[dict[str, Any]],
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
            _refuse_incompatible(field, reason, unmapped_fields)
            return
        field_mapping[field] = {
            "dest_column": best_match,
            "confidence": best_score,
        }
        taken_columns.add(best_match)
        return

    # Coalesce: this field's strongest candidate is a column a different
    # field already claimed (e.g. `entityName` once `name` is taken by a
    # literal `name` field from another origin). Rather than drop it, record
    # it as an additional source for that column — `_transform_records`
    # (load_data.py) already coalesces multiple sources per record,
    # preferring whichever is non-null, so this only needs to stop dropping
    # the mapping here.
    #
    # Gated on `_safe_coalesce_target`, NOT on score alone: `entityName` and
    # `fullName` both score identically well against `name` by plain string
    # similarity (both trivially contain the substring "name"), but they are
    # different concepts — `fullName` means `legal_name` (see the
    # field_mapping prompt's worked example). Coalescing on score alone was
    # tried and reproduced exactly this corruption live: `fullName`'s legal
    # name silently overwrote the correct `name` value. The qualifier
    # allowlist only trusts unambiguous scope-narrowing prefixes.
    any_match, any_score = best_any
    if any_match and any_match in taken_columns and _safe_coalesce_target(field, any_match):
        incompatible, reason = _incompatible_with_column(
            field, any_match, dest_columns or {}, unified_schema
        )
        if not incompatible:
            field_mapping[field] = {
                "dest_column": any_match,
                "confidence": any_score,
                "mapped_by": "coalesce",
            }
            return

    if best_score >= 0.3:
        # Low confidence — flag as unmapped for human review
        unmapped_fields.append(
            {
                "field": field,
                "confidence": best_score,
                "suggested_column": best_match,
                "reason": "Low confidence match; requires human review",
            }
        )
    else:
        # No match found — BUG A FIX: destination schema is authoritative.
        # Do not create new columns; mark as unmapped instead.
        unmapped_fields.append(
            {
                "field": field,
                "confidence": 0.0,
                "suggested_column": None,
                "reason": "No matching destination column found; destination schema is authoritative",
            }
        )


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
        samples_str = ", ".join(json.dumps(s, ensure_ascii=False, default=str) for s in samples[:3])
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
    from src.llm import PROMPT_TEMPLATES, LLMClient

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
            human_template.replace("{entity_type}", entity_type)
            .replace("{dest_table}", str(dest_table))
            .replace("{destination_schema}", dest_cols_str)
            .replace("{source_fields_block}", _format_source_fields_block(fields, unified_schema))
        )
        # Set when a reviewer sent the mappings gate back for rework.
        hint = str((llm_config or {}).get("retry_hint") or "").strip()
        if hint:
            user_prompt = (
                f"{user_prompt}\n\n--- REVIEWER FEEDBACK ON YOUR PREVIOUS ANSWER ---\n"
                f"A human reviewed your previous output for this exact task and rejected "
                f"it for the reason below. Produce a different answer that addresses it "
                f"directly. Do not repeat the previous answer.\n\nReason: {hint}\n"
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
            field: dec for field, dec in result.items() if field in fields and isinstance(dec, dict)
        }
        logger.info(
            "LLM decided %d/%d undecided fields for entity '%s'",
            len(decisions),
            len(fields),
            entity_type,
        )
        return decisions

    except Exception as exc:
        logger.warning(
            "LLM field-mapping failed (%s); using heuristic tiers for %d fields",
            exc,
            len(fields),
        )
        return None


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokenize_field(name: str) -> list[str]:
    """Split a field name into lowercase words, camelCase included.

    ``foundingDate`` -> ``["founding", "date"]``, so it can be compared with
    the destination's ``founded_date``. Raw string comparison could not see
    those as the same concept, which is how origin1's camelCase names ended
    up unmapped.
    """
    spaced = _CAMEL_BOUNDARY_RE.sub("_", name)
    return [t for t in re.split(r"[^a-z0-9]+", spaced.lower()) if t]


def _stem(token: str) -> str:
    """Strip one inflectional ending so ``founded``/``founding`` agree.

    Deliberately not a general prefix match: ``descriptors`` and
    ``description`` share eight leading characters but are different
    concepts, and a prefix rule mapped the former onto the latter.
    """
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("ed") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _tokens_equivalent(a: str, b: str) -> bool:
    """Two word-tokens naming the same thing, allowing inflection."""
    return a == b or _stem(a) == _stem(b)


def _token_overlap(source: str, dest: str) -> float:
    """Jaccard overlap of two field names' word tokens (0.0-1.0)."""
    st, dt = _tokenize_field(source), _tokenize_field(dest)
    if not st or not dt:
        return 0.0
    matched = [t for t in st if any(_tokens_equivalent(t, u) for u in dt)]
    union = len(st) + len(dt) - len(matched)
    return len(matched) / union if union else 0.0


# Generic qualifier words that only narrow SCOPE, never MEANING, of the
# concept they precede — "entityName" means the same thing as "name", just
# scoped to "this entity" explicitly. Deliberately small and conservative: a
# qualifier that changes meaning ("full", "legal", "short", "display",
# "ceo", "founder") must NOT be here, or `fullName` (which the field_mapping
# prompt already knows means `legal_name`, not `name` — see its worked
# example) would silently coalesce onto an already-correct `name` column
# and overwrite it, instead of needing the LLM/human judgment call it
# actually requires. Found live: exactly this corruption, reproduced
# against a real cross-origin Boeing pair.
_SAFE_COALESCE_QUALIFIERS = frozenset(
    {
        "entity",
        "org",
        "organization",
        "company",
        "business",
        "record",
        "source",
    }
)


def _safe_coalesce_target(field: str, dest_col: str) -> bool:
    """True when ``field`` is *only* ``dest_col`` behind one safe qualifier.

    e.g. ``entityName`` -> ``name``. Heuristic string similarity cannot
    distinguish "same concept, scoped" from "different concept that happens
    to share a word" — ``entityName`` and ``fullName`` both score equally
    well against ``name`` by substring/token overlap alone. Only this
    explicit, narrow allowlist is trusted to let a field coalesce onto a
    column another field already claimed; anything else is left unmapped
    for the LLM or a human to resolve.
    """
    field_tokens = _tokenize_field(field)
    dest_tokens = _tokenize_field(dest_col)
    if len(field_tokens) != len(dest_tokens) + 1:
        return False
    # Every field token must either match a destination token, or be the
    # single allowed qualifier — two qualifiers, or a field token that
    # matches nothing at all, both fail closed.
    extra = [t for t in field_tokens if not any(_tokens_equivalent(t, d) for d in dest_tokens)]
    return len(extra) == 1 and extra[0] in _SAFE_COALESCE_QUALIFIERS


def _heuristic_match(source: str, dest: str) -> float:
    """Compute a heuristic match score between a source field and dest column.

    Returns 0.0-1.0 confidence score.
    """
    s = source.lower().replace(" ", "_").replace("-", "_")
    d = dest.lower().replace(" ", "_").replace("-", "_")

    if s == d:
        return 1.0
    if s in d or d in s:
        return 0.8
    if s.replace("_", "") == d.replace("_", ""):
        return 0.85

    # Word-level comparison, so camelCase source names can reach their
    # snake_case destination column. Deliberately requires a strong overlap:
    # `ceoName` and `founderName` both share the token `name` with the `name`
    # column and must NOT be mapped onto it, so a single shared token
    # (Jaccard 0.5) stays below the bar and is left for the LLM to judge.
    overlap = _token_overlap(source, dest)
    if overlap >= 0.6:
        return 0.5 + 0.35 * overlap
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
    if (
        s in variant_to_canonical
        and d in variant_to_canonical
        and variant_to_canonical[s] == variant_to_canonical[d]
    ):
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
