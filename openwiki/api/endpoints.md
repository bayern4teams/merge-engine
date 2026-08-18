---
type: api endpoints
title: API Endpoints
description: Complete inventory of FastAPI endpoints in merge-engine, with request/response schemas, source symbols, and delegation targets.
tags: [api, endpoints, routes]
---

# API Endpoints

## Workflow Endpoints

### `POST /api/workflow`
Start a new merge workflow.

**Request body:**
```json
{
  "user_request": "string",           // Natural language description
  "origins": [...],                   // List of origin connection configs
  "destination": {...},               // Destination connection config
  "profile_name": "string",           // Optional: load saved profile
  "full_remerge": false,              // Force full extraction
  "conflict_policy": {...},           // Override profile's policy
  "matching_rules": {...},            // Override profile's rules
  "run_config": {...}                 // Additional run config
}
```

**Response:** `{"thread_id": "uuid", "status": "running"}`

**Source:** Delegates to `WorkflowManager.start_workflow()` at `src/orchestrator/workflow_manager.py:141`.

### `POST /api/workflow/{thread_id}/pause`
Pause a running workflow at the next interrupt point.

**Response:** `{"status": "paused"}`

**Source:** `WorkflowManager.abort_workflow()` — sets internal abort flag.

### `POST /api/workflow/{thread_id}/resume`
Resume a paused workflow with human feedback.

**Request body:**
```json
{
  "feedback": {
    "decision": "approve|edit|retry|reject",
    "data": {...}                      // Gate-specific edits
  }
}
```

**Source:** `WorkflowManager.resume_workflow()` at `src/orchestrator/workflow_manager.py:1198`. Applies per-gate feedback:
- **mapping edits:** validated against live schema, rewrites `field_mappings`
- **cluster decisions:** `apply_cluster_decisions()` mutates spool data
- **scope plan edits:** merges user edits into `scope_plan`
- **error gate:** increments `gate_retry_counts`, populates `retry_hints`

### `GET /api/workflow/{thread_id}/status`
Get current workflow status.

**Response:**
```json
{
  "status": "running|paused|completed|failed|rejected|aborted",
  "gate": "human_review_deps|...",    // Current or last gate
  "progress": {...},
  "errors": [...],
  "timings": {...}
}
```

**Source:** `WorkflowManager.get_workflow_status()` at `src/orchestrator/workflow_manager.py:842`. Rehydrates from checkpoint if needed via `rehydrate_workflow()`.

### `GET /api/workflow/{thread_id}/timeline`
Get timing, cost, and entity resolution data for a completed or in-flight run.

**Response:** `{"timings": {...}, "cost": {...}, "entities": {...}}`

**Source:** Reads from `COST_LEDGER`, `SEMANTIC_CACHE`, and `_load_run_report()` at `src/api/main.py:84`.

### `GET /api/workflow/{thread_id}/timeline/match-inspection`
Read-only view into `resolve_entities` output for a workflow.

**Parameters:** `entity_type` (optional)

**Source:** `WorkflowManager.get_match_inspection()` at `src/orchestrator/workflow_manager.py:982`.

### `GET /api/workflows/active`
List all non-completed workflows.

**Response:** `[{thread_id, status, gate, ...}, ...]`

**Source:** `WorkflowManager.list_active_workflows()` at `src/orchestrator/workflow_manager.py:1455`.

## Connection Endpoints

### `GET /api/connections/recent`
Return recent origins/destinations with human-readable labels.

**Response:**
```json
{
  "recent_origins": [{"connection": {...}, "type": "postgres", "label": "🐘 postgresql → ..."}],
  "recent_destinations": [...]
}
```

**Source:** `src/ui/connections.py:load_persistent_history()` → `conn_label()` formatting.

### `POST /api/connections/recent`
Add an item to the recent connections list.

**Request:**
```json
{"connection": {...}, "type": "postgres|clickhouse|mongodb", "kind": "origin|destination"}
```

**Source:** `src/ui/connections.py:add_to_recent()` — de-duplicates on (host, database, type), trims to `max_size=5`.

### `POST /api/connections/test`
Test a database connection and discover schemas.

**Request:**
```json
{
  "connection": {...},
  "type": "postgres|clickhouse|mongodb",
  "discover_schemas": true
}
```

**Response:**
```json
{
  "success": true,
  "message": "...",
  "schemas": {origin_id: {tables: [...]}}
}
```

**Source:** `src/ui/components/db_discovery.py:test_and_discover_schemas()`.

### `POST /api/connections/sample-rows`
Get sample rows from discovered tables.

**Request:**
```json
{"connection": {...}, "type": "...", "tables": [{"name": "...", "limit": 10}]}
```

**Source:** `src/ui/components/db_discovery.py:sample_table_rows()`.

## Schema Discovery

### `POST /api/discover-schema`
Schema discovery via the MCP tool interface.

**Source:** Delegates to `src/ui/components/db_discovery.py:test_and_discover_schemas()`.

## Profile Endpoints

### `POST /api/profiles/build`
Build a profile from the current workflow state.

**Source:** `src/orchestrator/profiles.py:build_profile()` — captures `entity_tables`, `matching_rules`, `conflict_policy`, `field_mappings`. Strips secrets.

### `POST /api/profiles/save`
Save a profile to disk.

**Source:** `src/orchestrator/profiles.py:save_profile()` → writes to `config/profiles/<safe_name>.json`.

### `GET /api/profiles`
List all saved profiles.

**Source:** `src/orchestrator/profiles.py:list_profiles()` — reads `config/profiles/*.json`.

### `GET /api/profiles/{name}`
Load a specific profile (with secrets stripped).

**Source:** `src/orchestrator/profiles.py:load_profile()` + `strip_secrets()`.

### `DELETE /api/profiles/{name}`
Delete a saved profile.

**Source:** `src/orchestrator/profiles.py:delete_profile()`.

## Configuration Endpoints

### `GET /api/config`
Return effective configuration with provenance tracking.

**Response:**
```json
{
  "config": {...},
  "overrides": {...},
  "sources": {"llm.provider": "override", "llm.model": "file"}
}
```

**Source:** `get_config()` closure at `src/api/main.py:411` with `_provenance()` function returning `"override"`, `"env"`, or `"file"`.

### `PUT /api/config/platform`
Deep-merge a platform override into `config/platform_overrides.yaml`.

**Request:** Override dict matching `platform_overrides.yaml` structure.

**Source:** `save_platform_overrides()` at `src/utils/config_loader.py:182` — strips secrets before writing.

### `POST /api/config/env`
Apply session-only environment variable overrides.

**Request:** `{"VAR_NAME": "value", ...}`

**Source:** Populates `_SESSION_ENV_OVERRIDES` at `src/api/main.py:256`. Reset on process restart.

### `POST /api/config/env/reset`
Clear all session environment overrides.

**Source:** Resets `_SESSION_ENV_OVERRIDES = {}`.

## Cost Ledger

### `GET /api/cost-ledger`
Return the LLM cost ledger (token accounting per call).

**Source:** `COST_LEDGER` from `src/llm/cost.py` — a `dict[str, dict]` mapping request keys to token usage.

### `GET /api/semantic-cache/stats`
Return semantic cache hit/miss statistics.

**Source:** `SEMANTIC_CACHE.hits` and `SEMANTIC_CACHE.misses` from `src/llm/cost.py`.

## Run Reports

### `GET /api/workflow/{thread_id}/report`
Read the reconciliation report for a completed run.

**Source:** `_load_run_report()` at `src/api/main.py:84` — reads `logs/run_reports/<thread_id>.json` produced by `build_reconciliation_report()` in `src/orchestrator/nodes/load_data.py`.

## MCP Endpoints

### `POST /api/mcp/tool`
Call an MCP tool directly (discover_schema, extract_data, resolve_entities, etc.).

**Request:**
```json
{"tool": "discover_schema", "args": {"connection": {...}, "type": "postgres"}}
```

**Source:** Each handler is in `_TOOL_HANDLERS` at `src/mcp_server/server.py:348-356`.

## Internal FastAPI Components

| Component | Lines | Purpose |
|-----------|-------|---------|
| `get_app()` | `src/api/main.py:509` | Creates FastAPI app with lifespan, CORS, static file mount |
| `get_manager()` | `src/api/main.py:100` | Lazy `WorkflowManager` singleton |
| `_init_env()` | `src/api/main.py:57` | Loads `.env` file |
| `_load_run_report()` | `src/api/main.py:84` | Reads reconciliation report JSON |
| `_build_manager()` | `src/api/main.py:111` | Instantiates `WorkflowManager`, returns singleton |
| `HealthCheck` | `src/api/main.py:481` | Pydantic model for health endpoint |
| `WorkflowStart` | `src/api/main.py:174` | Request model for workflow start |
| `WorkflowResume` | `src/api/main.py:186` | Request model for workflow resume with gate feedback |
| `PlatformDestinationOverride` | `src/api/main.py:225` | Override model for destination config |
| `PlatformLLMOverride` | `src/api/main.py:233` | Override model for LLM config |
| `PlatformRetryOverride` | `src/api/main.py:241` | Override model for retry config |
| `PlatformExtractionOverride` | `src/api/main.py:246` | Override model for extraction config |

## Test Coverage

- **Unit test:** `tests/unit/test_api.py` — endpoint request/response validation, error handling
- **Integration test:** `tests/integration/test_graph_workflow.py` — full workflow through API
- **Integration test:** `tests/integration/test_workflow_manager.py` — WorkflowManager lifecycle via API
