# Stage 4 — threshold calibration from hard_pairs evidence (pre-registration)

Date written: 2026-11-26, BEFORE any sweep ran (mission pre-registration rule).

## Question
`_AUTO_MATCH = 0.95` is marked provisional in `/v1/entity-types` (never
presented as calibrated). Does the benchmark's own `hard_pairs` truth honestly
support 0.95 — or a different number — as the auto-merge bar?

## Data
The four suites already baked-off in-process (identical load as d1_bakeoff):
dblp_acm (full), abt_buy (full), amazon_google (full), dblp_scholar@8000
(documented I-6 SUBSAMPLE prefix). Comparator arm = A_tree = the CURRENT
in-tree `idf_soft_similarity` (post D5/D6 code), scoring `_resolve_core`
final pair scores — the same quantity the app compares against the bar.
LLM adjudication OFF (borderline pairs keep their heuristic score, the same
convention as every D-series bake-off; noted deviation: the app's LLM tier
may lift a handful of borderline pairs above the bar that here stay heuristic).

## Sweep
tau ∈ {0.50, 0.51, …, 0.99} (50 points). For each suite × tau over the full
scored-pair set (identical set at every tau):
  precision = tp/(tp+fp) over pairs scored ≥ tau (truth pairs by entity_id),
  recall    = tp / true_pairs,
  over_merged = clusters (transitive closure of ≥ tau edges) mixing entities.

## Pre-registered decision rule
  supported(tau, suite):  precision ≥ 0.95 AND tp ≥ 10 AND recall > 0
  tau*(suite)  = the SMALLEST supported tau (prefer recall where precision
                 already holds — a bar that is unnecessarily high is as wrong
                 as one that is too low).
  If tau* exists for EVERY suite: calibrated global bar = max tau*(suite)
    (safe everywhere); publish per-type via /v1/entity-types source:"calibrated"
    and move _AUTO_MATCH if the max differs from 0.95 — "the number moves —
    say so with the table."
  If tau* exists only for SOME suites: keep 0.95 global provisional; publish
    per-type calibrated bars ONLY for the suites that support one, with the
    type mapping stated; record the rest honestly as "no tau supports 0.95".
  If tau* exists nowhere: 0.95 stays provisional; the report's acceptances
    8/9 stay BLOCKED with the table as the evidence. No post-hoc bar-fitting
    to a favourite subset.

## Deviations (record as they happen)
- (none yet)

## Result
(written after measurement)
