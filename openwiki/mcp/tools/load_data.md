---
type: mcp tool
title: load_data Tool
description: MCP tool that upserts unified records into destination databases (PostgreSQL or ClickHouse), handling foreign key translation, value coercion, and conflict tracking with provenance metadata.
tags: [mcp, tool, load_data, destination, upsert]
---

# load_data Tool

## Purpose

Upserts unified records into the destination database. Handles:
1. **Foreign key translation** — resolves destination table IDs for related entities before writing
2. **Value coercion** — converts source values to destination column types (magnitude parsing, date normalization)
3. **Conflict tracking** — tracks insert vs. update counts per entity
4. **Provenance** — each record carries origin metadata showing which source databases contributed

## Handler

**File:** `src/mcp_server/server.py:224-250` (tool definition), `src/mcp_server/tools/load_data.py:load_data_handler` (handler), `src/mcp_server/tools/load_data.py:load_data_impl` (shared impl), `src/mcp_server/tools/load_data.py:_get_destination_connector` (connector factory)

## Tool Definition

```python
Tool(
    name="load_data",
    description="Upsert unified records into destination with FK translation, value coercion, and conflict tracking.",
    input_schema={"type": "object", "properties": {
        "entity_type": "string",
        "records": [...],              // Unified records
        "mapping": {...},              // Field → destination column map
        "destination": {...},          // Destination config
        "known_cluster_ids": {...},    // From previous runs
        "provenance": {...},           // Origin metadata
    }},
)
```

## Implementation

`load_data_impl` (at `src/mcp_server/tools/load_data.py`) performs:

1. **Destination connector creation** — via `_get_destination_connector(dest_type, dest_config)`:
   - PostgreSQL → `PostgreSQLDestinationConnector`
   - ClickHouse → `ClickHouseDestinationConnector`
2. **Foreign key translation** — looks up destination IDs from `known_cluster_ids` for each referenced entity, building FK references
3. **Value coercion** — calls `_coerce_to_column_type()` which delegates to `coerce_value()` from `src/utils/value_coercion.py` for type-safe conversion
4. **Upsert** — executes INSERT ... ON CONFLICT UPDATE (PostgreSQL) or INSERT ... ON DUPLICATE KEY UPDATE (ClickHouse)
5. **Reconciliation** — returns `{inserted: N, updated: N, failed: N}` per entity

## State Integration

The orchestrator node (`load_data_node` in `graph.py`) wraps this with:
- **Scoped merge guardrail** — `_scoped_merge_guardrail_failure()` checks if a scoped merge formed too many singleton clusters with zero auto-merges (Airbus/Lockheed failure pattern), escalating to human if found
- **Reconciliation report** — `build_reconciliation_report()` produces per-entity metrics
- **Transaction wrapping** — a mid-load failure rolls back, leaving destination and watermarks untouched
- **Entity map persistence** — `_persist_entity_map()` saves cluster → destination IDs for incremental runs
- **Watermark update** — calls `update_extraction_state_node` after successful load

## Cross-References

| Concern | Page |
|---------|------|
| Orchestrator node | [Load Data Node](/openwiki/orchestrator/nodes/load_data.md) |
| Destination connectors | [Destination Connectors](/openwiki/connectors/destinations.md) |
| Value coercion | [Value Coercion](/openwiki/utils/value_coercion.md) |
| Reconciliation | [Reconciliation Reports](/openwiki/orchestrator/reconciliation_reports.md) |
