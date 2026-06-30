"""
parse_request node: Extracts target entity types from user's natural language prompt.

Uses the LLM with the request_parsing prompt template to identify which
entity types the user wants to import.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.llm import LLMClient, PROMPT_TEMPLATES

logger = logging.getLogger("merge-engine.orchestrator.parse_request")


def _safe_format(template: str, **kwargs: Any) -> str:
    """Format a template string, handling JSON examples with curly braces.
    
    The prompt templates contain JSON examples with curly braces that would
    be misinterpreted by str.format(). We escape all braces except the known
    placeholder, then do a simple string replacement.
    """
    # Escape all curly braces first
    escaped = template.replace("{", "{{").replace("}}", "}}}")
    # Un-escape the known placeholder
    escaped = escaped.replace("{{{{user_request}}}}", "{user_request}")
    try:
        return escaped.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        # If formatting fails, do a simple string replacement instead
        return template.replace("{user_request}", str(kwargs.get("user_request", "")))


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
    prompt_template = PROMPT_TEMPLATES.get("request_parsing", "")

    if not prompt_template:
        logger.warning("request_parsing prompt not found, using fallback")
        prompt_template = (
            "Extract entity types from this request as a JSON array: {user_request}"
        )

    user_prompt = _safe_format(prompt_template, user_request=user_request)
    logger.debug("Prompt template loaded (%d chars, truncated to %d)", len(user_prompt), min(len(user_prompt), 500))
    logger.debug("User prompt (first 500 chars): %s", user_prompt[:500])

    try:
        logger.debug("Calling LLMClient.call_json()")
        result = await client.call_json(
            system_prompt="You extract entity types from user requests. Return only JSON.",
            user_prompt=user_prompt,
        )
        logger.debug("LLM response received: type=%s, repr=%.300s", type(result).__name__, repr(result))
        # Handle both list and dict response formats
        if isinstance(result, list):
            target_entities = result
        elif isinstance(result, dict):
            # LLM may return {"entities": [...]} or similar
            target_entities = result.get("entities", result.get("target_entities", []))
        else:
            target_entities = []

        logger.debug("Extracted target_entities: %s", target_entities)
        logger.info("Parsed request: entities = %s", target_entities)
        # Return ALL required state fields to prevent state loss with
        # StateGraph(dict) which uses an overwrite reducer.
        return {
            "target_entities": target_entities,
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
