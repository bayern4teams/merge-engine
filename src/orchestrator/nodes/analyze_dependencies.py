"""
analyze_dependencies node: Uses LLM to build entity dependency tree from schemas.

Parses the collected schemas and schema samples to identify FK relationships
and semantic dependencies between entity types.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.llm import PROMPT_TEMPLATES, LLMClient
from src.mcp_server.tools.select_entity_tables import select_entity_tables_impl
from src.orchestrator.utils.dependency_sort import topological_sort
from src.orchestrator.utils.retry_hints import apply_retry_hint

logger = logging.getLogger("merge-engine.orchestrator.analyze_dependencies")


async def analyze_dependencies(state: dict[str, Any]) -> dict[str, Any]:
    """Analyze schemas to determine entity dependencies.

    Parameters
    ----------
    state : dict
        MergeState with ``schemas`` and ``target_entities`` populated.

    Returns
    -------
    dict
        Update dict with ``dependency_tree`` and ``extraction_order`` keys.
    """
    logger.debug("ENTER analyze_dependencies")
    logger.debug("State keys: %s", list(state.keys()))
    target_entities = state.get("target_entities", [])
    schemas = state.get("schemas", {})
    llm_config: dict[str, Any] = state["llm_config"]
    # Only THIS call's own errors (Phase 0 Sec 0.8) - seeding with the
    # existing history meant the returned list already contained it, and
    # operator.add's reducer then appended that same history a second time
    # onto state, duplicating it on every single call.
    errors: list[str] = []

    logger.debug("target_entities: %s", target_entities)
    logger.debug("schemas keys: %s", list(schemas.keys()))
    for origin_id, schema_data in schemas.items():
        table_count = len(schema_data.get("tables", []))
        logger.debug("Schema %s: %d tables", origin_id, table_count)

    if not target_entities:
        msg = "No target entities parsed; skipping dependency analysis."
        logger.warning(msg)
        logger.debug("EXIT analyze_dependencies (no entities)")
        return {
            "dependency_tree": {},
            "extraction_order": [],
            "errors": [*errors, msg],
        }

    # Decide WHICH tables hold each entity before reasoning about their order.
    # Uses the full unfiltered catalog, so tables whose names don't resemble
    # the entity ("customer" for person) are still considered.
    entity_tables: dict[str, Any] = {}
    table_selection_notes = ""

    # A profile that already pins these entities' tables is reused rather than
    # re-derived (Phase O.1). Re-asking the LLM would spend a call to
    # re-decide something a human already confirmed, and it might answer
    # differently — which is the whole reason profiles exist.
    pinned = state.get("entity_tables") or {}
    reuse_pinned = bool(pinned) and all(e in pinned for e in target_entities)

    if reuse_pinned:
        entity_tables = pinned
        table_selection_notes = (
            f"Reused from saved profile {state.get('profile_name') or '(unnamed)'}."
        )
        logger.info(
            "Reusing pinned table selection for %s",
            ", ".join(target_entities),
        )
    else:
        try:
            selection = await select_entity_tables_impl(
                target_entities=target_entities,
                schemas=schemas,
                llm_config=llm_config,
                user_request=state.get("user_request", ""),
            )
            entity_tables = selection.get("entity_tables", {})
            table_selection_notes = selection.get("notes", "")
            for entity, tables in entity_tables.items():
                logger.info(
                    "Entity '%s' -> %d source table(s): %s",
                    entity,
                    len(tables),
                    ", ".join(f"{t['origin_id']}:{t['table']}" for t in tables) or "none",
                )
        except Exception as exc:
            # Never fail the run over table selection — extraction falls back
            # to the alias heuristic when entity_tables is empty.
            logger.warning("Entity table selection failed: %s", exc, exc_info=True)

    # Build schema summary for LLM prompt
    schema_summary = _build_schema_summary(schemas, target_entities)
    logger.debug("Schema summary generated (%d chars)", len(schema_summary))

    client = LLMClient(llm_config)
    prompts = PROMPT_TEMPLATES.get("dependency_analysis", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")

    if not system_prompt or not human_template:
        system_prompt = system_prompt or (
            "You analyze database schemas to determine which entities must be "
            "extracted before others. Return only JSON."
        )
        human_template = human_template or (
            "Analyze these schemas and determine dependencies for entities: {target_entities}\n\n{schema_info}"
        )

    # The prompt contains literal JSON examples with curly braces,
    # so str.format() would raise KeyError — substitute placeholders directly.
    user_prompt = human_template.replace("{target_entities}", json.dumps(target_entities)).replace(
        "{schema_info}", schema_summary
    )
    # If a reviewer rejected the previous dependency tree, their reason is
    # appended here so this rerun answers the objection.
    user_prompt = apply_retry_hint(user_prompt, state, "analyze_dependencies")
    logger.debug("LLM prompt prepared (first 300 chars): %s", user_prompt[:300])

    try:
        logger.debug("Calling LLMClient.call_json for dependency analysis")
        result = await client.call_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        logger.debug("LLM response received: %s", json.dumps(result, default=str)[:500])

        # Parse response
        dependency_tree = result.get("dependency_tree", {})
        logger.debug("Parsed dependency_tree: %s", dependency_tree)

        # Compute extraction order via topological sort
        extraction_order = topological_sort(dependency_tree)
        logger.debug("Computed extraction_order: %s", extraction_order)

        logger.info(
            "Dependency analysis complete: tree=%s, order=%s",
            dependency_tree,
            extraction_order,
        )

        # Only the keys this node actually computes - a key it omits is left
        # untouched by LangGraph's per-key TypedDict state update, not
        # cleared (Phase 0 Sec 0.8; the passthrough here predated that being
        # verified and cited a StateGraph(dict) overwrite semantics this
        # graph hasn't used for a long time).
        return {
            "dependency_tree": dependency_tree,
            "dependency_analysis_warning": "",
            "extraction_order": extraction_order,
            "entity_tables": entity_tables,
            "table_selection_notes": table_selection_notes,
            "interrupt_data": None,
        }

    except Exception as exc:
        msg = f"Dependency analysis failed: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
        warning = (
            "Dependency order is UNVERIFIED — the LLM analysis failed, so this is "
            "a flat, unordered fallback. Review carefully before approving; extraction "
            "may hit foreign-key violations if a real dependency was missed."
        )
        return {
            "dependency_tree": {e: [] for e in target_entities},
            "dependency_analysis_warning": warning,
            "extraction_order": list(target_entities),
            "entity_tables": entity_tables,
            "table_selection_notes": table_selection_notes,
            "errors": errors,
            "interrupt_data": None,
        }
    finally:
        logger.debug("EXIT analyze_dependencies")


def _build_schema_summary(schemas: dict[str, Any], target_entities: list[str]) -> str:
    """Build a compact schema summary string for the LLM prompt."""
    lines: list[str] = []
    for origin_id, schema_data in schemas.items():
        lines.append(f"\n=== Origin: {origin_id} ({schema_data.get('origin_type', '?')}) ===")
        for table in schema_data.get("tables", []):
            table_name = table.get("name", "?")
            # Skip tables not potentially related to target entities
            if not _table_may_be_related(table_name, target_entities):
                continue

            lines.append(f"\n  Table: {table_name}")
            lines.append(f"    Columns: {', '.join(c['name'] for c in table.get('columns', []))}")

            # Foreign keys
            fks = table.get("foreign_keys", [])
            if fks:
                for fk in fks:
                    lines.append(
                        f"    FK: {table_name}.{fk.get('column', '?')} -> "
                        f"{fk.get('ref_table', '?')}.{fk.get('ref_column', '?')}"
                    )

            # Sample row (first 2 fields)
            sample = table.get("sample_row", {})
            if sample:
                sample_str = ", ".join(f"{k}={v!r}" for k, v in list(sample.items())[:3])
                lines.append(f"    Sample: {{{sample_str}}}")

    return "\n".join(lines)


# Common irregular plurals not caught by the +s/+es heuristic below.
_IRREGULAR_PLURALS = {"person": "people"}


def _table_may_be_related(table_name: str, target_entities: list[str]) -> bool:
    """Heuristic: check if a table name plausibly matches a target entity."""
    if not target_entities:
        # No entity filter information — include every table rather than
        # hiding schema from the LLM.
        return True
    name_lower = table_name.lower().replace("_", " ").replace("-", " ")
    for entity in target_entities:
        if entity in name_lower or name_lower in entity:
            return True
        # Check common plural/singular variants
        entity_plural = entity + "s"
        entity_possessive = entity + "'" + "s"
        if entity_plural in name_lower or entity_possessive in name_lower:
            return True
        if entity + "es" in name_lower:
            return True
        irregular = _IRREGULAR_PLURALS.get(entity)
        if irregular and irregular in name_lower:
            return True
    return False
