---
type: mcp tool
title: unify_entity_group Tool
description: MCP tool that merges matched record clusters into unified records, applying a conflict resolution policy (priority-based → completeness-based → recency-based) and producing provenance metadata.
tags: [mcp, tool, unify, conflict_resolution]
---

# unify_entity_group Tool

## Purpose

Takes a cluster of matching records (produced by `resolve_entities`) and merges them into a single unified record. Handles null complementation (fill gaps from any member), conflict detection (different values for the same field), and conflict resolution using a configurable policy.

## Handler

**File:** `src/mcp_server/server.py:177-198` (tool definition), `src/mcp_server/tools/unify_entity_group.py:unify_entity_group_handler` (handler), `src/mcp_server/tools/unify_entity_group.py:unify_entity_group_impl` (shared impl)

## Tool Definition

```python
Tool(
    name="unify_entity_group",
    description="Merge attributes within a cluster: complement nulls, flag conflicts, produce unified record.",
    input_schema={"type": "object", "properties": {
        "entity_type": "string",
        "records": [...],              // Cluster members
        "conflict_policy": {...},      // Per-field: priority|completeness|recency
        "mapping": {...},              // Field → destination column map
    }},
)
```

## Implementation

`unify_entity_group_impl` (at `src/mcp_server/tools/unify_entity_group.py`) performs:

1. **Null complementation** — for each field in the unified schema, if no record provides a value, try the next member until a non-null value is found
2. **Conflict detection** — if two or more records provide different non-null values for the same field, it's a conflict
3. **Conflict resolution** — per-field policy from `conflict_policy`:
   - **priority** — prefer a specific origin (e.g., "always use PostgreSQL for email")
   - **completeness** — prefer the record with more non-null fields overall
   - **recency** — prefer the most recently updated record (uses watermark/update timestamp)
4. **Provenance** — the unified record includes metadata about which source each field came from and whether any conflicts were resolved

## State Integration

The orchestrator node (`unify_entity_groups` in `graph.py`) calls this per cluster and stores the result in `MergeState.unified_entities` with `operator.or_` semantics (dict merge). Conflict statistics are accumulated in `MergeState.resolution_stats`.

## Cross-References

| Concern | Page |
|---------|------|
| Orchestrator node | [Unify Entities Node](/openwiki/orchestrator/nodes/unify_entities.md) |
| Conflict policy | [Saved Profiles](/openwiki/orchestrator/profiles.md) |
| Spool storage | [DuckDB Spool](/openwiki/orchestrator/spool.md) |
