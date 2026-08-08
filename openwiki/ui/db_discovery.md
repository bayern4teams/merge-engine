---
type: ui component
title: Database Discovery Helpers
description: >
  UI helpers for database discovery: connection testing, schema discovery,
  and sample row retrieval for PostgreSQL, MongoDB, and ClickHouse.
tags: [ui, db-discovery, connection-testing, schema]
---

# Database Discovery Helpers

## Purpose

Provides UI components for testing database connections and discovering
schema metadata. Called from both the FastAPI backend and directly from
the React frontend.

## File

`src/ui/components/db_discovery.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `test_and_discover_schemas(connection, origin_type)` | Test connection and discover full schema |
| `sample_table_rows(connection, table, n=5)` | Get sample rows from a table |

### test_and_discover_schemas

```python
async def test_and_discover_schemas(connection, origin_type):
    """Test a database connection and discover its schema.
    
    Returns: (success: bool, schema: dict, error: str | None)
    """
```

Creates a connector via `create_connector(origin_type, connection)`, tests
connectivity via `discover_schema()`, and returns the schema or error.

### sample_table_rows

```python
async def sample_table_rows(connection, table, n=5):
    """Extract sample rows from a table for UI preview."""
```

Uses the connector's `extract()` method with a small limit to fetch sample
data.

## Integration

Called from the API layer:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/connections/test` | POST | Test connection and discover schema |
| `/api/connections/sample` | POST | Get sample rows from a table |

## Test Coverage

- `tests/unit/test_api.py` — tests the discovery endpoints
- `tests/unit/test_postgres_connector.py` — PostgreSQL schema discovery
- `tests/unit/test_mongodb_connector.py` — MongoDB schema discovery
