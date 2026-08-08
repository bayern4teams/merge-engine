---
type: orchestrator node
title: Unify Entity Groups Node
description: Merges matched entity clusters into unified records using conflict resolution policies (priority → completeness → recency). Reads clusters from the DuckDB spool and writes unified records back.
tags: [node, unify, conflict_resolution, survivorship]
---

# Unify Entity Groups Node

## Purpose

Merges matched entity clusters into unified records. For each cluster (a group of records determined to represent the same real-world entity), the node:

1. **Complements nulls** — fills missing fields from any cluster member
2. **Flags conflicts** — identifies fields where cluster members disagree
3. **Resolves conflicts** — applies the conflict policy (priority → completeness → recency)
4. **Produces unified records** — one record per cluster with resolved values

## File

`src/orchestrator/nodes/unify_entities.py` (~6.6KB)

## Conflict Resolution Chain

The conflict policy is a priority chain:

| Priority | Policy | Behavior |
|----------|--------|----------|
| 1st | **Priority** | Per-field source priority (e.g., "PostgreSQL wins over MongoDB") |
| 2nd | **Completeness** | Use the value from the record with the most non-null fields |
| 3rd | **Recency** | Use the value from the most recently updated record |

The node applies policies in order: if a priority rule exists for the field, use it; if not but a completeness rule exists, use it; if neither, fall back to recency.

## Implementation

### Per-Entity Clusters

Reads clusters from the DuckDB spool via `clusters_for()`. Each entity type's clusters are processed independently.

### Unification Logic

For each cluster:

```python
unified = {}
for field in all_fields:
    values = [record[field] for record in cluster if record.get(field)]
    if not values:
        continue
    if len(values) == 1:
        unified[field] = values[0]  # No conflict, single value
    else:
        unified[field] = resolve_conflict(values, conflict_policy, field)
```

### Error Handling

Seeds with existing `errors` (not overwrites) to avoid double-counting via `operator.add`.

## State

Produces:

```python
{
    "unified_entities": {
        "entity_type": [
            {
                "record_id": "...",
                "cluster_id": "...",
                "source_records": ["origin_1:table:1", ...],
                "fields": {...},
                "conflicts": [
                    {"field": "email", "sources": ["origin_1", "origin_2"]},
                ],
            }
        ]
    },
}
```

## Retry Policy

Carries `NETWORK_RETRY` — reads from the spool (DuckDB), though the computation itself is pure Python.

## Test Coverage

- `tests/unit/test_unify_entities_node.py` — conflict resolution correctness, tiebreaks
- `tests/unit/test_conflict_tiebreaks.py` — priority/completeness/recency ordering

## Cross-References

| Concern | Page |
|---------|------|
| Graph wiring | [State Graph](/openwiki/orchestrator/graph.md) |
| Conflict policy | [Profiles](/openwiki/orchestrator/profiles.md) |
| Spool storage | [DuckDB Spool](/openwiki/orchestrator/spool.md) |
