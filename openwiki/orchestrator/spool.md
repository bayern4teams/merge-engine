---
type: data storage
title: DuckDB Spool
description: Per-run DuckDB file for extracted rows, replacing in-memory lists that blow up checkpoint size. Supports blocking/scoring as SQL against the same file.
tags: [spool, duckdb, storage, extraction]
---

# DuckDB Spool

## Purpose

Replaces the original approach of storing extracted rows in a Python list inside `MergeState.extracted_data` and serializing it into a LangGraph checkpoint on every step. That made peak memory and checkpoint size proportional to source data size — a 43 million-row merge could never start, let alone survive a human walking away from a review gate.

The spool stores rows in a DuckDB file under `logs/spool/<run_id>.duckdb`; state carries only `{entity: {"table": ..., "row_count": ...}}`. DuckDB is columnar and out-of-core, so later phases can do blocking and scoring as SQL against the same file.

## Row Storage Format

Rows are stored as JSON text in a single `payload` column rather than typed columns. Origins disagree about schema by definition — a fixed table would need migration every time a new origin contributed a field. DuckDB reads JSON natively, so SQL phases can still project individual fields.

## Tables

Each entity type has three tables in the spool file (via `_table_for(entity_type, kind)`):

| Kind | Table | Purpose |
|------|-------|---------|
| `rows` | `<entity>_rows` | Extracted source records |
| `unified` | `<entity>_unified` | Merged output records |
| `clusters` | `<entity>_clusters` | Grouping decisions |

Before the spool, these three used to sit in checkpointed state — measured on a 2,284-record merge, the checkpoint was 69.6% `unified_entities` and 28.2% `entity_clusters`.

## Bulk Insert Performance

`_bulk_insert()` (lines 47-84) hands DuckDB a columnar frame via pandas for ~1,300x faster inserts than row-by-row executemany:

| Method | 50,000 rows |
|--------|-------------|
| `executemany` (row-by-row) | 259 seconds |
| Columnar frame (pandas) | 0.19 seconds |

Falls back to chunked executemany (2,000 rows/chunk) if pandas is unavailable — degrades in speed, not correctness.

## Cleanup Policy

`cleanup_old_spools()` sweeps the spool directory, removing files untouched for 6+ hours (`_SPOOL_MIN_AGE_SECONDS`). Anything more recent is assumed to belong to an active run (possibly in another process).

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `spool_path(run_id)` | `spool.py:87` | `logs/spool/<safe_run_id>.duckdb` |
| `RunSpool` | `spool.py` | Context manager for spool file operations |
| `entity_rows(run_spool, entity)` | `spool.py` | Read all rows for an entity |
| `entity_row_count(run_spool, entity)` | `spool.py` | COUNT(*) for an entity |
| `store_clusters(run_spool, entity, clusters)` | `spool.py` | Write cluster decisions |
| `clusters_for(state, entity)` | `spool.py` | Read clusters from spool |
| `unified_for(state, entity)` | `spool.py` | Read unified records from spool |
| `entity_origin_counts(state, entity)` | `spool.py` | Per-origin extraction counts |
| `collection_count(conn, table)` | `spool.py` | Raw COUNT(*) via DuckDB connection |
| `collection_records(conn, table, limit)` | `spool.py` | LIMIT N rows from table |

## Spool Handle

When data is stored in the spool, the `MergeState.extracted_data` dict carries only a handle:

```python
{"table": "customers_rows", "row_count": 15234}
```

Not the actual list of dicts. `is_handle(value)` and `make_handle()` manage this pattern.

## Test Coverage

- `tests/unit/test_spool.py` — bulk insert performance, cleanup policy, JSON row storage
