---
type: orchestrator overview
title: Orchestrator Overview
description: LangGraph StateGraph-based pipeline orchestrator for merge-engine, comprising 17 nodes (12 functional + 5 HITL gates) wired with retry policies, conditional edges, and cross-cutting timing instrumentation.
tags: [orchestrator, langgraph, workflow, graph]
---

# Orchestrator Overview

## Purpose

The orchestrator builds and executes a LangGraph `StateGraph` that represents the entire merge pipeline as a directed graph. Each node is a plain async function that transforms a typed `MergeState` dict. The graph is compiled once and reused across all workflow runs.

## Node Count

17 nodes total:
- **12 functional nodes**: preflight, parse_request, discover_schemas, analyze_dependencies, map_fields, plan_scope, extract_data, resolve_entities, unify_entity_groups, load_data_node, update_extraction_state_node, handle_error
- **5 HITL gates**: human_review_deps, human_review_scope, human_review_mappings, human_review_matches, human_final_approval

## Cross-Cutting Concerns

### Timing Instrumentation (`_timed()`)

Every node is wrapped by `_timed(name, fn)` (graph.py:311-331) which measures wall-clock seconds via `time.monotonic()` and writes to `MergeState.timings` with `operator.or_` semantics (dict merge):

```python
def _timed(name: str, fn: NodeFn) -> NodeFn:
    @functools.wraps(fn)
    async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic()
        result = await fn(state)
        elapsed = time.monotonic() - start
        result = dict(result) if result else {}
        result["timings"] = {name: elapsed}
        return result

    return wrapper
```

Timings are compared against `docs/verification/BUDGETS.yaml` by `drive_app.py --assert-budgets`.

### Retry Policy (`NETWORK_RETRY`)

```python
NETWORK_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    max_interval=30.0,
    jitter=True,
    retry_on=_is_transient,
)
```

Applied to all fallible nodes that touch databases or the LLM API:
- preflight, parse_request, discover_schemas, analyze_dependencies
- extract_data, resolve_entities, unify_entity_groups
- map_fields, load_data_node, update_extraction_state_node

**Not applied to**: HITL gates (retrying human review cannot help), handle_error.

### Error Handling

Each fallible node has a conditional edge to `handle_error` via `_fallible_router()`. The error gate routes based on the error's `disposition`:
- `disposition: "fail"` → abort (unrecoverable: unreachable DB, refused auth)
- `disposition: "escalate"` → human review (recoverable but needs judgment: data quality)

### Retry Hints

When a user sends a HITL gate back with "retry", their written reason is stored in `state["retry_hints"]` keyed by the producer node. The node reads it and appends it to its LLM prompt via `with_retry_hint()`.

See [Retry Hints](/openwiki/orchestrator/utils/retry_hints.md).

## Compilation

The graph is compiled by `build_graph()` (graph.py) which:
1. Adds all nodes with retry policies
2. Adds all edges via `NODE_SUCCESSOR` map
3. Adds conditional edges for gates via `_gate_router()`
4. Adds error handling edges via `_fallible_router()`
5. Returns `builder.compile(checkpointer)` for the WorkflowManager

## Node Execution Order

The linear path through the graph (ignoring conditional edges):

```
preflight → parse_request → discover_schemas → analyze_dependencies
    → human_review_deps → map_fields → plan_scope → extract_data
    → resolve_entities → unify_entity_groups → human_review_matches
    → human_review_scope → human_review_mappings → human_final_approval
    → load_data_node → update_extraction_state_node → END
```

## Test Coverage

- **Unit tests**: `tests/unit/test_graph_timing.py` (timing instrumentation), `tests/unit/test_human_nodes.py` (HITL logic)
- **Integration tests**: `tests/integration/test_graph_workflow.py` (full graph execution), `tests/integration/test_workflow_manager.py` (lifecycle)

## Cross-References

| Concern | Page |
|---------|------|
| State graph wiring | [State Graph](/openwiki/orchestrator/graph.md) |
| Workflow lifecycle | [Workflow Manager](/openwiki/orchestrator/workflow_manager.md) |
<!-- openwiki: broken internal link [/openwiki/data/state_schema.md] file "/openwiki/data/state_schema.md" does not exist. Fix the href or restore the target, then delete this comment. -->
| MergeState schema | [Merge State](/openwiki/data/state_schema.md) |
| Error taxonomy | [Error Handling](/openwiki/orchestrator/utils/error_handling.md) |
| Retry hints | [Retry Hints](/openwiki/orchestrator/utils/retry_hints.md) |
| DuckDB spool | [DuckDB Spool](/openwiki/orchestrator/spool.md) |
