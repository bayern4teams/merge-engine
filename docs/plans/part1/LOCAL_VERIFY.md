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

### P11 — bounded per-entity field mapping
- Why cloud could not verify: no LLM endpoint / databases.
- Command: full merge run; time the `map_fields` node (logs / Langfuse span).
- Independent check: endpoint request log shows at most `_LLM_CONCURRENCY` (4) concurrent map_fields calls; mapping output identical to a run with `_LLM_CONCURRENCY = 1`.
- Expected evidence: `map_fields` share of wall clock below 44 % (before); no endpoint rate-limit errors; dest_connector sampling stays correct under concurrent callers.
- Result (filled in locally):

### P14 — token-grain IDF for text fields
- Why cloud could not verify: no restored corpus / ground truth.
- Command: full merge run vs the parent of the "P14:" commit; score both with ground truth.
- Independent check: ground-truth precision/recall for `company` and person entities, stratified; list the company FPs that share only a legal-form token.
- Expected evidence: fewer company FPs carried by a shared high-df token (report: 4 company FPs were parent/subsidiary or jurisdictional variants); person recall not lower beyond the initials/middle-initial debt; inspect the 0.70 band edge.
- Result (filled in locally):

### P3 — value-based inclusion-dependency discovery
- Why cloud could not verify: no origin databases.
- Command: full merge run; read `dependency_tree` / `column_dependencies` from the analyze_dependencies state and the join graph edges.
- Independent check: SQL `SELECT count(*) FROM staff s JOIN company c ON s.main_entity_id = c.companyid` (expect 13 518) and `SELECT min(companyid), max(companyid), count(DISTINCT companyid) FROM company` — **if companyid is dense (count = max - min + 1) the coincidence rule will NOT infer this edge**; then it must come from a declared FK or the gate.
- Expected evidence: `dependency_tree` non-empty (before: `{}`); the `staff.main_entity_id -> company.companyid` edge present unless companyid is dense; no edge onto `company._id`.
- Result (filled in locally):

### P5 — schema-agnostic token blocking
- Why cloud could not verify: no origin databases (the `staff` entity).
- Command: full merge run vs the parent of the "P5:" commit; read `resolution_stats.<entity>` (`candidate_pairs`, pruned counts) and the blocking log lines.
- Independent check: ground-truth pair completeness for `staff`/person and company (PC and RR computed from the truth set, not from the engine), plus wall time and peak memory of resolve_entities.
- Expected evidence: `staff` candidates far below ~962 000 at higher precision than ≈ 0.0002 (before); no giant component; PC not below the parent commit's.
- Result (filled in locally):

### P4 — junction/weak roles at the deps gate, dimension rows with zero new columns
- Why cloud could not verify: no origin or destination databases (26-table schema, real Postgres FK ordering, `ensure_table`), no UI rendering of `table_roles`.
- Command: full merge run vs the parent of the "P4:" commit; read the deps-gate payload `data.table_roles`, the field mappings (`mapped_by: dimension`) and the load result per table.
- Independent check: SQL — `SELECT count(*) FROM industry` / `product_category` (expect > 0, one row per distinct text: compare to `SELECT count(DISTINCT <text col>)` on the origin), `SELECT count(*) FROM company WHERE industry_id IS NOT NULL` against the origin's non-null text count; `information_schema.columns` count per destination table unchanged vs before the run.
- Expected evidence: entities with zero mappings below 8 of 16; `industry` and `product_category` non-empty; no new destination column; junction/weak proposals plausible at the gate (weak may be noisy — ceiling).
- Result (filled in locally):

### P6 — three-way discrimination (measured generic / exclusive values)
- Why cloud could not verify: no origin databases; the real share of phone/postcode columns that differ per origin and the real tax-id typo rate decide how many pairs move to review.
- Command: full merge run vs the parent of the first "WIP P6" commit (055446b); read `resolution_stats.company` (`pending_merge_candidates`, `possibly_related`) and the clusters.
- Independent check: ground-truth company pairs (restored `gt_score.py`): Match precision/recall and the number of review items, per outcome class; sample 20 `possibly_related` edges and confirm by SQL they share an address/phone but are different companies.
- Expected evidence: same-email truth pairs merge; same-address/different-tax-ID pairs reach review (not auto-merged, not dropped); review volume not above the parent's; `possibly_related` never inside a cluster.
- Result (filled in locally):

### P9 — decision memory (file)
- Why cloud could not verify: no real LangGraph checkpointer pause/resume through the UI; no live run with the file inspected.
- Command: two identical runs with `MERGE_ENGINE_DECISION_MEMORY=<path>`; answer the matches gate in run 1.
- Independent check: open the JSON file (one entry per answered pair, `merge`/`keep_separate` only); run 2's matches gate does not pause, `resolution_stats.<e>.decisions_from_memory` equals the answered count; an aborted gate adds nothing.
- Expected evidence: run 2 asks zero; changing one record attribute re-asks exactly that pair.
- Result (filled in locally):

### P7 — typed relationship edges and the name-free relationship index
- Why cloud could not verify: no origin databases; whether P3 finds the real self-reference / junction INDs (dense integer keys are skipped by the coincidence rule, see P3) and whether raw FK columns survive extraction.
- Command: full merge run; read `relationship_edges` from the analyze_dependencies state and `data.relationship_cycles` in the deps-gate payload; check the log line "Relationship index built from N relation row(s)".
- Independent check: SQL on the origin — `SELECT count(*) FROM company_relation` (report: 21 rows recovered before; true count higher) against the RELATED edges for that junction; for any self-referencing column `SELECT count(*) FROM <t> WHERE <ptr> IS NOT NULL` against the DIRECT_PARENT count for that `column`.
- Expected evidence: RELATED edges = junction rows with both ends non-null; DIRECT_PARENT = non-null pointers to existing keys; cycles listed, not dropped; the relationship index non-empty only from junctions between company keys.
- Result (filled in locally):
