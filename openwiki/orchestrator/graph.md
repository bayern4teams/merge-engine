---
type: graph definition
title: State Graph Definition
description: Complete LangGraph StateGraph definition for merge-engine, including all 17 nodes, edges, conditional edges, retry policies, gate routers, and the MergeState TypedDict schema.
tags: [graph, langgraph, state, nodes, edges]
---

# State Graph Definition

## Purpose

The state graph is built in `src/orchestrator/graph.py` and represents the complete merge pipeline as a directed graph. It wires all orchestrator nodes, conditional edges, interrupt points, and retry policies into a single compiled LangGraph workflow.

## Constants

### HITL_NODES (line 52-59)

```python
HITL_NODES = [
    "human_review_deps",
    "human_review_mappings",
    "human_review_scope",
    "human_review_matches",
    "human_final_approval",
    "handle_error",
]
```

`handle_error` is listed last because it is not a normal gate — it's a gate the pipeline hopes never to reach.

### REVIEW_GATES (line 62)

```python
REVIEW_GATES = HITL_NODES[:-1]  # All except handle_error
```

The four gates a healthy run passes through, for UI stepper purposes.

### NODE_SUCCESSOR (lines 92-102)

Maps each fallible node to its success successor:

```python
NODE_SUCCESSOR = {
    "preflight": "parse_request",
    "parse_request": "discover_schemas",
    "discover_schemas": "analyze_dependencies",
    "analyze_dependencies": "human_review_deps",
    "map_fields": "plan_scope",
    "extract_data": "resolve_entities",
    "resolve_entities": "unify_entities",
    "unify_entities": "human_final_approval",
    "load_data": "update_extraction_state",
}
```

### GATE_PRODUCER (lines 106-111)

Maps each HITL gate to the node whose output the gate reviews. Used for the "retry" decision:

```python
GATE_PRODUCER = {
    "human_review_deps": "analyze_dependencies",
    "human_review_mappings": "map_fields",
    "human_review_scope": "plan_scope",
    "human_review_matches": "resolve_entities",
}
```

### MAX_GATE_RETRIES (line 115)

A user can send the same gate back this many times (3) before the graph moves on regardless.

## Key Functions

### `_is_transient(exc)` (lines 65-72)

Retry predicate for `RetryPolicy`. Classifies exceptions via `classify_exception()` and returns True only for `TransientError`. A raw driver exception that nobody wrapped defaults to its type's heuristic.

### `_gate_router(gate, forward)` (lines 118-171)

Builds the conditional-edge router for one HITL gate. Accepts four decisions:

| Decision | Behavior |
|----------|----------|
| `approve` / `edit` | Continue to `forward` (edits already merged into state) |
| `retry` | Go back to `GATE_PRODUCER[gate]` with reason in prompt |
| `reject` / `abort` | End the run. Nothing written. |
| Unrecognized | Forward (preserves legacy behavior) |

Legacy shape `{"approved": bool}` is also supported for pre-vote decisions.

### `_fallible_router(node, forward)` (lines 173-210)

Routes based on whether `node` just recorded a failure. Checks `state["failure"]["node"]`.

### `_failure_route(state, node)` (lines 180-206)

Distinguishes two dispositions:

| Disposition | Behavior |
|-------------|----------|
| `escalate` | Human choice: retry, skip, or abandon |
| `fail` | Abort — run cannot work at all |

### `_error_gate_router` (lines 240-278)

Routes out of the error gate on retry/skip/abort decision.

### `get_initial_state()` (lines 445-485)

Builds the initial `MergeState` dict with defaults: empty `errors`, empty `handled_failures`, empty `timings`, empty `gate_retry_counts`, empty `retry_hints`.

### `build_graph()` (lines 303-476)

Compiles the full state graph:
1. Creates `StateGraph(MergeState)`
2. Wraps each node with `_timed(name, fn)`
3. Adds all nodes with `retry_policy=NETWORK_RETRY` (selective)
4. Adds edges from `NODE_SUCCESSOR`
5. Adds conditional edges for gates from `_gate_router()`
6. Adds error edges from `_fallible_router()`
7. Adds `handle_error` with conditional router
8. Compiles with checkpointer and returns

## MergeState TypedDict

<!-- openwiki: broken internal link [/openwiki/data/state_schema.md] file "/openwiki/data/state_schema.md" does not exist. Fix the href or restore the target, then delete this comment. -->
The 43-key state schema is documented in [Merge State](/openwiki/data/state_schema.md). Key merge semantics:

| Reducer | Fields | Behavior |
|---------|--------|----------|
| `operator.or_` | `extraction_watermarks`, `entity_clusters`, `resolution_stats`, `unified_entities`, `field_mappings`, `unmapped_fields`, `timings` | Dict merge — new values overwrite existing keys |
| `operator.add` | `errors`, `handled_failures` | List concatenation |
| (none) | All others | Replace — entire value is overwritten |

## Test Coverage

- `tests/unit/test_graph_timing.py` — timing instrumentation correctness
- `tests/unit/test_human_nodes.py` — gate router decision logic
