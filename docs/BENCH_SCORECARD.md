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

**Crosswalk note:** google_v1 crosswalk scoring demands the truth's XW row
ids, which no app run can observe; emitting app cluster ids would be
guaranteed false positives, so predictions omit the kind (recall 0 recorded
honestly). This is a suite/scoring observation for the owner, not a key edit.

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
