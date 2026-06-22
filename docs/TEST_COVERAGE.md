# Test Coverage Mapping

## Summary

| Test Suite | Files | Tests | Pipeline Coverage |
|------------|-------|-------|-------------------|
| Unit Tests | 13 | ~275 | All pipeline stages |
| Integration Tests | 12 | ~99 | Full workflow pipelines |
| **Total** | **25** | **374** | **Complete** |

---

## Test File to Pipeline Stage Mapping

### 1. Configuration Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/unit/test_config_loader.py` | ~30 | **Config Loading** — Pydantic models, env var resolution, secrets merging |
| `tests/integration/test_config_resolution.py` | ~13 | **End-to-End Config** — File → env vars → secrets → validation |

**Source:** `src/utils/config_loader.py`, `src/config/schema/`

---

### 2. Database Connectors Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/unit/test_connectors.py` | ~16 | **Connector Factory** — Postgres, MongoDB, Databricks creation and validation |

**Source:** `src/mcp_server/connectors/{base,postgres,mongodb,databricks}.py`

---

### 3. Utility Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/unit/test_dependency_sort.py` | ~15 | **Dependency Analysis** — Topological sort, cycle detection, execution groups |
| `tests/unit/test_id_utils.py` | ~17 | **ID Generation** — UUID hex generation, dash conversion, namespace IDs |
| `tests/unit/test_retry.py` | ~12 | **Retry Logic** — Exponential backoff, jitter, max retries |
| `tests/unit/test_logging_config.py` | ~8 | **Structured Logging** — JSON format, context injection |
| `tests/unit/test_state.py` | ~10 | **MergeState** — State operations, JSON serialization, graph validation |

**Source:** `src/utils/{id_utils,logging_config,config_loader}.py`, `src/orchestrator/utils/{dependency_sort,retry}.py`, `src/orchestrator/state.py`

---

### 4. LLM Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/unit/test_llm_client.py` | ~12 | **LLM Client** — OpenAI/Anthropic/Ollama init, async generation, streaming |

**Source:** `src/llm/client.py`, `src/llm/prompts/*.txt`

---

### 5. Entity Resolution Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/unit/test_resolve_entities_detail.py` | ~15 | **Entity Resolution** — BlockGenerator, score calculator, matcher, unifier, HITL reviewer |
| `tests/integration/test_resolve_entities.py` | ~13 | **Entity Resolution Pipeline** — Exact blocking, single record, different records, human review flag |

**Source:** `src/mcp_server/tools/resolve_entities.py`, `src/orchestrator/nodes/resolve_entities.py`, `unify_entities.py`

---

### 6. Field Mapping Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/unit/test_map_fields_detail.py` | ~15 | **Field Mapping** — FieldMapBuilder, mapping generation, type coercion, transformation rules |
| `tests/integration/test_field_mapping.py` | ~8 | **Field Mapping Pipeline** — Schema discovery → LLM generation → validation |

**Source:** `src/mcp_server/tools/map_fields.py`, `src/orchestrator/nodes/map_fields_node.py`

---

### 7. Data Extraction Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/integration/test_extract_data.py` | ~8 | **Data Extraction** — Pagination, single batch, unknown origin, error handling, filtering, batch size |

**Source:** `src/mcp_server/tools/extract_data.py`, `src/orchestrator/nodes/extract_data.py`

---

### 8. Data Loading Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/integration/test_load_data.py` | ~7 | **Data Loading** — Basic flow, no data, connector failure, transform records |

**Source:** `src/mcp_server/tools/load_data.py`, `src/orchestrator/nodes/load_data.py`

---

### 9. Graph/Workflow Orchestration Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/integration/test_graph_workflow.py` | ~8 | **LangGraph Workflow** — Graph build, initial state, stream with mocked nodes, state transitions |
| `tests/integration/test_workflow_manager.py` | ~9 | **Workflow Manager** — Start, status, abort, list active, resume, cleanup |

**Source:** `src/orchestrator/graph.py`, `src/orchestrator/workflow_manager.py`

---

### 10. Human-in-the-Loop Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/integration/test_human_in_the_loop.py` | ~17 | **HITL Workflow** — Review matches, mappings, deps, final approval, error retry, full flow |

**Source:** `src/orchestrator/nodes/human_nodes.py`

---

### 11. UI Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/unit/test_ui_components.py` | 24 | **UI Components** — ChatState, render_dependency_editor, render_match_reviewer, render_mapping_reviewer, render_final_summary |
| `tests/integration/test_ui.py` | 11 | **UI Integration** — Render with realistic data, ChatState transitions, module structure |

**Source:** `src/ui/{gradio_app,__init__.py}`, `src/ui/components/{dependency_editor,match_reviewer,mapping_reviewer,summary_view}.py`

---

### 12. Edge Cases & Cross-Origin Stage

| Test File | Tests | Pipeline Stage |
|-----------|-------|----------------|
| `tests/integration/test_edge_cases.py` | 30 | **Edge Cases** — Duplicate detection, null handling, special chars, SKU normalization, MongoDB flattening, ID generation, cross-origin linkage |

**Source:** Tests all pipeline stages with edge case data

---

## Pipeline Stage Coverage Matrix

```
┌─────────────────────────────────┬──────────┬──────────┐
│ Pipeline Stage                  │ Unit     │ Integration │
├─────────────────────────────────┼──────────┼──────────┤
│ Config Loading                  │    ✅    │    ✅    │
│ Database Connectors             │    ✅    │          │
│ LLM Client                      │    ✅    │          │
│ Dependency Analysis             │    ✅    │          │
│ Schema Discovery                │          │    ✅    │
│ Data Extraction                 │          │    ✅    │
│ Entity Resolution               │    ✅    │    ✅    │
│ Entity Unification              │    ✅    │          │
│ Field Mapping                   │    ✅    │    ✅    │
│ Data Loading                    │          │    ✅    │
│ HITL (4 interrupt points)       │          │    ✅    │
│ Workflow Orchestration          │    ✅    │    ✅    │
│ UI Components (4 renderers)     │    ✅    │    ✅    │
│ Error Handling & Retry          │    ✅    │    ✅    │
│ Edge Cases (cross-origin)       │          │    ✅    │
└─────────────────────────────────┴──────────┴──────────┘
```

---

## Bugs Found During Testing

1. **`src/ui/gradio_app.py` line 75**: `nonlocal workflow_manager` → changed to `global workflow_manager` (3 occurrences: `start_workflow`, `poll_status`, `submit_feedback`, `abort_workflow`)
   - **Root Cause**: `workflow_manager` is a module-level global, not an enclosing function variable. `nonlocal` only works for enclosing scopes.
   - **Impact**: Import error when any code tried to import `gradio_app`.
   - **Fix**: Changed all 4 `nonlocal workflow_manager` to `global workflow_manager`.

---

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run unit tests only
python -m pytest tests/unit/ -v

# Run integration tests only
python -m pytest tests/integration/ -v

# Run UI tests only
python -m pytest tests/unit/test_ui_components.py tests/integration/test_ui.py -v

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=html