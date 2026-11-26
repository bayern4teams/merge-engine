# Benchmark scorecard — merge-engine vs the ground-truth suites

Scored by the box's own tools only (`playbook/merge-engine/tools/score.py` via
`run_driver.py`; google_v1 additionally by its `evaluation/evaluate.py`).
App runs on the laptop against origins on `template@10.10.9.41`. Every score
carries the flag **`provisional-key`** except `google_v1` (owner-final).
`—` = metric not produced for that run. Wall = end-to-end run wall-clock.

| suite | key | tag | wall | pairwise P/R/F1 | cluster F1 | conflict acc | ambig-F1 | relation acc | xw P/R | mapping acc | run id / repro |
|---|---|---|---|---|---|---|---|---|---|---|---|
| google_v1 | **final** | stage0g_scoped | 1997 s | same 0.000/0.000/0.000; related 1.000/0.075/0.138; different 1.000/0.062/0.116 | — | 1.000 (7/7) + abstain 1/1 | 0.000 | 1.000 (2/2 emitted) | 0/0 (see note) | 0.511 | `20261125-1529_2d1101e_stage0g_scoped`; repro in `docs/BENCH_STAGE0.md` |
| google_v1 | final | stage0f_full | 1700 s | not emitted (see BENCH_STAGE0 findings 2–3) | — | 15/16 (driver) | — | — | — | 0.398 in-process arm | `20261125-1451_2d1101e_stage0f_full` |
| dblp_acm | provisional-key | s1dblp5_full | 1229 s | 0.925 / 0.956 / 0.940 | (must_merge 2127/2224; must_not_auto 41/46; must_not 252/270) | 3829/4081 = 0.938 | — | — | — | 0.667 | `20261125-1759_906e9bb_s1dblp5_full_DRAFT`; repro below¹ |
| abt_buy | provisional-key | s1abtbuy_full | 265 s | 0.043 / 0.015 / 0.022 | (must_merge 17/1118; must_not_auto 23/24; must_not 50/124) | 1749/1905 = 0.918 | — | — | — | 0.727 | `20261125-1825_d0cdd42_s1abtbuy_full_DRAFT`; same repro, bench=abt_buy² |
| amazon_google | provisional-key | s1amaz3_full | 549 s | 0.055 / 0.077 / 0.064 (missing 0) | (must_merge 119/1553; must_not_auto 40/295; must_not 252/423) | 2144/3094 = 0.693 | — | — | — | 0.750 | `20261126-1514_84ec8df_s1amaz3_full_DRAFT`; same repro, bench=amazon_google³ |
| dblp_scholar_sub8k | provisional-key | s1ds8k_full | 542 s | 0.458 / 0.257 / 0.330 (missing 0) | (must_merge 149/579; must_not_auto 1/1; must_not 16/17) | 178/180 = 0.989 | — | — | — | 0.667 | `20261126-1535_84ec8df-dirty_s1ds8k_full_DRAFT`; same repro, bench=dblp_scholar_sub8k⁴ |
| febrl | provisional-key | s1febrl_full | 190 s | 0.991 / 0.999 / 0.995 (missing 0) | (must_merge 13953/13972; must_not_auto 76/76; must_not 100/100) | 12783/17030 = 0.751 | — | — | — | 20/60 = 0.333 | `20261126-1614_b5b0dbe_s1febrl_full_DRAFT`; same repro, bench=febrl⁵ |

**Crosswalk note:** google_v1 crosswalk scoring demands the truth's XW row
ids, which no app run can observe; emitting app cluster ids would be
guaranteed false positives, so predictions omit the kind (recall 0 recorded
honestly). This is a suite/scoring observation for the owner, not a key edit.

¹ **dblp_acm repro:**
`cd ~/Projects/merge-engine && .venv/bin/python .tmp/bench_tools/run_driver.py dblp_acm --request full --allow-draft --tag <tag> --max-minutes 170 --port 7881`
(tree `906e9bb`: D5 cap + D9 stats in). **I-1 flake:** 2 of the 5 identical
launches died before load because `parse_request` renamed the entity
`publication`→`paper` (docs/BENCH_ISSUES.md) — the completed row is a
'publication' draw; treat run-to-run *completability* (not the numbers) as
the flake-affected metric. Numbers note: pairwise F1 0.940,
must_not_auto_merge 41/46, must_not_merge 252/270 on the D5-fixed tree —
the 5+9 misses are over-merges of the forbidden classes (the destructive
direction; per-pair attribution not verified — scores.json carries no pair
detail).

² **abt_buy:** same command with `bench=abt_buy`. Honest headline: this suite
is the app's weakest terrain — pairwise recall 0.015 (17 of 1,118 must_merge
pairs merged). Blocking reached 30,680 candidates from 2.36 M, but true
Abt-Buy duplicates score below the auto bar: long noisy product names with
model-code variants where `_conflicting_codes` is masked by shared family
codes (docs/BENCH_ISSUES.md I-5) while the IDF/name signal is too weak to
carry 0.95 alone. 1,262 borderline pairs exceeded the LLM budget (200);
1,062 kept heuristic scores. The in-process D1 bake-off (four comparator
arms) moves nothing here (best F1 0.0164, A-tree) — the gap is the
comparator's mid band + the code rule, not the soft-compare budget.

³ **amazon_google:** same command with `bench=amazon_google`, tree `84ec8df`
(I-1 fix in). Two earlier attempts died (I-8); the instrumented retry
completed — peak app-tree RSS 1.46 GiB, deaths attributed to host memory
pressure from my own parallel probes, not the app (docs/BENCH_ISSUES.md I-8).
Same product-suite floor as abt_buy: recall 0.077 (119/1553 must_merge),
and I-5 code masking applies here too; destination cell accuracy 0.767,
conflicts 2144/3094 = 0.693 is the suite's noisiest-field load (description).

⁴ **dblp_scholar_sub8k:** the honest 5M-pair-suite row the owner asked for. The
full dblp_scholar corpus hits the app's own 5,000,000-pair OOM guard
(I-6) — instead of monkey-patching the guard, this run uses a new draft
package, `dblp_scholar_sub8k`: the exact D1/`I-6` documented prefix (dblp's
2,616 rows + scholar's first 5,384 = 8,000 records → 174,743 candidate pairs
in the D1 measurement, 152,113 here), with truth FILTERED from dblp_scholar's
own labels (hard pairs need both ends; destination/conflicts cover CLOSED
entities only — survivorship never depends on unloaded records; box
`selfcheck.py` green, `load_origins.py` zero mismatches, owner approval still
pending like every draft). The app row independently corroborates the D1
in-process bake-off on the same prefix (A_tree 0.439/0.259/0.326 vs app
0.458/0.257/0.330). Mapping 8/12: the 4 misses are `dblp.papers.{title,
authors,venue,year}` expected→UNMAPPED by the scorer; the gate payload lists
mapping rows keyed `public.papers` without per-origin attribution, so the
cause (per-origin rows not emitted vs mapper skipping dblp's bare names) is
NOT established from this run — recorded as an observation, not filed.

⁵ **febrl:** first Stage-1 row on a 5-origin suite (21,000 records; truth
pairs collide across files BY DESIGN — `benchmark.yaml` header: rec numbers
repeat, only d1/d2/d3 within-file and a↔b cross-file links are truth). ER is
the mission's best so far (0.991/0.999/0.995, must_merge 13953/13972, every
forbidden-pair guard clean: 76/76 + 100/100). The weak column is mapping
20/60 = 0.333 — `scores.json` wrong list is EXACTLY 40 UNMAPPED misses, 10
columns each for origins `a`, `d1`, `d2`, `d3`: the app mapped only origin
`b`'s person-shaped table and left the four siblings' columns unmapped
(same UNMAPPED-miss shape as footnote 4, at 4× scale; mapped-side precision
1.0, recall 0.2). Conflicts 12783/17030 = 0.751 rides the same cause
(unmapped columns can't resolve). Single-run observation, not filed as an
issue.

## Suite-level verdicts so far

- **google_v1 (final):** plumbing proven (see `docs/BENCH_STAGE0.md`).
  Bottleneck is scoped-extraction reach (scope plan seeded 2/10 family
  companies; LLM timeouts force heuristic fallback), NOT matching quality:
  everything the app did see it mostly got right (conflicts 7/7, relations
  2/2, `different`/`related` emitted at precision 1.000).
- Crashes/defects recorded with reproduction: dependency LLM timeout
  fallback, scoped link-path seed loss, unprojectable-row mass in the full
  request (BENCH_STAGE0 findings 1–3), blind-approval gate refusal
  (APP_FINDINGS #3/#4, confirmed live and worked around in the driver by
  explicit threshold confirmation).

## Queue (stage order per the suite authors)

dblp_acm (running) → abt_buy, amazon_google, febrl, geo_settlements,
affiliations, pagila, chinook, musicbrainz20k, diffbot, zoominfo, linkedin →
diffbot_zoominfo, diffbot_linkedin, zoominfo_linkedin → gleif_sirene,
gleif_norway → diffbot_zoominfo_linkedin (last).
