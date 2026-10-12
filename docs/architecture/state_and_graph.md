[< back to docs/INDEX.md](../INDEX.md)

# MergeState and the graph

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

- Part 1 added plain replace-semantics keys that gates read from *state*
  (a gate body does not run at its pause): `table_roles` and
  `relationship_edges` (proposed at the dependency gate, never acted on),
  `pre_load_thresholds` (the thresholds confirmed at the final gate, kept so a
  load retried through the error gate is judged against them, not the
  proposals) and `metadata_overrides` (the per-run ETL-metadata layer from
  `run_config`). They must be declared in `MergeState`, or LangGraph drops
  them.

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
