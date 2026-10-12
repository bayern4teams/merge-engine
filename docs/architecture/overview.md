[< back to docs/INDEX.md](../INDEX.md)

# merge-engine — Technical Reference

This is a from-scratch, read-it-and-understand-everything walkthrough of the
codebase: every subsystem, the key functions and variables in it, and —
wherever a real choice was made between two approaches — *why* the code went
one way and not the other. It is written to be read start to finish in one
sitting, not skimmed.

Scope note, honestly stated: this covers every subsystem and every
architecturally significant function. It does not annotate every private
helper or every line of every test file — those are readable directly, and
this document tells you where to find them and what the surrounding
decisions were. Where a decision is non-obvious, it's explained; where code
is simple and self-explanatory, it isn't padded with restatement.

---

## Table of contents

This walkthrough is split across `docs/architecture/*.md` by subsystem (Phase
0 Sec 0.4). Sections 0–1 are in this file; the rest are one click away.

0. [Where everything lives — the file map](#0-where-everything-lives--the-file-map) *(this file)*
1. [The mental model](#1-the-mental-model) *(this file)*
2. [MergeState — the single source of truth](state_and_graph.md#2-mergestate--the-single-source-of-truth)
3. [The graph: nodes, edges, and why routing is a function, not a table](state_and_graph.md#3-the-graph-nodes-edges-and-why-routing-is-a-function-not-a-table)
4. [The error taxonomy and why classification lives in one place](state_and_graph.md#4-the-error-taxonomy-and-why-classification-lives-in-one-place)
5. [The DuckDB spool — why rows left graph state](data_pipeline.md#5-the-duckdb-spool--why-rows-left-graph-state)
6. [Connectors and keyset pagination](data_pipeline.md#6-connectors-and-keyset-pagination)
6b. [Source analysis: catalog tables, ETL columns, inclusion dependencies, roles](data_pipeline.md#6b-source-analysis-catalog-tables-etl-columns-inclusion-dependencies-roles)
7. [Entity resolution: Fellegi–Sunter scoring, blocking, clustering](data_pipeline.md#7-entity-resolution-fellegi-sunter-scoring-blocking-clustering)
8. [Unification and conflict resolution](data_pipeline.md#8-unification-and-conflict-resolution)
9. [Field mapping and the destination schema](data_pipeline.md#9-field-mapping-and-the-destination-schema)
10. [Loading — transactional, and honestly not always](data_pipeline.md#10-loading--transactional-and-honestly-not-always)
11. [The LLM layer: client, prompts, cache, cost ledger](llm_and_profiles.md#11-the-llm-layer-client-prompts-cache-cost-ledger)
12. [Run profiles — pinning non-deterministic decisions](llm_and_profiles.md#12-run-profiles--pinning-non-deterministic-decisions)
13. [WorkflowManager — threads, resume, and the as_node trap](orchestration_and_ui.md#13-workflowmanager--threads-resume-and-the-as_node-trap)
14. [The FastAPI + React surface](orchestration_and_ui.md#14-the-fastapi--react-surface)
15. [Observability](orchestration_and_ui.md#15-observability)
16. [Testing strategy](testing_and_decisions.md#16-testing-strategy)
17. [Index of every design decision and its alternative](testing_and_decisions.md#17-index-of-every-design-decision-and-its-alternative)
18. [Known problems — what is actually wrong right now](known_problems.md#18-known-problems--what-is-actually-wrong-right-now)
19. [Future risks — what will break as this grows](known_problems.md#19-future-risks--what-will-break-as-this-grows)
20. [Ideas and features worth building next](known_problems.md#20-ideas-and-features-worth-building-next)

---

## 0. Where everything lives — the file map

Read this section first if you want to find something fast. Every path is
verified against the current tree, not remembered.

### The LLM: where calls are made and where prompts live

**Every LLM request in this entire project goes through one class**:
`LLMClient` in **`src/llm/client.py`**. Its three entry points are `.call()`
(plain text), `.call_json()` (structured/JSON response), and `.call_batch()`
(many prompts, one call site). The semantic cache and cost ledger hook in
inside `.call()`, which is why they cover 100% of LLM traffic automatically —
there is no second path that could bypass them.

There are exactly **five places** that construct an `LLMClient`, and they are
the five stages where this project uses an LLM at all:

| Stage | Call site | What it decides |
|---|---|---|
| Request parsing | `src/orchestrator/nodes/parse_request.py:40` | Which entity types the user asked for |
| Dependency analysis | `src/orchestrator/nodes/analyze_dependencies.py:110` | Table dependency order |
| Entity→table selection | `src/mcp_server/tools/select_entity_tables.py:190` | Which tables hold which entity (Phase K.5) |
| Entity resolution | `src/mcp_server/tools/resolve_entities.py:1091` | Borderline match adjudication only |
| Field mapping | `src/mcp_server/tools/map_fields.py:376` | Source field → destination column |

Note what is *not* in that list: nothing per-row. Every LLM call is
`O(schema)` or `O(borderline pairs)`, never `O(rows)` — that is the
deliberate boundary described in the project plan as "more LLM *influence*,
not more LLM *per row*."

**Where the prompts are now — answering your question directly.** They are
still plain `.txt` files in **`src/llm/prompts/`**, but the naming changed:
each stage is now a **pair** of files rather than one. The old single
`<name>.txt` files were deleted and replaced by:

```
src/llm/prompts/
  request_parsing.system.txt        request_parsing.human.txt
  dependency_analysis.system.txt    dependency_analysis.human.txt
  entity_table_selection.system.txt entity_table_selection.human.txt   ← new in Phase K.5
  entity_resolution.system.txt      entity_resolution.human.txt
  field_mapping.system.txt          field_mapping.human.txt
```

The split is meaningful, not cosmetic: the **`.system.txt`** file carries the
role, the rules, and the output contract (what JSON shape the model must
return); the **`.human.txt`** file carries few-shot examples plus the
placeholder template that live input gets substituted into. Keeping them
apart means you can change the examples a stage learns from without touching
the output contract that downstream Python code parses — the two change for
completely different reasons and at different rates.

They are loaded **once at import time** by **`src/llm/__init__.py`**
(`_PROMPTS_DIR` at line 20, `_STAGE_NAMES` at line 22) into a module-level
dict `PROMPT_TEMPLATES[stage]["system" | "human"]`. If you edit a prompt file,
you must restart the app — there is no hot reload.

⚠️ **A trap worth knowing about**: `_read_prompt()` returns an **empty string**
if the file doesn't exist, rather than raising. Rename or typo a prompt
filename and that stage silently runs with an empty system prompt instead of
failing — the LLM will still answer, just without any of its rules or output
contract. See §18.

### The graph and its nodes

| What | Where |
|---|---|
| Graph wiring (nodes, edges, gates, retry policy) | `src/orchestrator/graph.py` → `_build_builder()` (line 282) |
| `MergeState` TypedDict — the full state shape | `src/orchestrator/graph.py:392` |
| Initial state construction | `src/orchestrator/graph.py` → `get_initial_state()` (line 536) |
| Node implementations — one file per node | `src/orchestrator/nodes/*.py` |
| The four HITL gate nodes | `src/orchestrator/nodes/human_nodes.py` |
| Error escalation gate | `src/orchestrator/nodes/human_nodes.py` → `handle_error` |
| Preflight checks | `src/orchestrator/nodes/preflight.py` |
| Run lifecycle, resume, thread tracking | `src/orchestrator/workflow_manager.py` |

The heavy logic is deliberately **not** in the node files. A node is a thin
async adapter that reads state, calls an implementation function in
`src/mcp_server/tools/`, and returns a state update. That separation is what
lets the tools be unit-tested without constructing a graph or a state dict at
all — e.g. `resolve_entities_impl()` is called directly by hundreds of tests.

### The real work — implementation functions

| Subsystem | Where |
|---|---|
| Schema discovery | `src/mcp_server/tools/discover_schema.py` |
| Catalog / ETL-metadata classification (P12) | `src/mcp_server/tools/metadata_classification.py` |
| Join graph (inclusion dependencies, row relationships) | `src/mcp_server/tools/join_graph.py` |
| Extraction (batching, spooling, watermarks) | `src/mcp_server/tools/extract_data.py` |
| Entity resolution: token blocking, scoring, clustering | `src/mcp_server/tools/resolve_entities.py` |
| Fellegi–Sunter probability model | `src/mcp_server/tools/fellegi_sunter.py` |
| Label-free cluster quality signals (P16) | `src/mcp_server/tools/cluster_quality.py` |
| Cross-run memory of band decisions (P9) | `src/orchestrator/decision_memory.py` |
| Per-cluster merge + conflict survivorship | `src/mcp_server/tools/unify_entity_group.py` |
| Field mapping | `src/mcp_server/tools/map_fields.py` |
| Destination write | `src/mcp_server/tools/load_data.py` |
| Database drivers | `src/mcp_server/connectors/{postgres,clickhouse,mongodb}.py` |
| Keyset pagination helpers | `src/mcp_server/connectors/keyset.py` |
| Incremental-run watermarks | `src/mcp_server/connectors/watermark.py` |
| DuckDB row spool | `src/orchestrator/spool.py` |
| Saved run profiles | `src/orchestrator/profiles.py` |
| Error taxonomy + classification | `src/orchestrator/utils/retry.py` |

### Configuration — the four places settings come from

1. **`config/config.yaml`** — the main file. LLM provider/model/`base_url`/
   temperature, the feature flags `entity_resolution_enabled` and
   `field_mapping_enabled`, retry policy (`max_attempts`, backoff bounds),
   extraction batch sizes, and destination *defaults* for the UI form.
   **No credentials belong here** — it uses `${ENV_VAR}` placeholders.
2. **`.env`** — the actual secrets: `LLM_API_KEY`, and the `LANGFUSE_*` keys.
   Template in `.env.example`.
3. **The UI at runtime** — all origin and destination connections. These are
   deliberately never in a config file; they're entered per-session and
   cached in `config/recent_connections.json` (see the warning in §18).
4. **`config/profiles/*.json`** — saved run profiles (§12), which pin the
   four LLM decisions but never credentials.

The loader is **`src/utils/config_loader.py`**: `load_config()` (line 160)
reads the YAML, `_resolve_env_vars()` (line 128) substitutes `${VAR}`
placeholders from the environment (with `.env` already loaded at module
import), and the result is validated through Pydantic models defined in the
same file. `_DEFAULT_CONFIG_PATH` (line 157) is where `config/config.yaml`
is hardcoded as the default.

### Entry points and the web layer

| What | Where |
|---|---|
| Process entry point / CLI args | `src/main.py` → `main()`, `parse_args()` |
| HTTP API — all routes | `src/api/main.py` |
| React UI source | `src/ui/frontend/src/` (`App.tsx`, `api.ts`, `components/`) |
| Built UI bundle FastAPI serves | `src/ui/frontend/dist/` |
| Connection history persistence | `src/ui/connections.py` |
| Schema-discovery UI helper | `src/ui/components/db_discovery.py` |
| Logging setup (incl. the Windows UTF-8 fix) | `src/utils/logging_config.py` |
| Langfuse tracing wiring | `src/llm/tracing.py` |

### Where runtime state accumulates on disk

| Path | What | Size right now |
|---|---|---|
| `data/checkpoints.db` | LangGraph SQLite checkpointer — every run's state | 6.5 MB + 4 MB WAL |
| `logs/spool/*.duckdb` | Per-run extracted rows | **3.4 GB** |
| `logs/run_reports/*.json` | Reconciliation report per run | 56 files |
| `logs/debug.log`, `logs/app.log` | Application logs | **201 MB**, 53 MB |
| `config/recent_connections.json` | Connection history — **includes passwords** | small |

---

## 1. The mental model

merge-engine is a **LangGraph state machine** wrapped by a **FastAPI HTTP
API** wrapped by a **React UI**. The graph is the actual product; the API and
UI exist to drive it and show a human its four checkpoints.

A "run" is one execution of the graph, identified by a `thread_id` (LangGraph
vocabulary) which this codebase also calls `run_id` interchangeably — you'll
see both names for the same string. Each run is checkpointed to SQLite after
every node, which is the only reason a human can walk away from a review gate
and come back an hour (or a day) later and have the run still be exactly
where they left it.

The four things a human is asked to approve, in order:

1. **Which tables hold which business entity** (`human_review_deps`) — e.g.
   "customer in Postgres and users in Mongo are both the `person` entity."
2. **Which records the matcher decided are the same real-world entity**
   (`human_review_matches`) — only shown when the matcher produced
   low-confidence clusters or uncertain pairs (the 0.70-0.95 band) worth a
   second look; the operator can decide those pairs one by one.
3. **The field mapping to the destination schema**
   (`human_review_mappings`) — only shown when fields couldn't be mapped
   with high confidence.
4. **Final approval before anything is written** (`human_final_approval`) —
   always shown, no matter how confident everything upstream was. It also
   shows the destination tables that cannot hold their data and the pre-load
   invariants; thresholds confirmed here are enforced, and a violation refuses
   the load (nothing is written).

Everything else in this document is either "how the graph gets from one of
these gates to the next" or "what happens around the graph to make it usable
and observable."

---
