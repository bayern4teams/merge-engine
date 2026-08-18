---
type: connector
title: ClickHouse Connector
description: ClickHouse source connector using clickhouse-connect. Supports schema discovery, keyset pagination, and scratch-table support for large IN filters (up to 50K+ IDs).
tags: [connector, clickhouse, scratch-table, in-filter]
---

# ClickHouse Connector

## Purpose

ClickHouse source connector for schema discovery and data extraction. Features scratch-table support for large IN filters to avoid HTTP field-size limits.

## File

`src/mcp_server/connectors/clickhouse.py` (~21KB)

## Connection

```python
import clickhouse_connect

client = clickhouse_connect.get_client(
    host=host, port=port, user=user, password=password, database=database
)
```

## Schema Discovery

Queries ClickHouse system tables:

```sql
SELECT name, type FROM system.columns WHERE table = :table_name
```

## Keyset Pagination

Uses `keyset.py` utilities with ClickHouse's native `LIMIT ... OFFSET` replaced by seek-based pagination:

```sql
SELECT * FROM table
WHERE (id > :cursor_id)
ORDER BY id
LIMIT 1000
```

## Scratch Table Support

For large IN filters (tens of thousands of IDs), creates a temporary scratch table in ClickHouse:

```python
async def create_id_scratch_table(values: list[Any]) -> str:
    table_name = f"scratch_{uuid4().hex[:8]}"
    con.execute(f"CREATE TABLE {table_name} (id String) ENGINE = Memory")
    con.execute(f"INSERT INTO {table_name} VALUES {values_as_tuples}")
    return table_name
```

Used by the `in_scratch` filter operation:

```python
# Instead of: WHERE id IN (1, 2, 3, ..., 50000)
# Use: WHERE id IN (SELECT id FROM scratch_table)
```

The scratch table is auto-dropped after extraction.

## Test Coverage

- `tests/unit/test_clickhouse_scratch_table.py` — scratch table creation and cleanup
- `tests/integration/test_extract_data.py` — extraction with ClickHouse
