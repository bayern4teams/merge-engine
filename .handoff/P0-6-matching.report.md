# P0-6 matching baseline (derive / baseline) - BEFORE numbers on current code

Branch feat/part1-fixes, nothing under src/ touched. Python .venv311 (3.11).
Reproduce everything:
`export TMPDIR=$PWD/.tmp; .venv311/bin/python -m tests.part1.baseline_matching` (writes
tests/part1/before_matching.json, ~1 s, deterministic: diffed across 3 runs incl. PYTHONHASHSEED 1/5/99).
Scorer self-proof: `.venv311/bin/python -m tests.part1.scorer` (or `pytest tests/part1/scorer.py`).
Files: tests/part1/{__init__,scorer,fixtures_matching,baseline_matching}.py, before_matching.json.
Section 2.4 literal grep over my files + json: clean. ruff lint/format: clean on my files.

## Scorer (scorer.py)
Pair-level. `score_clusters(pred, truth, source_of)` -> for all / cross_source / within_source:
tp, fp, fn counts plus the exact fp_pairs / fn_pairs. Counts only, no rates.
Self-proof output: PASS identity (tp2 fp0 fn0, no pairs named); PASS injected FP (merge c1+d1 ->
fp=1, fp_pairs=[(c1,d1)], in cross_source stratum only); PASS injected FN (split b1/b2 -> fn=1,
fn_pairs=[(b1,b2)], in within_source stratum only).

## P2 - block larger than _MAX_COMPARISONS (250,000)
Shape: 800 records, every name `widget <token>`, one block of C(800,2)=319,600 pairs. 40 planted
duplicate pairs (second name = first with one letter dropped, so no small exact-value block rescues
them), shuffled, so spread through the block. Call: `_generate_candidate_pairs(recs, stats=stats)`.
BEFORE:
- candidate pairs returned 250,000; pairs truly discarded (unique) 69,600
- planted true pairs found 31/40; **9 lost to the cap** (pair completeness 31/40). Lost pair ids are in the json.
- stats = {uncompared_pairs: 389200, candidate_pairs: 250000, blocks_used: 2, blocks_purged: 2}
- nothing raised; exactly 1 logger WARNING; the run result only echoes the number (load_data.py:165 copies it).
Surprise: reported uncompared_pairs (389,200) is 5.6x the true discard (69,600). Two keys
(snd:w323, tok:widget) each yield the same 800-member block; the overflow estimate counts the second
block in full (it is not deduped against the first). The code comment calls it "a lower bound";
here it is an over-count. Any after-fix check on "reported == actual" will see this.

## P6 - three-way company set
Truth/expected: (a) same email -> Match -> auto-merged; (b) same address, different tax id ->
Possible Match -> review band (neither auto-merged nor silently rejected); (c) same address+phone,
different company -> Possibly Related -> not merged, not sent to a human. Run through
`_resolve_core` (score) and `resolve_entities_impl` with no LLM config (outcome; resolve_cache.put
patched out, cache disabled). Two runs per case: isolated (2 records, heuristic path, no model fit) and
with 14 filler companies (4 filler duplicates) so the Fellegi-Sunter model fits (model_fit=true).
| case | isolated | with model fit |
|---|---|---|
| a same email | 1.0 auto-merged (meets) | 0.7594 review band (MISSES: a Match is sent to review) |
| b diff tax id | 0.775 review band (meets) | 0.0151 **excluded, silently** (MISSES: Possible Match rejected) |
| c same addr+phone, names differ | never a candidate pair (not co-blocked) -> excluded | same |
| c2 same, but names co-block (`Kestrel Supply` vs `Kestrel Enterprises`) | 0.667 excluded (meets) | 0.3227 excluded (meets) |
Notes: (b) isolated lands in review only through the heuristic path; as soon as a model fits, the
conflicting tax id caps probability and the pair falls far below auto_exclude=0.70, i.e. exactly the
"silently rejected" outcome the report forbids. (c) "meets" is weak: address and phone are not
blocking fields, so the pair is never even compared; c2 was added to score it. No typed
"Possibly Related" edge exists anywhere, so (c) cannot be verified as an edge, only as not-merged.
Caveat: the filler variant is shape-sensitive (an unsupervised model on 14 records); treat its exact
scores as indicative, the outcome class is what the after-run should compare.

## P20 - giant-component bridge (scored pairs fed to `_build_clusters`, async, via asyncio.run)
Two true clusters of 5 (complete internal edges, mixed sources), then:
| variant | largest cluster | cluster sizes | tp / fp / fn (all) |
|---|---|---|---|
| bridge_weak: internal 0.98, one bridge 0.96 (above auto_match 0.95) | 5 | 5,5 | 20 / 0 / 0 |
| chain_via_singleton: a0-x-b0 at 0.96 | 5 | 5,5,1 | 20 / 0 / 0 |
| bridge_stronger_than_internal: internal 0.96, bridge 0.99 | 2 | 2,1x8 | 0 / **1** / **20** (fp pair a0-b0; fn 12 cross + 8 within) |
| sparse_true_cluster_chain: one true 5-cluster seen only as a 0.97 chain (not a report shape) | 1 | 1x5 | 0 / 0 / **10** |
Surprise: the report's own shape (bridge_weak) is ALREADY handled - `_MIN_COHESION` average-linkage
cut drops the weaker bridge. largest_component_size before the split is 10/11 (the giant component
is formed, then cut). The current code fails the other two: when the wrong edge is the strongest
it keeps only that edge and shatters both true clusters; and any true cluster that blocking only
saw as a chain/sparse graph is destroyed because missing edges count as 0 in average linkage.
So P20's acceptance should include bridge_stronger_than_internal and sparse_true_cluster_chain, not
just bridge_weak (which a no-op would already pass).

## Could not reproduce / limits
- Real giant component scale (thousands of nodes) and live-corpus numbers: not synthesisable here.
- P2 uses a flat name field only; the ordering of lost pairs depends on record order in the block
  (deterministic here, seeded shuffle).
- The splink engine path and the LLM adjudication path are not exercised.
