"""
analyze_dependencies node: Uses LLM to build entity dependency tree from schemas.

Parses the collected schemas and schema samples to identify FK relationships
and semantic dependencies between entity types.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.llm import LLMClient, PROMPT_TEMPLATES
from src.orchestrator.utils.dependency_sort import topological_sort

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
    target_entities = state.get("target_entities", [])
    schemas = state.get("schemas", {})
    llm_config: dict[str, Any] = state["llm_config"]
    errors: list[str] = list(state.get("errors", []))

    if not target_entities:
        msg = "No target entities parsed; skipping dependency analysis."
        logger.warning(msg)
        return {
            "dependency_tree": {},
            "extraction_order": [],
            "errors": errors + [msg],
        }

    # Build schema summary for LLM prompt
    schema_summary = _build_schema_summary(schemas, target_entities)

    client = LLMClient(llm_config)
    prompt_template = PROMPT_TEMPLATES.get("dependency_analysis", "")

    if not prompt_template:
        prompt_template = (
            "Analyze these schemas and determine dependencies for entities: {target_entities}\n\n{schema_info}"
        )

    user_prompt = prompt_template.format(
        target_entities=json.dumps(target_entities),
        schema_info=schema_summary,
    )

    try:
        result = await client.call_json(
            system_prompt=(
                "You analyze database schemas to determine which entities must be "
                "extracted before others. Return only JSON."
            ),
            user_prompt=user_prompt,
        )

        # Parse response
        dependency_tree = result.get("dependency_tree", {})
        notes = result.get("notes", "")

        # Compute extraction order via topological sort
        extraction_order = topological_sort(dependency_tree)

        logger.info(
            "Dependency analysis complete: tree=%s, order=%s",
            dependency_tree,
            extraction_order,
        )

        return {
            "dependency_tree": dependency_tree,
            "extraction_order": extraction_order,
            "errors": errors,
        }

    except Exception as exc:
        msg = f"Dependency analysis failed: {exc}"
        logger.error(msg)
        errors.append(msg)
        return {
            "dependency_tree": {e: [] for e in target_entities},
            "extraction_order": list(target_entities),
            "errors": errors,
        }


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
                sample_str = ", ".join(
                    f"{k}={v!r}" for k, v in list(sample.items())[:3]
                )
                lines.append(f"    Sample: {{{sample_str}}}")

    return "\n".join(lines)


def _table_may_be_related(table_name: str, target_entities: list[str]) -> bool:
    """Heuristic: check if a table name plausibly matches a target entity."""
    name_lower = table_name.lower().replace("_", " ").replace("-", " ")
    for entity in target_entities:
        if entity in name_lower or name_lower in entity:
            return True
        # Check common plural/singular variants
        entity_plural = entity + "s"
        entity_possessive = entity + "'" + "s"
        if entity_plural in name_lower or entity_possessive in name_lower:
            return True
    return True  # Default: include all tables (schema summary may be small enough)