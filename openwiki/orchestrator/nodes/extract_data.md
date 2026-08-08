---
type: orchestrator node
title: Extract Data Node
description: Dependency-ordered data extraction with keyset pagination, scratch table filters for ClickHouse, and chunked IN filters for large scope plans. Stores results in the DuckDB spool.
tags: [node, extract_data, pagination, spool]
---

# Extract Data Node

## Purpose

Extracts data for all entities in dependency order. Handles batch pagination (keyset seek pagination for PostgreSQL, MongoDB `_id`-based pagination), watermark pushdown, scratch table filters for ClickHouse, and chunked IN filters for large scope plans.

## File

`src/orchestrator/nodes/extract_data.py` (~41KB — one of the largest node files)

## Key Mechanisms

### Dependency-Ordered Extraction

Uses `find_execution_groups()` from `dependency_sort.py` to determine extraction order. Entities with no dependencies are extracted first; dependent entities wait until their dependencies are complete.

### Keyset (Seek) Pagination

Replaces OFFSET/LIMIT pagination. Uses `choose_keyset()` to pick the best ordering column(s), then paginates with `WHERE (col1, col2) > (val1, val2)`. Deterministic — no duplicate rows even under concurrent writes.

### Scratch Table Filters (ClickHouse)

For large IN filters (tens of thousands of linked IDs), creates a temporary scratch table in ClickHouse:

```python
async def _scratch_filter(origin_config, column, values):
    # Create scratch table, return in_scratch filter
    # Caller MUST invoke cleanup() in finally block
```

### Chunked IN Filters

Splits large IN filters (>500 values) into multiple independent queries to avoid ClickHouse's HTTP field-size limit. Used for scoped merge linked-entity filters.

### Result Storage

Results are stored in the DuckDB spool, not in `MergeState.extracted_data`. The state carries only a handle:

```python
{"customers_rows": {"table": "customers_rows", "row_count": 15234}}
```

## State

Produces:

```python
{
    "extracted_data": {"entity_type": {"table": "name", "row_count": N}},
    "extraction_watermarks": {
        "{origin}:{table}": {"column": "id", "kind": "numeric", "last_value": 12345}
    },
}
```

## Retry Policy

Carries `NETWORK_RETRY` — extraction touches databases.

## Test Coverage

- `tests/unit/test_extract_data_node.py` — most comprehensive node test (45KB): keyset pagination, scratch tables, chunked filters, watermark pushdown

## Cross-References

| Concern | Page |
|---------|------|
| Spool storage | [DuckDB Spool](/openwiki/orchestrator/spool.md) |
| Keyset pagination | [Keyset Utilities](/openwiki/connectors/shared_utilities.md) |
| Scratch tables | [ClickHouse Connector](/openwiki/connectors/clickhouse.md) |
| MCP tool | [extract_data Tool](/openwiki/mcp/tools/extract_data.md) |
