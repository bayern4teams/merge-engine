---
type: connector
title: Destination Connectors
description: PostgreSQL and ClickHouse destination connectors for writing unified records. Handles schema creation, upserts via INSERT ... ON CONFLICT or INSERT INTO ... SELECT, FK translation, and extraction state tracking.
tags: [connector, destination, postgresql, clickhouse, upsert]
---

# Destination Connectors

## Purpose

PostgreSQL and ClickHouse destination connectors for writing unified records. Handle schema creation, upserts with foreign key translation, extraction state tracking, and value coercion to destination types.

## File

| Connector | File |
|-----------|------|
| PostgreSQL Destination | `src/mcp_server/connectors/postgresql_destination.py` (~39KB) |
| ClickHouse Destination | `src/mcp_server/connectors/clickhouse_destination.py` (~22KB) |

## PostgreSQLDestinationConnector

### Connection

```python
import psycopg2

conn = psycopg2.connect(host=host, port=port, dbname=database, user=user, password=password)
```

### Schema Creation

Creates tables with appropriate column types inferred from the field mapping and value coercion analysis.

### Upsert Pattern

```sql
INSERT INTO target_table (columns...)
VALUES (values...)
ON CONFLICT (primary_key) DO UPDATE SET
    col1 = EXCLUDED.col1,
    col2 = EXCLUDED.col2,
    ...
```

### FK Translation

Translates source record IDs to destination primary keys when loading related entities:

```python
# Before: person.company_id = 999 (source ID)
# After:  person.company_id = 42 (destination ID)
```

### Extraction State Tracking

Creates and maintains `merge_engine_extraction_state` table:

```sql
CREATE TABLE merge_engine_extraction_state (
    origin_id VARCHAR,
    table_name VARCHAR,
    watermark_column VARCHAR,
    watermark_kind VARCHAR,
    last_value TEXT,
    run_id VARCHAR,
    rows_extracted INT,
    PRIMARY KEY (origin_id, table_name)
)
```

## ClickHouseDestinationConnector

### Connection

```python
import clickhouse_connect

client = clickhouse_connect.get_client(...)
```

### Upsert Pattern

Uses ClickHouse's `INSERT INTO ... SELECT ... FROM input` pattern with ON DUPLICATE KEY UPDATE:

```sql
INSERT INTO target_table
SELECT * FROM input
WHERE NOT EXISTS (
    SELECT 1 FROM target_table
    WHERE target_table.pk = input.pk
)
```

### Schema Extension

Supports `ALTER TABLE ... ADD COLUMN` for unmapped fields.

## Value Coercion

Both destination connectors use `coerce_value()` from `src/utils/value_coercion.py` to adapt source values to destination types. Tracks `attempted`/`failed` counts in the reconciliation report.

## Test Coverage

- `tests/unit/test_postgresql_destination.py` — PostgreSQL destination (37KB)
- `tests/integration/test_load_data.py` — end-to-end loading
