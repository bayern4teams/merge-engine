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
