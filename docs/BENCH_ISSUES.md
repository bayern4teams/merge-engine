# Recorded benchmark issues (with reproduction) — merge-engine vs box suites

## I-1  parse_request entity-name flake → zero mapping coverage → guardrail refuses load (dblp_acm, full request)

Severity: P1 for this suite — kills ~1/3 of runs; the two entities of the run
name differently across identical runs:

- s1dblp  (b3a83b6): resolve ran entity **'publication'**, 287 borderline pairs.
- s1dblp2 (c811d5e): mapping_coverage measured **0.8** (entity matched the
  destination table `publication`); run later killed by a driver bug (I-2).
- s1dblp3 (1dbe5a1-dirty, D5 fixes in progress): parse_request extracted
  `target_entities: ['paper']` (log: parse_request:271). Every stage followed
  the wrong name: `map_fields` looked up destination columns for entity
  'paper' — none exist (the fixed destination contract table is
  `publication`) — so the LLM mapped **0/5 fields** ("no destination column
  available in the provided schema"), the pre-load guardrail fired
  (`mapping_coverage = 0, threshold 0.1`) and refused to load.
  **The guardrail did exactly its job; the defect is upstream.**

Root-cause pointer: `parse_request` LLM takes the entity name from the SOURCE
table names (`public.papers`) instead of the destination contract / declared
`entity_types: [publication]`; it is non-deterministic which name it emits.
Observed rate on identical full-request runs: 2/4 (s1dblp, s1dblp2 got
'publication'; s1dblp3, s1dblp4 got 'paper' despite the request text saying
"merge all publication records" — the LLM renames publication->paper from the
source table). Fix shape (owner call — not mine mid-benchmark): reconcile
extracted target_entities against destination table names (or suite
entity_types) before propagating, with the destination contract winning.

**FIXED (owner assigned 2026-11-26).** Reconciliation now happens inside
`parse_request._finish` — the destination contract wins, deterministically:
(a) a group name resolving to a contract table via the existing alias
vocabulary takes that table's own name (added the scholarly-publication
alias group so `paper`/`papers` resolve to `publication`); (b) when the
contract is EXACTLY ONE table, every source group folds into it (the
destination says: these sources describe one thing). Ambiguous contracts
never fold. Unreadable contract → naming stands and the guardrail remains
the backstop. The `entity_tables` cache fingerprint is now destination-
scoped so a name cannot replay against a different destination DB.
Proof in `tests/unit/test_parse_request_node.py::TestDestinationContractNaming`:
BOTH captured flake answers ('paper' from s1dblp3, 'publication' from
s1dblp5) replayed through the node emit `['publication']` with membership
intact, and the real `pre_load_gate` coverage invariant passes on the
contract name the reconciled run now carries.

Reproduction (needs the box):

```
cd ~/Projects/merge-engine && .venv/bin/python .tmp/bench_tools/run_driver.py \
  dblp_acm --request full --allow-draft --tag repro_i1 --max-minutes 170 --port 7881
# expect ~1/3 of runs: debug.log "Extracted target_entities: ['paper']" then
# "Field mapping for 'paper': 0 mapped" then load_data refusal.
# evidence of a bad run: .tmp/benchmark_runs/dblp_acm/20261125-1702_1dbe5a1-dirty_s1dblp3_full_DRAFT
```

## I-2  (harness, FIXED) driver overrode the gate's proposed thresholds

run_driver confirmed human_final_approval with hardcoded
`flagged_clusters: 1.0` while the run measured 17 (gate itself proposed 34 —
data-derived `2*median`). s1dblp2 died on this, not on an app defect. Fixed:
driver now confirms the gate's own proposed thresholds verbatim.

## I-3  (harness, FIXED) driver teardown crash

`proc.wait(timeout=30)` raised after SIGTERM teardown, losing score output of
an otherwise-completed run (s1dblp). Fixed: escalate to SIGKILL.

## I-5  shared family code masks a model-code conflict (over-merge, destructive direction)

`_conflicting_codes` calls codes conflicting only when NO code of one name
appears anywhere in the other. Verified live (in-process, this tree):
"…Media Player - MB562LL/A" vs "…MB565LL/A": code sets
{mb562ll, 120gb} / {mb565ll, 120gb}; the shared `120gb` makes
`_conflicting_codes` False, the pair scores 1.000 and auto-merges. Same for
"Samsung YP-S2ZW" vs "YP-S2ZG", "…- Sil" vs "…- Bla" (no codes at all),
"LaCie … 301278" vs "301829". This is the abt_buy FP class in the D1
bake-off driver (top FPs all at conf 1.0) and plausibly part of dblp_acm's
must_not_auto_merge 41/46 + must_not_merge 252/270 misses. Fix shape
(owner call): per-code conflict should ignore codes shared by BOTH names
(the pair shares 120gb, differs on mb562ll/mb565ll — a model variant), i.e.
compare the code sets after removing the intersection, mirroring how
shared rare-name tokens are already handled. Not touched mid-benchmark.
Repro: `python -c "from src.mcp_server.tools.resolve_entities import
_conflicting_codes as f; print(f({'name': 'x 120GB - MB562LL/A'},
{'name': 'x 120GB - MB565LL/A'}))"`  # prints False, wants True

## I-4  (pre-existing, HEAD) numeric-literal gates red at HEAD

test_p14_acceptance::test_6c fails at HEAD (introduced by 2f90c83:
`_SOFT_CALLS_PER_TOKEN = 8` unbaselined), and every "vs parent" gate that
re-runs it (p10/p13/p17/d1/d2/d13/d15) fails transitively. Baseline suite
run (clean HEAD) proves it is not from the D5 fix.

## I-6  dblp_scholar exceeds the candidate-pair guard in-process (D1 blocker)

The D1 bake-off on full dblp_scholar (66,879 records: dblp 2,616 + scholar
64,263) dies in `_generate_candidate_pairs`:
`CandidatePairBudgetExceeded: Blocking retained more than 5000000 distinct
candidate pairs` — `_MAX_CANDIDATE_PAIRS = 5_000_000` is the app's
out-of-memory guard and it refuses to truncate silently (correct app
behaviour; the bake-off must respect it). Measured ladder (prefix n ->
retained pairs): 8,000 -> 174,743; 16,000 -> 499,217; 24,000 -> 936,329;
32,000 -> 1,488,715; 66,879 -> > 5,000,000 (guard fires; growth ~O(n^1.9),
so the guard binds somewhere around 50k records for this corpus). The bake-off
row is labeled SUBSAMPLE at n=8,000.
Consequence for the app: whatever the suite's real app path does with this
guard decides whether Stage 1 can score dblp_scholar at all — the guard
error names the levers (tighter blocking keys / raise the guard), owner
call. The bake-off will NOT monkey-patch the guard upward on the full set
without stating it.

**Policy exercised (2026-11-26, owner instruction "8k-subscribe policy so a
5M-pair-suite row exists honestly"):** new draft package
`dblp_scholar_sub8k` — the documented prefix (dblp 2,616 + scholar 5,384 =
8,000 records) with truth FILTERED from dblp_scholar's own labels (closed
entities only for destination truth). Builder stored at
`scratch/merge-engine/build_sub8k.py` on the box. selfcheck green,
load_origins zero mismatches, Stage-1 row completed — docs/BENCH_SCORECARD.md
row 4. The guard itself remains untouched app behavior; full-corpus scoring
still awaits the owner's lever choice.

## I-7  (harness, FIXED) driver died on one timed-out status poll

`s1amaz` (20261125-2335_a5911a1) died mid-judgement (~62/200 LLM pair
calls) when ONE `/api/workflow/<tid>/status` poll exceeded the driver's 60 s
read timeout — the app can block a status read behind long synchronous work.
Fixed in run_driver.py: poll failures print and retry instead of raising
(same crash also killed the first s1dblp attempt at teardown, see I-3). The
underlying app behavior (a status endpoint that can block > 60 s) is an
observation for the owner, not a benchmark blocker.

## I-8  amazon_google app run dies after match-model fit (twice)

s1amaz2 (20261125-2346_3e39933) survived every harness poll (I-7 retry
worked: ~40 ReadTimeouts logged) but the APP went silent 2 min after
"Match model fitted over 359326 candidate pairs (match_prior=0.001)" — last
log 23:48, process later found gone; the machine was at 14/15 GiB RAM with
load 300+ at the time (my own dblp_scholar probes contributed). Driver
timed out at budget with 0/1553 must_merge — the run never got past scoring.
s1amaz died at the same resolve stage one attempt earlier. Observation for
the owner: in-process scoring of a 359k-pair amazon_google candidate set is
where this suite dies; the abt_buy run (30k pairs) passed the same stage.
Root cause (OOM vs hang) unconfirmed — no dmesg/journal access here.

**RESOLVED by retry with instrumentation (s1amaz3, 20261126).** Third run
on the same suite/tree completed in 549.4 s (ER 0.055/0.077/0.064). RSS of
the app's whole process tree, sampled every 15 s (run_driver.py `rss.log`):
peak **1.46 GiB** across the run, plateaus ~0.9 GiB while scoring the 359k
candidate pairs — no OOM trajectory anywhere. Host at the time: ~8.8/15 GiB
used with my probes gone. The two earlier deaths were the host under
self-inflicted memory pressure (parallel D1/dblp probes), not an app defect:
**I-8 closes as environment, not app.** Keep the sampler: any future silent
death now carries its own evidence.

## I-9  mapper maps ONE origin of a multi-origin same-shape suite, siblings UNMAPPED

Two independent Stage-1 runs, same failure shape, no intervention between
them:

- febrl (s1febrl): mapping 20/60 = 0.333. `scores.json` wrong list = 40
  UNMAPPED misses, exactly 10 columns for each of origins a/d1/d2/d3; only
  origin b's person table mapped. Mapped-side precision 1.0, recall 0.2.
  Conflicts rode down with it: 12783/17030 = 0.751 (unmapped columns cannot
  conflict-resolve).
- geo_settlements (s1geo): mapping 14/32 = 0.438. Wrong list = 18 UNMAPPED
  misses, exactly 6 columns each for dbp/fb/nyt; only gns mapped — and gns
  is the HIGHEST-priority origin (`origin_priority: {gns: 2, ...}`), so
  destination-cell damage is smaller than febrl's (0.818 cell accuracy,
  conflicts 2306/2858 = 0.807 still depressed).

Shape: when several origins expose near-identical table shapes to one
destination entity, the field mapper emits mapping rows for ONE origin and
leaves every sibling column UNMAPPED — while ER on the same runs stays high
(febrl 0.991/0.999/0.995), i.e. entity resolution sees all origins but the
column mapping does not. The I-1 fix (destination-contract naming) is in
both runs' tree; this is downstream of it, in mapping, not parse_request.
Attribution inside map_fields is NOT established (needs a focused repro —
single candidate: map_fields maps only the priority-top table per entity).
Cost is measured (the two mapping/conflict numbers above). Priority: blocks
any mapping-accuracy acceptance on every multi-origin suite (most of the
remaining program).

## I-10  single-column within-origin suite: match model fits to match_prior=0.000 and excludes EVERY candidate pair

affiliations (s1aff, `20261126-1623_80fee52_s1aff_full_DRAFT`): one origin,
one business column (`affil1`), 2,260 records, 16,795 truth must_merge pairs.
Log chain (debug.log, all within 4 s):

- `_generate_candidate_pairs: 30593 pairs … from 865 blocks` (blocking fine);
- `fellegi_sunter:518 — Estimated match model over 30593 pairs:
  match_prior=0.021, fields=['affil1']` (init sane);
- `resolve_entities:1666 — Match model fitted over 30593 candidate pairs
  (match_prior=0.000)` (fit COLLAPSED to zero);
- `resolve_entities:823 — Built 2260 clusters: matched=0, excluded=30593`.

Scored row: ER P undefined / R 0.000 (0/16795 must_merge), cell accuracy
0.214, conflicts 69/323. The app's own guard rails were clean (must_not_auto
87/87, must_not 91/91) — it achieved them by merging NOTHING. Mapping 3/3
(the single-origin case is exactly where I-9 cannot bite).
Shape: EM over a one-field comparison space converges to the degenerate
zero-match fixed point, then every scored pair falls under the exclude line.
Root cause inside the fit NOT established (would need the m/u tables; one
run's evidence). Cost: the entire suite's recall (0/16795) — an ER floor
equal to zero for single-column within-origin data.
