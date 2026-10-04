# Local verification checklist — what the cloud could not prove

Everything here is **implemented, unverified**. The user runs each entry locally against
the real databases; CLAUDE.md's rule applies — confirm with an *independent* check
(direct SQL, log inspection, a regenerated report), never the app's own success message.

## Before anything: restore the databases (Phase 0.7)

None of the report's live numbers can be re-checked as things stand. Per report §0.2,
since the measured run `ground_truth` was **dropped**, `global_warehouse` was **wiped**
(`merge_engine_entity_map` no longer exists) and the `enrichment` origin credentials
**changed**. The report's evidence artifacts (`phase3/state.json`, `phase3/gates.jsonl`,
`score.json`, `gt_score.py`, `gt_rows.py`, `run_merge.py`) are **not in this repository**
(searched in Phase 0.6), so the "before" values below can only be taken from the report's
text, not re-derived.

1. Rebuild `ground_truth` with [GROUND_TRUTH_FULL_CORPUS_GUIDE.md](../GROUND_TRUTH_FULL_CORPUS_GUIDE.md)
   (or [GROUND_TRUTH_MANUAL_BUILD_GUIDE.md](../GROUND_TRUTH_MANUAL_BUILD_GUIDE.md)) and
   restore both origins with working credentials.
2. Reset `global_warehouse` to the destination schema of report §0.7 (including the
   one-column `public.company_industry` — do **not** fix its DDL first, P17 depends on
   seeing it broken).
3. Write or recover the scorer **before** the first run (plan §2.7) and prove it both
   ways — identity returns a perfect result, an injected FP and FN are caught and named
   — exactly as report §0.2 shows. `tests/part1/scorer.py` is the synthetic-fixture
   version of that proof and can be adapted.
4. Run once on `main` (pre-Part-1) and confirm the "before" values below reproduce. A
   before-value that does not reproduce on restored data is itself a finding: record it
   here instead of treating the corresponding "after" as an improvement.

## "Before" values the user must see change (report §0.3 and per-item Evidence)

Counts, not rates; stratify cross-source vs within-source (report §0.4). Review rate goes
beside every precision figure.

| item | before (report) | where it was measured |
|---|---|---|
| run | terminal status `completed` despite everything below; 828.1 s wall (395.4 s warm LLM cache) | §0.1, §0.3 |
| scoring | company TP 13 / FP 4 / FN 0; person TP 3 / FP **14 382** / FN 4 | §0.3 |
| scoring | rows short of expected **10 847**; over-merged person clusters **1 401** | §0.3 |
| P1 | zoominfo `company`: **28 of 29** fields unmapped, reason `"No matching destination column found; destination schema is authoritative"`; `companyname` → `['companyname']` | P1 Evidence |
| P2 | `staff`: ~**962 000** candidates, **711 670** discarded unexamined; run-wide **9 556 382** discarded. **Caveat (Phase 0.6):** these come from `uncompared_pairs`, which over-counts when one block arrives under several keys (synthetic fixture: 389 200 reported vs 69 600 truly discarded). Count the true discard independently (distinct pairs generated minus distinct pairs scored) on the restored data before comparing | P2 Evidence |
| P3 | 26 tables → `dependency_tree = {}`, `column_dependencies = []`; `staff.main_entity_id = company.companyid` joins all **13 518** rows but is never proposed | P3 Evidence |
| P4 | **8 of 16** entities with zero field mappings; `industry` **0** rows, `product_category` **0** rows | P4 Evidence, §0.7 |
| P5 | `staff` blocks on surname alone → ~962 000 candidates at precision ≈ **0.0002** → giant component | P5 Evidence |
| P6 | **4** company FPs are parent/subsidiary or jurisdictional variants, not duplicates | P6 Evidence |
| P7 | `company_relation` **21** rows; no origin relationship derived into a typed edge | P7 Evidence, §0.7 |
| P8 | `_MAX_LLM_PAIRS = 200`, closest-to-auto-match first, 40 pairs per prompt | P8 Evidence |
| P9 | every gate decision discarded; run n+1 re-asks everything | P9 Evidence |
| P10 | no stage fails on results: `company.name` NULL **78 / 99**; `legal_name`, `industry_id`, `location_id`, `employee_count` **100 % NULL** | §0.3, P10 Evidence |
| P11 | `map_fields` ≈ **44 %** of wall clock, serial per entity | P11 Evidence |
| P12 | catalog tables and `created_at` / `etl_batch_id` / `_row_version` profiled and mapped | P12 Evidence |
| P13 | `u` estimated by EM on the blocked candidate set only | P13 Evidence |
| P14 | term frequency at value grain, not token grain | P14 Evidence |
| P15 | pairs in 0.70 ≤ s < 0.95 neither merged nor recorded | P15 Evidence |
| P16 | no label-free signal printed | P16 |
| P17 | `company_industry` (one `id` column) loaded **539** key-only rows | P17, §0.7 |
| P18 | `numeric_max` disagreements discarded silently; `first_seen` ordered by extraction order | P18 |

The Phase 0 synthetic before-numbers (mechanism level, not live) are in
`tests/part1/before_matching.json` and `tests/part1/before_schema.json`; see
[PHASE0_REPORT.md](PHASE0_REPORT.md).

## Per-item entries

Entry format (one per item, added by the lead when it accepts the item):

```
### P<n> — <title>
- Why cloud could not verify: <no database / no LLM endpoint / ...>
- Command: <exact command>
- Independent check: <SQL / file / report to compare>
- Expected evidence: <numbers from the report's Evidence section, or "any value > X because ...">
- Result (filled in locally): 
```

### P1 — vocabulary-driven field-name tokeniser
- Why cloud could not verify: no origin databases / destination schema.
- Command: `python -m src.main --mode gradio --port 7860`, run the full two-origin merge.
- Independent check: the run's `map_fields` result for zoominfo `company` (`field_mapping`, `unmapped_fields`, new `ambiguous_splits`); SQL `SELECT count(*) FILTER (WHERE name IS NULL), count(*) FROM company` on the destination.
- Expected evidence: zoominfo `company` mapped fields > 1 of 29 (before: 28 unmapped); `companyname` tokenised `company`,`name`; `company.name` NULL count below 78 / 99. Note `ambiguous_splits` is not yet surfaced by `map_fields_node` (lane B, P11/P12).
- Result (filled in locally):

### P2 — purging + meta-blocking replace the 250k comparison cap
- Why cloud could not verify: no origin databases (the `staff` entity).
- Command: full merge run; then read `resolution_stats.<entity>` (`candidate_pairs`, `pairs_pruned_by_purging`, `pairs_pruned_by_meta_blocking`, `uncompared_pairs`).
- Independent check: count distinct blocking pairs independently (`_blocking_keys_for` over the restored `staff` rows) and compare against `candidate_pairs` + pruned counts; score the run against ground truth (pair completeness on the restored truth set).
- Expected evidence: `uncompared_pairs` = 0 (before: 711 670 for `staff`); either the run completes with the pruned counts shown, or it FAILS with `CandidatePairBudgetExceeded` (guard 5M pairs) — never `completed` with silent discards. Watch memory (~300 B/pair) and the WNP loss: synthetic attack measured 4-19 % of pairs the uncapped old code found are pruned (accepted ceiling; P5 revisits blocking).
- Result (filled in locally):

### P18 — survivorship five sub-fixes
- Why cloud could not verify: no restored corpus.
- Command: full merge run; open the run report's `entities.<entity>.numeric_disagreements` and each unified record's `_conflicts`.
- Independent check: SQL over the two origins for revenue/employee_count values that differ for the same resolved company; compare the count with `numeric_disagreements.<field>.clusters`.
- Expected evidence: numeric disagreements now listed with spread (before: discarded silently); `first_seen` winners identical across two runs with shuffled extraction order; `winning_rule` reads `longest` where it read `completeness`.
- Result (filled in locally):

### P13 — u from a random sample of the unblocked cross product
- Why cloud could not verify: no restored corpus / ground truth.
- Command: full merge run with and without this commit (parent: the commit before "P13:"); score both with the restored ground truth.
- Independent check: ground-truth pair precision/recall per entity, stratified cross- vs within-source (counts, not rates), plus the review-band volume.
- Expected evidence: `log2(m/u)` for the blocking field no longer ~0 (synthetic: 0.29 bits -> ~4.5 bits); precision at the auto-match bar not lower than before; watch for true matches with few agreeing fields dropping below auto_exclude (see HANDOFF escalation on P6 case a).
- Result (filled in locally):

### P12 — catalog tables and ETL metadata columns excluded, with overrides
- Why cloud could not verify: no origin databases.
- Command: full merge run; inspect `schemas[<origin>]["classification"]` in the run state and the mapping result.
- Independent check: list origin tables/columns with SQL (`information_schema.columns`) and compare with what was mapped and what matching saw (`resolution_stats`), and with the destination rows.
- Expected evidence: catalog tables and `created_at` / `etl_batch_id` / `_row_version` absent from mapping and matching (before: profiled and mapped); metadata columns still present in the run output; a `RunConfig.metadata_overrides` business override restores a flagged column.
- Result (filled in locally):
