# Owner decision memos — measured options (no implementation)

Written 2026-11-26 as instructed: these are owner calls; nothing below is
implemented. Every cost quoted is from a run on this machine's box
(`template@10.10.9.41`), cited to the run folder or ledger row.

## D-A. I-5 — shared family code masks the model-code conflict

Defect (docs/BENCH_ISSUES.md I-5): `_conflicting_codes` fires only when
NEITHER side carries ANY code of the other. One shared family token
(`120gb`) masks a differing model code (`MB562LL/A` vs `MB565LL/A`): the pair
escapes the PRE-D2 conflict cap and can score 1.0 — the over-merge direction,
the one this mission scores worst (F0.5 bias).

Measured cost of the status quo (scorecard rows, app runs):

- abt_buy: must_not_merge 50/124 → **74 forbidden merges auto-applied**;
  precision 0.043 at recall 0.015.
- amazon_google: must_not_merge 252/423 → 171 forbidden edges; P 0.055.
  (These counts include all over-merge causes; I-5 is the verified dominant
  one in the D1 FP sample — e.g. `MB562LL/A` vs `MB565LL/A` scoring 1.0.)

Options, as measured by `.tmp/bench_tools/i5_options.py` (in-process,
LLM off, A_tree comparator, same scorer as every D-series bake-off; table
filled from `.handoff/STAGE4.i5-options.json`):

| arm | abt_buy P/R/F1 | over-merged | amazon P/R/F1 | over-merged | dblp_scholar@8k P/R | dblp_acm P/R (regression) |
|---|---|---|---|---|---|---|
| main (today) | (fill from measurement) | | | | | |
| opt1 all-codes-disjoint | | | | | | |
| opt2 last-code (model code) | | | | | | |

Reading for the call (pre-registered shapes, not post-hoc):

- **Option 1 (all codes must appear)** is the strictest reading of PRE-D2's
  own docstring sentence ("neither code appears in the other"). Cost: any
  name that gains a token on one side (typos, "Model X (2019)" vs "Model X")
  now fires the conflict → those true duplicates leave the auto band for
  review (recall@auto drops; they are NOT lost — the D-series band keeps
  them reviewable). Watch the must_merge pass-rate, not just P/R.
- **Option 2 (last-code positional)** targets exactly the family+model shape.
  Cost: corpora where the LAST code is a family and the model sits earlier
  invert the rule and over-fire; publication/person suites must show the cap
  never fires (their codes are years/ids — the regression columns).
- If neither option's measured deltas justify the change (precision gain <
  recall-to-review cost on the suites this app actually ships), the honest
  owner call is: keep I-5 open as a KNOWN ceiling, documented, and spend the
  fix on blocking/mid-band instead (D1's negative result already says the
  budget is not the binding constraint here).

## D-B. google_v1 scoped extraction reaches 2/16 companies (scope defect)

Defect (docs/BENCH_STAGE0.md findings 1–3, scoped run
`20261125-1529_2d1101e_stage0g_scoped`): the scoped request for `Google LLC`
truth-in-set is 10 companies + related rows; the LLM scope plan TIMED OUT
(300 s, served `qwen3.8-flash-next`) and fell back to the heuristic
v3_discovery plan `{company: 5, contact: 1, staff: 1, product: 1}` — and
extraction delivered only **2** company records, so ER compared 1 pair
(`same` recall 0 is arithmetic). Dependency analysis timed out too; the
scoped link-path walk also lost tables outright (finding 2:
`parent_company_id` never captured for `audit_log`).

Options with measured costs:

1. **Timeout/fallback policy only** (smallest change).
   The failure mode is SILENT: a 300 s LLM timeout prints one WARNING and
   the run continues on a heuristic that reaches 20 % of the truth scope.
   Cost to fix is near-zero code (surface the fallback in the gates
   payload / pause for confirmation like the threshold gate); cost in
   practice = slower scoped runs (a human sees "heuristic scope, reach
   unknown" and re-tries or edits the plan). Does not fix reach itself —
   2/16 stays 2/16 when the LLM is absent.
2. **Fix the heuristic's reach** (real fix, bigger surface).
   Measured anatomy of the 2/16 loss: plan says 5, extraction delivered 2 —
   the plan's seed predicate over `db_core:public.company` matched 5, but
   the link-path walk lost the second origin's companies entirely (finding
   2's seed-column gap) AND the subject-scope extractor never re-expanded
   family members by name (the truth's 10 in-set companies include rows
   reachable only via `parent_company_id` / contact links). A fix touches
   plan_scope's link-path seed capture + family expansion; the honest cost
   is a Part-3-scope work item (scope planning is the stage with the
   timeouts, so any fix must also raise the LLM budget or go structural-
   only). Until then every scoped scorecard row is a scope-plumbing test,
   not an ER test — which is exactly how stage0g is labeled in
   docs/BENCH_SCORECARD.md.
   Recommended owner order: 1 now (honesty), 2 scheduled with Part 3.

Neither option is implemented — owner's call on both.
