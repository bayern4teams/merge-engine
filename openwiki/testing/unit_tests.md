---
type: unit tests
title: Unit Tests
description: >
  Unit test inventory for merge-engine: 58+ test files covering individual
  nodes, connectors, tools, utilities, and evaluation criteria.
tags: [testing, unit-tests, pytest, test-inventory]
---

# Unit Tests

## Purpose

Individual component tests verifying correct behavior of nodes, connectors,
tools, and utilities in isolation.

## File

`tests/unit/` — 58+ test files

## Test Inventory by Area

### Connector Tests

| File | Size | Purpose |
|------|------|---------|
| `test_postgres_connector.py` | 31KB | PostgreSQL connector: schema discovery, extraction, keyset pagination |
| `test_mongodb_connector.py` | 27KB | MongoDB connector: collection discovery, document extraction |
| `test_databricks_connector.py` | 22KB | Databricks connector: dual role testing |
| `test_postgresql_destination.py` | 37KB | PostgreSQL destination: upsert, schema creation |
| `test_clickhouse_scratch_table.py` | — | ClickHouse scratch table for large IN filters |
| `test_connectors.py` | — | General connector tests |
| `test_connectors_base.py` | — | BaseConnector interface |
| `test_connector_filters.py` | — | Filter normalization |
| `test_keyset_pagination.py` | — | Keyset pagination cursors |
| `test_keyset_non_unique.py` | — | Keyset with non-unique keys |

### Orchestrator Node Tests

| File | Size | Purpose |
|------|------|---------|
| `test_extract_data_node.py` | 45KB | Largest: batch extraction, scratch tables, chunked filters |
| `test_load_data_node.py` | 29KB | Destination upsert, FK translation, reconciliation |
| `test_analyze_dependencies_node.py` | 19KB | Dependency tree, topological sort |
| `test_human_nodes.py` | 19KB | HITL interrupt nodes |
| `test_map_fields_node.py` | 17KB | Field mapping, heuristic scoring |
| `test_resolve_entities_node.py` | — | Entity clustering |
| `test_unify_entities_node.py` | — | Conflict resolution |
| `test_parse_request_node.py` | — | Request parsing |
| `test_discover_schemas_node.py` | — | Schema discovery |
| `test_plan_scope.py` | — | Scope planning |

### MCP Tool Tests

| File | Purpose |
|------|---------|
| `test_mcp_discover_schema.py` | discover_schema tool |
| `test_mcp_extract_data.py` | extract_data tool |
| `test_mcp_load_data.py` | load_data tool |
| `test_mcp_map_fields.py` | map_fields tool |
| `test_mcp_resolve_entities.py` | resolve_entities tool |
| `test_mcp_unify_entity_group.py` | unify_entity_group tool |
| `test_mcp_server.py` | MCP server registration |
| `test_select_entity_tables.py` | Entity table selection |

### Utility Tests

| File | Purpose |
|------|---------|
| `test_config_loader.py` | 13KB: config loading, validation, override merging |
| `test_id_utils.py` | UUID generation and validation |
| `test_logging_config.py` | Structured logging setup |
| `test_value_coercion.py` | Value type coercion |
| `test_tracing.py` / `test_utils_tracing.py` | Tracing |
| `test_retry.py` | Retry logic and exception classification |
| `test_dependency_sort.py` | Topological sort |
| `test_entity_aliases.py` | Entity name matching |
| `test_resolve_cache.py` | Resolution cache |
| `test_spool.py` | DuckDB spool operations |
| `test_workflow_persistence.py` | Checkpoint persistence |

### Evaluation Tests

| File | Purpose |
|------|---------|
| `test_scope_plan_eval.py` | Scope plan evaluation |
| `test_mapping_gate.py` | Mapping gate |
| `test_run_profiles.py` | Profile save/load |
| `test_dest_dedup.py` | Destination deduplication |
| `test_phase_k_gates.py` | Phase K gates |
| `test_phase_l_matching.py` | Phase L matching |
| `test_phase_m_resilience.py` | 24KB: Phase M resilience |
| `test_matching_rules_and_policy.py` | Matching rules |
| `test_hybrid_llm_paths.py` | Hybrid LLM paths |
| `test_matching_performance.py` | Matching performance |
| `test_llm_client.py` | LLM client |
| `test_llm_cost.py` | LLM cost accounting |
| `test_api.py` | API endpoints |

## Test Coverage

- 58+ unit test files
- All major components covered
- Representative assertions on failure cases and edge conditions
