"""
parse_request node: Extracts target entity types from user's natural language prompt.

Uses the LLM with the request_parsing prompt template to identify which
entity types the user wants to import.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm import LLMClient, PROMPT_TEMPLATES

logger = logging.getLogger("merge-engine.orchestrator.parse_request")


async def parse_request(state: dict[str, Any]) -> dict[str, Any]:
    """Parse user request to extract target entity types.

    Parameters
    ----------
    state : dict
        MergeState with ``user_request`` field populated.

    Returns
    -------
    dict
        Update dict with ``target_entities`` key.
    """
    logger.debug("ENTER parse_request")
    logger.debug("State keys: %s", list(state.keys()))
    logger.debug("user_request (truncated): %.200s", state.get("user_request", ""))
    logger.debug("llm_config keys: %s", list(state.get("llm_config", {}).keys()))
    logger.debug("origins count: %d", len(state.get("origins", [])))

    user_request: str = state["user_request"]
    llm_config: dict[str, Any] = state["llm_config"]

    client = LLMClient(llm_config)
    prompts = PROMPT_TEMPLATES.get("request_parsing", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")

    if not system_prompt or not human_template:
        logger.warning("request_parsing prompt not found, using fallback")
        system_prompt = system_prompt or "You extract entity types from user requests. Return only JSON."
        human_template = human_template or "Extract entity types from this request as JSON: {user_request}"

    user_prompt = human_template.replace("{user_request}", user_request)
    logger.debug("Prompt loaded (%d chars, truncated to %d)", len(user_prompt), min(len(user_prompt), 500))
    logger.debug("User prompt (first 500 chars): %s", user_prompt[:500])

    try:
        logger.debug("Calling LLMClient.call_json()")
        result = await client.call_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        logger.debug("LLM response received: type=%s, repr=%.300s", type(result).__name__, repr(result))
        target_entities = result.get("entity_types", []) if isinstance(result, dict) else []

        # Widened breadth (2026-07-30, remediation #1 in
        # merge-engine-remediation-1-to-6): distinguish types the user actually
        # asked for from types a wildcard rule merely inferred. Downstream,
        # extract_data.py uses this to decide whether a missing source table is
        # a hard failure (explicit) or a silent skip (inferred) -- the old
        # unconditional "product" wildcard used to turn every company-only
        # corpus into a hard error. Default to target_entities when the key is
        # absent (e.g. an older saved profile / a model that ignores the new
        # instruction) so behaviour degrades to "everything is explicit", which
        # is the strictest/safest fallback, not a silent widening of failures.
        explicit_target_entities = (
            result.get("explicit_entity_types", target_entities)
            if isinstance(result, dict)
            else []
        )
        if not isinstance(explicit_target_entities, list):
            explicit_target_entities = target_entities
        # explicit_entity_types must be a subset of entity_types by contract;
        # enforce it defensively in case the LLM's output violates its own contract.
        explicit_target_entities = [e for e in explicit_target_entities if e in target_entities]

        # A request about one named organisation ("all information about Nova")
        # scopes the whole merge to that subject. Everything downstream that
        # filters — scope planning, extraction, destination dedup — reads this.
        # An empty/"none" scope preserves the original whole-table behaviour.
        subject = str(result.get("subject", "")).strip() if isinstance(result, dict) else ""
        scope_type = str(result.get("scope_type", "none")).strip().lower() if isinstance(result, dict) else "none"
        merge_scope = {
            "active": bool(subject) and scope_type not in ("", "none"),
            "subject": subject,
            "scope_type": scope_type or "none",
        }

        logger.debug("Extracted target_entities: %s", target_entities)
        logger.debug("Extracted explicit_target_entities: %s", explicit_target_entities)
        logger.info(
            "Parsed request: entities = %s, explicit = %s, scope = %s",
            target_entities,
            explicit_target_entities,
            f"{scope_type}:{subject!r}" if merge_scope["active"] else "none",
        )
        # Return ALL required state fields to prevent state loss with
        # StateGraph(dict) which uses an overwrite reducer.
        return {
            "target_entities": target_entities,
            "explicit_target_entities": explicit_target_entities,
            "merge_scope": merge_scope,
            "origins": state.get("origins", []),
            "destination": state.get("destination", {}),
            "llm_config": state.get("llm_config", {}),
            "extraction_config": state.get("extraction_config", {}),
            "schemas": state.get("schemas", {}),
            "dependency_tree": state.get("dependency_tree", {}),
            "extraction_order": state.get("extraction_order", []),
            "extracted_data": state.get("extracted_data", {}),
            "entity_clusters": state.get("entity_clusters", {}),
            "unified_entities": state.get("unified_entities", {}),
            "field_mappings": state.get("field_mappings", {}),
            "pending_schema_changes": state.get("pending_schema_changes", []),
            "human_feedback": state.get("human_feedback"),
            "load_approval": state.get("load_approval"),
            "errors": state.get("errors", []),
            "retry_counts": state.get("retry_counts", {}),
            "interrupt_data": None,
        }

    except Exception as exc:
        logger.error("Failed to parse request: %s", exc, exc_info=True)
        logger.error("Exception type: %s, exception class: %s", type(exc).__name__, exc.__class__)
        # Fallback: return empty list to let downstream nodes handle gracefully
        # Return ALL required fields to prevent state loss with StateGraph(dict)
        return {
            "target_entities": [],
            "explicit_target_entities": [],
            "merge_scope": {"active": False, "subject": "", "scope_type": "none"},
            "errors": [f"Request parsing failed: {exc}"],
            "origins": state.get("origins", []),
            "destination": state.get("destination", {}),
            "llm_config": state.get("llm_config", {}),
            "extraction_config": state.get("extraction_config", {}),
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "human_feedback": state.get("human_feedback"),
            "load_approval": state.get("load_approval"),
            "retry_counts": {},
            "interrupt_data": None,
        }
    finally:
        logger.debug("EXIT parse_request")
