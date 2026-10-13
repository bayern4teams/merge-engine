# PRE-D2 verdict (attack) -- commit 893da8f `_code_tokens` / `_conflicting_codes`

**ACCEPT (scoped), keep the 0.93 cap.** The report's ask (variants must not AUTO-merge) is met on synthetic and real
Abt-Buy; two documented gaps and one cross-entity regression (below) are not blockers for PRE-D2 but must be known.
Waive derive check 1b (`test_variants_are_not_parked_just_under_the_bar`, 6 fail): it asks for more than the report
(a score below 0.90) and contradicts the established identity-conflict cap (0.93, `_score_pair`). Left unedited so the
disagreement stays visible; the lead decides the waiver (as D3 F1).

## Checks run
* `tests/part1/test_pre_d2_acceptance.py` with `ABT_BUY_DIR`: 36 pass, 6 fail (all 1b, value 0.93 exactly).
* `tests/part1/test_pre_d2_attack.py` (new): 35 pass, 5 xfail-strict (documented gaps), ~47 s with real data.
  Method: patching `_conflicting_codes` to False reproduces the parent exactly (it has only two call sites), so old and
  new are compared in one process on any corpus. The with-price synthetic case asserts the old behaviour DID auto-merge siblings.
* Suites `tests/part1 tests/unit tests/integration`, parent 22685c3 in a throwaway worktree vs HEAD: fail sets identical
  (22 pre-existing failures in both: PRE-D1 x13, D5/P6/P13/P14/D1/D2 literal gates, ...). The parent run had 3 extra
  `arch_lint` failures that are worktree artefacts (no `.venv` in the worktree). 3480 passed (HEAD) / 3477 (parent), 39 xfailed in both.
  No test went from pass to fail or fail to pass. (Worktree removed; an unrelated `.tmp/wt-d4` belongs to another agent.)

## Real Abt-Buy (2,173 records, 30,680 scored pairs, perfectMapping 1,097 true)
|  | parent | HEAD |
|---|---|---|
| auto (>=0.95) | 940 (4 true) | 335 (4 true) |
| review band [0.70,0.95) | 658 (32 true) | 1,262 (32 true) |
| false auto | 936 | 331 |

* Pair set identical (30,680). The 22,678 flagged pairs: all end < 0.95 (max 0.93, 943 pairs sit exactly at 0.93). Every
  unflagged pair score is bit-identical to the parent (0 of ~8,000 differ); flagged ones differ by < 5e-5 when they were
  already low (the conflicting branch returns `min(p, 0.93)` unrounded; cosmetic).
* Transitions: 605 auto -> band, **0 true**; 1 band -> low, 0 true; nothing else moves. 604 of the 605 are within-origin
  siblings (Abt-Abt / Buy-Buy), 1 cross-origin. True pairs lost from auto or band: **0**.
* Review load: +604 pairs (+92%), all false. Band precision 32/658 = 4.9% -> 32/1262 = 2.5%.
* 40 of 1,097 true pairs have conflicting codes (27 reach scoring). 39 were already < 0.70 (model rejects them, e.g.
  manufacturer part number vs retailer SKU: `PG40TBK` vs `0615B013`); 1 sits in the band (0.832,
  `CHS4001` vs `CHS40001`, a typo).
* LLM budget (`_MAX_LLM_PAIRS` = 200, nearest the band middle): top-200 is identical before/after (7 true of 200, none at 0.93),
  so the new 0.93 pairs do not crowd out genuine borderline pairs.

## 0.93 vs exclude (< 0.70), numbers
Exclude would drop the band 1,262 -> 236 (31 of 32 true kept) and cost exactly **1 true pair (3% of the band, 0 of auto)**.
Gain: ~1,026 fewer pairs to review, none true. Benchmark-wise exclusion looks better, but it is not reproducible on other data:
the 39 low true pairs show the failure mode (two code namespaces for one product); in a corpus where price/description
agree, such a true pair would be dropped irreversibly under exclude and merely reviewed under 0.93. 0.93 is also the
existing treatment of a contradicting declared id. Recommendation: 0.93 now; if the review queue is the pain, fix it
in the review UI/ordering (group sibling pairs), not by hiding pairs. Caveat: with the LLM judge on, a 0.93 pair that the LLM
answers `p >= 0.95` is auto-merged (`_judge_pairs_with_llm`, same for declared conflicts), so "never auto-merge" holds
only for the heuristic path; excluded pairs would never reach the judge.

## Attacks (all in the attack file)
Pass: case, separators, split codes (`20-WL BK`), `4 GB` vs `4gb`, code on one side only, one code containing the other,
`3M`/`A1 Telekom` vs their longer forms, empty/None/non-str/odd unicode input, symmetry (2,000 random pairs), different name
field per origin, no numeric constant in either new function, no word list in code, person records (given_name/surname/
address with `12a`, `apt 4b`) untouched (0 flagged, scores identical), tiny 2-record corpus without a model.
Conflict (by design): `4GB` vs `8GB`, `720p` vs `1080p`, `3M Company` vs `A1 Telekom Austria`.
Gaps (pinned xfail-strict; they fail the suite when someone closes them):
1. `X100` vs `X1000`: the squashed-containment test hides a base code that prefixes the variant's code. Same hole is why 228
   of the 331 residual false autos survive (`DVPFX820` vs `DVPFX820R`, `MB15-PNK-SEE-V2` vs `MB15-ORG-SEE-V2`).
2. Hyphen splits a code: `WD-65736` is `wd` + digit-only `65736`, never a code (`Mitsubishi WD-65736` vs `WD-60735` stays auto, 1.0).
   Digit-only models (`Q 300` vs `Q 320`) likewise. 103 residual false autos have a code on one side only.
3. Code only in the description is invisible (only the best name field is read).
4. **Regression outside products:** a code conflict now beats a shared deterministic key. Two orgs `Zorvex Holdings A1` /
   `... B2` sharing a website: parent 1.0, HEAD **0.010** (below the exclude bar, not 0.93, because `conflicting` also skips
   the deterministic match and `_three_way` honours it as declared). Contradicts "weaker than a declared id". Rare (needs
   disjoint codes in both names) but unguarded. Also hazard: `_best_name_field` takes the FIRST hint match in dict order, so a
   record with `street_name` before `given_name` makes `12a` vs `12b` a conflict.
5. Nit: the `_conflicting_codes` docstring quotes corpus codes `20WLBK`/`20WLWH` (the AST gate excludes docstrings; xfail test).

## Implemented, unverified (needs live runs; the user must re-run)
* Abt-Buy through the real pipeline with the LLM judge ON (all numbers above are `_resolve_core`, LLM off): merged-cluster
  size (report's cluster 16) and precision/recall; the new 0.93 pairs mostly overflow the 200-pair LLM budget and keep 0.93.
* Amazon-Google (same product shape, not run), DBLP-ACM / DBLP-Scholar (`title` is the name field; titles with `P2P`/`3D`/`B2B`
  tokens could flag), Febrl/NC-voters persons (guards pass on synthetic only), any org dataset with a website/ticker key (gap 4).
* Cluster-level figure from the report (largest cluster 16 -> ?) is a `_build_clusters` output, not measured here.
