"""
parse_request node: Extracts target entity types from user's natural language prompt.

Uses the LLM with the request_parsing prompt template to identify which
entity types the user wants to import based on discovered database schemas.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm import PROMPT_TEMPLATES, LLMClient

logger = logging.getLogger("merge-engine.orchestrator.parse_request")


def _build_full_schema_summary(schemas: dict[str, Any]) -> str:
    """Build a complete schema catalog summary across all origins."""
    lines: list[str] = []
    for origin_id, schema_data in (schemas or {}).items():
        origin_type = schema_data.get("origin_type", "?")
        lines.append(f"\n=== Origin: {origin_id} ({origin_type}) ===")
        for table in schema_data.get("tables", []):
            table_name = table.get("name", "?")
            cols = [c.get("name", "") for c in table.get("columns", []) if c.get("name")]
            lines.append(f"\n  Table: {table_name}")
            if cols:
                lines.append(f"    Columns: {', '.join(cols)}")
            sample = table.get("sample_row", {})
            if sample:
                sample_str = ", ".join(f"{k}={v!r}" for k, v in list(sample.items())[:4])
                lines.append(f"    Sample: {{{sample_str}}}")
    return "\n".join(lines) if lines else "(No discovered schemas available)"


async def parse_request(state: dict[str, Any]) -> dict[str, Any]:
    """Parse user request to extract target entity types and entity table mappings.

    Parameters
    ----------
    state : dict
        MergeState with ``user_request`` and ``schemas`` fields populated.

    Returns
    -------
    dict
        Update dict with ``target_entities``, ``explicit_target_entities``,
        ``merge_scope``, and ``entity_tables`` keys.
    """
    logger.debug("ENTER parse_request")
    logger.debug("State keys: %s", list(state.keys()))
    logger.debug("user_request (truncated): %.200s", state.get("user_request", ""))
    logger.debug("llm_config keys: %s", list(state.get("llm_config", {}).keys()))
    logger.debug("origins count: %d", len(state.get("origins", [])))
    schemas = state.get("schemas", {})
    logger.debug("schemas count: %d", len(schemas))

    user_request: str = state["user_request"]
    llm_config: dict[str, Any] = state["llm_config"]

    client = LLMClient(llm_config)
    prompts = PROMPT_TEMPLATES.get("request_parsing", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")

    if not system_prompt or not human_template:
        logger.warning("request_parsing prompt not found, using fallback")
        system_prompt = (
            system_prompt or "You extract entity types from user requests. Return only JSON."
        )
        human_template = (
            human_template
            or "Extract entity types from this request as JSON: {user_request}\n\nSchema:\n{schema_info}"
        )

    schema_summary = _build_full_schema_summary(schemas)
    # CLAUDE.md hard constraint: prompt substitution is literal str.replace(), never .format()
    user_prompt = human_template.replace("{user_request}", user_request).replace(
        "{schema_info}", schema_summary
    )
    logger.debug(
        "Prompt loaded (%d chars, truncated to %d)", len(user_prompt), min(len(user_prompt), 500)
    )
    logger.debug("User prompt (first 500 chars): %s", user_prompt[:500])

    try:
        logger.debug("Calling LLMClient.call_json()")
        result = await client.call_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        logger.debug(
            "LLM response received: type=%s, repr=%.300s", type(result).__name__, repr(result)
        )

        target_entities = result.get("entity_types", []) if isinstance(result, dict) else []
        raw_entity_tables = result.get("entity_tables", {}) if isinstance(result, dict) else {}

        # Validate/clean entity_tables
        entity_tables: dict[str, list[dict[str, str]]] = {}
        if isinstance(raw_entity_tables, dict):
            for ent, tbls in raw_entity_tables.items():
                if isinstance(tbls, list):
                    cleaned_tbls = []
                    for t in tbls:
                        if isinstance(t, dict) and "origin_id" in t and "table" in t:
                            cleaned_tbls.append(
                                {"origin_id": str(t["origin_id"]), "table": str(t["table"])}
                            )
                    entity_tables[ent] = cleaned_tbls

        # Fallback: if entity_types is empty but entity_tables has keys
        if not target_entities and entity_tables:
            target_entities = list(entity_tables.keys())

        explicit_target_entities = (
            result.get("explicit_entity_types", target_entities) if isinstance(result, dict) else []
        )
        if not isinstance(explicit_target_entities, list):
            explicit_target_entities = target_entities
        explicit_target_entities = [e for e in explicit_target_entities if e in target_entities]

        subject = str(result.get("subject", "")).strip() if isinstance(result, dict) else ""
        scope_type = (
            str(result.get("scope_type", "none")).strip().lower()
            if isinstance(result, dict)
            else "none"
        )
        merge_scope = {
            "active": bool(subject) and scope_type not in ("", "none"),
            "subject": subject,
            "scope_type": scope_type or "none",
        }

        table_selection_notes = (
            str(result.get("notes", "")).strip() if isinstance(result, dict) else ""
        )

        logger.debug("Extracted target_entities: %s", target_entities)
        logger.debug("Extracted explicit_target_entities: %s", explicit_target_entities)
        logger.debug("Extracted entity_tables: %s", entity_tables)
        logger.info(
            "Parsed request: entities = %s, explicit = %s, scope = %s, tables = %d entities mapped",
            target_entities,
            explicit_target_entities,
            f"{scope_type}:{subject!r}" if merge_scope["active"] else "none",
            len(entity_tables),
        )

        return {
            "target_entities": target_entities,
            "explicit_target_entities": explicit_target_entities,
            "merge_scope": merge_scope,
            "entity_tables": entity_tables,
            "table_selection_notes": table_selection_notes,
            "interrupt_data": None,
        }

    except Exception as exc:
        logger.error("Failed to parse request: %s", exc, exc_info=True)
        logger.error("Exception type: %s, exception class: %s", type(exc).__name__, exc.__class__)
        return {
            "target_entities": [],
            "explicit_target_entities": [],
            "merge_scope": {"active": False, "subject": "", "scope_type": "none"},
            "entity_tables": {},
            "table_selection_notes": "",
            "errors": [f"Request parsing failed: {exc}"],
            "interrupt_data": None,
        }
    finally:
        logger.debug("EXIT parse_request")
