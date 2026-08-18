---
type: orchestrator node
title: Human-in-the-Loop Interrupt Nodes
description: All five HITL interrupt nodes (human_review_deps, human_review_scope, human_review_mappings, human_review_matches, human_final_approval) plus the error handling gate (handle_error). These use LangGraph's interrupt_before mechanism to pause the graph and collect user decisions.
tags: [node, hitl, human-in-the-loop, interrupt]
---

# Human-in-the-Loop Interrupt Nodes

## Purpose

These nodes pause the LangGraph and collect user decisions. The graph uses LangGraph's `interrupt_before` mechanism — the node body is never executed when paused. On resume, the user's feedback is merged into state before the graph continues.

## File

`src/orchestrator/nodes/human_nodes.py` (~23KB)

## All HITL Nodes

| Node | Interrupts Before | Reviews |
|------|-------------------|---------|
| `human_review_deps` | Dependency analysis | Entity-table mapping, dependency tree, column dependencies |
| `human_review_scope` | Scope planning | Scoped merge subject, per-entity join plan |
| `human_review_mappings` | Field mapping | Unified field → destination column mapping |
| `human_review_matches` | Entity resolution | Entity clusters (keep/split/reject decisions) |
| `human_final_approval` | Load data | Final reconciliation report, approval to write |

## Decision Vocabulary

Each gate accepts four decisions:

| Decision | Behavior |
|----------|----------|
| **approve** | Continue to the next node |
| **edit** | Continue (edits are already merged into state before resume) |
| **retry** | Go back to the producing node with user's reason in prompt |
| **reject** | End the run. Nothing written. |
| **abort** | End the run (synonym for reject) |

Legacy shape `{"approved": bool}` is still supported.

## Interrupt Data Schemas

### human_review_deps

```python
{
    "node": "human_review_deps",
    "title": "Review Dependency Tree",
    "data": {
        "dependency_tree": {...},
        "extraction_order": [...],
        "column_dependencies": [...],
        "entity_tables": {...},
        "origin_schemas": {"origin_1": {"table_1": ["col_1", ...]}},
        "table_selection_notes": "...",
        "warning": "...",  # Optional
    },
}
```

### human_review_scope

```python
{
    "node": "human_review_scope",
    "title": "Confirm Merge Scope",
    "data": {
        "subject": "Nova",
        "scope_type": "company",
        "candidate_rows": [...],
        "scope_plan": {...},
    },
}
```

### human_review_matches

```python
{
    "node": "human_review_matches",
    "title": "Review Entity Clusters",
    "data": {
        "clusters": [...],
        "entity_type": "...",
        "auto_merged": N,
        "human_reviewed": N,
        "uncompared_pairs": N,
    },
}
```

Clusters carry `required_human_review` flag for borderline cases.

### human_review_mappings

```python
{
    "node": "human_review_mappings",
    "title": "Review Field Mappings",
    "data": {
        "mappings": [...],
        "unmapped_fields": [...],
        "pending_schema_changes": [...],
    },
}
```

### human_final_approval

```python
{
    "node": "human_final_approval",
    "title": "Final Approval",
    "data": {
        "reconciliation_report": {...},
        "load_approval": True / False,
    },
}
```

## handle_error Gate

The error gate (`handle_error`) is the fifth item in `HITL_NODES` but not in `REVIEW_GATES` — it's a gate the pipeline hopes never to reach. It accepts:

| Decision | Behavior |
|----------|----------|
| **retry** | Re-run the failed node (with retry hint if available) |
| **skip** | Continue from the failure as if the node produced nothing |
| **abort** | End the run |

## Cluster Edit Application

When the user makes decisions at the match gate (`human_review_matches`), the node applies cluster edits via `apply_cluster_decisions()` from `cluster_edits.py`, which operates on the spool data directly.

## Test Coverage

- `tests/unit/test_human_nodes.py` — interrupt data schemas, decision routing (19KB)
- `tests/integration/test_human_in_the_loop.py` — end-to-end HITL flows

## Cross-References

| Concern | Page |
|---------|------|
| Graph wiring | [State Graph](/openwiki/orchestrator/graph.md) |
| Workflow resume | [Workflow Manager](/openwiki/orchestrator/workflow_manager.md) |
| Cluster edits | [Cluster Edits](/openwiki/orchestrator/utils/cluster_edits.md) |
| Error handling | [Error Handling](/openwiki/orchestrator/utils/error_handling.md) |
