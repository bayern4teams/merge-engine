# Performance Bottlenecks — Observed 2026-08-01, Not Yet Fixed

Found while live-verifying Boeing end-to-end (Phase 7 of the remediation
plan, see project memory `merge-engine-remediation-1-to-6.md`). These are
documented for a future pass — **nothing in this file has been changed**.
They are separate from the 8 correctness bugs fixed the same night (schema
type mismatches, FK translation, value coercion) — those made the pipeline
finish at all; these make it slow even when it does finish correctly.

Timings below are all from real Boeing runs against ClickHouse origins with
~2.9M and ~1.27M `Company` rows respectively (see
`docs/diagnostics/BOEING_APP_VS_MANUAL_DIAGNOSIS.md` for the dataset).

## 1. Entity-cluster partitioning is the dominant cost, not scoring — O(n²), unguarded, single-threaded

**Where:** `src/mcp_server/tools/resolve_entities.py`, `_build_clusters` →
`_partition_component` → `_average_linkage` / `_split_weakest_link`
(roughly lines 1442–1690).

**What's slow:** Candidate-pair generation and scoring are already
budget-capped at `_MAX_COMPARISONS = 250_000` (the block-overflow fix from
earlier this session made that step fast — 2–4 seconds even for 34,782
records). But *after* scoring, building the final clusters from the scored
pairs runs three O(n²) passes per connected component with no size guard:

- `_partition_component`'s conflict-check: nested loop over every pair of
  members in a component.
- `_average_linkage`: another full pairwise pass.
- `_split_weakest_link`: sorts all present edges and can recurse, each
  recursive call doing another O(n²)-shaped pass.

None of this is capped the way blocking/scoring now is. If auto-matched
pairs chain together (via union-find) into one large connected component —
which is exactly what happens when a blocking key groups many genuinely
different records with a few strong pairwise edges threading through them —
the partitioning cost explodes independent of how good the earlier budget
cap was.

**Evidence (same night, after the block-overflow fix was already in
place):**

| Entity | Records | Blocking+scoring | Clustering (this step) |
|---|---|---|---|
| `person` | ~7,000–7,800 | seconds | **~10–25 minutes** |
| `experience` | 34,782 | ~2–4 seconds | **~20–30 minutes** |
| `technology` | 1,503 (small!) | seconds | **~9–11 minutes** |

`technology` is the most telling data point: only 1,503 records, yet
clustering took nearly as long as `person`'s ~7,000. That strongly suggests
a small number of pathologically large/dense components, not a general
per-record cost — worth profiling directly (log component sizes going into
`_partition_component`) before picking a fix, rather than guessing.

**Not fixed because:** this needs profiling to find the actual worst-case
component(s) before choosing between a size cap (mirroring the block-overflow
fix — skip the expensive O(n²) checks above some threshold and fall back to
a cheaper method) versus an algorithmic change (e.g. hierarchical/greedy
partitioning instead of pairwise average-linkage). Either is a bigger,
riskier change than tonight's crash-fixes and deserves its own verification
pass.

## 2. Field-mapping LLM calls run serially per entity type

**Where:** `src/orchestrator/nodes/map_fields_node.py`, the `for entity_type
in target_entities:` loop — each iteration's `await map_fields_impl(...)`
blocks the next entity's mapping call.

**What's slow:** Each entity type's undecided fields are mapped via one LLM
round-trip, and these calls are fully independent of each other (different
entity types, different prompts, no shared state) — a textbook
`asyncio.gather` opportunity. Right now, with N entity types needing LLM
mapping, wall time is N × (LLM latency), not max(LLM latency).

**Not fixed because:** untouched tonight; flagged for the future pass.

## 3. Pairwise scoring and match-model fitting are pure-Python, not vectorized

**Where:** `src/mcp_server/tools/resolve_entities.py`'s scoring loop, and
`src/mcp_server/tools/fellegi_sunter.py`'s match-model fit.

**What's slow:** Even after the async-yield fix (this session's Bug B, which
only fixed HTTP responsiveness, not throughput), scoring up to 250,000 pairs
is a per-pair Python loop with per-field comparisons and dict lookups.
Fitting the Fellegi-Sunter match model over the same 250,000 pairs is
likewise pure Python. Both are the kind of workload numpy/pandas
vectorization typically speeds up by one to two orders of magnitude.

**Not fixed because:** untouched tonight; flagged for the future pass —
would need care to keep the field-comparison semantics (which already
special-case things like phonetic keys and term frequency) identical.

## 4. ClickHouse extraction: chunked `IN (...)` filters trade correctness for round-trips

**Where:** `src/orchestrator/nodes/extract_data.py`, `_chunked_filters`
(this session's Bug A fix).

**What's slow:** The chunking fix (500 ids per chunk) was necessary —
without it, ClickHouse's HTTP interface rejects an oversized filter outright
— but it's a correctness fix, not a speed one. For Boeing's 34,782-id
`_resolve_link_via` case, that's **~70 separate round-trips** for what could
be one query against a temp table joined on the id list.

**Not fixed because:** a temp-table/JOIN approach is a bigger connector
change (needs a scratch table lifecycle, cleanup on failure, etc.) than the
chunking fix, and the chunking fix was already sufficient to unblock
correctness tonight.

## 5. No mid-run resume — a crash always reprocesses every entity from scratch

**Where:** the overall `resolve_entities` → `load_data` flow.

**What's slow:** Watermarks make a *successful* run idempotent across
re-runs (a second run won't re-extract already-loaded data). But if a run
fails partway through `load_data` (as every attempt did tonight until the
8th fix), the *next* run redoes every entity's blocking, scoring, and
clustering from zero — there's no cached intermediate state to resume from.
Given item #1 above, that's ~30–60+ minutes of re-work per fix-and-retry
cycle, which is exactly what made tonight's iteration loop slow (8 fixes ×
~1–1.5 hours per full re-verification run).

**Not fixed because:** would need persisting clustering results keyed by a
content hash of the input records, invalidated appropriately — a real
design decision, not a quick patch.

## Operational friction (not a production bottleneck, but cost real time tonight)

- **No hot config reload.** Every `config/config.yaml` change (e.g. the LLM
  model swap) requires killing and restarting the app server — there's no
  watch/reload mechanism. Happened 6+ times tonight.
- **`drive_app.py`/`resume_drive.py`'s fixed iteration budget + 3-second
  poll interval** meant every ~15-60 minute stretch of legitimate
  computation (see item #1) required manually resuming the driver script.
  Not a pipeline bottleneck, but worth raising the default `max_iters` or
  polling interval for anyone driving long runs headlessly.

## Not a bottleneck, but related — why parity is 12%, not a performance issue

The first real completed Boeing run (this session) landed at 12.0% overall
row parity against the hand-merge golden. That's a **data-quality** gap
(uniform survivorship, entity-breadth caps, matcher precision), already
tracked in `merge-engine-app-vs-manual-gap.md` and
`docs/diagnostics/BOEING_APP_VS_MANUAL_DIAGNOSIS.md` — not something on this list, and
not something tonight's fixes touched or were meant to touch.
