---
type: mcp tool
title: extract_data Tool
description: MCP tool that extracts rows from an origin database using batch pagination (OFFSET/LIMIT for most sources, keyset for PostgreSQL), with optional watermark pushdown and filter application.
tags: [mcp, tool, extract_data, pagination]
---

# extract_data Tool

## Purpose

Extracts rows from an origin database in batches, supporting both OFFSET/LIMIT and MongoDB `_id`-based skip pagination. Applied filters (including `_imported` flag filtering) reduce extraction to relevant rows. Watermark column pushdown avoids re-extracting already-processed rows.

## Handler

**File:** `src/mcp_server/server.py:112-153` (tool definition), `src/mcp_server/tools/extract_data.py:extract_data_handler` (handler), `src/mcp_server/tools/extract_data.py:extract_data_impl` (shared implementation)

## Tool Definition

```python
Tool(
    name="extract_data",
    description="Extract rows filtered by _imported flag or custom filter; supports OFFSET/LIMIT and MongoDB _id-based skip pagination.",
    input_schema={"type": "object", "properties": {
        "connection": {...},
        "type": "postgres|clickhouse|mongodb",
        "table": "string",
        "filter": {...},              // Optional: column filter
        "offset": 0,
        "limit": 1000,
        "watermark_column": "string", // Optional: column for incremental
        "watermark_value": "...",     // Optional: resume from here
    }},
)
```

## Implementation

The handler delegates to `extract_data_impl`:

1. **Connector creation:** `create_connector(type, connection)`
2. **Pagination:** Calls `connector.extract(table, filter, offset, limit)` which handles:
   - **PostgreSQL:** keyset (seek) pagination via `choose_keyset()` — picks the best ordering column, uses `WHERE (col1, col2) > (val1, val2)` for deterministic ordering
   - **ClickHouse:** keyset pagination with same logic, supports `in_scratch` filter via scratch tables
   - **MongoDB:** `_id`-based skip pagination (not OFFSET)
3. **Watermark support:** If `watermark_column` is provided, the connector adds `WHERE watermark_col > watermark_value` as a pushdown filter
4. **Filter normalization:** The `filter` dict is normalized via `normalize_filter()` to handle `eq`, `ilike`, `in` operations

## Shared Implementation

`extract_data_impl` (at `src/mcp_server/tools/extract_data.py`) is also called by the `extract_data` orchestrator node. The node version wraps it with:
- Dependency-ordered extraction across entities
- Chunked IN filters for large scope plans
- Scratch table filter for ClickHouse
- Result storage in the DuckDB spool

## Cross-References

| Concern | Page |
|---------|------|
| Shared impl | [extract_data Tool Impl](/openwiki/mcp/tools/extract_data.md) |
| Orchestrator node | [Extract Data Node](/openwiki/orchestrator/nodes/extract_data.md) |
| Keyset pagination | [Keyset Utilities](/openwiki/connectors/shared_utilities.md) |
| Connector factory | [Connectors Overview](/openwiki/connectors/overview.md) |
