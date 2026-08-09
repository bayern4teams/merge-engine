"""plan_scope tool: turn a subject ("Nova") into a per-table extraction plan.

When a request is scoped to one organisation, the pipeline must extract only
that organisation's data — its own record, its people, its products — and
nothing about any other. This stage decides, for every target entity and every
source table, HOW to restrict extraction to the subject:

- ``match``  — keep rows whose text column contains the subject value
               (the company's own name; a denormalised ``company_name``).
- ``link``   — keep rows whose id column references the subject's matched rows
               in the same origin (``company_id IN (…)``).

An LLM does the reasoning because the shape varies per origin and most origins
(ClickHouse, MongoDB) have no declared foreign keys to follow — the link column
has to be inferred from names. A heuristic floor runs when the LLM is
unavailable so a scoped run still does *something* sensible rather than nothing.

Cost is O(schema): one call per run, never per row.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.mcp_server.tools.select_entity_tables import (
    _known_tables,
    _resolve_table,
    build_table_catalog,
)
from src.orchestrator.utils.entity_aliases import concept_matches

logger = logging.getLogger("merge-engine.tools.plan_scope")

# Column names that, absent a declared FK, mark a text column as carrying an
# organisation's own identifying value — used by the heuristic floor. Bare
# "org" is deliberately excluded: it matches "org_id", an id column, not a name.
_NAME_COLUMN_HINTS = ("name", "title", "company", "organization", "organisation")

# Column-name fragments that mark an id column as linking to a company/org.
_LINK_COLUMN_HINTS = ("company", "org", "employer", "account", "customer")


def _is_name_column(col: str) -> bool:
    """A text column that carries the subject's own identifying value.

    Excludes id columns explicitly: ``org_id`` contains the "org" family but is
    a key, not a name, and matching a subject name against it never works.
    """
    lc = col.lower()
    if lc == "id" or lc.endswith("_id"):
        return False
    return any(h in lc for h in _NAME_COLUMN_HINTS)


def _columns_of(table: dict[str, Any]) -> list[str]:
    return [str(c.get("name", "")) for c in table.get("columns", []) if c.get("name")]


def _iter_tables(schemas: dict[str, Any]):
    """Yield (origin_id, table_dict, full_name) across every origin."""
    for origin_id, schema_data in (schemas or {}).items():
        for table in schema_data.get("tables", []):
            full = table.get("full_name") or table.get("name") or ""
            if full:
                yield origin_id, table, full


def _heuristic_plan(
    subject_entity: str,
    target_entities: list[str],
    schemas: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """A best-effort plan when the LLM is unavailable.

    Subject-entity tables are matched on a name-like column. Dependent-entity
    tables are linked on an id column that references the subject entity — a
    declared FK first, then a name hint. A dependent table with neither is left
    out rather than extracted in full, because extracting it whole would pull in
    every other organisation's rows, which is the one thing a scoped run must
    not do.
    """
    plan: dict[str, list[dict[str, Any]]] = {e: [] for e in target_entities}

    # Names of the subject entity's tables, per origin, for FK ref matching.
    subject_tables: dict[str, set[str]] = {}
    for origin_id, table, full in _iter_tables(schemas):
        name = table.get("name", "")
        if concept_matches(subject_entity, name) or concept_matches(subject_entity, full):
            subject_tables.setdefault(origin_id, set()).add(name.lower())

    for entity in target_entities:
        for origin_id, table, full in _iter_tables(schemas):
            name = table.get("name", "")
            cols = _columns_of(table)
            lower = {c.lower(): c for c in cols}

            is_subject_table = concept_matches(entity, name) or concept_matches(entity, full)

            if entity == subject_entity:
                if not is_subject_table:
                    continue
                match_cols = [c for c in cols if _is_name_column(c)] or (
                    [cols[1]] if len(cols) > 1 else cols[:1]
                )
                if match_cols:
                    plan[entity].append(
                        {
                            "origin_id": origin_id,
                            "table": full,
                            "mode": "match",
                            "columns": match_cols[:2],
                            "reason": "subject entity's own record (heuristic)",
                        }
                    )
                continue

            # Dependent entity: only include a table that plausibly holds this
            # entity AND has a way to link to the subject.
            if not is_subject_table:
                continue
            link_col = None
            for fk in table.get("foreign_keys") or []:
                ref = str(fk.get("ref_table", "")).lower()
                if any(st in ref or ref in st for st in subject_tables.get(origin_id, set())):
                    link_col = fk.get("column")
                    break
            if link_col is None:
                for c in lower:
                    if c.endswith("_id") and any(h in c for h in _LINK_COLUMN_HINTS):
                        link_col = lower[c]
                        break
            if link_col:
                plan[entity].append(
                    {
                        "origin_id": origin_id,
                        "table": full,
                        "mode": "link",
                        "columns": [link_col],
                        "link_entity": subject_entity,
                        "reason": "links to the subject entity (heuristic)",
                    }
                )
                continue

            # No direct link: look for a junction table (jobs / employment)
            # that ties this entity to the subject — "people who work at Nova".
            via = _find_junction(schemas, origin_id, table, subject_entity, entity)
            if via:
                plan[entity].append(
                    {
                        "origin_id": origin_id,
                        "table": full,
                        "mode": "link_via",
                        "columns": [via["entity_key"]],
                        "link_entity": subject_entity,
                        "via_table": via["via_table"],
                        "via_from_column": via["from_column"],
                        "via_to_column": via["to_column"],
                        "reason": f"linked to the subject via {via['via_table']} (heuristic)",
                    }
                )

    return plan


def _find_junction(
    schemas: dict[str, Any],
    origin_id: str,
    entity_table: dict[str, Any],
    subject_entity: str,
    entity: str,
) -> dict[str, Any] | None:
    """Find a junction table linking ``entity_table`` to the subject entity.

    A junction has one column referencing the subject (``company_id``/``org_id``)
    and one referencing this entity (its primary key, e.g. ``person_id``). This
    is what turns "people who work at Nova" — an employment row, not a column on
    the person — into an extractable filter.
    """
    entity_pk = None
    for c in entity_table.get("primary_key") or []:
        if c:
            entity_pk = c
            break
    if not entity_pk:
        # Fall back to a conventional "<entity>_id" name.
        for col in entity_table.get("columns", []):
            name = str(col.get("name", ""))
            if name.endswith("_id") and entity in name.lower():
                entity_pk = name
                break
    if not entity_pk:
        return None

    schema_data = (schemas or {}).get(origin_id, {})
    for table in schema_data.get("tables", []):
        if table is entity_table:
            continue
        cols = {str(c.get("name", "")): str(c.get("name", "")) for c in table.get("columns", [])}
        lower = {c.lower(): c for c in cols}
        # Must reference the subject...
        from_col = None
        for c in lower:
            if c.endswith("_id") and any(h in c for h in _LINK_COLUMN_HINTS):
                from_col = lower[c]
                break
        # ...and this entity (by the entity's own key name).
        to_col = lower.get(entity_pk.lower())
        if from_col and to_col and from_col != to_col:
            return {
                "via_table": table.get("full_name") or table.get("name"),
                "from_column": from_col,
                "to_column": to_col,
                "entity_key": entity_pk,
            }
    return None


def _sanitize_plan(
    proposed: dict[str, Any],
    target_entities: list[str],
    subject_entity: str,
    schemas: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Keep only well-formed table filters naming real (origin, table) pairs."""
    known = _known_tables(schemas)
    clean: dict[str, list[dict[str, Any]]] = {e: [] for e in target_entities}
    for entity, items in (proposed or {}).items():
        if entity not in clean:
            continue
        for item in items or []:
            if not isinstance(item, dict):
                continue
            origin_id = str(item.get("origin_id") or "").strip()
            table = str(item.get("table") or "").strip()
            resolved = _resolve_table(origin_id, table, known)
            if resolved is None:
                logger.warning(
                    "Scope plan named a table not in any schema: %s.%s — dropped.",
                    origin_id,
                    table,
                )
                continue
            mode = str(item.get("mode") or "").strip().lower()
            columns = [str(c) for c in (item.get("columns") or []) if str(c)]
            if mode not in ("match", "link", "link_via") or not columns:
                logger.warning(
                    "Scope plan entry for %s has no usable mode/columns — dropped.",
                    resolved,
                )
                continue
            entry = {
                "origin_id": resolved[0],
                "table": resolved[1],
                "mode": mode,
                "columns": columns[:3] if mode == "match" else columns[:1],
                "reason": str(item.get("reason", ""))[:200],
            }
            if mode in ("link", "link_via"):
                entry["link_entity"] = str(item.get("link_entity") or subject_entity)
            if mode == "link_via":
                # A two-hop link is only usable with its junction fully named;
                # a partial one silently degrades to "extract nothing", so drop it.
                via_table = str(item.get("via_table") or "").strip()
                from_col = str(item.get("via_from_column") or "").strip()
                to_col = str(item.get("via_to_column") or "").strip()
                if not (via_table and from_col and to_col):
                    logger.warning(
                        "Scope plan link_via for %s is missing its junction "
                        "(via_table/from/to) — dropped.",
                        resolved,
                    )
                    continue
                # Resolve the junction table name against the schema too.
                via_resolved = _resolve_table(origin_id, via_table, known)
                entry["via_table"] = via_resolved[1] if via_resolved else via_table
                entry["via_from_column"] = from_col
                entry["via_to_column"] = to_col
            clean[entity].append(entry)
    return clean


async def plan_scope_impl(
    subject: str,
    subject_entity: str,
    target_entities: list[str],
    schemas: dict[str, Any],
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the per-table extraction plan for a scoped ("Nova") merge.

    Returns ``{"scope_plan": {...}, "notes": str, "llm_used": bool}`` where
    ``scope_plan`` is ``{subject, subject_entity, entities: {entity: [filters]}}``.
    The heuristic floor is always computed; the LLM plan replaces it when
    available and well-formed, else the floor stands.
    """
    subject = str(subject or "").strip()
    subject_entity = str(subject_entity or "company").strip() or "company"
    floor = _heuristic_plan(subject_entity, target_entities, schemas)

    def _wrap(entities: dict[str, list[dict[str, Any]]], notes: str, llm_used: bool):
        return {
            "scope_plan": {
                "subject": subject,
                "subject_entity": subject_entity,
                "entities": entities,
            },
            "notes": notes,
            "llm_used": llm_used,
        }

    def _ensure_complete_origin_coverage(entities: dict[str, list[dict[str, Any]]]):
        all_origins = list((schemas or {}).keys())
        for entity in target_entities:
            existing_entries = entities.setdefault(entity, [])
            if not existing_entries:
                for oid in all_origins:
                    existing_entries.append(
                        {
                            "origin_id": oid,
                            "table": "—",
                            "mode": "not_found",
                            "columns": [],
                            "reason": f"No matching or linked {entity} table found in {oid}",
                        }
                    )

    llm_enabled = bool(llm_config) and llm_config.get("scope_planning_enabled", True)
    if not subject or not target_entities or not schemas or not llm_enabled:
        _ensure_complete_origin_coverage(floor)
        return _wrap(floor, "", False)

    from src.llm import PROMPT_TEMPLATES, LLMClient

    prompts = PROMPT_TEMPLATES.get("scope_planning", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")
    if not system_prompt or not human_template:
        logger.warning("scope_planning prompts missing; using heuristic scope plan")
        _ensure_complete_origin_coverage(floor)
        return _wrap(floor, "", False)

    catalog = build_table_catalog(schemas, include_foreign_keys=True)
    user_prompt = (
        human_template.replace("{subject}", subject)
        .replace("{subject_entity}", subject_entity)
        .replace("{target_entities}", json.dumps(target_entities))
        .replace("{table_catalog}", catalog)
    )

    try:
        client = LLMClient(llm_config)
        result = await client.call_json(system_prompt=system_prompt, user_prompt=user_prompt)
        proposed = result.get("entities", {}) if isinstance(result, dict) else {}
        notes = str(result.get("notes", "")) if isinstance(result, dict) else ""
        clean = _sanitize_plan(proposed, target_entities, subject_entity, schemas)

        # If the model returned nothing usable for any entity, the heuristic
        # floor is a better answer than an empty plan that extracts nothing.
        if not any(clean.values()):
            logger.warning("LLM scope plan had no usable entries; falling back to heuristic.")
            _ensure_complete_origin_coverage(floor)
            return _wrap(floor, notes or "LLM plan empty; used heuristic.", False)

        # Fill in entities the model skipped from the floor, so a partial answer
        # never silently drops an entity the user asked to scope.
        for entity, entries in floor.items():
            if not clean.get(entity) and entries:
                clean[entity] = entries

        _ensure_complete_origin_coverage(clean)
        logger.info(
            "Scope plan for %r: %s",
            subject,
            {e: len(v) for e, v in clean.items()},
        )
        return _wrap(clean, notes, True)

    except Exception as exc:
        logger.warning(
            "LLM scope planning failed (%s); using the heuristic scope plan.",
            exc,
        )
        _ensure_complete_origin_coverage(floor)
        return _wrap(floor, f"LLM scope planning unavailable: {exc}", False)


# How many subject rows to show at the confirmation gate, and per table, so the
# probe is a preview ("which Nova did you mean") rather than a real extraction.
_PROBE_TOTAL = 40
_PROBE_PER_TABLE = 20


async def probe_subject_candidates(
    subject: str,
    scope_plan: dict[str, Any],
    origins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fetch a few subject rows so a human can confirm *which* subject is meant.

    Reads only the subject entity's ``match`` tables, with the same ILIKE
    predicate extraction will use, capped hard. Best-effort: any origin that is
    unreachable or misconfigured is skipped, because a preview must never be
    what stops a run — the gate can still show the plan without it.
    """
    from src.mcp_server.tools.extract_data import extract_data_impl

    subject = str(subject or "").strip()
    subject_entity = scope_plan.get("subject_entity", "company")
    plans = (scope_plan.get("entities") or {}).get(subject_entity, [])
    by_id = {str(o.get("id")): o for o in (origins or [])}

    candidates: list[dict[str, Any]] = []
    for entry in plans:
        if entry.get("mode") != "match" or len(candidates) >= _PROBE_TOTAL:
            continue
        origin = by_id.get(str(entry.get("origin_id")))
        if not origin or not origin.get("connection"):
            continue
        columns = entry.get("columns") or []
        if not columns:
            continue
        # OR across the match columns is expressed as one ILIKE here (the first
        # column); the gate is a preview, not the authoritative filter.
        filt = {columns[0]: {"op": "ilike", "value": subject}}
        try:
            result = await extract_data_impl(
                origin_config=origin,
                table_name=entry["table"],
                columns=["*"],
                batch_size=_PROBE_PER_TABLE,
                filter=filt,
            )
        except Exception as exc:
            logger.warning(
                "Scope probe skipped %s.%s: %s",
                entry.get("origin_id"),
                entry.get("table"),
                exc,
            )
            continue
        for row in (result.get("rows") or [])[:_PROBE_PER_TABLE]:
            candidates.append(
                {
                    "origin_id": entry.get("origin_id"),
                    "table": entry.get("table"),
                    "fields": {
                        k: v
                        for k, v in row.items()
                        if not str(k).startswith("_") and v not in (None, "")
                    },
                }
            )
            if len(candidates) >= _PROBE_TOTAL:
                break
    logger.info("Scope probe found %d candidate subject row(s)", len(candidates))
    return candidates


async def plan_scope_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    return await plan_scope_impl(**arguments)
