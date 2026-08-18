---
type: utility
title: Resolve Cache
description: Cross-run resolution result cache using DuckDB persistence. Prevents re-computing matching decisions after a downstream failure by caching results keyed on a deterministic content hash.
tags: [cache, resolve, caching, duckdb]
---

# Resolve Cache

## Purpose

Prevents `resolve_entities` from recomputing matching decisions after a downstream failure (e.g., a `load_data` bug fix). When the same entity data is extracted again, the cache returns the previous clustering result immediately, saving significant computation time.

## Implementation

**File:** `src/mcp_server/tools/resolve_cache.py` (7,435 bytes)

**Location:** Despite being conceptually part of entity resolution, the module lives at `src/mcp_server/tools/resolve_cache.py` — NOT under `src/orchestrator/utils/`.

## Constants

| Name | Value | Purpose |
|------|-------|---------|
| `_SPOOL_DIR` | `Path("logs") / "spool"` | Directory for cache DuckDB file |
| `CACHE_PATH` | `_SPOOL_DIR / "_resolve_cache.duckdb"` | Persistent cache file |
| `_CACHE_FORMAT_VERSION` | `1` | Schema version for future migrations |

## Key Functions

### `content_hash()` (lines 64–90)

Deterministic SHA-256 hash of everything that can change the matching outcome. **Order-independent** over records (sorted by stable `_record_id`). The hash payload includes:
- `cache_format_version` — format version for migration detection
- `entity_type` — the entity type being resolved
- `records` — sorted by `_record_id` (`"{origin_id}:{origin_table}:{origin_id_row}"`)
- `matching_rules` — blocking keys, unique ID fields, strictness
- `subject` — optional scoped merge subject
- `blocking_keys` — explicit blocking key override
- `llm_requested` — whether LLM scoring was enabled

**Key design**: `known_cluster_ids` is deliberately **EXCLUDED** from the hash. It's a cheap, pure function of `(entity_type, member_record_ids, known_cluster_ids)`. Including it would make the cache miss every time as destination rows accumulate.

### DuckDB Persistence (lines 93–106)

```sql
CREATE TABLE IF NOT EXISTS resolve_cache (
    content_hash VARCHAR PRIMARY KEY,
    entity_type VARCHAR,
    created_at TIMESTAMP DEFAULT current_timestamp,
    result_json JSON
)
```

### `get()` (lines 109–134) — Reader

- Best-effort: returns `None` on **any** failure (corrupt file, missing duckdb, concurrent lock)
- `MERGE_ENGINE_DISABLE_RESOLVE_CACHE` env var forces every read to miss (used by diagnostics scripts)
- Never lets a cache read break a merge run

### `put()` (lines 137–148) — Writer

- Best-effort: a write failure is logged, not raised
- Uses DELETE + INSERT (not UPSERT) for idempotency

### `apply_known_cluster_ids()` (lines 151–175)

Re-derives every cluster's `cluster_id` for the **current** run on a cache hit:

```python
def apply_known_cluster_ids(
    result: dict[str, Any],
    entity_type: str,
    known_cluster_ids: dict[str, str] | None,
) -> dict[str, Any]:
```

Reusing a cached `cluster_id` verbatim would carry a stale destination row ID — corrupting the current run's load. This function calls `_assign_cluster_id()` to map cached clusters to the current run's ID space.

## Key Design Principle

Every read/write degrades to a no-op on failure — a cache miss means the entity gets recomputed (correct, only slower). This must **never** be the thing that breaks a merge run.

## Cross-References

| Concern | Page |
|---------|------|
| resolve_entities tool | [resolve_entities Tool](/openwiki/mcp/tools/resolve_entities.md) |
| resolve_entities node | [Resolve Entities Node](/openwiki/orchestrator/nodes/resolve_entities.md) |
| DuckDB spool | [DuckDB Spool](/openwiki/orchestrator/spool.md) |
