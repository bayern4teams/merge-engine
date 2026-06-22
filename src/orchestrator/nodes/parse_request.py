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
    user_request: str = state["user_request"]
    llm_config: dict[str, Any] = state["llm_config"]

    client = LLMClient(llm_config)
    prompt_template = PROMPT_TEMPLATES.get("request_parsing", "")

    if not prompt_template:
        logger.warning("request_parsing prompt not found, using fallback")
        prompt_template = (
            "Extract entity types from this request as a JSON array: {user_request}"
        )

    user_prompt = prompt_template.format(user_request=user_request)

    try:
        result = await client.call_json(
            system_prompt="You extract entity types from user requests. Return only JSON.",
            user_prompt=user_prompt,
        )
        # Handle both list and dict response formats
        if isinstance(result, list):
            target_entities = result
        elif isinstance(result, dict):
            # LLM may return {"entities": [...]} or similar
            target_entities = result.get("entities", result.get("target_entities", []))
        else:
            target_entities = []

        logger.info("Parsed request: entities = %s", target_entities)
        return {"target_entities": target_entities}

    except Exception as exc:
        logger.error("Failed to parse request: %s", exc)
        # Fallback: return empty list to let downstream nodes handle gracefully
        return {"target_entities": [], "errors": [f"Request parsing failed: {exc}"]}