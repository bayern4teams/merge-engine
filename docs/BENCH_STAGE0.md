# Benchmark Stage 0 — plumbing proof on `google_v1` (2026-11-25)

Goal (mission Stage 0): run the REAL app end-to-end on `google_v1`, emit the
long-form predictions CSV, and have the suite's `evaluation/evaluate.py`
parse and score it. Compared against `evaluation/example_scores.json` (the
suite's worked example) — a gross gap is ours, not the key's.

## Result: PLUMBING PROVEN, QUALITY LOW (measured)

Two runs completed end-to-end (status `completed`), predictions emitted,
`evaluate.py` scored them with `missing_pair_predictions: 0` — the format
contract is met.

| run | request | wall | outcome |
|---|---|---|---|
| `20261125-1451_2d1101e_stage0f_full` | full | 1700 s | completed; 25 entities; massive unprojectable-row losses (see findings) |
| `20261125-1529_2d1101e_stage0g_scoped` | scoped (Google LLC family) | 1997 s | completed; scored below |

`evaluate.py` on the scoped run (predictions emitted by
`.tmp/bench_tools/emit_predictions.py` from the destination DB +
`merge_engine_entity_map` provenance):

| metric | example | stage0g app |
|---|---|---|
| pairwise same P/R/F1 | high (worked example) | 0.000 / 0.000 / 0.000 (15 FN) |
| related F1 / different F1 | — | 0.138 / 0.116 |
| ambiguous-detection F1 | — | 0.000 |
| relation accuracy | — | 1.000 (2/2 emitted; 10 GT total) |
| crosswalk P/R | — | 0 / 0 (app cannot observe XW ids — see note) |
| conflict resolution accuracy | — | **1.000 (7/7)** + ambiguous abstention 1/1 |
| mapping accuracy (driver scores.json) | — | 0.511 (68/133) |
| destination cell accuracy | — | 0.247 |
| hard pairs must_merge | — | 0/15 |

Honest abstentions: pairs the app never mapped are emitted BLANK (scored as
misses, never fabricated); 267/278 pairs were blank because the scoped
extraction only reached 2 company records — the bottleneck is scope, not ER.

## Findings (each reproduced from the run logs)

1. **LLM timeouts corrupt the fallback path.** `Request timed out.` hit
   dependency analysis and LLM scope planning (300 s timeouts against the
   served `qwen3.8-flash-next`); both silently fell back to heuristics. The
   heuristic v3_discovery scope plan for `Google LLC` planned
   `{company: 5, contact: 1, staff: 1, product: 1}` — the truth's scoped
   `in` set is 10 companies (`truth/steps/scope_rows.csv`), and extraction
   finally delivered only **2** company records → ER compared 1 pair,
   formed 2 clusters, merged nothing → `same` recall 0 is arithmetic.
2. **Scoped link-path seed defect:** `link_path's seed column
   'parent_company_id' for db_core:public.audit_log was never captured from
   the subject table's own extraction` — scoped walks lose tables wholesale.
3. **Full-request load losses (stage0f):** 18 destination tables "cannot
   hold its linkage" (field mapping never produced their columns) → ~70
   rows held back; only `company` (16 rows) and `contact` (6) loaded.
   `Dependency analysis failed: Request timed out.` in this run too.
4. **Crosswalk scoring is unwinneable from the app side** (XW ids are truth
   row-identifiers; no run can emit them) — recorded as a suite/scoring
   note, not a defect we patch by guessing ids.
5. Self-FK and mutual-IND dependency crashes fixed en route (commits
   `9a75599`, `2d1101e`); gate approval needs explicit threshold
   confirmation, blind approve is refused (APP_FINDINGS #3/#4 confirmed live).

## Reproduce

```bash
# run (needs bench box on LAN, .env credentials, app on :7881)
.venv/bin/python .tmp/bench_tools/run_driver.py google_v1 --request scoped \
    --tag <tag> --max-minutes 40 --port 7881
# predictions from the destination DB provenance
.venv/bin/python .tmp/bench_tools/emit_predictions.py \
    .tmp/benchmark_runs/google_v1/<run> <suite_dir> <run>/system_output.csv
# score (never our own scorer)
python <suite>/evaluation/evaluate.py --predictions <run>/system_output.csv \
    --output <run>/eval_scores.json
```

Suite copies: `/home/bayern/.hermes/cache/scratch/bench/google_v1` (from
`template@10.10.9.41:/home/benchmark/suites/merge-engine/google_v1`, keys
unmodified). Evaluator self-check: replaying the bundled
`example_system_output.csv` reproduces `example_scores.json` exactly.
