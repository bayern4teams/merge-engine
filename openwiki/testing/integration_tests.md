---
type: integration tests
title: Integration Tests
description: >
  Integration test inventory for merge-engine: 11 integration test files
  covering full pipeline workflows, Boeing/Airbus/Lockheed golden subjects,
  and end-to-end validation.
tags: [testing, integration, pipeline, golden-subjects]
---

# Integration Tests

## Purpose

End-to-end tests validating full pipeline workflows, multi-database
interactions, and realistic merge scenarios.

## File

`tests/integration/` — 11 test files

## Test Inventory

| File | Size | Purpose |
|------|------|---------|
| `test_boeing_full_pipeline.py` | 24KB | Boeing full pipeline end-to-end |
| `test_full_pipeline_manual.py` | 36KB | Manual pipeline execution |
| `test_edge_cases.py` | 35KB | Edge case handling |
| `test_graph_workflow.py` | 22KB | Graph execution workflow |
| `test_workflow_manager.py` | 14KB | Workflow lifecycle testing |
| `test_human_in_the_loop.py` | 15KB | HITL interrupt/resume testing |
| `test_load_data.py` | 10KB | Destination loading |
| `test_config_resolution.py` | 9KB | Configuration resolution |
| `test_field_mapping.py` | 7KB | Field mapping workflows |
| `test_extract_data.py` | 6KB | Data extraction workflows |
| `test_resolve_entities.py` | 6KB | Entity resolution workflows |

## Golden Subjects

Three golden subjects for eval: `"boeing"`, `"airbus"`, `"lockheed"`

Each has:
- Pre-defined source data
- Expected merge results
- Reconciliation reports

## Test Coverage

- 11 integration test files
- Covers full pipeline from request to load
- HITL interrupt and resume flows validated
- Multi-database interactions tested
