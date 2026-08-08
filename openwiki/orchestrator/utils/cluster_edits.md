---
type: utility
title: Cluster Edits
description: Applies HITL reviewer decisions (keep/split/reject) to entity clusters stored in the DuckDB spool. Called from the match review gate.
tags: [cluster-edits, hitl, spool, decisions]
---

# Cluster Edits

## Purpose

Applies HITL reviewer decisions (keep/split/reject) to entity clusters stored in the DuckDB spool. Called from the match review gate (`human_review_matches`) when a user reviews clusters and decides to keep, split, or reject groups of records.

## File

`src/orchestrator/utils/cluster_edits.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `apply_cluster_decisions(clusters, decisions)` | Apply user's keep/split/reject decisions to clusters |
| `rebuild_cluster_ids()` | Re-derive cluster IDs after splits/merges |

## Decision Types

| Decision | Effect |
|----------|--------|
| **keep** | No change — cluster stays as-is |
| **split** | Records in the cluster are assigned to new independent clusters |
| **reject** | Records are excluded from further processing (dropped) |

## Integration

Called by `resume_workflow()` in the WorkflowManager when resuming from the `human_review_matches` gate. Reads the user's decisions from `human_feedback["clusters"]`, applies them to the spool data, and updates `entity_clusters` in the state.

## Test Coverage

- `tests/unit/test_human_nodes.py` — cluster edit application during HITL resume
