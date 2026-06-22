"""LLM integration modules for merge-engine.

Provides prompt templates and an async LLM client for
entity resolution, dependency analysis, and field mapping.
"""

from __future__ import annotations

from pathlib import Path

from src.llm.client import LLMClient, create_llm_client

# ── Prompt template paths ───────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _read_prompt(filename: str) -> str:
    """Read a prompt template file and return its contents."""
    path = _PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


PROMPT_TEMPLATES: dict[str, str] = {
    "request_parsing": _read_prompt("request_parsing.txt"),
    "dependency_analysis": _read_prompt("dependency_analysis.txt"),
    "entity_resolution": _read_prompt("entity_resolution.txt"),
    "field_mapping": _read_prompt("field_mapping.txt"),
}


# Convenience access to individual prompts
request_parsing_prompt = PROMPT_TEMPLATES["request_parsing"]
dependency_analysis_prompt = PROMPT_TEMPLATES["dependency_analysis"]
entity_resolution_prompt = PROMPT_TEMPLATES["entity_resolution"]
field_mapping_prompt = PROMPT_TEMPLATES["field_mapping"]

__all__ = [
    "LLMClient",
    "create_llm_client",
    "PROMPT_TEMPLATES",
    "request_parsing_prompt",
    "dependency_analysis_prompt",
    "entity_resolution_prompt",
    "field_mapping_prompt",
]
