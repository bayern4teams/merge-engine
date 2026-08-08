---
type: quickstart
title: merge-engine Wiki — Quickstart
description: >
  Entry point to the merge-engine wiki. High-level map of the system, cross-references to major pages, and a task-routing table from engineering intent to relevant pages, source entrypoints, focused tests, and validation commands.
tags: [quickstart, navigation, overview]
---

# merge-engine Wiki — Quickstart

## What is merge-engine?

merge-engine merges data from multiple heterogeneous databases (PostgreSQL, MongoDB, ClickHouse) into one destination (PostgreSQL, ClickHouse, or Databricks), with an LLM doing schema understanding and ambiguous judgment calls, and a human approving four checkpoints before anything is written.

## Architecture at a Glance

```
React (Vite/TS) UI  ──HTTP──>  FastAPI (src/api)  ──>  WorkflowManager
                                                            │
                                                     LangGraph StateGraph
                                                    (src/orchestrator/graph.py)
                                                            │
        ┌───────────────────────────────────────────────────────────────────┐
        │ preflight → parse_request → discover_schemas → [HITL: entities]   │
        │   → extract_data → resolve_entities → [HITL: matches] → unify     │
        │   → map_fields → [HITL: mapping] → [HITL: final] → load_data     │
        └───────────────────────────────────────────────────────────────────┘
                     │                              │                  │
              DuckDB spool                    LLM client          Origin/dest
          (logs/spool/<run>.duckdb)      (semantic cache +        connectors
                                           cost ledger)      (postgres/clickhouse/mongo)
```

Every node is a plain async function operating on a typed `MergeState` dict; LangGraph's SQLite checkpointer persists state after every node, which is what makes the human-review pauses (`interrupt_before`) survive an app restart. Row data does not live in that checkpointed state — it lives in a per-run DuckDB file (the "spool").

## Wiki Map

### System Architecture
- [Architecture Overview](./architecture/overview.md) — what merge-engine does, why, and how

### API Layer
- [API Overview](./api/overview.md) — FastAPI app, architecture, how it wraps WorkflowManager
- [API Endpoints](./api/endpoints.md) — complete endpoint inventory with schemas

### MCP Server
- [MCP Server Overview](./mcp/overview.md) — tool registration, shared implementation pattern
- [discover_schema](./mcp/tools/discover_schema.md) — schema discovery tool
- [extract_data](./mcp/tools/extract_data.md) — data extraction tool
- [resolve_entities](./mcp/tools/resolve_entities.md) — entity resolution tool
- [unify_entity_group](./mcp/tools/unify_entity_group.md) — entity group unification
- [map_fields](./mcp/tools/map_fields.md) — field mapping tool
- [load_data](./mcp/tools/load_data.md) — destination upsert tool
- [extend_schema](./mcp/tools/extend_schema.md) — schema extension tool

### Database Connectors
- [Connector Overview](./connectors/overview.md) — BaseConnector, factory, registry, shared utilities
- [PostgreSQL](./connectors/postgres.md) — schema discovery, keyset pagination, watermark pushdown
- [MongoDB](./connectors/mongodb.md) — collection discovery, document extraction
- [ClickHouse](./connectors/clickhouse.md) — scratch-table support for large IN filters
- [Databricks](./connectors/databricks.md) — dual role (source + destination)
- [Destination Connectors](./connectors/destinations.md) — PostgreSQL/ClickHouse destination writers
- [Shared Utilities](./connectors/shared_utilities.md) — filters, keyset, watermark

### Orchestrator (LangGraph Workflow)
- [Orchestrator Overview](./orchestrator/overview.md) — LangGraph StateGraph builder, cross-cutting concerns
- [Graph Definition](./orchestrator/graph.md) — all 17 nodes, edges, conditional edges, retry policies
- [Workflow Manager](./orchestrator/workflow_manager.md) — start/pause/resume/status, checkpoint management, HITL resume
- [DuckDB Spool](./orchestrator/spool.md) — per-run DuckDB files for extracted rows
- [Saved Profiles](./orchestrator/profiles.md) — pinned entity_tables, matching_rules, conflict_policy, field_mappings
- [Reconciliation Reports](./orchestrator/reconciliation_reports.md) — per-entity audit numbers and data quality grading

#### Pipeline Nodes
- [preflight](./orchestrator/nodes/preflight.md) — DB connectivity, LLM availability, disk space
- [parse_request](./orchestrator/nodes/parse_request.md) — LLM-based target entity extraction
- [discover_schemas](./orchestrator/nodes/discover_schemas.md) — concurrent schema discovery
- [analyze_dependencies](./orchestrator/nodes/analyze_dependencies.md) — dependency tree, topological sort
- [extract_data](./orchestrator/nodes/extract_data.md) — dependency-ordered extraction, keyset pagination
- [resolve_entities](./orchestrator/nodes/resolve_entities.md) — blocking → heuristic → LLM → Union-Find
- [unify_entities](./orchestrator/nodes/unify_entities.md) — conflict resolution per cluster
- [map_fields](./orchestrator/nodes/map_fields.md) — unified field → destination column mapping
- [plan_scope](./orchestrator/nodes/plan_scope.md) — per-entity extraction plan for scoped merges
- [load_data](./orchestrator/nodes/load_data.md) — destination upsert, FK translation, reconciliation
- [update_extraction_state](./orchestrator/nodes/update_extraction_state.md) — watermark persistence
- [Human in the Loop](./orchestrator/nodes/human_in_the_loop.md) — HITL interrupt nodes and decisions

#### Orchestrator Utilities
- [Dependency Sort](./orchestrator/utils/dependency_sort.md) — topological sort, execution groups
- [Cluster Edits](./orchestrator/utils/cluster_edits.md) — apply HITL keep/split/reject decisions
- [Error Handling](./orchestrator/utils/error_handling.md) — error taxonomy, exception classification
- [Retry Hints](./orchestrator/utils/retry_hints.md) — user feedback to LLM prompt bridge
- [Resolve Cache](./orchestrator/utils/resolve_cache.md) — cross-run resolution result cache
- [Entity Aliases](./orchestrator/utils/entity_aliases.md) — business concept ↔ table matching
- [Entity Types](./orchestrator/utils/entity_types.md) — canonical entity type definitions
- [Fellegi-Sunter](./mcp/tools/fellegi_sunter.md) — probabilistic weight estimation
- [Entity Table Selection](./orchestrator/utils/select_entity_tables.md) — LLM-based table selection

### LLM Integration
- [LLM Overview](./llm/overview.md) — unified client, cost controls, tracing
- [LLM Client](./llm/client.md) — LangChain wrapper, quota fallback chain, model chain
- [Cost Controls](./llm/cost.md) — semantic cache (5000 entries), cost ledger
- [Tracing](./llm/tracing.md) — Langfuse integration, span nesting
- [Prompt Templates](./llm/prompts.md) — 6 stages, system/human template pairs

### Configuration
- [Configuration Overview](./configuration/overview.md) — config.yaml, env var resolution, overrides
- [Configuration Models](./configuration/models.md) — Config, DestinationConfig, LLMConfig, etc.
- [Platform Overrides](./configuration/overrides.md) — runtime config overlay, provenance tracking

### User Interface
- [UI Overview](./ui/overview.md) — React SPA, FastAPI serving, API client
- [Recent Connections](./ui/recent_connections.md) — connection history persistence
- [DB Discovery Helpers](./ui/db_discovery.md) — connection testing, schema discovery

### Data Model
- [MergeState TypedDict](./data/merge_state.md) — all 43 keys, types, reducers, producer/consumer mapping
- [Connector Data Contracts](./data/connector_contracts.md) — discover_schema, extract, map, load contracts

### Utilities
- [Config Loader](./utils/config_loader.md) — load_config(), Pydantic validation, ${ENV_VAR} resolution
- [UUID Utilities](./utils/id_utils.md) — random and deterministic UUID generation
- [Logging](./utils/logging.md) — structlog, JSON formatting, Windows UTF-8
- [Value Coercion](./utils/value_coercion.md) — source-value to destination-type reconciliation
- [Span Tracing](./utils/tracing.md) — connector-level span context manager

### Testing
- [Testing Overview](./testing/overview.md) — pytest, conftest fixtures, test organization
- [Unit Tests](./testing/unit_tests.md) — 58+ test files by component
- [Integration Tests](./testing/integration_tests.md) — 11 integration test files
- [Evaluation & Golden Data](./testing/evaluation.md) — scoring, prompt harness, golden datasets

### Operations
- [Deployment Overview](./deployment/overview.md) — entrypoints, CLI modes, logs, process management
- [Docker Configuration](./deployment/docker.md) — Dockerfile, compose stacks, Langfuse
- [Operational Scripts](./operations/scripts.md) — check.py, bootstrap.py, lint suite, diagnostics

### Existing Documentation
- [Documentation Index](./docs/index.md) — `/docs/` architecture docs, ADRs, verification docs

## Task Routing Table

| Change Area / Intent | Relevant Page(s) | Source Entrypoint(s) | Focused Test(s) |
|---------------------|-----------------|---------------------|-----------------|
| Change pipeline workflow order | [Orchestrator Overview](./orchestrator/overview.md), [Graph Definition](./orchestrator/graph.md) | `src/orchestrator/graph.py` | `tests/integration/test_graph_workflow.py` |
| Add new pipeline node | [Node Templates](./orchestrator/nodes/) | `src/orchestrator/nodes/*.py` | `tests/unit/test_*_node.py` |
| Fix entity matching | [Resolve Entities Node](./orchestrator/nodes/resolve_entities.md), [Resolve Cache](./orchestrator/utils/resolve_cache.md), [Fellegi-Sunter](./mcp/tools/fellegi_sunter.md) | `src/mcp_server/tools/resolve_entities.py` | `tests/unit/test_resolve_entities_node.py` |
| Add new database source | [Connector Overview](./connectors/overview.md), [PostgreSQL Example](./connectors/postgres.md) | `src/mcp_server/connectors/*.py` | `tests/unit/test_*_connector.py` |
| Change destination write | [Destination Connectors](./connectors/destinations.md), [Load Data Node](./orchestrator/nodes/load_data.md) | `src/mcp_server/connectors/*_destination.py` | `tests/unit/test_*_destination.py` |
| Modify HITL review flow | [HITL Overview](./orchestrator/nodes/human_in_the_loop.md), [Workflow Manager](./orchestrator/workflow_manager.md) | `src/orchestrator/nodes/human_nodes.py`, `src/orchestrator/workflow_manager.py` | `tests/unit/test_human_nodes.py` |
| Change LLM behavior | [LLM Client](./llm/client.md), [Cost Controls](./llm/cost.md), [Prompt Templates](./llm/prompts.md) | `src/llm/client.py` | `tests/unit/test_llm_client.py` |
| Modify configuration | [Config Loader](./utils/config_loader.md), [Overrides](./configuration/overrides.md) | `src/utils/config_loader.py` | `tests/unit/test_config_loader.py` |
| Fix data extraction performance | [Extract Data Node](./orchestrator/nodes/extract_data.md), [DuckDB Spool](./orchestrator/spool.md) | `src/orchestrator/nodes/extract_data.py`, `src/orchestrator/spool.py` | `tests/unit/test_extract_data_node.py` |
| Add new API endpoint | [API Overview](./api/overview.md), [Endpoints](./api/endpoints.md) | `src/api/main.py` | `tests/unit/test_api.py` |
| Change reconciliation report | [Reconciliation Reports](./orchestrator/reconciliation_reports.md) | `src/orchestrator/nodes/load_data.py` (build_reconciliation_report) | `tests/integration/test_load_data.py` |

## Validation Commands

| Change | Validation |
|--------|-----------|
| Code style | `ruff check .` |
| Type checking | `mypy src/` |
| All tests | `pytest tests/` |
| Unit tests only | `pytest tests/unit/` |
| Integration tests | `pytest tests/integration/` |
| Full quality gate | `python scripts/check.py` |
| Lint + types + arch + docs | `python scripts/lint_arch.py && python scripts/lint_docs.py` |

## Key Files Quick Reference

| Concern | File |
|---------|------|
| Entrypoint | `src/main.py` |
| Graph builder | `src/orchestrator/graph.py` |
| Workflow manager | `src/orchestrator/workflow_manager.py` |
| Config | `config/config.yaml` |
| Platform overrides | `config/platform_overrides.yaml` (gitignored) |
| Checkpoints | `data/checkpoints.db` |
| Spool files | `logs/spool/<run_id>.duckdb` |
| Resolve cache | `logs/spool/_resolve_cache.duckdb` |
| Run reports | `logs/run_reports/<thread_id>.json` |
| Prompt templates | `src/llm/prompts/*.txt` |
| Recent connections | `config/recent_connections.json` |
