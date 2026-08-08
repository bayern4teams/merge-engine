---
type: connector
title: PostgreSQL Connector
description: PostgreSQL source connector using SQLAlchemy async engine and asyncpg driver. Supports schema discovery via information_schema, keyset (seek) pagination, watermark pushdown for incremental extraction, and type-aware timestamp casting.
tags: [connector, postgres, keyset-pagination, watermark]
---

# PostgreSQL Connector

## Purpose

PostgreSQL source connector for schema discovery, data extraction, and import flagging. Uses SQLAlchemy for connection management and asyncpg for the async driver.

## File

`src/mcp_server/connectors/postgres.py` (~24KB)

## Connection

```python
drivername = "postgresql+asyncpg"
url = f"{drivername}://{user}:{password}@{host}:{port}/{database}"
engine = create_async_engine(url, pool_size=10, max_overflow=20)
```

Connection lifecycle:
1. `_ensure_engine()` — lazy creates async SQLAlchemy engine
2. `discover_schema()` — queries `information_schema` for tables, columns, types, foreign keys
3. `extract()` — batch extraction with keyset pagination
4. `close()` — disposes the async engine

## Schema Discovery

Queries `information_schema.tables` and `information_schema.columns`:

```python
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position
```

Also discovers foreign keys from `information_schema.table_constraints` and `information_schema.key_column_usage`.

## Keyset Pagination

Uses `keyset.py` utilities for seek-based pagination that avoids OFFSET's O(n) scan:

```sql
SELECT * FROM table
WHERE (id > :cursor_id) OR (id = :cursor_id AND rowid > :cursor_rowid)
ORDER BY id, rowid
LIMIT 1000
```

Timestamp columns are automatically cast (TIMESTAMP vs TIMESTAMPTZ) based on the cursor value's shape:

```python
_TIMESTAMPTZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)$"
)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?$")
```

## Watermark Support

Uses `watermark.py` to detect the best watermark column (highest cardinality numeric/timestamp column) for incremental extraction pushdown.

## Test Coverage

- `tests/unit/test_postgres_connector.py` — comprehensive connector tests (31KB)
