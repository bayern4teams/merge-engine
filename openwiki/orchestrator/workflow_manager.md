---
type: workflow manager
title: Workflow Manager
description: Workflow lifecycle manager for merge-engine covering start, pause, resume, status, and abort operations with SQLite checkpoint persistence, profile integration, HITL resume mechanics, and error gate bookkeeping.
tags: [workflow, manager, lifecycle, checkpoint]
---

# Workflow Manager

## Purpose

`WorkflowManager` in `src/orchestrator/workflow_manager.py` manages the full lifecycle of merge workflows with pause/resume capability via LangGraph checkpoints. Used by both the FastAPI backend and the Gradio UI.

## Public Methods

| Method | Lines | Purpose |
|--------|-------|---------|
| `start_workflow()` | 141-292 | Start new merge workflow in background daemon thread |
| `get_workflow_status()` | 842-884 | Get current status, rehydrating from checkpoint if needed |
| `rehydrate_workflow()` | 886-942 | Rebuild in-memory entry from persisted checkpoint after app restart |
| `get_match_inspection()` | 982-1023 | Read-only view into resolve_entities output |
| `apply_mapping_feedback()` | 1025-1145 | Rebuild mapping state from human-edited rows |
| `resume_workflow()` | 1198-1404 | Resume paused workflow with user feedback |
| `abort_workflow()` | 1441-1453 | Mark workflow as aborted |
| `list_active_workflows()` | 1455-1472 | List non-completed workflows |
| `cleanup_completed()` | 1474-1492 | Remove completed workflows from memory |

## Initialization

### SQLite Checkpointer (lines 49-93)

Built on sync `SqliteSaver` (not async) because each graph run uses a fresh event loop in a daemon thread while the API calls `get_state`/`update_state` from its own thread:

```python
class _LoopSafeSqliteSaver(SqliteSaver):
    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    # ... (alist, aput, aput_writes, adelete_thread)
```

The graph itself runs via `astream`, so async checkpoint methods are bridged to lock-serialized sync SQLite operations. Location: `data/checkpoints.db`.

If SQLite is unavailable, falls back to in-memory checkpointer (workflows will NOT survive restart).

## Resume Lifecycle (HITL Resume Trap)

### The Resume Trap (lines 1198-1404)

When a workflow is paused at a HITL gate, the graph is interrupted with `interrupt_before` — meaning the node body **never runs** for that gate. On resume, `resume_workflow()` must:

1. **Merge feedback into state** before calling `update_state(as_node=...)`
2. Apply **per-gate special handling**:
   - **mapping edits** (1228-1247): Validate against live schema via `_sanitise_entity_tables()`, rebuild `field_mappings`
   - **cluster decisions** (1308-1331): Apply `keep/split/reject` via `apply_cluster_decisions()` on spool data
   - **scope plan edits** (1301-1303): Merge user edits into `scope_plan`
   - **error gate** (1337-1353): Increment `gate_retry_counts`, populate `retry_hints`
3. **Retry count propagation** (1355-1368): Store reviewer's reason in `state["retry_hints"][producer_node]` for the LLM to see on retry
4. **Auto-approve logic** (1380-1392): If no edits and `auto_approve_gates` configured, skip the gate entirely

### `_build_interrupt_data()` (lines 663-840)

Synthesizes interrupt payloads when the node body hasn't run yet (the `interrupt_before` case). Mirrors the logic in `human_nodes.py` but builds the interrupt data on-the-fly from current state.

### `_finalize_stream_status()` (lines 367-506)

Post-stream status resolution after a graph stream ends. Distinguishes:
- **interrupted** — hit a HITL gate
- **completed** — reached END
- **failed** — hit handle_error with non-recoverable error
- **rejected** — user rejected at any gate
- **aborted** — external abort signal
- **auto-approved** — gate was auto-skipped

## Recovery After Restart

`rehydrate_workflow()` (lines 886-942) rebuilds in-memory workflow entries from the SQLite checkpoint after an app restart. It:
1. Lists all thread IDs from the checkpointer
2. Reads the last checkpoint for each thread
3. Reconstructs `workflow_entries` with status, gate, and timing data
4. Preserves partial results (extracted data in spool is still intact)

## Profile Capture

`_capture_profile()` (lines 508-524) writes decisions to a saved profile on clean completion, if `profile_name` was specified.

## Thread Safety

- Each workflow runs in a daemon thread with its own event loop
- The checkpointer uses a lock-serialized sync SQLite connection with `check_same_thread=False`
- Async checkpoint methods are bridged to sync via `asyncio.to_thread()`
- `_safe_state_values()` (lines 969-980) reads checkpoint state with failure handling

## Test Coverage

- `tests/unit/test_workflow_persistence.py` — checkpoint save/load integrity
- `tests/integration/test_workflow_manager.py` — full lifecycle (start/pause/resume/status)
- `tests/integration/test_human_in_the_loop.py` — HITL resume mechanics
