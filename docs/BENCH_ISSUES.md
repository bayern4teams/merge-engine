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
