"""User-supplied rework guidance for re-run nodes.

When a reviewer sends a HITL gate back with "retry", their written reason is
stored in ``state["retry_hints"]`` keyed by the node that produced the rejected
output (see ``GATE_PRODUCER`` in ``src/orchestrator/graph.py``). That node reads
it here and appends it to its LLM prompt, so the second attempt is actually
informed by the objection instead of repeating the first.
"""

from __future__ import annotations

from typing import Any

_HEADER = (
    "\n\n--- REVIEWER FEEDBACK ON YOUR PREVIOUS ANSWER ---\n"
    "A human reviewed your previous output for this exact task and rejected "
    "it for the reason below. Produce a different answer that addresses it "
    "directly. Do not repeat the previous answer.\n\nReason: "
)


def retry_hint(state: dict[str, Any], node: str) -> str:
    """The reviewer's reason for re-running ``node``, or '' if there is none."""
    hints = state.get("retry_hints") or {}
    return str(hints.get(node) or "").strip()


def apply_retry_hint(prompt: str, state: dict[str, Any], node: str) -> str:
    """Append the reviewer's rework guidance to ``prompt`` when one exists."""
    reason = retry_hint(state, node)
    if not reason:
        return prompt
    return f"{prompt}{_HEADER}{reason}\n"


def with_retry_hint(
    llm_config: dict[str, Any] | None, state: dict[str, Any], node: str
) -> dict[str, Any] | None:
    """Carry the hint into a tool that only receives ``llm_config``.

    ``resolve_entities`` and ``map_fields`` build their prompts down in the
    tools layer, which never sees graph state — so the hint rides along on the
    config dict they already take. Returns the config unchanged when there is
    no hint, and never mutates the caller's dict.
    """
    reason = retry_hint(state, node)
    if not reason or llm_config is None:
        return llm_config
    return {**llm_config, "retry_hint": reason}
