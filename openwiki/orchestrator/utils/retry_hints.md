---
type: utility
title: Retry Hints
description: Bridges HITL user feedback into LLM prompts. When a reviewer sends a gate back with "retry", their written reason is stored and appended to the producing node's LLM prompt.
tags: [retry-hints, feedback, llm, hitl]
---

# Retry Hints

## Purpose

User-supplied rework guidance for re-run nodes. When a reviewer sends a HITL gate back with "retry", their written reason is stored in `state["retry_hints"]` keyed by the node that produced the rejected output (see `GATE_PRODUCER` in `graph.py`). The node reads it and appends it to its LLM prompt.

## File

`src/orchestrator/utils/retry_hints.py` (~34 lines)

## Header Template

```python
_HEADER = (
    "\n\n--- REVIEWER FEEDBACK ON YOUR PREVIOUS ANSWER ---\n"
    "A human reviewed your previous output for this exact task and rejected "
    "it for the reason below. Produce a different answer that addresses it "
    "directly. Do not repeat the previous answer.\n\nReason: "
)
```

## Three Functions

| Function | Purpose |
|----------|---------|
| `retry_hint(state, node)` | Extract the reviewer's reason for re-running `node`, or `''` if none |
| `apply_retry_hint(prompt, state, node)` | Append the reviewer's feedback to a prompt string |
| `with_retry_hint(llm_config, state, node)` | Carry the hint into a tool that only receives `llm_config` |

### retry_hint(state, node) → str

Extracts `state["retry_hints"].get(node, "")`. Returns `''` if the node has no stored feedback.

### apply_retry_hint(prompt, state, node) → str

Appends the reviewer's feedback to a prompt string:

```python
def apply_retry_hint(prompt, state, node):
    reason = retry_hint(state, node)
    if reason:
        return prompt + _HEADER + reason
    return prompt
```

### with_retry_hint(llm_config, state, node) → dict | None

Carries the hint into a tool that only receives `llm_config` (used by `resolve_entities` and `map_fields` which build prompts in the tools layer, away from graph state):

```python
def with_retry_hint(llm_config, state, node):
    reason = retry_hint(state, node)
    if reason and llm_config is not None:
        return {**llm_config, "retry_hint": reason}
    return llm_config
```

Never mutates the caller's dict.

## State Flow

1. User clicks "retry" at gate `human_review_matches` with reason "cluster A and B should be separate"
2. `resume_workflow` stores `state["retry_hints"]["resolve_entities"] = "cluster A and B should be separate"`
3. On resume, `resolve_entities` reads via `with_retry_hint(llm_config, state, "resolve_entities")`
4. The LLM prompt receives the feedback header and reason
5. The LLM produces different cluster IDs

## State Key

```python
class MergeState(TypedDict):
    retry_hints: dict[str, str]  # {node_name: "reason for retry"}
```

## Test Coverage

- Implicitly tested through integration tests that exercise the retry decision at each gate
- `tests/integration/test_human_in_the_loop.py` — end-to-end HITL flows including retry

## Cross-References

| Concern | Page |
|---------|------|
| Error taxonomy | [Error Handling](/openwiki/orchestrator/utils/error_handling.md) |
| Gate routing | [State Graph](/openwiki/orchestrator/graph.md) |
| Workflow resume | [Workflow Manager](/openwiki/orchestrator/workflow_manager.md) |
