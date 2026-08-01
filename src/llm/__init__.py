"""LLM integration modules for merge-engine.

Provides prompt templates and an async LLM client for
entity resolution, dependency analysis, and field mapping.

Each stage's prompt is a (system, human) pair loaded from
``<name>.system.txt`` / ``<name>.human.txt`` in ``prompts/``: the system
message carries the role, rules, and output contract; the human message
carries few-shot examples plus the live input template.
"""

from __future__ import annotations

from pathlib import Path

from src.llm.client import LLMClient, create_llm_client

# ── Prompt template paths ───────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_STAGE_NAMES = (
    "request_parsing",
    "dependency_analysis",
    "entity_table_selection",
    "scope_planning",
    "entity_resolution",
    "field_mapping",
)


def _read_prompt(filename: str) -> str:
    """Read a prompt template file and return its contents."""
    path = _PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


PROMPT_TEMPLATES: dict[str, dict[str, str]] = {
    name: {
        "system": _read_prompt(f"{name}.system.txt"),
        "human": _read_prompt(f"{name}.human.txt"),
    }
    for name in _STAGE_NAMES
}

__all__ = [
    "LLMClient",
    "create_llm_client",
    "PROMPT_TEMPLATES",
]
