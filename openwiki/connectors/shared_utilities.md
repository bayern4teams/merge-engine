---
type: connector utility
title: Shared Connector Utilities
description: Shared utilities for database connectors, covering filter normalization in filters.py, keyset pagination cursors in keyset.py, and watermark column detection in watermark.py.
tags: [connector, filter, keyset-pagination, watermark, shared]
---

# Shared Connector Utilities

## Purpose

Shared utilities used across all database connectors. These are in `src/mcp_server/connectors/` rather than `src/utils/` because they are part of the MCP server's data extraction pipeline.

## Files

| Module | Purpose |
|--------|---------|
| `filters.py` | Filter normalization (eq, ilike, in, in_scratch) |
| `keyset.py` | Keyset (seek) pagination cursors |
| `watermark.py` | Watermark column detection |

## filters.py

Normalizes filter expressions across different database query languages:

```python
def normalize_filter(filter: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert merge-engine filter dict to database-specific SQL."""
    if filter is None:
        return None
    # eq: {"column": {"op": "eq", "value": 5}} -> column = 5
    # ilike: {"column": {"op": "ilike", "value": "%term%"}} -> column ILIKE %term%
    # in: {"column": {"op": "in", "value": [1,2,3]}} -> column IN (1,2,3)
    # in_scratch: {"column": {"op": "in_scratch", "value": "scratch_table"}} -> column IN (SELECT id FROM scratch_table)
```

### Filter Operations

| Operation | SQL Translation | Notes |
|-----------|-----------------|-------|
| `eq` | `column = value` | — |
| `ilike` | `column ILIKE pattern` | Case-insensitive |
| `in` | `column IN (val1, val2, ...)` | Chunked for large lists |
| `in_scratch` | `column IN (SELECT id FROM scratch_table)` | ClickHouse-only |

## keyset.py

Implements keyset (seek) pagination that avoids OFFSET's O(n) scan:

```python
def encode_cursor(key_columns: list[str], values: list[Any]) -> str:
    """Serialize key values into a cursor string."""
    return json.dumps({"keys": key_columns, "values": values})


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Deserialize cursor string back to key-value pairs."""
    return json.loads(cursor)


def advance_boundary(
    key_columns: list[str], current_values: list[Any], direction: str = "next"
) -> list[Any]:
    """Advance the cursor to the next/previous boundary."""
    ...
```

### Uniqueness

`assert_key_unique(engine, table, key_column)` verifies that the key column is unique. If not, a secondary tiebreaker column (ROWID/hash) is used.

### Chunking

For non-unique keys, the boundary is computed using `(key_column, tiebreaker_column)` tuples.

## watermark.py

Detects the best watermark column for incremental extraction:

```python
def detect_watermark_column(table_info: dict[str, Any]) -> dict[str, Any] | None:
    """Select the best watermark column from table metadata.
    
    Scores columns by:
    1. Numeric type > timestamp > string
    2. Higher cardinality is better
    3. Larger max value is better
    """
```

Returns `{"column": str, "kind": "numeric" | "timestamp"}` or `None` if no suitable column found.

## Test Coverage

- `tests/unit/test_connector_filters.py` — filter normalization
- `tests/unit/test_keyset_pagination.py` — keyset pagination encoding/decoding
- `tests/unit/test_keyset_non_unique.py` — non-unique key handling
