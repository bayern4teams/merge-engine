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

## Result (measured 2026-11-26, tree `84ec8df` = I-1 fix in, D5+D6 in)

| suite | scored pairs | true pairs | P/R at 0.95 (today's bar) | best P at ANY tau | tau* |
|---|---|---|---|---|---|
| dblp_acm | 95,850 | 2,224 | 0.858 / 0.962 | 0.914 (tau 0.99) | none |
| abt_buy | 35,090 | 1,118 | 0.048 / 0.014 | 0.096 (tau 0.94) | none |
| amazon_google | 377,563 | 1,553 | 0.070 / 0.072 | 0.077 (tau 0.99) | none |
| dblp_scholar@8000 | 174,743 | 579 | 0.447 / 0.261 | 0.490 (tau 0.99) | none |

Curves: `.handoff/STAGE4.sweep.json` (50 taus × 4 suites).

**Verdict (pre-registered branch: "tau* exists nowhere"):** `_AUTO_MATCH =
0.95` stays **provisional**; nothing is published as `source:"calibrated"`;
acceptances 8/9 stay BLOCKED with this table as the evidence. The number did
NOT move because no number clears the rule: no threshold on any suite's
score distribution reaches precision ≥ 0.95 with tp ≥ 10 — even tau = 0.99
leaves dblp_acm at 0.914 (201 FP pairs score ≥ 0.99).

What the table actually says (consistent with D1's negative and D5's win):
the precision losses are NOT a bar-placement problem. FPs concentrate in the
same ultra-high score band as TPs (containment-cliff / I-5 code-mask classes
— pairs the comparator genuinely cannot separate), so *no* cut of that axis
separates them; D5's field-conflict cap removed 37 of them precisely because
it is a different axis, not a tighter threshold. The honest calibration
statement for rev. d: the 0.95 bar costs nothing in recall it should keep
(0.962 on dblp_acm) and buys nothing it promises on product suites; keep it,
stop calling it provisional-candidate-for-calibration, and revisit only when
D6+D12 score calibration (Part 3) makes scores comparable across corpora.

Deviation recorded: LLM adjudication OFF for the sweep (pre-registered); the
app's LLM tier can lift a handful of borderline pairs, and the app-side
comparisons agree — app dblp_acm precision 0.925 (scorecard row 1) is also
< 0.95, so the conclusion is the same with LLM on.
