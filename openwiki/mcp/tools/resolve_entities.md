---
type: mcp tool
title: resolve_entities Tool
description: MCP tool that clusters matching records across origins using deterministic blocking, heuristic scoring (Jaccard/difflib), LLM scoring for borderline cases, and Union-Find clustering. Supports cross-run caching via DuckDB-backed resolve cache.
tags: [mcp, tool, resolve_entities, clustering, matching]
---

# resolve_entities Tool

## Purpose

Clusters records that represent the same real-world entity across multiple origin databases. Uses a four-phase pipeline:

1. **Deterministic blocking** — exact match on configured blocking keys (e.g., social_security_number, email)
2. **Heuristic scoring** — Jaccard similarity on set-based fields, difflib token_sort_ratio on text fields
3. **LLM scoring** — for pairs with heuristic score in the borderline band (0.70–0.95), asks the LLM to decide
4. **Union-Find clustering** — merges pairs above the merge threshold into connected components

## Handler

**File:** `src/mcp_server/server.py:154-176` (tool definition), `src/mcp_server/tools/resolve_entities.py:resolve_entities_impl` (handler and shared implementation)

## Tool Definition

```python
Tool(
    name="resolve_entities",
    description="Cluster matching records using deterministic blocking + LLM scoring; assigns UUIDs per cluster.",
    input_schema={"type": "object", "properties": {
        "entity_type": "string",
        "records": [...],              // List of record dicts
        "matching_rules": {...},       // blocking_keys, unique_id_fields, strictness
        "known_cluster_ids": {...},    // From previous runs (for incremental)
        "subject": "string",           // Optional: scoped merge subject
        "blocking_keys": ["string"],   // Override blocking keys
        "llm_requested": true,         // Whether to use LLM for borderline
    }},
)
```

## Implementation

`resolve_entities_impl` (at `src/mcp_server/tools/resolve_entities.py`, ~99KB — the largest single file) implements:

### Phase 1: Blocking
Pairs records by exact match on blocking keys. Supports multiple blocking strategies:
- **Exact match** — same value on blocking columns
- **Phonetic** — Soundex/Metaphone matching on name fields
- **Sorted-neighborhood** — sort by key, compare adjacent windows

### Phase 2: Heuristic Scoring
For each candidate pair, computes a composite similarity score:
- **Jaccard similarity** on set-valued fields (e.g., list of tags, address components)
- **difflib.token_sort_ratio** on text fields, adjusted by term frequency (Fellegi–Sunter principle: a match on a rare value counts more than a match on a common value)

### Phase 3: LLM Scoring
Pairs with heuristic score in [0.70, 0.95) are sent to the LLM with the `entity_resolution` prompt template for a human-readable justification.

### Phase 4: Union-Find Clustering
Pairs above the merge threshold are merged into connected components using a Union-Find data structure. Includes:
- **Conflict repair** — handles transitive merge conflicts
- **Cohesion cut** — prevents one weak chain from merging an entire connected component

### Cross-Run Cache
Before computing, checks `logs/spool/_resolve_cache.duckdb` for a matching `content_hash()`. On cache hit, returns the cached result with `apply_known_cluster_ids()` re-derived for the current run.

## Parallelism

When invoked from the orchestrator node, the tool runs in a `ProcessPoolExecutor` (bounded to 4 workers) — one process per entity type, each holding a full copy of that entity's records.

## Cross-References

| Concern | Page |
|---------|------|
| Orchestrator node | [Resolve Entities Node](/openwiki/orchestrator/nodes/resolve_entities.md) |
| Fellegi-Sunter weights | [Fellegi-Sunter](/openwiki/mcp/tools/fellegi_sunter.md) |
| Resolve cache | [Resolve Cache](/openwiki/orchestrator/utils/resolve_cache.md) |
| Entity aliases | [Entity Aliases](/openwiki/orchestrator/utils/entity_aliases.md) |
