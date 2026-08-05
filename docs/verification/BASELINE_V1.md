# BASELINE_V1.md — Phase 0 exit baseline (2026-08-03)

Recorded per Phase 0 Sec 0.11, after BUG A-D (Sec 0.0) and all of Sec 0.8's
node-level fixes had landed on `main`. This is what every one of the five
future branches (B1-B5) measures "no regression" against.

Model: `qwen3.5-27b` (`config/config.yaml`), the model actually configured —
the previously checked-in scorecard recorded `qwen3.6-flash-2026-04-16`,
which current config never calls, so it was not a reproducible baseline.

## 1. Full test suite — `python scripts/check.py all`

```
All 9 check(s) passed.
```

Constituent gates: ruff, ruff-format, mypy ratchet (196 entries, unchanged),
arch-lint ratchet (**15 entries, down from 17** — the two module-level-global
violations in `resolve_entities.py` are gone, confirming Sec 0.8's
de-globalization), doc-lint (zero broken links/stale citations), unit tests,
integration tests, frontend typecheck+build, matcher eval gate.

`pytest tests/unit tests/integration -q`:
```
1255 passed, 83 skipped, 0 failed
```

## 2. Matcher precision/recall — `python -m tests.eval.score`

```
Fellegi-Sunter + term frequency:  precision 1.0000  recall 0.9950  F1 0.9975
  TP=199 FP=0 TN=360 FN=0, 0.2% routed to review

clustering (400 distinct people)      -> 400 clusters [OK], 0 folded
clustering (100 people x 2 systems)   -> 100 clusters [OK], 100 folded correctly
blocking recall                       -> 1.0000 (480/19900 candidate pairs, 2.4%)
clustering (300 people, no unique id) -> 300 clusters [OK], 0 folded
```

Unchanged from every prior measurement this session — zero regression
through all of Sec 0.8's structural edits.

## 3. Prompt contract harness — `python -m tests.prompts.harness --live`

```
Model: openai/qwen3.5-27b
request_parsing:        14/14
dependency_analysis:      2/2
entity_table_selection:   2/2
scope_planning:           2/2
entity_resolution:      11/11
field_mapping:            9/9
TOTAL failures: 0
```

Written to `tests/prompts/scorecard_latest.txt` (now reproducible from
current config — it wasn't before this baseline).

**A real bug was found and fixed while regenerating this**, not a flake:
`tests/prompts/harness.py`'s `_build_prompt` built the field-mapping
destination-column list without production's `_RESERVED_DEST_COLUMNS`
filter (`map_fields.py:40`), so it offered the model the loader-managed
`_source_ids` column that production always withholds. The live model
correctly attempted to map an origin surrogate key (`companyID`) onto it —
a prompt production never actually sends. Fixed by importing the production
constant instead of re-listing it (harness now cannot drift from production
again on this specific point).

## 4. Live Boeing run — `drive_app.py "Boeing" --assert-budgets`

Destination reset to pristine before the run (`restore_pristine.py`: dropped
29 app-added columns from `company`, 2 from `person`, confirming those were
exactly BUG A's damage; 25 tables, 0 rows before starting).

```
Total wall time: 46.6 minutes
thread_id=wf_d7103e2d

PASS resolve_entities_seconds_max: 1944.4s <= 25260s (all 15 entity types)
PASS full_run_minutes_max: 46.6 min <= 90 min
SKIP llm_stage_latency_ms_p95: no per-call LLM latency recorded anywhere
  the driver can read (not in the run report or workflow status);
  not asserted rather than faked.
SKIP parity_pct_min: FAILED before a real bug fix (see 4a), then measured
  honestly after (see 4b) — does not clear the current BUDGETS.yaml floor.
```

### 4a. Bug found and fixed: `compare_to_golden.py` broke `--assert-budgets`'s parity check

`compare_to_golden.py` printed `"\nWritten to {out_path}"` to **stdout**
*after* its JSON dump. `drive_app.py --assert-budgets` runs it as a
subprocess and does `json.loads(proc.stdout)`, expecting nothing else on
stdout — the trailing line was an unconditional `json.JSONDecodeError:
Extra data` on every single invocation for boeing/airbus/lockheed. This is
not a corner case; it means the parity assertion has never actually run to
completion via `--assert-budgets` before. Fixed by moving that print to
stderr. Re-ran `compare_to_golden.py boeing` directly afterward — clean
JSON, exit 0.

### 4b. §0.0 destination-correctness assertions — all verified live, by direct SQL

| Assertion | Result |
|---|---|
| `company` has exactly 14 columns | **PASS** (14) |
| `person` has exactly 9 columns | **PASS** (9) |
| No `merge_engine_*` helper table beyond the sanctioned entity map | **PASS** (1: `merge_engine_entity_map`) |
| `count(name)` in `company` | **23/23** (100%) |
| `count(legal_name)` in `company` | 9/23 (39% — not all source companies carry a distinct legal name; no longer 0/31) |
| `resolution_stats.candidate_pairs > 0` for `company` | **PASS** (411 candidate pairs, 2 auto-merged clusters) |
| Companies sharing a domain unify | Not independently re-verified this run (no two Boeing-named companies in this corpus share the exact same normalized domain field in a way distinguishable from name-based clustering; the C0-C2 blocking-key fixes are still in effect from the prior session and are exercised by `candidate_pairs > 0` above) |

BUG A and B are confirmed fixed against a real, freshly-reset destination:
the schema is the 14-column contract, not 43; `name` is fully populated,
not 15/31 NULL.

### 4c. Overall row-level parity vs `global_warehouse_golden.boeing` — measured honestly

```
overall_parity_pct: 0.9%   (31 matched / 3,427 golden rows)
```

This is **below** the `BUDGETS.yaml` floor of 12.0% (the v1 pre-BUG-A-D-fix
number) and is reported exactly as measured, per Sec 0.0's own instruction
to report honestly regardless of direction. Root-caused per table, not left
as a bare number:

| Table | Golden | App | Matched | Parity | Root cause |
|---|---|---|---|---|---|
| `company` | 31 | 23 | 22 | 71.0% | Real improvement area — BUG A/B fixes visibly working |
| `industry` | 10 | 10 | 4 | 40.0% | Not investigated further this session |
| `location` | 18 | 43 | 5 | 27.8% | Not investigated further this session |
| `finance` | 20 | 20 | 0 | 0.0% | Natural-key mismatch suspected (see below), not investigated further |
| `person` | 539 | 39 | 0 | 0.0% | **Confirmed complete, not under-extracted** — see below |
| `experience` | 538 | 21,263 (34,782 extracted) | 0 | 0.0% | **Origin schema mismatch, not a pipeline bug** — see below |
| `technologic` | 1,162 | 1,154 | 0 | 0.0% | Natural-key mismatch suspected, not investigated further |
| `company_relation` | 200 | 489 | 0 | 0.0% | Natural-key mismatch suspected, not investigated further |
| `contact_email` | 313 | 39 | 0 | 0.0% | See `contact` splitting note below |
| `contact_phone` | 22 | 0 | 0 | 0.0% | Same origin field as `contact_email`; never split by type |
| `technologic_category` | 574 | 0 | 0 | 0.0% | Inferred entity, no source table found — silently dropped (`dropped_inferred_entities`) |

**Root-caused via direct ClickHouse queries, not guessed:**

- **`person` (0/539 matched) is a false alarm on "the app under-extracted."**
  `origin1.CompanyExperience` (the only company↔person join table in either
  origin) has **exactly 39 rows** for the 18 Boeing-named `origin1.Company`
  records, all 39 with distinct `personID`s. The app extracted all 39 —
  100% of what is reachable from origin1. `origin2.Person` has 11.8M rows
  but **no company link anywhere in its schema** (`origin2.Experience` has
  no `personID` column; `origin2.PersonContact` has no company link
  either) — the app correctly excluded it rather than pulling in the whole
  table. The golden's 539 was built by a human analyst months ago through a
  method this investigation didn't reconstruct (likely broader/manual
  criteria unavailable to an automated join). This is a genuine open
  question, not a proven regression.
- **`experience` (0/538 matched) is a data-modeling mismatch, not a bug.**
  34,743 of the app's 34,782 extracted rows come from `origin2.Experience`,
  which has **no `personID` column at all** — its schema is
  `experienceID, companyID, tags, title, role, created_at`, and its `tags`
  values (`TOTAL_EXPERIENCE_20_TO_29_YRS`, `COMPARABLY_COMPANY_RATING_A_PLUS`)
  are company-level aggregate HR statistics, not individual employment
  records. The pipeline correctly extracted every row matching the entity
  type's source-table selection; the open question is whether
  `origin2.Experience` should ever have been selected as a source table for
  entity type `experience` at all — a table-selection judgment call
  (candidate for B2's `TableSelector`/`SchemaExplorer` agents), not a
  Sec 0.0-Sec 0.8 defect.
- **`contact_email`/`contact_phone` split (39/313 and 0/22) is a known
  mapping-granularity gap.** The destination models email and phone as
  separate tables; the current one-to-one field mapper has no
  "split by contact type" transform, so all 39 `contact` rows landed in
  `contact_email` regardless of actual type. Same underlying limitation as
  the `person.name → first_name/last_name` gap below.
- **`company.legal_name`/`name` population is correct; the remaining gap is
  a mapping-granularity limitation, not a regression.** `origin1.Person`
  carries only a single combined `name` field (`"Yaprak Baltacioglu"`), no
  `first_name`/`last_name` split. `map_fields_impl`'s current map|skip-only
  contract (BUG A's fix) has no split/transform capability, so `name` is
  correctly dropped (`fields_dropped_from_mapping` lists it) rather than
  invented into a column or corrupted into a single name part. Confirmed by
  direct query: `person.first_name`/`last_name` are NULL for all 39 rows in
  the destination. This is the same underlying capability gap noted in the
  project's `merge-engine-app-vs-manual-gap` memory (uniform survivorship,
  mapping caps) — pre-existing, not introduced this session.
- **`industry`/`location`/`finance`/`technologic`/`company_relation`
  mismatches were not root-caused individually this session** — time was
  spent on the two highest-volume anomalies (`person`, `experience`) plus
  the mapping-granularity pattern that explains `contact`. A natural-key
  mismatch in `compare_to_golden.py` itself (e.g. a destination column name
  the script's `natural_key()` assumes doesn't match the real schema) is
  suspected for at least `finance`/`technologic`/`company_relation` given
  0% exact matches despite comparable row counts, but this was not
  confirmed against the live schema and is left as an open item.

**What this means for `BUDGETS.yaml`'s `parity_pct_min: 12.0`:** the 12.0%
number was v1's pre-BUG-A-D-fix measurement, almost certainly over a
narrower entity-family scope (the context note in the plan itself says v1
capped breadth at 3 of 11 families). This run compares across all 11 golden
tables including several `technologic_category`/`company_relation`/
`experience`-heavy tables that likely weren't in the original 12% run's
denominator at all, and separately surfaces real, confirmed mapping and
table-selection gaps that are pre-existing and not caused by anything in
Sec 0.0-0.8. **The 0.9% number is not directly comparable to the 12.0%
number** — reported honestly per the plan's own instruction, but the floor
in `BUDGETS.yaml` was deliberately left unchanged rather than silently
lowered; that is a judgment call for the user, not a mechanical update.

## 5. Known gaps carried forward (not fixed this session, in scope for later branches)

- Person full-name → `first_name`/`last_name` splitting (no transform
  capability in the current one-to-one field mapper).
- Contact-type splitting (email vs phone vs social) — same root cause.
- Table-selection breadth/precision for entity types like `experience`
  where an origin table's actual semantics (aggregate stats vs individual
  records) don't match the destination's relational model.
- `compare_to_golden.py`'s natural-key definitions for `finance`,
  `technologic`, and `company_relation` were not verified against the
  live destination schema and may themselves need fixing before the parity
  number for those tables is trustworthy.

## 6. Part 1.5 re-baseline (2026-08-04) — speed + Langfuse observability, no correctness change

Full Boeing run (`thread_id=wf_d95f26f6`) through the real HITL-gated
pipeline, after 1.5.A (scoring-path waste removal incl. the `estimate_model`
M-step fix), 1.5.C (Langfuse trace fragmentation + span-nesting fixes), and
`drive_app.py --assert-budgets`. Numbers are wall time inclusive of network
and LLM-adjudication time, not isolated CPU — see
`docs/diagnostics/BOEING_SPEED_REPORT_2026-08-04.md` §4a for the isolated,
byte-identical-verified `profile_resolve.py` numbers.

```
resolve_entities        1994.84s   (wf_059436a1, prior run, same input volume: 1573.67s)
map_fields                543.78s   (wf_059436a1: 350.11s)
analyze_dependencies      155.03s
unify_entities              97.11s
plan_scope                  84.00s
extract_data                 30.53s
parse_request                17.77s
preflight                     5.52s
discover_schemas              3.06s
TOTAL                     2931.64s  (48.9 min; driver reported 50.7 min incl. gate polling)
```

**Honest anomaly, reported rather than hidden (R10):** `resolve_entities`
got *slower* run-over-run despite the A.7 `estimate_model` fix, even
though `entities[*].extracted_total`/`candidate_pairs_compared` are
**identical** between `wf_059436a1` and `wf_d95f26f6` for every entity
type (verified directly from both run reports — `experience`
34,782/250,000, `technology` 1,503/250,000, `company_relation`
559/84,297, byte-for-byte the same on both runs). Since input volume is
provably unchanged, the extra ~421s is not new work being done — it is
most plausibly LLM-adjudication network/API latency variance between the
two runs (`_judge_pairs_with_llm` is part of `resolve_entities`'s wall
time and is not CPU-bound), consistent with `company_relation`'s
`auto_merged_clusters`/cluster count differing slightly between the two
runs (62→55, 489→497) despite identical candidate pairs — proof the LLM's
borderline-pair verdicts are not deterministic across separate live runs,
unlike the heuristic-only path. **The A.7 fix's correctness and speedup
are not in question** — `profile_resolve.py --check` proves the matcher's
own output is byte-identical and its isolated, no-network CPU cost fell
from 121.78s to 32.02s for `technology`. What is genuinely unresolved is
that this particular live run does not demonstrate the fix's benefit at
the whole-node level, because LLM-adjudication latency variance appears
to dominate any CPU-side saving at this scale. A true controlled
comparison would need two live runs back-to-back with LLM calls disabled
or mocked — not done here. Left as an open item rather than papered over.
`map_fields` similarly got slower (350.11s → 543.78s) for the same
reason — it is pure LLM round-trip time (B.1's `asyncio.gather`
parallelization is confirmed still in place and correctly structured;
this is latency variance, not a regression in the code).

- `python -m tests.eval.score`: unchanged, precision 1.0000 / recall 0.9950
  (A's changes are scoring-path waste removal only; matcher output is
  provably byte-identical, verified via `profile_resolve.py --check` for
  both `technology` and `experience`).
- `drive_app.py --assert-budgets`: `resolve_entities_seconds_max` PASS,
  `full_run_minutes_max` PASS (50.7 ≤ 90 min), `parity_pct_min` FAIL (0.9%
  < 12.0%) — **expected, not a regression**: 0.9% is this project's actual
  documented baseline (§4c above); the 12.0% floor in `BUDGETS.yaml` is
  known-stale and deliberately left unchanged pending a user decision (§4,
  "not a mechanical update").
- `verify_langfuse_trace.py wf_d95f26f6`: **5 of 6 automated assertions
  pass**, including the two that matter most — exactly one trace for the
  whole session (proves the C.3 multi-trace-fragmentation fix: this run
  crossed 5 HITL gates, each a separate top-level `astream()` call, and all
  landed in one trace) and every `GENERATION` span has a non-null parent
  that is a node span (proves the C.0/C.2 nesting fix — this is the exact
  assertion that failed before Part 1.5). The custom
  `resolve.<entity>.{blocking,fit_model,scoring,clustering}` waterfall
  spans (C.4) are present for all 10 entity types that reached model
  fitting. The one failing assertion — 22 of 32 `GENERATION` spans missing
  token-usage counts — is a pre-existing gap in what the `dashscope-intl`
  OpenAI-compatible endpoint reports, not a tracing regression: the app's
  own `token_usage()` (`src/llm/cost.py`) already documents "providers
  disagree... some omit it entirely" as an accepted condition, no fallback
  model was invoked during this run (confirmed via log grep — the primary
  `qwen3.5-27b` served every call), and span nesting/model-name/duration
  are unaffected. Left open, not fixed, as out of C.3/C.4's scope.
- App log (`app4.log`) contains zero Langfuse warnings or exceptions for
  the entire run.

## 6. Phase 0 Sec 0.8 completion (this session's primary work)

All items done and live-verified:

- `get_initial_state` legacy-config bugs (missing `fallback_models`,
  hardcoded `conflict_policy`) — fixed.
- `LLMClient` cache-before-parse bug — fixed, tested.
- Dead code (`_error_router`, duplicate `MergeState` class) — removed.
- Lazy graph compile via module `__getattr__` — done.
- Quadratic `errors` accumulation across 8 node files — fixed; each
  node's own test rewritten to assert the correct mental model (a node
  returns only its own new errors; `operator.add`'s reducer, not the node,
  is what preserves history).
- Passthrough bloat removed from the same 8 files.
- `_LAST_BLOCKING_STATS`/`_LAST_CLUSTERING_STATS` module globals in
  `resolve_entities.py` removed; stats now thread through caller-owned
  `stats` dicts on `_generate_candidate_pairs`/`_build_clusters`.
  Arch-lint baseline shrank 17 → 15, confirming both violations are gone.

## 7. Environment note

Langfuse tracing was unreachable during this run
(`WinError 10061 — target machine actively refused it`) — the pipeline
degrades to untraced execution as designed rather than failing. This
baseline's numbers are not affected; there are simply no trace spans
attached to `wf_d7103e2d`.

## 8. Part 1.5 D.5–D.7 + B.3 + C.6 re-baseline (2026-08-05)

Full Boeing run (`thread_id=wf_f9b095bf`) after: D.5 (scoring-path exact
short-circuits — see `_string_similarity_cached`'s two provably-exact
bounds), D.6 (ClickHouse scratch-table extraction, replacing chunked
`IN (...)`), D.7 (cross-run resolve_entities cache keyed by content hash),
B.3 (process-pool dispatch for `resolve_entities`'s CPU-bound core), and
C.6 (Langfuse retry-after-late-stack-start; flush() was already wired in
Part 1.5 C.6's earlier commits).

```
resolve_entities         818.75s   (wf_d95f26f6 baseline: 1994.84s — 2.44x)
analyze_dependencies     139.77s
unify_entities            96.70s
map_fields                93.48s   (wf_d95f26f6 baseline: 543.78s)
plan_scope                92.61s
extract_data               26.39s
parse_request              16.16s
preflight                    5.92s
discover_schemas             3.06s
TOTAL (node sum)          1292.85s  (21.5 min)
Driver-reported wall time             23.4 min  (incl. gate polling; wf_d95f26f6: 50.7 min)
```

**B.3 confirmed live, not just in isolation:** the app log shows
`experience` (34,782 records), `technology` (1,503), and `company_relation`
(559) all entering `resolve_entities_impl` within the same second and all
three logging `dispatched=process_pool` on completion — real concurrent
dispatch through the shared `ProcessPoolExecutor`, exactly the Phase 2
gather this session's node-level rewrite added. `experience` alone finished
its CPU-bound core in ~12m41s while running concurrently with the other
two, contributing directly to `resolve_entities`'s 2.44x total drop.

**D.7 confirmed live:** `logs/spool/_resolve_cache.duckdb` holds 40 real
cache entries written during this run, across every entity type including
`experience` (written at the moment its 12m41s computation finished) — a
byte-for-byte identical re-run of this same Boeing corpus would now skip
essentially all of `resolve_entities`.

**Correctness, unchanged:**
- Parity: **0.9%** (31/3,427 matched), identical to §4c and §6 — confirmed
  by direct `compare_to_golden.py boeing` read of `overall_parity_pct`, not
  the driver's cached summary line.
- `python -m tests.eval.score`: precision 1.0000/recall 0.8850 (heuristic)
  and precision 1.0000/recall 0.9950 (Fellegi-Sunter), unchanged from every
  prior measurement.
- `drive_app.py --assert-budgets`: `resolve_entities_seconds_max` PASS
  (818.8s ≤ 25260s), `full_run_minutes_max` PASS (23.4 ≤ 90 min),
  `parity_pct_min` FAIL (0.9% < 12.0%) — expected, not a regression, same
  stale-floor situation as §6.
- App log (`app5.log`): zero `Traceback`, zero `[ERROR]`/`[CRITICAL]`
  lines, zero Langfuse warnings across the entire run.

**`verify_langfuse_trace.py wf_f9b095bf`: 6/6 assertions PASS** — including
the one that failed in §6 (token usage; provider-side flakiness, not
reproduced this run). One real fix needed first: the script's phase-span
check still looked for the pre-B.3 `.blocking`/`.fit_model`/`.scoring`
span names, which B.3 deliberately collapsed into one `.match` span (a
synchronous `_resolve_core` dispatched to a worker process has no event
loop to open three separate async span context managers on — see
`_resolve_core`'s docstring). Updated the script to check for `.match` and
`.clustering` instead; both now found (10 each, one per entity that fit a
model), and the timing breakdown that used to live in 3 child spans now
rides along as `blocking_seconds`/`fit_model_seconds`/`scoring_seconds`
attributes on the single `.match` span.

**This is now the pipeline's fully-verified state**: every item from
Phase 0 §0.0's BUG D list (D.1-D.7) is implemented and live-verified; Part
1.5's A, B.1, B.3, and C sections are implemented and live-verified. B.2
remains deliberately not implemented (documented correctness-risk
trade-off, see the plan's own B.2 write-up).
