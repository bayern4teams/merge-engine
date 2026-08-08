---
type: connector
title: Databricks Connector
description: >
  Databricks Delta Lake connector with dual role: it serves as a source
  connector inheriting from BaseConnector for schema discovery and
  extraction, and as a destination writer supporting load_batch MERGE,
  add_column, and extend_schema_batch operations.
tags: [connector, databricks, delta-lake, destination]
---

# Databricks Connector

## Purpose

Databricks connector with a dual role:

1. **Source connector** — inherits from `BaseConnector` for schema discovery and data extraction
2. **Destination writer** — writes unified records to Databricks using Delta Lake MERGE operations

## File

`src/mcp_server/connectors/databricks.py` (~12KB)

## Source Interface

Inherits from `BaseConnector`:

- `discover_schema()` — lists Delta tables
- `extract()` — queries Delta tables with filters
- `close()` — disconnects

## Destination Interface

Additional methods not in BaseConnector:

| Method | Purpose |
|--------|---------|
| `load_batch(table, rows, columns)` | MERGE unified records into Delta table |
| `add_column(table, column, col_type)` | Add a new column to an existing Delta table |
| `extend_schema_batch(changes)` | Batch add columns for unmapped fields |

### load_batch — MERGE Operation

```python
async def load_batch(table, rows, columns):
    # 1. Create target table if not exists
    # 2. Build MERGE statement with source table
    # 3. Match on primary key
    # 4. UPDATE on match, INSERT on no match
    # 5. Return {inserted: N, updated: N, failed: N}
```

### extend_schema_batch

Used by the `extend_schema` MCP tool to add columns for unmapped fields:

```python
async def extend_schema_batch(changes):
    added = 0
    failed = 0
    for change in changes:
        try:
            await self.add_column(change["table"], change["column"], change["type"])
            added += 1
        except Exception:
            failed += 1
    return {"added": added, "failed": failed}
```

## Connection

```python
from databricks import sql

connection = sql.connect(
    server_hostname=host,
    http_path=http_path,
    access_token=access_token,
    catalog=catalog,
    schema=schema,
)
```

## Test Coverage

- `tests/unit/test_databricks_connector.py` — source and destination operations (22KB)
