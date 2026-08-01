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

0. [Where everything lives — the file map](#0-where-everything-lives--the-file-map)
1. [The mental model](#1-the-mental-model)
2. [MergeState — the single source of truth](#2-mergestate--the-single-source-of-truth)
3. [The graph: nodes, edges, and why routing is a function, not a table](#3-the-graph-nodes-edges-and-why-routing-is-a-function-not-a-table)
4. [The error taxonomy and why classification lives in one place](#4-the-error-taxonomy-and-why-classification-lives-in-one-place)
5. [The DuckDB spool — why rows left graph state](#5-the-duckdb-spool--why-rows-left-graph-state)
6. [Connectors and keyset pagination](#6-connectors-and-keyset-pagination)
7. [Entity resolution: Fellegi–Sunter scoring, blocking, clustering](#7-entity-resolution-fellegi-sunter-scoring-blocking-clustering)
8. [Unification and conflict resolution](#8-unification-and-conflict-resolution)
9. [Field mapping and the destination schema](#9-field-mapping-and-the-destination-schema)
10. [Loading — transactional, and honestly not always](#10-loading--transactional-and-honestly-not-always)
11. [The LLM layer: client, prompts, cache, cost ledger](#11-the-llm-layer-client-prompts-cache-cost-ledger)
12. [Run profiles — pinning non-deterministic decisions](#12-run-profiles--pinning-non-deterministic-decisions)
13. [WorkflowManager — threads, resume, and the as_node trap](#13-workflowmanager--threads-resume-and-the-as_node-trap)
14. [The FastAPI + React surface](#14-the-fastapi--react-surface)
15. [Observability](#15-observability)
16. [Testing strategy](#16-testing-strategy)
17. [Index of every design decision and its alternative](#17-index-of-every-design-decision-and-its-alternative)
18. [Known problems — what is actually wrong right now](#18-known-problems--what-is-actually-wrong-right-now)
19. [Future risks — what will break as this grows](#19-future-risks--what-will-break-as-this-grows)
20. [Ideas and features worth building next](#20-ideas-and-features-worth-building-next)

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
| Extraction (batching, spooling, watermarks) | `src/mcp_server/tools/extract_data.py` |
| Entity resolution: blocking, scoring, clustering | `src/mcp_server/tools/resolve_entities.py` |
| Fellegi–Sunter probability model | `src/mcp_server/tools/fellegi_sunter.py` |
| Per-cluster merge + conflict survivorship | `src/mcp_server/tools/unify_entity_group.py` |
| Field mapping | `src/mcp_server/tools/map_fields.py` |
| Destination write | `src/mcp_server/tools/load_data.py` |
| Database drivers | `src/mcp_server/connectors/{postgres,clickhouse,mongodb,databricks}.py` |
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
   low-confidence clusters worth a second look.
3. **The field mapping to the destination schema**
   (`human_review_mappings`) — only shown when fields couldn't be mapped
   with high confidence.
4. **Final approval before anything is written** (`human_final_approval`) —
   always shown, no matter how confident everything upstream was.

Everything else in this document is either "how the graph gets from one of
these gates to the next" or "what happens around the graph to make it usable
and observable."

---

## 2. MergeState — the single source of truth

`src/orchestrator/graph.py` defines `MergeState`, a `TypedDict`. Every node
reads a subset of it and returns a partial dict of updates; LangGraph merges
that partial dict into the running state according to each field's
**reducer**. Understanding reducers is the single most important thing to
understand about this codebase, because getting one wrong either silently
loses data or silently duplicates it.

A field with no reducer annotation gets LangGraph's default: **the new value
replaces the old one.** A field annotated `Annotated[T, operator.or_]` (dicts)
or `Annotated[T, operator.add]` (lists) gets **merged/appended** instead.

Why this matters, concretely:

- `entity_clusters`, `resolution_stats`, `unified_entities`, `field_mappings`,
  `unmapped_fields`, `extraction_watermarks` are all `operator.or_`. These are
  all keyed by entity type, and different nodes (or the same node called
  across a HITL resume) contribute different entities' worth of data over
  time. A plain replace reducer would let a later partial update for entity
  B silently erase entity A's already-computed result.
- `errors` and `handled_failures` are `operator.add` — an audit trail that
  must accumulate, never be overwritten.
- `failure` (singular, no reducer — replace semantics) is **deliberately
  not** an accumulating field. It represents "the one open failure right
  now"; a human dispositions it and it must become clearable (`{}`), which
  an `operator.or_` merge could never express (merging `{}` into an existing
  dict is a no-op, not a clear).
- `pending_schema_changes` is a plain list with **replace** semantics for the
  same reason — the mapping review gate needs to be able to *remove*
  cancelled proposals on resume, and `operator.add` can only ever grow a
  list, never shrink it.

**Two IDs to keep straight:** `run_id` names the LangGraph thread AND the
spool file AND the cost-ledger key AND the profile-capture key. It rides
inside `llm_config["run_id"]` specifically because the LLM client only ever
sees `llm_config`, not the top-level state — an early version of the cost
ledger keyed on a constant `session_name` string instead and every run
reported zero LLM calls until this was fixed (see §11).

`get_initial_state()` has two call paths: the UI path (given `origins` +
`destination` dicts directly) and a legacy `Config`-object path used by
tests and the CLI. Both build the exact same dict shape — this symmetry is
deliberate, so a test using the legacy path exercises identical downstream
code to a real UI-driven run.

---

## 3. The graph: nodes, edges, and why routing is a function, not a table

`src/orchestrator/graph.py` : `_build_builder()` wires 15 nodes. The full
linear path when nothing needs review:

```
preflight → parse_request → discover_schemas → analyze_dependencies
  → [human_review_deps] → extract_data → resolve_entities → unify_entities
  → map_fields → [human_final_approval] → load_data → update_extraction_state → END
```

`resolve_entities` and `map_fields` have **conditional** edges that only
detour to a review gate when there's something worth reviewing
(`has_low_confidence_clusters`, `has_unmapped_fields`) — a clean, unambiguous
run never bothers a human with the matches or mapping gates, but always
bothers them with dependency confirmation and final approval.

### Why every router is a closure over string names, not a lookup table

`_gate_router(gate, forward)` and `_fallible_router(node, forward)` are
**factories** that return a routing function, rather than one big `if/elif`
over every node name. Two reasons:

1. LangGraph's `add_conditional_edges` wants a callable per node; building
   fifteen near-identical inline lambdas is far more error-prone than
   building one parameterised closure and calling it fifteen times with
   different arguments.
2. `route.__name__ = f"route_{gate}"` is not cosmetic — LangGraph and
   LangGraph Studio use function names in their visualisation and logging,
   and an anonymous `<lambda>` shows up as indistinguishable noise when you
   have five of them.

### `NODE_SUCCESSOR` and `GATE_PRODUCER` — two small dicts doing a lot of work

`NODE_SUCCESSOR` answers "if this node's output were simply accepted as
final, where would the run go next?" It exists so the **error gate's "skip"
decision** and the **retry-loop bookkeeping** don't have to duplicate the
main edge topology — they look it up instead of re-encoding it. If you ever
add a node to the linear path, this dict (not the edges) is where a
skip-target bug would come from — add it here too.

`GATE_PRODUCER` answers "which node made the thing this gate is reviewing?"
so `retry` at a gate can route backward. Only three gates have a producer —
`human_final_approval` doesn't, because there's no upstream node whose retry
would change a final-approval decision; retrying it just re-attempts the
load.

### Two failure-routing layers that look similar but aren't

`_fallible_router` and `_gate_router` both return `"abort"` as one of their
outcomes, but they answer different questions:

- `_fallible_router` asks: *did the node that just ran record an unhandled
  `failure`?* (a database went unreachable, a table returned fewer rows than
  it claimed). This is **system state**, checked automatically after every
  fallible node.
- `_gate_router` asks: *what did the human just decide?* This is **user
  input**, checked only at the four HITL gates.

They're composed, not merged, because conflating "the system detected a
problem" with "the human said no" would make the error gate's audit trail
(`handled_failures`) mean two different things depending on which path led
there — and Phase M's whole design goal was that a failure always produces a
structured, inspectable record of what happened and why.

### `MAX_GATE_RETRIES = 3` and why it exists at both layers

Both the gate router and the error-gate router independently cap retries at
3 attempts (`gate_retry_counts` and `retry_counts` respectively — two
separate counters, because a gate retry and a node-failure retry are
different events that can both happen to the same node in the same run).
Without a cap, a user who keeps clicking "retry" with an unhelpful reason
would loop the graph forever; LangGraph has no built-in protection against
an infinite conditional-edge cycle, so this had to be hand-rolled.

### `interrupt_before=HITL_NODES`

The five HITL node names (including `handle_error`) are passed to
`builder.compile(interrupt_before=...)`. This is LangGraph's actual
human-in-the-loop primitive: the graph *literally stops executing* before
entering one of these nodes and returns control to the caller, and only
resumes when `WorkflowManager.resume_workflow` calls
`graph.update_state(...)` followed by another `stream()` call. Nothing about
this is polling or a fake pause — the Python call stack genuinely returns.

### Two compile paths, one intentional divergence

`build_graph(checkpointer=None)` is used by `WorkflowManager` for in-process
runs — it accepts (and needs) a real checkpointer (SQLite in production,
`MemorySaver` in tests) so state survives between HTTP requests.
`_get_server_graph()` / the module-level `graph` export is used **only** by
the LangGraph CLI dev server (`langgraph dev`, used for Studio visualisation)
which manages its own persistence and explicitly forbids a custom
checkpointer being passed in. These are not redundant — they're two
different hosts for the same graph definition, and conflating them was an
actual historical bug (see `merge-engine-hitl-resume-quirks` in project
memory: HITL node bodies never run on resume via `as_node=`, because the CLI
dev server's resume semantics differ from the in-process path's).

---

## 4. The error taxonomy and why classification lives in one place

`src/orchestrator/utils/retry.py` defines four exception classes, each
carrying a `disposition`:

| Class | Disposition | Meaning |
|---|---|---|
| `TransientError` | `retry` | Network blip, timeout, rate limit — the identical call is expected to eventually succeed. |
| `PermanentError` | `fail` | Bad SQL, refused auth, missing table — retrying wastes time and hides the real cause. |
| `DataQualityError` | `escalate` | The pipeline **could** continue, which is exactly why it must not do so silently — a human decides. |
| `UserInputError` | `fail` | The request/config itself is unusable — the fix is the user's, not the pipeline's. |

**Why `classify_exception()` exists as a single funnel function** rather than
letting each call site decide: database drivers each raise their own
exception hierarchy (`psycopg.OperationalError`, `pymongo.AutoReconnect`,
whatever `clickhouse_connect` throws), and the same underlying network blip
surfaces as a different type depending on which driver you're in. Rather
than writing (and maintaining, and forgetting to update) a `except (A, B, C,
D, E):` clause at every call site, one function inspects the exception's
*text* against two marker tuples (`_TRANSIENT_MARKERS`,
`_PERMANENT_MARKERS`) and returns a classified error. This is deliberately a
**substring match on the stringified exception**, not a type check — it's
the only signal that's portable across drivers that don't share a base
class.

**Permanent wins ties on purpose.** If a message matches both lists (it
won't, in practice, but the code is explicit about which wins if it ever
did), permanent wins, because "permission denied" appearing inside a
connection-refused-shaped message is a credentials problem no amount of
retrying fixes.

**Unrecognised exceptions default to `PermanentError`, not `TransientError`.**
This was a deliberate call, stated in the code's own comment: an unknown
failure that gets retried three times *looks like* flakiness (masking a real
bug behind a delay), while one that stops immediately gets read and fixed.
Defaulting the unknown case to "retry" would have been the more forgiving
choice and was rejected for that reason.

**`retry_on_transient` needed `functools.wraps` the hard way.** This
decorator (used to wrap connector `extract()` methods) originally didn't
preserve the wrapped function's signature. `extract_data.py` checks
`inspect.signature(connector.extract)` to decide whether to pass
`pk_columns` through (`_accepts_pk_columns`) — without `@functools.wraps`,
`inspect.signature` saw the decorator's own `(*args, **kwargs)` instead of
the real parameter list, so that check silently always returned `False` and
keyset pagination never activated on *any* connector, on *any* run, ever.
Nothing raised or logged — the pipeline just always fell back to slower
OFFSET pagination. This is the textbook argument for
`functools.wraps`: it's invisible until something introspects the wrapper,
and then it's catastrophically wrong.

`RetryPolicy` (LangGraph's own primitive, `NETWORK_RETRY` in `graph.py`) is
attached per-node at graph-build time rather than via this decorator for
anything that runs *as a graph node* — `retry_on_transient` remains in use
only where a function is called outside the graph's own retry machinery
(inside connector internals). Two retry mechanisms coexisting is not
redundancy for its own sake: LangGraph's `RetryPolicy` retries the whole node
(including any side effects before the failure point), while
`retry_on_transient` retries a much narrower unit of work.

---

## 5. The DuckDB spool — why rows left graph state

`src/orchestrator/spool.py`. Before Phase N, every extracted row was
appended to a Python list living directly in `MergeState.extracted_data`,
which LangGraph serialises into a SQLite checkpoint **after every single
node**, and again on every HITL pause/resume cycle. That makes both peak
memory and checkpoint size scale with the size of the *source data* — a
43-million-row table cannot be held in a Python list at all, and even a
2,284-record test merge measured a checkpoint that was 69.6%
`unified_entities` and 28.2% `entity_clusters` by size.

**The fix is a handle, not a smaller list.** `RunSpool` writes rows to
`logs/spool/<run_id>.duckdb`; state holds only
`{"row_count": N, "run_id": ..., "entity": ..., "kind": "rows"}` — a
constant-size dict regardless of how many rows that represents. Every
consumer goes through an accessor function
(`entity_rows`, `entity_row_count`, `entity_origin_counts`,
`entity_records_by_id`, `clusters_for`, `unified_for`) that transparently
handles **both** shapes: a spool handle, or a plain inline list. This
dual-shape design is what let most existing unit tests keep passing
unmodified — they hand rows in as plain lists, and only live/scale runs
actually touch a spool file.

**Rows are stored as JSON in one `payload` column, not as typed SQL
columns.** Different origins disagree about schema *by definition* — that's
the entire point of a merge tool — so a fixed relational schema for spooled
rows would need a migration every time a new origin contributed a new field.
DuckDB reads JSON natively and can still project fields out of it in SQL
(used by the Phase N.3 blocking/scoring queries), so this loses nothing
while staying schema-agnostic.

**`_bulk_insert` — the 1,300x fix.** The obvious way to insert many rows into
DuckDB is `executemany`; measured directly, inserting 50,000 rows with a JSON
payload column that way took **259 seconds** (~193 rows/s) because each row
pays its own JSON parse and bind. At that rate the real 43M-row table would
have taken over 60 hours and the extraction looked simply hung. The fix:
register a pandas DataFrame as a DuckDB virtual table
(`con.register(...)`) and run `INSERT INTO ... SELECT * FROM
_incoming_rows` — the same 50,000 rows in **0.19 seconds**. The slow
row-by-row path is kept only as a fallback if pandas is missing, so a
stripped environment degrades gracefully instead of breaking.

**`append()` verifies its own write, and this is not paranoia.** After every
`_bulk_insert`, `append()` queries `SELECT count(*)` on the table and
compares it against `self._written[entity_type] + len(batch)` — an
expectation the instance carries in memory, not derived from the file. This
is the fix for the single worst bug this project found: `cleanup_old_spools`
(described next) once deleted a spool file **while a different run was still
actively appending to it**, silently recreating an empty file underneath the
running extraction. Every subsequent append then "succeeded" against the
fresh empty table, and the run reported success having stored 19,076,150 of
43,276,150 rows. Critically, comparing against `MAX(seq)+1` *from the file
itself* would not have caught this — a wiped table restarts at seq 0 and
looks perfectly self-consistent from the inside. Only an expectation
carried from **outside** the file (this instance's own running total) can
detect that the file was swapped out underneath it.

**`cleanup_old_spools` — age is the primary guard now, not membership.** The
original design deleted every spool file whose `run_id` wasn't in the
caller's `keep_run_ids` set, and that set came from `_active_workflows` — an
**in-memory** dict that is empty after any app restart. So the bug above
wasn't a test artifact: restart the app while any run is sitting at a HITL
gate, start one new unrelated merge, and the parked run's spool — the exact
rows a human is about to come back and approve — gets swept. The fix
inverts the priority: a file touched within the last 6 hours
(`_SPOOL_MIN_AGE_SECONDS`) is **never** deleted, regardless of whether its
run_id is recognised; membership in the keep-set is only an *extra*
reprieve for files that are already old enough to be eligible.

---

## 6. Connectors and keyset pagination

`src/mcp_server/connectors/`: `postgres.py`, `clickhouse.py`, `mongodb.py`,
`databricks.py`, `base.py` (the abstract interface), plus two shared helper
modules: `keyset.py` and `watermark.py`.

**Why keyset (seek) pagination instead of `OFFSET`/`LIMIT`.** `OFFSET`
pagination makes the database re-scan and discard every already-returned row
on every page — cost grows with how deep into the table you are, not with
the page size. Keyset pagination instead carries a cursor
(`WHERE <key> > :last_seen ORDER BY <key> LIMIT :n`) so every page costs the
same regardless of depth. Measured directly on the real cell_towers table:
an aligned key stayed flat around 7 seconds per batch through the whole run;
a misaligned key (one that didn't match the table's physical `ORDER BY`)
grew 7.07s → 11.82s → 14.58s over just the first 17% of the table — the
exact OFFSET-shaped curve keyset pagination exists to remove. **The
practical rule this taught: always paginate on the key the schema-discovery
step actually reports as the table's sort order, never a hand-picked
"natural" key that merely looks unique.**

**Non-unique keys silently drop rows unless you compensate, and this was
measured, not assumed.** A naive keyset implementation uses a strict `>`
predicate at the batch boundary. If the ordering key isn't unique, every row
sharing the boundary value with the last row of the previous batch is
permanently excluded — nobody's `>` ever includes it again. Measured on a
synthetic 300,000-row table with a 3-column, 144-distinct-value key: **33%
of rows were silently lost, and the run reported success.** The fix
(`keyset.py`): the predicate becomes `>=`, and a `boundary_seen` count rides
in the cursor recording how many rows at that exact boundary value the
previous batch already returned, so the next batch knows to
`drop_consumed()` that many matching rows from its own front before
returning the rest. A **second**, subtler bug surfaced fixing the first one:
with `>=` and no further tiebreak, PostgreSQL doesn't guarantee equal-key
rows come back in the same order across two separate queries — it duplicated
3 of 16,044 real Pagila rows (counts matched, contents didn't). The fix
adds a deterministic tiebreak column to the `ORDER BY`: `md5(row::text)` for
Postgres, `cityHash64(*)` for ClickHouse.

**PostgreSQL's `PRIMARY KEY` can be trusted for uniqueness. ClickHouse's
declared "primary key" cannot, and the code treats them differently on
purpose.** `information_schema.table_constraints` in Postgres is a real
uniqueness guarantee enforced by the database. ClickHouse's
`system.columns.is_in_primary_key` is merely the sparse **sorting** key used
for its MergeTree index — it guarantees nothing about uniqueness. This was
verified directly against the real dataset used in this project: the
cell_towers table has 43,276,150 rows for only ~42.97 million distinct
5-column key tuples. Consequently:
- `postgres.py` computes `keyset_is_unique(keyset, pk_columns)` and, when
  true, takes a faster strict-`>` path — but still calls
  `assert_key_unique()` on every batch and raises `DataQualityError` if a
  key declared unique turns out not to be, rather than trusting the
  declaration and losing rows.
- `clickhouse.py` **never** takes that fast path at all — the
  `keyset_is_unique`/`assert_key_unique` machinery was deliberately removed
  from this file after the measurement above, not merely left unused. It
  always uses `>=` + `drop_consumed` + the `cityHash64(*)` tiebreak.

**`watermark.py`** is a separate concern from keyset pagination: it tracks,
per `{origin_id}:{table}`, the last value seen in a designated watermark
column (usually a timestamp or auto-increment ID) so a *subsequent run* of
the same merge only extracts rows that arrived since the last run — this is
what makes incremental re-runs cheap. It is written to
`merge_engine_extraction_state` by `update_extraction_state_node`, and only
after a successful load, so a failed run never advances a watermark past
data that didn't actually land.

---

## 7. Entity resolution: Fellegi–Sunter scoring, blocking, clustering

`src/mcp_server/tools/resolve_entities.py` orchestrates; `fellegi_sunter.py`
holds the scoring model. This is the subsystem the user explicitly called
out as the highest-consequence one to get right ("over-merging is the worse
error").

### Why Fellegi–Sunter, not similarity averaging

The original scorer averaged string similarities (Jaccard + `SequenceMatcher`)
and compared the result to two hand-picked constants (0.95 auto-match, 0.70
auto-exclude). That has no way to express the single most useful fact in
record linkage: **agreeing on a rare value is much stronger evidence than
agreeing on a common one.** Two people both named "Rory" is nearly worthless
evidence if 248 people in the table share that first name; two records with
the same rare surname is strong evidence, but a naive average can't tell the
difference between the two agreements.

Fellegi–Sunter formalises this. For each field, the model estimates:

```
m = P(field agrees | records ARE the same entity)
u = P(field agrees | records are NOT the same entity)
```

Each comparison contributes `log2(m/u)` bits of evidence when it agrees, or
`log2((1-m)/(1-u))` when it disagrees; per-field contributions sum into a
total match weight, which converts to a probability via a sigmoid
(`_sigmoid_bits`). This is exactly the arithmetic in `MatchModel.score()` and
`FieldModel.weight()`.

**`u` is where term frequency enters, and this is the actual point of the
whole subsystem.** For an *exact* agreement, `FieldModel.weight()` swaps in
the value's own observed frequency as `u` instead of the field's average
disagreement rate: `u = term_frequency.get(value)`. A value shared by 20% of
records has a correspondingly small `log2(m/u)` contribution; a value unique
in the dataset has a very large one. `build_term_frequencies()` computes this
once per entity type across every record, not once per pair — recomputing it
per pair would make the whole scoring pass quadratic in the wrong dimension.

**m and u are learned unsupervised, via a small EM loop
(`estimate_model`), not hand-tuned.** No labelled training pairs are
required. Starting from conservative priors (`_PRIOR_M`, `_PRIOR_U`), each
iteration (1) scores every candidate pair with the current model to get a
"responsibility" (how much this pair looks like a match), then (2)
re-estimates each comparison level's m/u weighted by those responsibilities,
mixed with the priors at a fixed weight (`_PRIOR_WEIGHT = 20.0`) so a small
or unlucky batch of candidate pairs can't swing the model to something
absurd. `_PRIOR_MATCH_RATE = 0.10` is the assumed base rate of true matches
among candidate pairs before any estimation — a prior, not a measured
constant, chosen conservatively.

**Comparison *levels*, not just agree/disagree.** `LEVEL_EXACT`,
`LEVEL_STRONG` (minor typo/formatting difference), `LEVEL_WEAK` (loosely
similar), `LEVEL_DISAGREE`, and `LEVEL_NULL` (one side missing — the field
contributes *zero* evidence, deliberately, rather than being coerced into
"disagree"; a missing value tells you nothing about whether two records
match). This preserves the pre-existing string-comparison logic
(`_has_conflicting_identity`, `_PARTIAL_NAME_FIELDS`, the fuzzy comparators)
as *inputs* to the probabilistic model instead of throwing that work away —
they now decide which level a comparison falls into, and Fellegi–Sunter
decides how much that level is worth.

**Declared unique identifiers get a real veto, and this needed a second,
separate wiring fix to actually apply everywhere.** `_has_conflicting_identity`
checks whether two records disagree on a field the caller has declared a
unique identifier (either via a hardcoded substring heuristic,
`_UNIQUE_IDENTITY_HINTS`, or explicitly via `matching_rules.unique_id_fields`,
Phase O.2). The first wiring only threaded `extra_unique_ids` through
`_score_pair`, which is the path used when the EM model has been fitted
(needs at least `_MIN_PAIRS_FOR_MODEL` = 30 candidate pairs). A unit test
using a 2-record case — nowhere near 30 pairs — caught that the *heuristic*
path (`_compute_heuristic_confidence`, which runs alone below that
threshold) never received `extra_unique_ids` at all, so two records sharing
a name but declaring different member IDs merged anyway on a small dataset.
Both paths now receive it. This is exactly the kind of gap that a design
review reading the code wouldn't necessarily catch — it took a
minimal-reproduction unit test.

### Blocking: why the old hard cap was a correctness bug, not a performance knob

Before Phase L, comparing every candidate pair above `_MAX_COMPARISONS =
10,000` simply broke out of every loop and logged a warning — the remaining
records were **never compared**, and the run still reported success. On a
2,484-record merge generating 28,170 candidate pairs, this silently dropped
18,170 of them, and — because block iteration order isn't guaranteed
stable — **two identical runs of the same data produced 2,483 and 2,482
output rows respectively**, with `llm_scored=0` in both, ruling out LLM
non-determinism as the cause. That's not a performance limitation described
in different words — it's data loss with no evidence it happened, which is
precisely the failure mode this whole project treats as unacceptable.

The fix is layered blocking, each layer cheap and SQL-expressible against the
DuckDB spool: multi-rule deterministic blocking (several explicit blocking
keys unioned, rather than one first-token key), phonetic/normalised keys so
spelling variants co-block, a sorted-neighborhood sliding-window pass that
catches near-misses no exact key catches, and block-size purging (a block
holding 20% of the dataset carries no discriminating information and is
dropped). Any pair that genuinely cannot be compared under any layer is
**counted and surfaced in the reconciliation report** — never silently
dropped. Measured blocking recall on the eval harness: **1.0000** at 2.4% of
the cost of comparing all pairs.

### Clustering: repair + cohesion cut, not raw transitive closure

Raw union-find over every edge scoring ≥ the auto-match threshold has one
structural weakness: one bad edge chain-merges an entire connected
component (`A` matches `B`, `B` matches `C`, therefore `A`/`B`/`C` are all
one cluster, even if `A` and `C` share nothing). Two passes were added on top
of the connected components:

- **Repair**: a component containing a hard conflict on a declared unique
  identifier (differing email, tax ID, member ID) is split rather than
  merged — a unique-identifier disagreement is treated as stronger evidence
  than any amount of accumulated similarity.
- **Cohesion cut**: average-linkage clustering runs *inside* each surviving
  component and cuts weak internal links, so a chain like `A–B–C` where
  `A↔B` is strong but `B↔C` is only just above threshold can break into two
  clusters instead of one.

**Borderline pairs (between the auto-exclude and auto-match thresholds) are
no longer auto-unioned.** Given the user's explicit priority — "over-merging
is the worse error, bias every threshold toward splitting" — a borderline
edge with real evidence behind it but not enough to clear the bar goes to
human review instead of merging unreviewed.

**Cluster IDs are deterministic, computed as
`sha256(f"{entity_type}:{'|'.join(sorted(record_ids))}")[:32]`, not random
UUIDs.** The previous behaviour (`generate_uuid()` per cluster per run) meant
re-running identical data on unchanged source rows produced a *different*
cluster ID every time, and since `load_data.py` uses the cluster ID as the
destination primary key when nothing better is available, re-running the
same merge **duplicated every row** in the destination. A deterministic hash
of the sorted member record IDs makes the same input cluster always resolve
to the same output ID — a prerequisite for the watermark-based incremental
extraction in §6 to actually be safe to re-run.

### Per-entity matching rules (Phase O.2)

`matching_rules` in state, keyed by entity type:
`{blocking_keys, unique_id_fields, strictness}`. `strictness` is one of
`loose`/`normal`/`strict`, mapping to auto-match thresholds `0.90`/`0.95`/
`0.98` (`_thresholds_for` in `resolve_entities.py`) — **it only ever moves
the auto-merge bar, never the auto-exclude bar**, so a stricter setting sends
*more* pairs to human review rather than merging more aggressively; that's
the recoverable direction given the "over-merging is worse" priority. An
inverted configuration (`auto_match <= auto_exclude`) or an unrecognised
strictness string falls back to the historical defaults (0.95/0.70) with a
logged warning, rather than either crashing or silently merging everything.
An entity with no configured rules gets those exact same historical defaults
— **this is the compatibility contract**: the whole eval harness (precision
1.0000, recall 1.0000, cluster counts unchanged) was re-run after adding this
feature specifically to prove that an unconfigured entity behaves
byte-identically to before Phase O existed.

---

## 8. Unification and conflict resolution

`src/mcp_server/tools/unify_entity_group.py` (per-cluster merge logic),
called from `src/orchestrator/nodes/unify_entities.py`.

**Conflict resolution is a configurable rule chain, not a hardcoded
if/elif.** Before Phase O.3, surviving-value selection for a field with
disagreeing candidates was a fixed chain: origin priority, then
completeness (longest non-empty value), then recency. `conflict_policy` in
state now lets a per-field (or `"_default"`) list of rules override that:
`priority | completeness | recency | shortest | first_seen`.
`resolve_policy()` looks up the field's chain (or falls back to
`"_default"`, or the historical three-rule chain if neither is configured);
`_apply_rule()` applies one rule to the current candidate set, and
`_resolve_conflict()` walks the chain, stopping as soon as exactly one
candidate survives. **Unknown rule names are dropped with a logged warning,
never silently treated as `first_seen`** — a typo in a saved profile's
conflict policy should be visible, not quietly change behaviour to something
the user didn't ask for.

`shortest` (minimum non-zero length) is a genuinely new rule not present in
the original hardcoded chain — added because a per-field configurable system
with only the original three rules wouldn't actually let a user express
"prefer the terser value" for fields like normalised phone numbers or codes,
which is a real, common survivorship preference. If the whole configured
chain leaves more than one candidate standing, `first_seen` is the final,
always-available tiebreak, guaranteeing the function always returns exactly
one value.

---

## 9. Field mapping and the destination schema

`src/mcp_server/tools/map_fields.py`, orchestrated by
`src/orchestrator/nodes/map_fields_node.py`. Maps each unified entity's
fields onto destination table columns, using heuristics first (name
similarity, type compatibility) and falling back to an LLM call for fields
the heuristic can't map with confidence (`field_mapping_enabled` in
`llm_config`).

**A pinned mapping (Phase O.4) must still reconcile against the live
destination — this was found by live verification, not by reading the code,
and would have shipped otherwise.** The first implementation of "reuse a
saved mapping" simply set `field_mappings[entity_type] = pinned_mapping` and
skipped calling `map_fields_impl` entirely — which also skipped computing
`pending_schema_changes`, the list of destination columns that need
creating. Against a genuinely fresh destination database, this meant **no
columns were ever created**: a live two-run test showed run 1 (which derived
the mapping itself) building a 9-column table, and run 2 (replaying the
pinned mapping) building a 1-column table. The fix reconciles the pinned
mapping's target columns against the destination's *actual current* schema
on every run — `existing = {c["name"].lower() for c in dest_columns}`, and
for every pinned `(source_field → target)` pair whose `dest_column` isn't
already present, a schema-change entry is generated via `_map_type_generic`
just as if the mapping had been freshly derived. A saved profile pins *which*
mapping to use, not an assumption that the destination already has the
columns for it.

**A second, smaller bug was caught fixing the first one:**
`field_mappings[entity][source_field]` is a **dict**
(`{dest_column, confidence}`), not a bare column-name string — the first
attempt at the reconciliation fix assumed a bare string and needed
correcting once the pinned-mapping shape was actually inspected.

---

## 10. Loading — transactional, and honestly not always

`src/mcp_server/tools/load_data.py`, `src/mcp_server/connectors/
postgresql_destination.py`, `src/mcp_server/connectors/
clickhouse_destination.py`.

**`load_data_node` will not write unless `load_approval is True`, checked
independently of gate routing.** This is a deliberate belt-and-suspenders
design: the graph's edge routing (§3) is *supposed* to prevent reaching
`load_data` after a rejection, but the node itself also refuses to write if
the approval flag isn't set, so a routing bug can never silently turn a
rejection into a write. Before this existed, rejecting the final approval
gate set `load_approval=False` and the graph proceeded to `load_data`
anyway, which never read that flag at all — a complete no-op reject that
looked, from the UI, like it had worked.

**PostgreSQL destination loads are staged and swapped atomically; ClickHouse
loads are not, and the code says so rather than pretending.**
`PostgreSQLDestinationConnector` writes into a `<table>__staging_<run_id>`
table first, then merges/swaps it into the real table inside one
transaction — either the whole entity lands, or none of it does, and
watermarks only advance after that commit succeeds. Killing the destination
mid-load and checking the real table is the actual live verification this
project ran for Phase M (`SELECT count(*)` unchanged, watermark
untouched). ClickHouse has no equivalent multi-statement transaction
primitive in the way this project uses it, so its destination connector is
documented as **not** atomic — an honest, stated limitation rather than a
false claim of parity.

**Foreign-key columns are translated from raw source ids to destination
cluster ids at load time (2026-08-01), not left as-is.** A cluster's
destination `id` is `deterministic_uuid(entity_type, *member_record_ids)` (see
§8) — never equal to any individual member's raw source id — but a field
mapped onto an FK column (e.g. `company_relation.company_a_id`) still carries
that raw id, copied straight from the origin record. Left untranslated, every
FK to an already-loaded parent violates the constraint immediately
(`Key (company_a_id)=(<raw id>) is not present in table "company"`).
`PostgreSQLDestinationConnector.get_foreign_keys(table)` queries
`information_schema` for the table's real FK constraints (column →
referenced table, no hardcoded names), and `load_data_impl` (`src/
mcp_server/tools/load_data.py`) — **after** `_transform_records` applies the
field mapping, since the FK column names only exist post-mapping — calls
`_translate_foreign_keys` to rewrite each FK value to the referenced row's
cluster id via a `{raw_id: cluster_id}` lookup built from
`entity_clusters` state. An id with no known cluster is set to `NULL`
rather than left dangling (a genuinely unresolvable link, e.g. a category
name that was never its own extracted entity, is worth losing — a load that
crashes the whole transaction over it is not). One subtlety that cost a
second bug fix: a referenced entity type that was resolved but had **zero**
clusters this run produces a real, empty lookup dict (`{}`) — Python's
`if not lookup:` treats that identically to "never looked up at all," which
skipped translation and let the bad value through. The fix checks
`referenced_table not in cluster_lookup_for_table` (key presence) instead of
dict truthiness.

**Value coercion (`src/mcp_server/tools/value_coercion.py`) also grew two
more classes of "not malformed, just not what this column's type expects"**
handling, in the same spirit as the money-string/hex-id cases already
documented in §9: an empty string (`""`) is a missing-value placeholder for
any non-text-like target type, coerced to `NULL` rather than rejected
(Postgres has no implicit `'' → NULL` cast for numeric/date/bool columns);
and Diffbot's date encoding (a leading `d` tag, `XX` standing in for an
unknown month/day — `"d1997-XX-XX"`) is normalized to a plain ISO date via
`normalize_date_like()`, filling unknown components with `01` rather than
failing the whole batch on the first partial date.

---

## 11. The LLM layer: client, prompts, cache, cost ledger

`src/llm/client.py` wraps LangChain's `ChatOpenAI`-compatible client;
`src/llm/prompts/` holds `.system.txt`/`.human.txt` template pairs per LLM
call site (dependency analysis, entity-table selection, entity resolution,
field mapping, request parsing); `src/llm/cost.py` is the semantic cache and
cost ledger (Phase O.5).

**Why a semantic cache at all, and why its key includes more than just the
prompt text.** Identical (prompt, model, temperature) combinations recur
constantly across runs — re-running the same merge, or running variations of
it, regenerates prompts that are often character-for-character identical.
`cache_key()` hashes `(model, temperature, normalised system prompt,
normalised human prompt, response_format, json_mode)` — **every field that
can change the answer's shape or content is part of the key**, deliberately,
because a cache entry that could return an answer different from what the
model would currently produce is worse than no cache at all; the whole value
of caching collapses the moment it can silently serve a stale-shaped
response. `normalise_prompt()` collapses whitespace runs before hashing, so
two prompts differing only in incidental formatting still share a cache
entry.

**`SemanticCache` is a bounded, thread-safe LRU**
(`OrderedDict`-backed, `_max_entries=5000` default, `threading.Lock`
guarded). `.get()` returns `(found: bool, value)` rather than just `value`,
specifically so a legitimately cached `None` response is distinguishable
from a cache miss — returning bare `None` for both would make a cached
"the model said nothing useful" indistinguishable from "never asked."

**`CostLedger` records tokens, not currency, on purpose.** Prices aren't
hardcoded anywhere in this codebase, because the actual LLM model in use
changes whenever a quota runs out — as of 2026-08-01 this is handled
automatically (see below), not by manually editing `config.yaml` — and a
dollar figure computed against the wrong model's pricing is actively
misleading — worse than not reporting a cost at all. `token_usage()`
extracts real usage from a LangChain response's `usage_metadata` /
`response_metadata`, trying both OpenAI-style (`prompt_tokens`/
`completion_tokens`) and generic (`input_tokens`/`output_tokens`) field
names, and returns `(0, 0)` — never an estimate — if neither is present. A
cache **hit** still calls `COST_LEDGER.record(..., cached=True, ...)`, which
increments `tokens_saved` by an estimated token count
(`_estimate_tokens`, a character-count/4 heuristic used *only* for this
savings figure, never for real billing) instead of the real (zero) token
cost of a cache hit — so the ledger can report both "how much this run
actually cost" and "how much the cache saved it."

**The cost ledger initially recorded nothing, and the bug is worth
understanding because it's a generic trap.** The ledger keyed its
accumulation on `session_name`, a fixed constant string
(`"merge-engine"`), not on the actual run's `thread_id`. Every run therefore
accumulated into the same bucket forever, and because the ledger is queried
per-run via `GET /api/workflow/{id}/cost`, every individual run's query
showed **0 calls** even while the semantic cache correctly logged 18
lookups for that run. The fix threads the real `run_id` into `llm_config` at
both call sites in `get_initial_state()` (`{**llm_config, "run_id": run_id}`
— see §2), which `LLMClient.__init__` reads via
`config.get("run_id", "") or session_name` (falling back to the old
behaviour only if `run_id` is genuinely absent, e.g. very old checkpoints).

**Automatic quota fallback (2026-08-01).** `LLMClient.call()` builds
`self.model_chain = [config.model] + config.fallback_models` and tracks a
`_model_chain_idx`. On an exception whose text matches `_is_quota_error`
(substrings: `quota`, `429`, `403`, `rate limit`, `insufficient_quota`,
`allocationquota`, `billing`), `_advance_model()` moves to the next entry,
resets the cached `_model` so `_get_model()` rebuilds against the new name,
and the same `call()` invocation retries — the caller never sees the
failure unless every model in the chain is exhausted. A non-quota error
(malformed request, network blip) does **not** advance the chain — it would
fail identically on every model, so burning through the list would only
delay the real failure. This replaced a manual runbook (edit
`config.yaml`, kill the server, restart, re-launch the workflow) that cost
real wall-clock time during live verification: see `llm-model-fallback-chain`
in project memory for the current 9-model chain and why it changed.

**A module-scope placement bug that silently truncated a whole class.**
`_estimate_tokens` was initially inserted *inside* `client.py`'s `LLMClient`
class body, mid-method. Python doesn't raise an error for this — it just
ends the class definition early at the dedent, silently turning
`call_json`/`call_batch` (everything textually after the misplaced function)
into orphaned **module-level** functions instead of class methods. Five
previously-passing tests failed with
`'LLMClient' object has no attribute 'call_json'`. The lesson generalises:
a helper function accidentally placed at the wrong indentation level inside
a class body is a silent structural bug in Python, not a syntax error — it
only surfaces when something calls the now-missing method.

---

## 12. Run profiles — pinning non-deterministic decisions

`src/orchestrator/profiles.py` (Phase O). This subsystem exists because of a
measured, not assumed, problem: **two full runs of identical source data,
producing identical candidate pairs (28,170), identical clusters (2,482),
and identical row counts, nonetheless produced destination schemas with 15
columns in one run and 13 in the other** — because the LLM's field-mapping
call is not deterministic on where `customer_id` folds into `id`. Matching is
deterministic (it's arithmetic over a fitted model); the *metadata* decisions
around it are not, because they go through an LLM. A profile is what makes
"re-run the same merge" a meaningful, reproducible operation instead of a
coin flip on schema shape.

`PINNED_KEYS = ("entity_tables", "matching_rules", "conflict_policy",
"field_mappings")` — exactly the four things Phase O made pin-able,
corresponding to O.1 through O.4. `build_profile()` constructs the saved
JSON, `save_profile()` persists it under `config/profiles/<name>.json`
(preserving the original `created_at` across re-saves), `load_profile()` /
`list_profiles()` / `delete_profile()` round it out.

**Secrets are stripped twice, deliberately redundantly.**
`strip_secrets()` recursively blanks any dict key whose lowercased name
matches `password|passwd|secret|api_key|token`, and it's called both by the
caller building the profile *and again, unconditionally, inside
`save_profile()`* — "never trust the caller" is the explicit design
rationale, because a profile file is the one artifact in this system
explicitly intended to be portable/shareable, and a single missed call site
anywhere upstream must not be able to leak a live credential into it.

**Credentials are restored by connection coordinates, never by list
position.** `connection_key(origin)` builds
`"host|port|database|user|type"` (lowercased, stripped); when a saved
profile is replayed, `restore_credentials(saved, live)` matches each saved
origin to a *currently configured* live origin by this key, not by index in
a list. If origins were reordered (or one was removed) since the profile was
saved, matching by position would silently repoint the profile's pinned
decisions at the wrong database. `restore_credentials` also returns a
`missing` list of coordinates it couldn't match — the caller must treat any
non-empty `missing` as "this run cannot proceed," never merely as a
warning to log and continue past.

**A node reusing a pin must actually skip the LLM call it's pinning, or the
profile is decorative.** `analyze_dependencies` checks whether every target
entity already has a pinned table selection and, if so, skips calling
`select_entity_tables_impl` (§7's O.1 companion) entirely, recording a note
that it reused the profile. `map_fields_node` does the equivalent for a
pinned mapping (with the reconciliation fix described in §9). Simply storing
pins without a node actually branching on their presence would silently keep
calling the LLM every run and re-deriving a possibly-different answer,
making the profile a lie about what actually happened.

**`capture_from_state` only fires on clean completion, not on any run that
touched a profile.** `WorkflowManager._finalize_stream_status()` calls
`_capture_profile()` only inside the branch where `wf["status"] ==
"completed"` — never on a failed or partially-skipped run. Capturing from an
incomplete run would pin a half-formed mapping (e.g. only some entities
processed before a failure) that the *next* run would then silently inherit
as if it were a complete, validated decision.

---

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

## 16. Testing strategy

`tests/unit/` — one file per subsystem, 908 tests passing as of the last
full run (0 failed, 83 skipped; skips require a live database container).
`tests/eval/` — a **labelled-pair precision/recall harness**, deliberately
separate from `tests/unit/`, because "the matcher didn't crash" and "the
matcher is precise" are different claims requiring different kinds of
tests — the eval harness is what produces the actual precision/recall
numbers cited throughout this document and `TODO.md`, derived from real
databases with known ground truth (a genuine duplicate in Pagila's `staff`
table; repeated titles in `sample_mflix`) plus every human decision captured
at the match-review gate during live runs. `tests/prompts/` — golden-file
tests asserting the LLM prompt templates render as expected for representative
inputs, independent of whether the LLM itself is reachable.

**A repeated pattern worth internalising**: several "bugs" caught during
Phase L/O verification were actually bugs in the *test*, not the product —
a cache-key test padding the wrong string, a ClickHouse uniqueness test that
needed correcting after the deliberate design decision to never trust
ClickHouse's declared key, a cleanup test assuming membership-only sweeping
after that behaviour was intentionally changed. The discipline that mattered
was not treating "a test fails" as automatic proof the code is wrong —
each failure was traced to its actual cause before anything was changed.

---

## 17. Index of every design decision and its alternative

A quick-reference table for "why did it do X and not Y," each pointing back
to the section with the full reasoning.

| Decision | Alternative considered | Why not the alternative | Section |
|---|---|---|---|
| Rows in a per-run DuckDB spool | Keep rows in checkpointed graph state | Checkpoint size scales with source data; 43M rows cannot fit | §5 |
| DuckDB bulk insert via registered DataFrame | `executemany` | 1,300x slower; 43M rows would take 60+ hours | §5 |
| Age-based spool cleanup guard | Membership-only cleanup (`_active_workflows`) | In-memory set is empty after restart; deleted a live run's rows | §5 |
| Keyset (seek) pagination | `OFFSET`/`LIMIT` | Cost grows with depth; keyset stays flat | §6 |
| `>=` + boundary-seen skip-count | Strict `>` predicate | Silently drops rows sharing the boundary key (measured 33% loss) | §6 |
| Deterministic row-hash tiebreak in `ORDER BY` | `>=` alone | Equal-key rows aren't stably ordered across queries; duplicated 3/16,044 rows | §6 |
| ClickHouse never trusts its declared primary key | Shared fast-path with Postgres | Measured: sparse sort key, not unique (43.28M rows / ~42.97M distinct tuples) | §6 |
| Fellegi–Sunter + term frequency | Averaged string similarity vs. 2 constants | Can't express "rare value agreement is stronger evidence" | §7 |
| EM-estimated m/u, unsupervised | Hand-tuned constants / labelled training | No labels available; constants don't adapt per dataset | §7 |
| Layered blocking (deterministic+phonetic+sorted-neighborhood) | Hard comparison cap (`_MAX_COMPARISONS`) | Cap silently dropped candidate pairs and still reported success | §7 |
| Repair + cohesion cut clustering | Raw transitive closure (union-find only) | One bad edge chain-merges an entire component | §7 |
| Deterministic cluster IDs (hash of sorted member IDs) | Random UUID per cluster per run | Re-running identical data duplicated every destination row | §7 |
| `strictness` only moves the auto-*match* bar | Also moving auto-exclude | User priority: over-merging is the worse error; only the recoverable direction should move by default | §7 |
| Configurable per-field conflict policy | Hardcoded priority→completeness→recency | User wants customisable survivorship; unconfigured entities keep the old chain exactly | §8 |
| Pinned field mapping reconciles against live destination schema | Reuse pinned mapping as-is | A fresh destination never got its columns; 1-column table where derivation gave 9 | §9 |
| PostgreSQL destination: stage + atomic swap | Batch-by-batch upsert | Mid-load failure used to leave the destination half-written | §10 |
| Cache key includes model/temperature/format/json_mode | Cache key = prompt text only | A cache that can return an answer different from what the model would currently give is worse than no cache | §11 |
| Cost ledger records tokens, not currency | Hardcoded per-model $/token pricing | Model changes whenever a quota runs out; a wrong price is worse than none | §11 |
| `run_id` carried inside `llm_config` | Global/session-level cost tracking | Keying on a constant `session_name` made every run report 0 calls | §11 |
| Credentials restored by connection coordinates | Restored by list position/index | Reordering origins would silently repoint a profile at the wrong database | §12 |
| Profile capture only on clean completion | Capture on any run touching a profile | A failed/partial run would pin a half-formed mapping as if validated | §12 |
| UI polls `/status` every 2s | WebSocket/SSE push | Runs pause for human-timescale durations; nothing to push most of the time, and polling survives a refresh with no reconnect logic | §14 |
| Frontend built to a static bundle, served by FastAPI | Separate frontend dev server in production | One process, one port, no CORS, frontend/backend ship as one versioned artifact | §14 |
| Self-hosted Langfuse | Hosted SaaS tracer | Trace data derives from the user's real database rows; keep it off third-party infra by default | §15 |

---

## 18. Known problems — what is actually wrong right now

Verified against the current tree, not remembered. Ordered by how much they'd
hurt. Nothing here is speculative — each was confirmed by running the check
shown.

### 18.1 🔴 The frontend cannot be built from a fresh clone

`.gitignore:39` has a blanket `*.json` rule (intended for data fixtures). It
also matches `src/ui/frontend/package.json`, `package-lock.json`, and
`tsconfig.json`. Confirmed:

```
$ git check-ignore -v src/ui/frontend/package.json
.gitignore:39:*.json    src/ui/frontend/package.json

$ git ls-files src/ui/frontend
(empty)
```

**Nothing in the frontend directory is tracked by git.** Anyone cloning this
repo gets no `package.json`, so `npm install` is impossible and the UI cannot
be rebuilt — and since `dist/` is also ignored (by the top-level `dist/`
rule), they get no pre-built bundle either. The app would start and serve
nothing.

This is the single most important thing to fix before handing the repo to
anyone else. It needs a negation rule (`!src/ui/frontend/package.json` etc.)
or a narrower data-fixture rule than blanket `*.json`.

### 18.2 🟠 `config/config.secrets.yaml` exists but is never loaded

The file sits in `config/`, and `_deep_merge()` exists in
`config_loader.py:113` to merge exactly that kind of overlay — but
`load_config()` **never calls it**. Confirmed: the only references to
`_deep_merge` in the whole codebase are its own recursion and one unit test
in `tests/integration/test_config_resolution.py`.

The danger is specific: a file named `config.secrets.yaml` invites someone to
put a real credential in it, believing it will be used. It will be silently
ignored, and the credential sits on disk doing nothing but existing. Either
wire it up or delete it — leaving a plausible-looking secrets file that
nothing reads is worse than having neither.

### 18.3 🟠 Saved connections store database passwords in plaintext

`config/recent_connections.json` stores each recent origin and destination
including `connection.password` as plaintext. Confirmed by reading the file:
every entry has a non-empty password field.

Two mitigating facts, both real: the file is gitignored (all of `config/` is),
so it never reaches the repo; and this is a local developer tool, not a
multi-tenant service. But note the **inconsistency** — `profiles.py` goes to
deliberate lengths to strip secrets (twice, §12), while the file sitting next
to it in the same directory keeps them raw. Anyone with filesystem access, or
anyone who copies a machine backup, has the database credentials.

### 18.4 🟡 The comparison budget still truncates — but no longer silently

`_MAX_COMPARISONS = 250_000` in `resolve_entities.py:49` is still enforced
(line 638), and pairs beyond it are still dropped (`pairs[:_MAX_COMPARISONS]`,
line 650). What changed in Phase L is that the count is now recorded in
`_LAST_BLOCKING_STATS["uncompared_pairs"]` and surfaced in the reconciliation
report, instead of being a warning nobody read.

So the honest status is: **this is a bounded, visible, reported limit rather
than silent data loss.** The budget was also raised 25× (10,000 → 250,000)
once memoisation made scoring 24× cheaper. But a large enough entity can
still exceed it, and when it does, the pairs beyond the cut are not compared.

### 18.5 🟡 ClickHouse destination loads are not atomic

Only `PostgreSQLDestinationConnector` does stage-then-swap. A ClickHouse load
that fails midway leaves a partially written destination. Documented
everywhere rather than papered over, but it is a real gap in the "all or
nothing" guarantee (§10).

### 18.6 🟡 Databricks is implemented but never verified

`src/mcp_server/connectors/databricks.py` is registered in the connector
factory and will be selected if someone picks that type. Unlike
PostgreSQL/ClickHouse/MongoDB, it has **never been exercised against a live
instance** in this project's verification runs. Treat it as untested code, not
a supported path.

### 18.7 🟡 Local state is growing without any retention policy

`logs/spool/` is at **3.4 GB** and `logs/debug.log` at **201 MB** right now.
The spool has a 6-hour age guard for *deletion safety* (§5) but nothing
enforces an upper bound on total size, and the logs have no rotation
configured at all. `data/checkpoints.db` (6.5 MB + 4 MB WAL) accumulates every
run's full state history with no pruning.

### 18.8 🟢 Repo hygiene issues

- **`.langgraph_api/*.pckl` are tracked in git** — binary runtime state that
  changes on every run, producing meaningless diffs. Needs `git rm --cached`;
  a `.gitignore` entry alone won't untrack them.
- **`node_modules/` is not ignored** (2,420 files, 65 MB) — this is the
  "1000+ files" seen when staging.
- **`.gitignore:75` says `.agetnt/`** — a typo. The real directory is
  `.agents/`, so the rule matches nothing.
- **`src/tests/` and `src/config/` are empty duplicate directories** left over
  from a refactor; the real ones are at the project root. No code references
  either.
- **`.agents/skills/langfuse/` contains a nested `.git` repository** — a
  vendored third-party skill unrelated to this project's Langfuse tracing.
  Committing it as-is would create a broken submodule reference.

---

## 19. Future risks — what will break as this grows

These are not bugs today. They are the places where a reasonable next step —
more data, more users, a second server process — meets an assumption the
current design makes.

### 19.1 Entity resolution has never been measured at scale

**This is the largest honest gap in the project.** Phase N proved
*extraction* at 43M rows. It did not prove *resolution* at 43M rows, and the
two have fundamentally different cost curves: rows scale linearly, but
candidate pairs scale with the square of block size. The 43M-row proof covers
the data path only — extract, spool, reconcile.

The comparison budget (§18.4) means a 43M-row resolution wouldn't fail, it
would **truncate** — and report that it truncated. That's the designed
behaviour, but it means the practical answer to "can this deduplicate 43
million records?" is currently *unknown*, not *yes*. Measuring it is the
single most valuable next experiment.

### 19.2 `_active_workflows` is in-memory, so the app cannot scale beyond one process

Run tracking lives in a plain dict inside `WorkflowManager`. Start a second
uvicorn worker and the two processes have disjoint views of which runs exist:
a status poll load-balanced to the wrong worker reports a live run as
missing. This also means the app **cannot** be run with `--workers 2`.

This is the same in-memory assumption that caused the spool deletion bug
(§5) — that one is fixed at the symptom level with an age guard, but the root
cause (authoritative run state living in process memory rather than in the
checkpoint database) is still there.

### 19.3 The 6-hour spool guard is a heuristic, and heuristics have edges

A spool file untouched for 6 hours is assumed abandoned. A run genuinely
parked at a HITL gate for longer than that — over a weekend, say, which is
exactly what a human-in-the-loop system invites — becomes eligible for
deletion again the moment someone starts a new merge. The window is much
safer than the original membership-only check, but the correct fix is a real
liveness record in the checkpoint DB, not a longer timeout.

### 19.4 SQLite's single writer will become the concurrency ceiling

`data/checkpoints.db` is written after every node of every run. SQLite allows
one writer at a time; several concurrent merges will serialise on it and
eventually surface as lock timeouts. LangGraph ships a Postgres checkpointer
for exactly this, and swapping it is a config-level change — but nothing
today tells you when you're approaching the limit.

### 19.5 Prompt files fail open, not closed

`_read_prompt()` returns `""` for a missing file (§0). Rename a prompt, or
mistype a stage name, and that stage runs with an **empty system prompt** —
no role, no rules, no output contract — and the LLM will still return
*something*, which downstream code will then try to parse. The failure
surfaces as confusing JSON parse errors far from the actual cause. A missing
prompt should raise at import.

### 19.6 The semantic cache is per-process and dies on restart

`SEMANTIC_CACHE` is an in-memory `OrderedDict`. Restarting the app throws away
every cached response, and two workers would each keep their own. The measured
saving was real (20,494 tokens on one replay run), but it only ever applies
within a single process lifetime.

### 19.7 The learned matching model is discarded after every run

The EM-estimated m/u probabilities (§7) are refit from scratch on every run.
The model that a large, well-labelled run learned — genuinely valuable,
dataset-specific knowledge — is thrown away when that run ends. Nothing
persists it and nothing lets a later run start from it.

### 19.8 No authentication on the API

Every route in `src/api/main.py` is unauthenticated. Anyone who can reach the
port can enumerate database schemas, start merges, and abort running ones.
Fine for `localhost` single-user development, which is what it is today;
unacceptable the moment it's bound to anything else.

### 19.9 LLM provider drift

The model chain is configured in `config.yaml` (`llm.model` +
`llm.fallback_models`), and `LLMClient` now advances through it
automatically on a quota error (§11). Different models honour
`response_format` / JSON mode differently, so a fallback that keeps the
pipeline running can also quietly change output shape — and prompts tuned
against one model are not automatically valid against the next. One
concrete failure mode already seen live: a quota-exhausted model made
scope-planning fall back to a weaker heuristic that found zero subject
candidates, and the pipeline still reported `status=completed` with every
entity count at zero — a hollow success, not a crash. Automatic fallback
closes the *quota* half of this risk; it does nothing about output-shape
drift between models, which is still unmeasured.

### 19.10 Entity-cluster partitioning is O(n²) per connected component, unguarded

`_build_clusters` → `_partition_component` / `_average_linkage` /
`_split_weakest_link` (`resolve_entities.py`, §7) run three unguarded O(n²)
passes over every connected component formed by union-find on the scored
pairs. Unlike blocking/scoring, which is capped at `_MAX_COMPARISONS`, there
is no size cap here — if auto-matched pairs chain into one large component,
partitioning cost explodes independent of how good the earlier budget cap
was. Live evidence (Boeing, 2026-08-01): `technology` (1,503 records, small)
took ~9-11 minutes to cluster — nearly as long as `person` (~7,000
records) — meaning a handful of pathologically large/dense components, not
a general per-record cost. This was the dominant runtime cost of every
Boeing verification run that night, well above blocking/scoring or the load
itself. Full detail and four other bottlenecks (serial per-entity LLM
mapping calls, non-vectorized scoring, chunked-IN-filter round-trips, no
mid-run resume) in `docs/PERFORMANCE_BOTTLENECKS.md` — documented, not yet
fixed, pending a dedicated profiling pass.

---

## 20. Ideas and features worth building next

Grouped by what they'd get you. Rough effort in brackets. Nothing here is
started; this is the honest "what would I do with another month" list.

### Directly closes a gap named above

1. **Measure resolution at scale** [M] — run the matcher over 1M, then 10M
   records and record pair counts, wall clock, memory, and truncation rate.
   Turns §19.1 from unknown into a number. *Highest value item on this list.*
2. **Move run tracking into the checkpoint DB** [M] — kills §19.2 and §19.3
   at the root, and unlocks multi-worker deployment.
3. **Postgres checkpointer option** [S] — removes the SQLite write ceiling
   (§19.4). Mostly configuration; LangGraph supports it directly.
4. **Persist the learned m/u model per profile** [M] — §19.7. A profile
   already pins the LLM's decisions; pinning the *matcher's* learned model is
   the natural completion of that idea, and makes re-runs cheaper as well as
   more reproducible.
5. **Shared cache backend (Redis/SQLite)** [S] — makes §19.6's savings
   survive restarts and span workers.
6. **Fail-closed prompt loading + a prompt contract test** [S] — §19.5.
7. **API authentication** [S] — §19.8. Even a single static token would move
   this from "unacceptable off localhost" to "deployable internally."
8. **Retention policies** for spools, logs and checkpoints [S] — §18.7.

### Makes the matching better

9. **Embedding + ANN blocking layer (HNSW/FAISS)** [L] — the one blocking
   layer from the original Phase L plan that was left out, gated behind a
   config flag because it adds a model dependency. It's the recall layer for
   records no deterministic or phonetic rule catches: different transliteration,
   nicknames, reordered name parts.
10. **Active learning from gate decisions** [L] — every keep/split/merge a
    human makes at the match gate is a free labelled example. They're already
    captured for the eval harness; feeding them back as supervision would let
    the model improve with use rather than starting cold each run.
11. **Explainability in the review UI** [S] — `MatchModel.explain()` already
    returns per-field evidence in bits, sorted by magnitude, and the UI
    doesn't show it. This is nearly free and would make the match gate far
    more reviewable: "merged because tax_id matched exactly (+18.2 bits)"
    beats "confidence 0.97."
12. **Per-entity threshold tuning from the eval harness** [M] — suggest
    strictness per entity from measured precision/recall instead of asking a
    user to guess between loose/normal/strict.

### Makes it more useful operationally

13. **Scheduled / triggered runs** [M] — profiles (§12) were explicitly built
    as "the foundation for scheduled runs" and nothing schedules them yet.
    Combined with watermarks (§6), this gives genuine incremental sync.
14. **Dry-run diff mode** [M] — the plan called for a `dry_run` flag that
    stages everything and reports the exact diff without committing. Partly
    present in the staging path; not exposed as a user-facing mode.
15. **Destination connectors: MongoDB, Databricks, S3/Parquet** [M each] —
    Mongo is origin-only today; Databricks is unverified (§18.6).
16. **ClickHouse atomic loads** [M] — insert into a temp table then
    `EXCHANGE TABLES` / partition move, closing §18.5.
17. **Run comparison view** [S] — two runs of the same profile, diffed:
    row counts, cluster counts, schema. The 15-vs-13-column discovery that
    motivated all of Phase O was found by hand; it should be a button.
18. **Cost budget enforcement** [S] — the ledger measures tokens but nothing
    acts on them. A per-run ceiling that pauses at a gate rather than
    silently spending would make "use the LLM more" a bounded decision.

### Bigger bets

19. **Streaming / CDC ingestion** [XL] — watermarks give batch incrementality;
    consuming a change stream (Debezium, logical replication) would make this
    continuous instead of scheduled.
20. **Multi-destination fan-out** [L] — the single-destination limit is a
    deliberate product constraint, not a technical one. Writing the same
    unified result to several destinations is a natural extension.
21. **A rules DSL for survivorship** [L] — §8's policy chain is expressive but
    positional. Real MDM systems eventually need conditional rules ("prefer
    origin A for this field, unless its value is older than 30 days").
22. **Golden-record lineage** [L] — record which origin row contributed each
    surviving field value, and expose it. Currently the merge decision is
    made and the provenance is not retained per field, which is the first
    thing a data steward asks for.
