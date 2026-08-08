---
type: orchestrator utility
title: Reconciliation Reports
description: >
  Reconciliation reports and scoped merge guardrails: per-entity extraction/clustering/conflict/load numbers, data quality grading, and the pre-load guardrail that prevents duplicate loading.
tags: [reconciliation, reports, data-quality, guardrails]
---

# Reconciliation Reports

## Purpose

Per-run reconciliation numbers for auditability. Built by `load_data_node`
and written to `logs/run_reports/<thread_id>.json`. Also includes the scoped
merge guardrail that fires when matcher collapse would ship duplicates.

## File

`src/orchestrator/nodes/load_data.py` — `build_reconciliation_report()` (lines 86-251), `_scoped_merge_guardrail_failure()` (lines 42-83)

## Reconciliation Report Structure

```json
{
  "run_id": "...",
  "generated_at": "2024-...",
  "entities": {
    "company": {
      "extracted_per_origin": {"ch": 1000, "pg": 500},
      "extracted_total": 1500,
      "clusters_formed": 42,
      "uncompared_pairs": 0,
      "candidate_pairs_compared": 5000,
      "auto_merged_clusters": 35,
      "human_reviewed_clusters": 7,
      "unified_records": 40,
      "conflicts": 12,
      "conflict_fields": ["phone"],
      "conflict_winning_rules": {"priority": 10, "recency": 2},
      "rows_inserted": 38,
      "rows_updated": 2,
      "rows_dropped": 0,
      "type_coercion_failures": 0,
      "fields_dropped_from_mapping": 0,
      "subject_unification_ratio": 0.97
    }
  },
  "new_columns": ["middle_name"],
  "errors": [],
  "load_failed_entities": [],
  "dropped_inferred_entities": [],
  "families_requested": 3,
  "families_loaded": 3,
  "completeness_pct": 100.0,
  "data_quality": {
    "grade": "A",
    "red_flags": []
  },
  "timings": {
    "preflight": 0.5,
    "parse_request": 2.3,
    ...
  }
}
```

## Data Quality Grading

| Grade | Condition |
|-------|-----------|
| A | Clean — no red flags |
| B | Other red flags |
| C | Type coercion failures OR fields dropped from mapping |
| F | Load rolled back OR zero auto-merges in scoped merge |

## Scoped Merge Guardrail

`_scoped_merge_guardrail_failure()` fires for scoped merges where:

1. `merge_scope.active` is True (scoped merge)
2. Subject entity is set
3. ≥5 clusters formed
4. Multi-record clusters exist (2+ records per cluster)
5. **Zero** auto-merges among them (all need human review)

This is the exact signature of the Airbus 20→20 / Lockheed 9→9 matcher-collapse failures. When it fires, the run is paused with a `DataQualityError` at `disposition: "escalate"` for human review.

**Threshold**: `_SCOPED_MERGE_GUARDRAIL_MIN_CLUSTERS = 5`

## API Exposure

`/api/workflow/{thread_id}/timeline` — timing, cost, and entity data from the report.

## Test Coverage

- `tests/integration/test_load_data.py` — load data workflow
- `tests/unit/test_dest_dedup.py` — destination deduplication
