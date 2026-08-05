"""select_entity_tables tool implementation.

Decides which source tables hold records of each target entity.

Before this existed, selection was a hardcoded synonym list
(``ALIAS_GROUPS``): a table only counted as holding ``person`` records if its
name literally appeared in that list. Pagila's ``customer`` and MongoDB's
``users`` are not in it, so a two-origin merge silently ran against one table
and nobody was told the rest of the data had been dropped.

The LLM now reads the FULL table catalog — every table in every origin, with
column names and row counts, no name pre-filtering — and proposes candidates
per entity. The alias heuristic is kept as a floor (anything it matched is
still included even if the LLM is unavailable) rather than as the ceiling.
Cost is O(schema), not O(rows): one call per run regardless of data size.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.orchestrator.utils.entity_aliases import concept_matches

logger = logging.getLogger("merge-engine.tools.select_entity_tables")

# Column-name shapes that mark a table as a relationship/log rather than a
# store of entity records. Only used for the heuristic floor — the LLM is told
# the same rules and reasons about them with far more context.
_MAX_CATALOG_COLUMNS = 40


def build_table_catalog(schemas: dict[str, Any], include_foreign_keys: bool = False) -> str:
    """Render every table in every origin as a compact catalog for the prompt.

    Deliberately unfiltered: the whole point of this stage is to consider
    tables whose names don't resemble the entity name.

    ``include_foreign_keys`` adds a line per table listing its declared foreign
    keys (``column -> ref_table.ref_column``). Scope planning needs these to
    trace "which people work at this company"; entity-table *selection* does not
    and leaves them off to keep its prompt lean. Note that ClickHouse and
    MongoDB origins have no declared FKs at all — the planner must fall back to
    column-name reasoning there, which is exactly why an LLM plans the joins.
    """
    lines: list[str] = []
    for origin_id, schema_data in (schemas or {}).items():
        origin_type = schema_data.get("origin_type", "?")
        lines.append(f"=== Origin: {origin_id} ({origin_type}) ===")
        for table in schema_data.get("tables", []):
            name = table.get("full_name") or table.get("name") or "?"
            columns = [c.get("name", "?") for c in table.get("columns", [])]
            shown = columns[:_MAX_CATALOG_COLUMNS]
            more = (
                ""
                if len(columns) <= _MAX_CATALOG_COLUMNS
                else f", …(+{len(columns) - _MAX_CATALOG_COLUMNS} more)"
            )
            row_count = table.get("row_count")
            count_str = f" [{row_count} rows]" if row_count is not None else ""
            lines.append(f"  {name}{count_str} — {', '.join(shown)}{more}")
            if include_foreign_keys:
                fks = table.get("foreign_keys") or []
                if fks:
                    rendered = ", ".join(
                        f"{fk.get('column')} -> {fk.get('ref_table')}.{fk.get('ref_column')}"
                        for fk in fks
                    )
                    lines.append(f"      FK: {rendered}")
    return "\n".join(lines)


def heuristic_candidates(entity_type: str, schemas: dict[str, Any]) -> list[dict[str, Any]]:
    """Alias-group matches for an entity — the offline floor.

    Mirrors the original ``_find_entity_tables`` name matching so a run without
    a working LLM behaves exactly as it did before this stage existed.
    """
    from src.orchestrator.utils.entity_aliases import _group_of

    out: list[dict[str, Any]] = []
    for origin_id, schema_data in (schemas or {}).items():
        for table in schema_data.get("tables", []):
            table_name = table.get("name", "")
            full_name = table.get("full_name", table_name)
            matched = any(concept_matches(entity_type, n) for n in (table_name, full_name))
            if not matched:
                bare = full_name.lower().split(".")[-1].strip() or table_name.lower()
                if _group_of(bare) is None:
                    matched = any(
                        concept_matches(entity_type, tok) for tok in bare.split("_") if tok
                    )
            if matched:
                out.append(
                    {
                        "origin_id": origin_id,
                        "table": full_name,
                        "confidence": 0.8,
                        "reason": "name matches known aliases for this concept",
                        "source": "heuristic",
                    }
                )
    return out


def _known_tables(schemas: dict[str, Any]) -> dict[str, dict[str, str]]:
    """``{origin_id: {alias: canonical_full_name}}`` for name resolution.

    Every alias a model might use (bare name, qualified name) maps to the ONE
    canonical full name, so ``public.customer`` and ``customer`` can never
    both survive as separate picks and get the table extracted twice.
    """
    known: dict[str, dict[str, str]] = {}
    for origin_id, schema_data in (schemas or {}).items():
        aliases: dict[str, str] = {}
        for table in schema_data.get("tables", []):
            name = table.get("name")
            full = table.get("full_name") or name
            if not full:
                continue
            for alias in {full, name, full.split(".")[-1]}:
                if alias:
                    aliases.setdefault(alias, full)
        known[origin_id] = aliases
    return known


def _resolve_table(
    origin_id: str, table: str, known: dict[str, dict[str, str]]
) -> tuple[str, str] | None:
    """Map an LLM-named (origin, table) to its canonical form, or None."""
    # The model may have abbreviated or reordered the origin id; fall back to
    # searching every origin rather than dropping a real table.
    candidates = {origin_id: known[origin_id]} if origin_id in known else known

    for oid, aliases in candidates.items():
        canonical = aliases.get(table) or aliases.get(table.split(".")[-1])
        if canonical:
            return oid, canonical
    return None


async def select_entity_tables_impl(
    target_entities: list[str],
    schemas: dict[str, Any],
    llm_config: dict[str, Any] | None = None,
    user_request: str = "",
) -> dict[str, Any]:
    """Choose the source tables holding each target entity.

    Returns ``{entity_type: [{origin_id, table, confidence, reason, source}]}``
    under ``entity_tables``, plus ``notes`` and ``llm_used``.

    The heuristic result is always included; the LLM only ever ADDS tables.
    Any table the LLM names that doesn't exist in the discovered schema is
    dropped with a warning rather than passed downstream.
    """
    logger.debug(
        "ENTER select_entity_tables_impl: entities=%s, origins=%d",
        target_entities,
        len(schemas or {}),
    )

    selected: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[tuple[str, str]]] = {}
    for entity in target_entities:
        selected[entity] = heuristic_candidates(entity, schemas)
        seen[entity] = {(c["origin_id"], c["table"]) for c in selected[entity]}

    llm_enabled = bool(llm_config) and llm_config.get("table_selection_enabled", True)
    if not target_entities or not schemas or not llm_enabled:
        logger.info(
            "Table selection: heuristic only (%s)",
            "LLM disabled" if not llm_enabled else "nothing to select",
        )
        return {"entity_tables": selected, "notes": "", "llm_used": False}

    from src.llm import PROMPT_TEMPLATES, LLMClient

    prompts = PROMPT_TEMPLATES.get("entity_table_selection", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")
    if not system_prompt or not human_template:
        logger.warning("entity_table_selection prompts missing; keeping heuristic candidates")
        return {"entity_tables": selected, "notes": "", "llm_used": False}

    catalog = build_table_catalog(schemas)
    user_prompt = human_template.replace("{target_entities}", json.dumps(target_entities)).replace(
        "{table_catalog}", catalog
    )
    if user_request:
        user_prompt += f"\n\n(The user's original request was: {user_request})\n"

    notes = ""
    try:
        client = LLMClient(llm_config)
        result = await client.call_json(system_prompt=system_prompt, user_prompt=user_prompt)
        proposed = result.get("entity_tables", {}) if isinstance(result, dict) else {}
        notes = str(result.get("notes", "")) if isinstance(result, dict) else ""

        known = _known_tables(schemas)
        added = invented = 0
        for entity, items in (proposed or {}).items():
            if entity not in selected:
                # The model answered for an entity nobody asked about.
                continue
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                origin_id = str(item.get("origin_id") or "").strip()
                table = str(item.get("table") or "").strip()
                if not table:
                    continue
                resolved = _resolve_table(origin_id, table, known)
                if resolved is None:
                    logger.warning(
                        "LLM proposed a table that is not in any discovered "
                        "schema: %s.%s — dropped.",
                        origin_id,
                        table,
                    )
                    invented += 1
                    continue
                key = resolved
                if key in seen[entity]:
                    continue
                seen[entity].add(key)
                try:
                    confidence = float(item.get("confidence", 0.7))
                except (TypeError, ValueError):
                    confidence = 0.7
                selected[entity].append(
                    {
                        "origin_id": key[0],
                        "table": key[1],
                        "confidence": max(0.0, min(1.0, confidence)),
                        "reason": str(item.get("reason", ""))[:200],
                        "source": "llm",
                    }
                )
                added += 1

        logger.info(
            "Table selection: LLM added %d table(s) beyond the name heuristic%s. Final: %s",
            added,
            f", dropped {invented} invented" if invented else "",
            {e: len(v) for e, v in selected.items()},
        )
        return {"entity_tables": selected, "notes": notes, "llm_used": True}

    except Exception as exc:
        logger.warning(
            "LLM table selection failed (%s); falling back to the name "
            "heuristic alone. Tables whose names don't resemble the entity "
            "will not be included this run.",
            exc,
        )
        return {
            "entity_tables": selected,
            "notes": f"LLM table selection unavailable: {exc}",
            "llm_used": False,
        }


async def select_entity_tables_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    return await select_entity_tables_impl(**arguments)
