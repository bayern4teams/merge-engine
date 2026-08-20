# ADR 0015: P-9's engine, wired in as a flag inside `plan_scope_impl`

**Status:** accepted, deliberately narrower than the plan's own framing.

## Context

Branch_Plan.md's P-9 text says: "This is where V2's `plan_scope`/`extract_data` get
replaced by the general engine — behind a flag, with the Airbus/Boeing baselines as the
regression gate." Investigating how this codebase already does engine-swap flags
(`resolve_entities.py`'s Splink-vs-builtin matching engine, gated on
`matching_rules.engine`) found the precedent: an **in-function branch inside the
existing node**, not a new graph node or conditional edge, falling through silently to
the old path on any failure.

## Decision

`plan_scope.py`'s `plan_scope_impl` gains a `scope_config.engine == "v3_discovery"`
branch, following that exact precedent:

- `_v3_discovery_plan()` (new) computes the same wire shape `_heuristic_plan()` already
  produces (`{entity: [{origin_id, table, mode, path, hops, confidence, ...}]}`), but via
  `entity_extraction_v3.traverse_subgraph()` — a policy-capped BFS (`max_depth`/
  `max_nodes`/`max_edges`/`min_edge_confidence`/`allowed_sources`/cycle prevention) over
  the same real `JoinGraph` the heuristic floor already builds — instead of one
  `find_paths` call per (subject-table, dependent-table) pair.
- Because the output shape is identical, `extract_data.py` (the node) needs **zero
  changes** to consume either engine's plan. `_entry_from_hops` (already written for the
  heuristic floor and the LLM-sanitizer) accepts `entity_extraction_v3`'s
  `NodeProvenance.path` directly, since it stores direction-aware `PathHop`s (the same
  type `join_graph.find_paths` returns), not raw edges.
- Any exception during the v3 attempt, or an empty resulting plan, is logged and the V2
  heuristic floor (already computed unconditionally above) stands untouched — matching
  `resolve_entities.py`'s "falls through to the builtin path below exactly as if no
  engine were configured."

**This ADR deliberately scopes the flag to planning only, not extraction execution.**
`extract_data.py`'s actual row-pulling (watermarks, incremental extraction, per-entity
grouping, family tables, chunked/scratch-table `IN` filters) is a 1000+ line, heavily
battle-tested node; swapping its execution path behind the same flag was judged higher
risk than the value it would add given `entity_extraction_v3.execute_extraction()`
already proves the same fixpoint hop-walking approach works correctly against real data
(see below) as a directly-callable, independently useful capability. A future ADR can
revisit wiring `execute_extraction` into the `extract_data` node itself if a concrete
need for its extra capabilities (context scores, coverage matrix, cost-estimate preview)
inside the live pipeline arises.

## Live verification (Airbus/Boeing baselines, per the plan's own regression-gate text)

Real ClickHouse `origin1`/`origin2`, real `JoinGraph`, real connectors throughout — no
mocking. Two real bugs were caught and fixed by this verification, neither of which the
unit tests (written before the live run) exercised:

1. `execute_extraction` called `connector.extract(table, [], ...)` — `[]` is not this
   codebase's "all columns" convention (`["*"]` is; confirmed against
   `clickhouse.py`'s own `col_list = "*" if columns == ["*"] else ...`), producing a
   malformed `SELECT ... FROM` with no column list, a ClickHouse syntax error on the
   very first live call.
2. After fixing (1), every downstream hop still came back with 0 rows. Root cause:
   `execute_extraction` ran every propagated join-column value through
   `join_graph.normalize_val()` before using it in the next hop's `IN` filter.
   `normalize_val()` lowercases and strips URL prefixes — correct for its actual job
   (cross-column equality *comparison* during inclusion-dependency verification), wrong
   for producing values to filter a real, case-sensitive database with: a real id like
   `E-xSjHM5bMJmwwRfrYLwwkw` filtered on its lowercased form matches nothing. Fixed by
   using the raw seed values verbatim.

With both fixed, `discover_anchors("Boeing")` against real `origin1.Company` returned 36
ranked candidates including **three separate rows literally named "Boeing" exactly** —
concrete, live confirmation of why §31 ("never auto-merge on name equality") matters,
not a theoretical concern.

`traverse_subgraph` from `(origin1, Company)` + `(origin2, Company)` with
`min_edge_confidence="verified"` reached exactly the 10 real edges/12 nodes BP-B2 already
established (Company → CompanyContact/CompanyExperience/CompanyFinancials/.../Location,
then CompanyExperience → Person at depth 2, context_score correctly decayed to 0.7671).
`plan_extraction`'s cost estimates came from the real `estimate_query_cost` — origin2's
`Company` table estimate (1,270,564 rows) exactly matches the direct `SELECT count()`
this branch's earlier BP-B2 work already confirmed independently.

`execute_extraction`, seeded with `entityName ILIKE '%Boeing%'` / `'%Airbus%'` against
real `origin1`, pulled real rows across every reached table. Cross-checked against the
hand-built golden baseline (`scripts/goldens/boeing_manual_merge.py`/
`airbus_manual_merge.py`, freshly regenerated this session): **financials matched
exactly both times** — 20/20 for Boeing, 28/28 for Airbus — the single cleanest
apples-to-apples number available (one source table, no dedup/entity-resolution step in
between). Person/company counts were consistent with golden's combined origin1+origin2
totals once origin2's contribution is subtracted (e.g. Boeing: golden's 539 persons =
39 from origin1 — matching this engine's origin1-only Person count of 39 — + ~500 from
origin2).

`plan_scope_impl` with `scope_config={"engine": "v3_discovery"}` produced a valid,
wire-compatible `scope_plan` for a real Boeing run: `company` matched in both origins,
`person` linked via a real 2-hop `origin1` path at `context_score=0.7671` — `origin2`
correctly excluded (no `origin2` edge to `Person` clears the `verified` bar, consistent
with BP-B2's finding that `origin2`'s relationships are mostly weak ~25%-overlap ones).

## Consequences

- A scoped merge run can opt into `scope_config.engine = "v3_discovery"` for planning
  today; default behavior (`engine` unset or `"v2"`) is provably unchanged (a dedicated
  test asserts the default-engine plan carries no `v3_discovery` marker in its reason
  string).
- `entity_extraction_v3.execute_extraction`'s single-batch-per-hop fetch does not
  paginate (unlike `extract_data.py`'s own chunked/scratch-table `IN`-filter handling) —
  acceptable for this ADR's proof-of-verification scope, a real gap if it were wired into
  the live pipeline's actual extraction step directly, which is exactly why that wiring
  is out of scope here (see above).
- `traverse_subgraph`'s BFS provenance keeps only the *first* (shortest) path to a node,
  same tradeoff `find_paths`'s ranking already makes for the heuristic floor.
