[< back to docs/INDEX.md](../INDEX.md)

# WorkflowManager, the API/UI surface, and observability

## 13. WorkflowManager — threads, resume, and the as_node trap

`src/orchestrator/workflow_manager.py`. This is the layer between the FastAPI
routes and the compiled LangGraph graph: it owns `start_workflow()`,
`resume_workflow()`, `get_workflow_status()`, and the in-memory
`_active_workflows` dict tracking which threads are currently known to this
process (the same dict whose emptiness-after-restart caused the spool
deletion bug in §5).

**A run executes on a background thread with its own event loop**, because
LangGraph's `stream()` is synchronous-blocking from the caller's point of
view but the app itself is async (FastAPI); the workflow manager tears the
event loop down between HITL pauses rather than holding one open indefinitely
per run — which is exactly why `RunSpool` (§5) is designed to be opened
per-operation rather than held open across the life of a run: a DuckDB
handle surviving past its owning event loop's teardown is how a run would
end up reading from an already-closed database file.

**The `as_node=` resume trap.** LangGraph's `update_state(..., as_node=X)`
resumes execution *as if node X had just produced the given update*, which
means **X's own function body never actually runs on that resume** — only
its outgoing edges are evaluated against the injected state. This is
correct and intentional for applying a human's edits at a review gate (the
gate node's own body is a no-op review step; the interesting work already
happened in the producing node before the interrupt). It is a real trap if
it's ever assumed that resuming "at" a node re-executes that node's logic —
it does not, and `handle_error`'s body specifically never runs on resume for
this reason (documented in project memory as an open gotcha to remember when
touching error-gate resume logic).

**Cost/profile capture happens *outside* the state lock, deliberately.**
`_finalize_stream_status()` determines `capture_profile` while holding the
workflow-tracking lock, but the actual `_capture_profile()` call (file I/O:
load profile, merge state, save) happens after releasing it — so a
profile-save failure can never block or corrupt the lock that every other
concurrent request needs to read workflow status.

---

## 13.5 Graph node order, and the undocumented `map_fields` move

`src/orchestrator/graph.py`'s `NODE_SUCCESSOR` dict is the single source of
truth for pipeline order; `HITL_NODES`, `GATE_PRODUCER`, `REVIEW_GATES`
(derived as `HITL_NODES[:-1]`) and every conditional edge are all driven from
it, so the topology below is exact, not aspirational:

```
preflight -> discover_schemas -> parse_request -> analyze_dependencies
  -> human_review_deps -> map_fields -> [human_review_mappings if unmapped fields]
  -> plan_scope -> [human_review_scope if merge_scope.active and scope_plan.entities]
  -> extract_data -> validate_data -> resolve_entities -> [human_review_matches if low-confidence]
  -> unify_entities -> human_final_approval -> load_data -> update_extraction_state -> END
```

Three things about this order are easy to get wrong from memory, because it
changed three times and only two of those changes were ever written down
before now:

1. **`discover_schemas` runs before `parse_request`**, not after. This was a
   deliberate Phase 9 change (`docs/plans/CHANGE_NODE_ORDER_COVERAGE_MATRIX.md`)
   so entity selection can be grounded in the schemas that actually exist
   rather than an LLM guessing blind.
2. **`map_fields` runs before `extract_data`**, not after `unify_entities`.
   This shipped in the same commit as (1) but was never called out in that
   plan document — it rode along undocumented. It is supported by
   `map_fields_node.py`'s `_build_source_schema_from_discovered`, an
   explicitly "run before extraction, from discovered schemas rather than
   unified records" code path — so it is not a bug, but it does mean
   `map_fields` no longer has `state["unified_entities"]` to work from at
   all; its field-mapping decisions are made against raw discovered columns,
   before a single row has been extracted.
3. **`validate_data` runs between `extract_data` and `resolve_entities`**
   (Phase 4.5, salvaged from the `B3-quality-lineage` branch). It is a thin,
   non-fallible node — a hard `row_count >= 1` / uniqueness / not-null / date
   check per entity, never Great Expectations despite an earlier draft of
   its docstring claiming otherwise (`src/orchestrator/nodes/validate_data.py`,
   `src/mcp_server/tools/data_quality.py`). It never auto-aborts the run; a
   `fail`-severity result is surfaced at `human_final_approval` as
   `quality_report`/`has_quality_failure` for a human to see and decide on.

All three changes mean the mapping gate (`human_review_mappings`) now happens
*before* the scope gate (`human_review_scope`) and long before any data
exists — worth remembering before assuming gate order matches when each
kind of information first becomes available.

---

## 14. The FastAPI + React surface

`src/api/main.py` (backend), `src/ui/frontend/` (React + TypeScript + Vite,
built to `dist/` and served by FastAPI's `StaticFiles` mount).

Routes, by purpose:

| Route | Purpose |
|---|---|
| `POST /api/connections/test` | Live-test an origin/destination connection and discover its schema |
| `GET/POST /api/connections/recent` | Locally persisted connection history (`config/recent_connections.json`) |
| `POST /api/workflow/start` | Start a run; accepts `profile_name` (replay a saved profile) and/or `save_as_profile` (save this run's config as a new profile before starting) |
| `GET /api/workflow/active` | Currently tracked runs |
| `GET /api/workflow/{id}/status` | Poll status — the UI's 2-second timer target |
| `GET /api/workflow/{id}/cost` | Token ledger + semantic-cache stats for this run (Phase O.5) |
| `POST /api/workflow/{id}/feedback` | Submit a HITL decision (`approve`/`edit`/`retry`/`abort`, plus any edits/reason) |
| `POST /api/workflow/{id}/abort` | Abandon a run |
| `GET/POST/DELETE /api/profiles[/{name}]` | Saved-profile CRUD |

**Why the UI polls rather than uses a websocket/SSE push.** A run can be
paused at a HITL gate for an arbitrary, human-timescale duration (minutes to
days) — there is no ongoing stream of events to push during that time, only
a state that occasionally changes. A 2-second poll against `/status` is
simpler to reason about, survives a browser refresh with zero reconnection
logic, and costs nothing extra during the (common) long idle stretches at a
gate.

**Why the frontend is a built, static bundle served by FastAPI rather than a
separate dev-server process in production.** One process to start
(`python -m src.main`), one port, no CORS configuration needed between a
separately-hosted frontend and backend, and no risk of the frontend serving
stale API assumptions against a backend that moved on — they ship and
version together as one artifact. `npm run build` regenerates `dist/`; the
backend never rebuilds it automatically, which is a deliberate line between
"code changed" and "deployed" — an accidental silent rebuild-on-boot could
serve unreviewed frontend changes.

---

## 15. Observability

Self-hosted [Langfuse](https://langfuse.com) (`docker/langfuse-compose.yml`),
wired via `src/llm/tracing.py`. Every LangGraph node maps to a `CHAIN` span,
every LLM call maps to a `GENERATION` span, and both are keyed by the run's
thread ID so a trace in the Langfuse UI corresponds exactly to one merge run
end to end — including every HITL pause, since the span tree persists across
the same checkpointed state the graph itself uses.

**Why self-hosted rather than a hosted SaaS tracer.** This project went
through two prior tracer choices before landing here (Langfuse's own
dashboard was chosen after a from-scratch LangSmith integration was fully
removed earlier in the project's history) — the durable reason documented in
project memory is that a fully local stack keeps trace data (which can
include prompt/response content derived from a user's real database rows)
off any third party's infrastructure by default, which matters more for this
tool than for a typical SaaS app given what it's built to process.

---

