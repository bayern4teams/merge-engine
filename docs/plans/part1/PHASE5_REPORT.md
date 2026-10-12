# Part 1 — Phase 5 report (integration and hand-over)

Branch `feat/part1-fixes`. All 20 items carry a lead verdict; 19 are `unverified-live` (the cloud has no origin DBs, destination or LLM endpoint) and P19 is `accepted-cloud`. The user verifies locally from [LOCAL_VERIFY.md](LOCAL_VERIFY.md) and merges; this branch is never merged by an agent.

The per-item acceptance/verdict/report files under `.handoff/` were purged after this report; they are in git at `1658036` (`git show 1658036:.handoff/P10.verdict.md`).

## Decisions waiting for the user

1. **CI does not run `tests/part1`.** `check.py test` runs `tests/unit tests/integration` only, so the 1,189 Part 1 acceptance/attack tests (the evidence behind every verdict) ran locally at each phase gate but never in CI. Adding them to the `test` stage is one line in `scripts/check.py`, but some tests read `P10_BASE_REV`-style git revisions that a shallow CI clone lacks; a repo-wide gate change, so not made unattended.
2. **Audit cuts that need other items' tests rewritten** (lane A, `ponytail-audit`, net -223 lines possible, -175 judged safe): the unreachable cohesion path in `src/mcp_server/tools/resolve_entities.py` (`_split_weakest_link`, `_average_linkage`, `_MIN_COHESION`, `min_cohesion` params, `bar_moved` in the node; ~-95, but P2/P15/P20 acceptance tests name these symbols); the `comparison_budget` warning block in `src/orchestrator/nodes/resolve_entities.py` (`uncompared_pairs` is a constant 0 since P2; ~-65 plus its unit tests); `_corpus_evidence` triple build (~-14, `dataclasses.replace`). Not applied: each edits verifier-owned acceptance tests of accepted items.
3. **`sql_lineage.py` has no caller.** It got a one-line typing fix (518dcf9) only to keep CI green after sqlglot 30.22; delete it or wire it.
4. **`scipy` is imported (lazily) by P20's `one_to_one` and declared in no manifest.** `one_to_one` has no caller (kept by decision c); declare scipy or drop the option.
5. **`decision_memory_path` / `quality_history_path` are read from state but not declared in `MergeState`**; only the env vars (`MERGE_ENGINE_QUALITY_HISTORY`, decision-memory env) work reliably. The docs say so.
6. **ADR 0014** (`docs/decisions/0014-...`): its consequence "the builtin engine's truncation stays as-is" is superseded by P2; ADRs are history, so it was not edited — add a superseding note or ADR if wanted.

## Item verdicts (final lead verdict, from HANDOFF.md)

| item | phase | state | lead verdict (abridged) |
|---|---|---|---|
| P1 | 1 | unverified-live | ACCEPT r2: 63 acceptance + 2 attack tests pass; 0 of 73 names regressed on a 50-column schema; debt: 1-letter vocabulary words |
| P2 | 1 | unverified-live | ACCEPT after attack F1-F3,F5 fixed: 40/40 planted (was 31/40), uncompared 0 (was 389,200), guard raises and fails the run; F4 WNP loss 4-19 % vs uncapped = accepted ceiling; block filtering omitted (ratio r is fitted, §2.4) |
| P3 | 2 | unverified-live | ACCEPT r2 (worker, lead diff review): 34/34 acceptance; value-based INDs, RHS-uniqueness (profile_column or complete sample), PK-subset filter, binomial pre-prune (miss < 1/pairs), coincidence test vs the parent's density over the child's range, cliff; decoy rejected at caps 50/500; cost 0-6,084 filtered queries vs 439,886 naive. Ceilings: FK into a DENSE surrogate sequence not inferred (declared FKs cover it); ... |
| P4 | 2 | unverified-live | ACCEPT r2 (worker, lead diff review): 37/37 acceptance; attack r1 REJECT (mapper Phase 1b took primary-column fields `Industry`->family table, routed child tables, self-FK label) -> r2: primary column wins, dimension = table the primary references and that does not reference back (FK info now passed by map_fields_node), label never FK/PK/taken/non-text; 43/44 attack (a3 strict xfail = ceiling: self-FK label when ... |
| P5 | 2 | unverified-live | ACCEPT r3 (lead item): 19/19 acceptance. Attack r1: realistic company corpus raised the guard, CJK lost; r2: typo'd-surname persons PC 0.965 -> 0.522 (asymmetric per-record filter). r3: token blocking (Unicode) -> local-IDF block filtering (geometric mean, chosen by measurement — disclosed) with the either-end union rule -> purging (dominance over all other blocks, or IDF < 1 bit, coverage by kept blocks) -> ... |
| P6 | 3 | unverified-live | ACCEPT r3 (lead item). r1 REJECT D1-D4 (declared conflict overridden; phone/postcode/name parts exclusive; disjoint-name conflict branch; triplicates made their email generic) -> r2 fixed them (generic = >cut holders AND >cut name groups; exclusive = shape/hint + within-entity cardinality 1, leave-one-out; names never exclusive; declared keys win). Attack r2 REJECT D5: zero tolerance -> one tax-id typo switched ... |
| P7 | 3 | unverified-live | ACCEPT r2 (lead item). r1 (d961b84): `join_graph.derive_relationship_edges` (self-IND -> DIRECT_PARENT + ULTIMATE_PARENT to the observed root, cycles listed not broken, P4 junction -> RELATED per row, tables read in full) + index from `table_roles.junction`; 54/54 acceptance (47 fail on parent). Attack r1 REJECT: d3 record id = any value the index knows (unrelated companies +0.3, 0.588 -> 0.888); d7 every ... |
| P8 | 3 | unverified-live | ACCEPT r2 (lead item). r1 (1231afe): one pair per call returning `{"probability", "reason"}`, malformed/NaN/out-of-range/bool/raise -> that pair keeps its heuristic, `_LLM_CHUNK_SIZE` removed, over-budget band sends the `_MAX_LLM_PAIRS` pairs nearest the band middle; prompt files + harness + golden (multi-pair cases split) changed with it (lead deviation). 40/40 acceptance (27 failed before). Attack ACCEPT with ... |
| P9 | 3 | unverified-live | ACCEPT r2 (lead). Module accepted 498bdc1 (48/48); gate hook 4fbdc86 (7 tests). Attack r1 (end-to-end: real node, router, resume_workflow): run 2 asks zero on both paths, partial memory pauses only for the rest, PYTHONHASHSEED/order-stable file, atomic under 8 threads, corrupt -> ask again, off by default — REJECT on 3 hook defects: unrecognised action remembered as keep_separate; abort/reject gate recorded its ... |
| P10 | 4 | unverified-live | ACCEPT r3 (lead, last round per user decision 2026-10-12). r2 findings 1-5 fixed in d7258a7, verifier r3 confirmed all 7 fixed incl. through the real graph. r3 REJECT findings: (1) MEDIUM-HIGH slack dead through the UI (Gates.tsx sends every proposal back) -> fixed: a returned value equal to the proposal is not an edit; (2) MEDIUM omitted name key (Mongo `_flatten_doc`) hid NULLs -> fixed for docs without another ... |
| P11 | 1 | unverified-live | ACCEPT on lead review (10-line Semaphore(_LLM_CONCURRENCY) around the existing gather; attack step replaced by line review — deviation, trivial diff): 25/25 acceptance incl. bound 1/2/4 peak tests; report's "serial" was stale, the defect was the missing bound |
| P12 | 1 | unverified-live | ACCEPT: 25/26 acceptance (4c documented skip); attack: generic audit/ETL/DBMS vocabulary (report sanctions name patterns), no corpus literal, overrides run>origin>default, corpus rows unchanged; ceilings: value patterns not implemented, dw_load_id/ingestion_time missed; per-run layer threaded via RunConfig.metadata_overrides (lead) |
| P13 | 1 | unverified-live | ACCEPT r2: 17/17 acceptance (lead adapted fit() to pass population_pairs) + consistency test (precision@0.5 1.0 / 0 FP vs old 0.97-0.99 / 1-2 FP on 3 seeds); prior rescaled to random-pair population (lead deviation); call site samples 3000 pairs from records sorted by id, seed 0 |
| P14 | 1 | unverified-live | ACCEPT r2 (lead item): 24/24 acceptance. Attack r1 rejected binary-token cosine (typos -> DISAGREE, person recall@0.7 0.85 -> 0.26). r2 = IDF-weighted symmetric Monge-Elkan over `_seq_ratio` (no new constant); text path only when both values have letters + a space. e2e synthetic: company recall@0.7 0.497 -> 0.611, @0.95 0.250 -> 0.296, precision 1.0; person @0.95 0.577 -> 0.552 and @0.7 0.845 -> 0.552 = knife ... |
| P15 | 3 | unverified-live | ACCEPT r2 (lead item): 118 pass + 4 strict xfail (acceptance 53, attack 65). Attack r1 REJECT: keep-apart dissolved the Match triangle around it; same-feedback split re-merged; NaN histogram crash; 2nd resume undid 1st. r2: strongest-first join with cannot-link (rejected pairs; human-reviewed clusters joinable only by an accepted pair), decisions persisted, NaN -> Exclude, `_build_clusters` cohesion unchanged ... |
| P16 | 4 | unverified-live | ACCEPT r2 (worker; lead diff review). `cluster_quality.quality_signals` (+ `hill_numbers`, `bcubed`, `homonymy_rate`) -> `report.entities[e].quality`; B-cubed vs previous run kept per entity in the file at state `quality_history_path` / env `MERGE_ENGINE_QUALITY_HISTORY`, keyed by run_id (preview + load of one run agree), run one None + "not available", empty run never saved. Name flag (decision c): names grouped ... |
| P17 | 3 | unverified-live | ACCEPT r2 (lead diff review): 55/55 acceptance+attack. Attack r1 REJECT: unmapped source field counted as a needed column -> healthy table falsely defective. r2: unmapped fields count only when no payload column is fed (a mapped-but-absent column always counts — derived test 1b), declared `primary_key` (else `id`), `unprojectable_rows` = withheld rows with any value, missing table reported (no `ensure_table` ... |
| P18 | 1 | unverified-live | ACCEPT: 54/55 acceptance (3b = rejected option B, strict xfail); attack follow-ups applied (comment reworded: longest is text-only and changes non-text winners — sanctioned by report ③; disagreement needs spread>0 and finite values). ③ = rename + `completeness` alias |
| P19 | 3 | accepted-cloud | ACCEPT r2 (lead review): 40 pass + 1 xfail (D6: no harness caller = Part 4 wiring). `blocking_report(records, candidates, truth=None, blocking_keys=None)` in tests/eval/score.py from the engine's `_token_keys`: RR, raw-collection K/k, PC/PQ only with matching truth (absent + reason otherwise, never 0); r2 fixed no-key block, operator keys, foreign truth ids, out-of-corpus candidates, N=0. Static: no label reaches ... |
| P20 | 3 | unverified-live | ACCEPT r4 narrowed (user decision c; lead item). Kept: WNP prune, components, triangle support (structural; fixes Phase 0 shape i), P6 repair now run INSIDE each triangle part (r4 finding 1: repair-first dissolved two bridged entities into singletons wherever ids exist; fixed, xfail flipped), optional `one_to_one` LP. Removed: name-cut/coherence split and its plumbing (`_name_cuts`, `_names_compatible`, ... |

Full rows (ceilings, waivers, evidence): [HANDOFF.md](HANDOFF.md).

## Phase 5 lane A — ponytail-review + ponytail-audit

### P5 lane A: ponytail-review (git diff origin/main...HEAD -- src) + ponytail-audit (graph-paired)

Read-only on src/ and tests/. Graph tiers used: graphify `affected`/`query`, codebase-memory-mcp `trace_path`
(graft not installed). Every "no caller" claim below was re-confirmed with grep. SAFE = pure deletion/shrink, no
behaviour change, no test change beyond deleting tests of deleted code. RISKY = changes behaviour, a public
signature, the UI, or needs a test rewritten.

#### Findings (ranked, biggest cut first)

src/mcp_server/tools/resolve_entities.py:L3436-3487: delete `_split_weakest_link` (52 lines), only reached through the `min_cohesion` branch of `_partition_component`, which its sole src caller `_split_component` (L3214) calls with `min_cohesion=0`. Nothing replaces it. SAFE
src/mcp_server/tools/resolve_entities.py:L3419-3433: delete `_average_linkage` (15 lines), its only src callers are the cohesion branch (L3413) and `_split_weakest_link` (L3483). Nothing replaces it. SAFE
src/mcp_server/tools/resolve_entities.py:L3412-3416: delete the "Cohesion cut" branch, replace with `return [members], 0, 0`; drop the `min_cohesion` param + its 4 docstring lines (L3367, L3377-3379) and the comment/refs at L3050, L3347. SAFE
src/mcp_server/tools/resolve_entities.py:L84-88: delete `_MIN_COHESION` (+ its comment) once the above is gone; also `min_cohesion` param of `rebuild_band_clusters` (L940, never read in its body) and src/orchestrator/nodes/resolve_entities.py:L19 import, L896-898 `bar_moved`, L910-912 argument. SAFE (rebuild's only src caller is that one site; tests/part1 acceptance files that freeze `_MIN_COHESION`/`_average_linkage` in BASELINE name lists, and test_p20_attack `test_dead_min_cohesion_report`, go with it)
src/orchestrator/nodes/resolve_entities.py:L456-511,L524: delete the `budget_truncated` / `comparison_budget` quality_update block and `**quality_update`, plus the `if uncompared:` warning (L419-426). `uncompared_pairs` is the constant 0 in both producers (resolve_entities.py L1382, splink_matcher.py L503) since P2 removed the budget, so every branch is unreachable. Nothing replaces it (keep the `uncompared_pairs` stat key itself: P2 tests pin it at 0). SAFE (delete the comparison_budget cases in tests/unit/test_resolve_entities_node.py)
src/mcp_server/tools/resolve_entities.py:L2506-2566: shrink `_corpus_evidence` building the 9-field frozen `CorpusEvidence` three times positionally. `probe = CorpusEvidence(...)`, then `evidence = replace(probe, generic_values=generic_values)` and `return replace(evidence, exclusive=..., agree=..., disagree=...)` (`dataclasses.replace`). SAFE
src/orchestrator/nodes/load_data.py:L407: delete `out["records"] = len(records)`, written, never read (pre_load_gate reads only fields, clustered, largest, identity_records and the four invariant keys). Nothing replaces it. SAFE (test_p10_attack reads `m["fields"]` only)
src/mcp_server/tools/map_fields.py:L452-454,L467-470,L472-475: delete the no-FK fallback `_named_like` and its two `elif` arms. NOTE the carry-over put this in join_graph.py; it is in map_fields.py `_dimension_label`. Reachable only when `get_foreign_keys` is absent or raises: family_map (`build_family_map`) is built from the destination's FKs, so a destination without `get_foreign_keys` (ClickHouse) has an empty family, and `_materialise_dimensions` (load_data.py L490) also returns early without it, so a label chosen by the fallback is never loaded. Require `foreign_keys` instead (`if table not in (destination_schema.get("foreign_keys") or {}).values(): continue`). RISKY (test_p4_acceptance L495 calls `map_fields_impl` without `foreign_keys` and exercises the fallback; rewrite those cases)
src/mcp_server/tools/cluster_quality.py:L172-186: shrink `_save`, an mkstemp/fdopen/replace/suppress dance duplicating orchestrator/decision_memory.py `_write`. `tmp = path.with_name(path.name + ".tmp"); tmp.write_text(json.dumps(history, sort_keys=True)); os.replace(tmp, path)` (the file header already accepts last-writer-wins). Same for `_load_history` vs `DecisionMemory._load`. RISKY (two concurrent writers now share one tmp name)
src/ui/frontend/src/components/Gates.tsx:L1002-1006 (`fails`) and its `<p className="text-danger">` at L1042: delete the client-side re-evaluation of `would_fail`. It ignores the per-entity slack that `pre_load_gate` applies, so it can announce a refusal the server will not make; the server's `pre_load_gate.would_fail` already says it. Show `gate.would_fail`. RISKY (UI)
src/mcp_server/tools/map_fields.py:L934-952 + L957-975: yagni `_vocab_splits` is a coercion wrapper (`frozenset(vocabulary)`, `list(...)`) over `_cached_vocab_splits`; its only src producer `_destination_vocabulary` already returns a frozenset. Name the cached function `_vocab_splits` and drop the wrapper and the `Any` typing of `vocabulary` params. RISKY (cache returns a shared list; callers must not mutate it, and any test passing a plain set breaks)
src/mcp_server/tools/resolve_entities.py:L3363-3416: shrink `_partition_component` return `(parts, repaired, cut)`; `cut` is now always 0 once the cohesion branch is gone and its sole src caller discards it (`split, was_repaired, _ =`). Return `(parts, repaired)`. RISKY (changes a tuple shape: test_phase_l_matching L421 asserts `cut == 0`)
src/mcp_server/tools/plan_scope.py:L541-546: yagni `confidences` param of `classify_junction_and_weak_tables`; its one src caller passes `_VERIFIED_CONFIDENCES` = the default tuple. Drop the param. RISKY (public signature)
src/orchestrator/nodes/load_data.py:L542-548 + Gates.tsx:L1027: `entities_at_worst` (carry-over: "duplicates entities_violating"). Partly refuted: it is NOT the same set, because `entities_at_worst` names the worst entity even when nothing violates and breaks ties by measured value, while `entities_violating` is empty then. Dropping it is possible (UI shows `entities_violating[name]`) but loses the "who is worst" context when the gate passes. RISKY (UI + payload; -5 lines)
src/orchestrator/nodes/discover_schemas.py:L54-55,L66: `run_kwargs` conditional kwarg. Exists only so a one-argument test stub (`side_effect_fn(origin)`, test_discover_schemas_node L105) keeps working. Passing `run_overrides=state.get(OVERRIDE_KEY)` unconditionally saves 1 line but breaks those stubs. Keep. RISKY (not worth it)
src/mcp_server/tools/resolve_entities.py:L3290-3322 `_one_to_one_parts` + L3011/L3029/L3127 `one_to_one`: no src caller confirmed (grep `one_to_one\s*=` over src finds none; only tests/part1/test_p20_attack.py passes it). User decision c keeps it. Not recommended for deletion.

#### Carry-over verdicts

- `_split_weakest_link` + cohesion branch + `min_cohesion` params: CONFIRMED dead from build and rebuild. The graph is WRONG here: codebase-memory `trace_path` lists `_build_clusters`, `rebuild_band_clusters`, `_split_component` as inbound callers because it sees the static `_partition_component -> _split_weakest_link` edge, but `_split_component` (L3214) hard-codes `min_cohesion=0` so `min_cohesion <= 0` returns at L3413. `rebuild_band_clusters` never reads its `min_cohesion` (grep of its body), the orchestrator still passes it. Only `tests/unit/test_phase_l_matching.py:408` calls `_partition_component` with the default (it only exercises the conflict branch, `cut == 0`). Findings 1-4 above. SAFE.
- `one_to_one`: CONFIRMED no src caller (grep). Kept by user decision c; no recommendation.
- `src/mcp_server/tools/sql_lineage.py`: CONFIRMED no src caller (graphify `affected` for `column_lineage` and `mapping_to_sql` returns only tests/unit/test_sql_lineage.py and TODO.md; grep over src finds no importer). It is NOT branch-added: the branch's only change is the one-line `schema or {}` CI fix (518dcf9). Outside the branch-scoped audit, so no deletion line is counted; as a standalone cleanup it is 83 + 52 test lines and the `lineage = ["sqlglot"]` extra still serves splink_matcher.
- `_named_like` no-FK fallback: located in map_fields.py, not join_graph.py. Near-dead (see finding 8), RISKY to remove.
- `discover_schemas` `run_kwargs`: LIVE (set when `metadata_overrides` is in state, which RunConfig feeds); exists for test stubs. Keep.

#### Audited, nothing to cut (so you need not look again)

join_graph.py (value-based IND discovery, `_walk_hierarchy`, `derive_relationship_edges`: each single-caller helper has exactly one live caller; `derive_relationship_edges`'s `roles=None` fallback is pinned by the P7 acceptance contract tests); `_keeps`, `_passes`, `_capture_and_cluster`, `_bounded`: single-use closures but each is the only way to pass state, kept; metadata_classification.py, decision_memory.py, fellegi_sunter.py, survivorship.py, unify_entity_group.py, api/main.py, graph.py, workflow_manager.py: no dead symbol found by the def-reference scan (159 added defs/constants, none with zero src references). Frontend reviewed for the diff only.
Not verified: no tests were run (read-only brief); the line counts are from the current tree.

net: -223 lines possible (SAFE -175: 95 cohesion dead code, 65 comparison-budget dead block, 14 `_corpus_evidence`, 1 unused measure; RISKY -48).

## Phase 5 lane B — ponytail-debt ledger + ponytail-gain

### P5 debt ledger and gain (lane B, read-only)

Branch `feat/part1-fixes`, compared with `origin/main` (507f9d4). Nothing in `src/` or `tests/` was edited.

#### 1. Ponytail debt ledger

Scan: `grep -rnI "ponytail:" src tests` (comment and docstring markers; the repo writes most of them in docstrings, so the skill's `#`/`//` prefix filter would have missed 31 of the 41).
Result: **41 markers, 21 with no trigger** (19 with one, 1 partial). 37 of the 41 are new on this branch; the 4 older ones are marked `pre`.
(`map_fields.py:465` says "(ponytail)" with no colon and is not counted.)

Trigger rule used: a trigger is a stated condition or event for revisiting ("Part 3", "once X", "if Y appears", "trigger:"). An upgrade path with no condition is **MISSING-TRIGGER (path only)**. A ceiling with neither path nor condition is **MISSING-TRIGGER**. Plan 2.3 asks only for "ceiling and upgrade path"; the trigger requirement is the brief's.

| # | file:line | ceiling / shortcut | deferred alternative | trigger |
|---|---|---|---|---|
| 1 | src/orchestrator/nodes/analyze_dependencies.py:147 | table_roles not recomputed when a pinned profile reuses the whole tree | none | MISSING-TRIGGER |
| 2 | src/orchestrator/nodes/load_data.py:413 | nameless doc carrying a name column no sibling has (`email`) is judged as a second naming style | none | MISSING-TRIGGER |
| 3 | src/orchestrator/nodes/load_data.py:464 | P10 invariant check can only refuse (no rollback); proposals relative to the run's own entities, so uniformly bad runs pass | thresholds learnt from past runs | enough confirmed runs in P16's history |
| 4 | src/orchestrator/nodes/load_data.py:619 | P17: a mapped column the table lacks empties the whole entity; no tables = nothing reported; composite key judged by first column; `held_links` grows with rows | hold links in the corpus, re-project after DDL fix | the corpus lands (Part 3) |
| 5 | src/orchestrator/nodes/validate_graph_consistency.py:22 `pre` | one compare op, same-entity facts only | more `compare_op` / `related_via` predicate | when a rule genuinely needs one |
| 6 | src/orchestrator/decision_memory.py:10 | P9 file store: last-writer-wins, no schema, full-scan queries | indexed revocable decision store in `merge_engine` | Part 3 (backfills from this file) |
| 7 | src/mcp_server/connectors/postgresql_destination.py:545 `pre` | ENTITY/CODE/MONEY kinds coerced to TEXT | reference-data lookups | MISSING-TRIGGER |
| 8 | src/mcp_server/tools/unify_entity_group.py:67 | P18 rules repair survivorship but losers are still destroyed at run end | read-time survivorship over a lossless corpus | Part 3 |
| 9 | src/mcp_server/tools/plan_scope.py:528 | P4 minimal key: ONE combination in schema order; others not enumerated | none | MISSING-TRIGGER |
| 10 | src/mcp_server/tools/plan_scope.py:562 | P4 junction/weak roles: sample proves non-uniqueness only; no composite FKs; one discriminator column | human-confirmed structure remembered across runs | Part 3 section 3.9 |
| 11 | src/mcp_server/tools/splink_matcher.py:267 `pre` | only the first extra exact-match column gets a blocking rule | reuse S-E's `count_comparisons_from_blocking_rule` cost check | MISSING-TRIGGER (path only) |
| 12 | src/mcp_server/tools/load_data.py:483 | P4 dimension find-or-create only among rows this function wrote; pre-existing same-text row is duplicated | human-confirmed structure | MISSING-TRIGGER |
| 13 | src/mcp_server/tools/metadata_classification.py:20 | P12 detection by name/DBMS convention; business fields that look like metadata are misclassified; no value patterns | classify by the 3.5 taxonomy (measured features) | Part 3 with 3.5 |
| 14 | src/mcp_server/tools/fellegi_sunter.py:137 | TF table is per-run | global per-run-snapshotted table (D-23) | Part 3 storage |
| 15 | src/mcp_server/tools/fellegi_sunter.py:174 | P14 soft Monge-Elkan instead of TF-IDF text weight | continuous `sum(-log2(df/N))` weight | MISSING-TRIGGER (path only) |
| 16 | src/mcp_server/tools/fellegi_sunter.py:309 | P13 rare values floor at a pseudo-count; score uncalibrated | Platt/isotonic calibration | review-band volume justifies it |
| 17 | src/mcp_server/tools/cluster_quality.py:56 | P20 name spread is character-level; acronym vs expansion reads as unrelated | none | MISSING-TRIGGER |
| 18 | src/mcp_server/tools/cluster_quality.py:69 | P16 signals not measurements; sibling/legal-form clusters unflagged; lone fused cluster unflagged; quality history JSON last-writer-wins | persisted time series + drift alerting; correlation clustering | Part 3; or local run shows flagged clusters are real wrong merges |
| 19 | src/mcp_server/tools/map_fields.py:239 | P4 dimension reference read off column names when FK info absent | none | MISSING-TRIGGER |
| 20 | src/mcp_server/tools/map_fields.py:943 | P1 dictionary split cannot resolve real ambiguity; ties reported, never picked | none | MISSING-TRIGGER |
| 21 | src/mcp_server/tools/map_fields.py:946 | P1 1-letter vocabulary word gives absurd unique splits (`idea` -> id/e/a) | minimum segment length (a fitted constant, so not added) | such a vocabulary appears |
| 22 | src/mcp_server/tools/join_graph.py:329 | P3 FK into a dense surrogate sequence (1..N) is not inferred | none (declared FK covers it) | MISSING-TRIGGER |
| 23 | src/mcp_server/tools/join_graph.py:351 | P3 full profile scan per surviving column | none | MISSING-TRIGGER |
| 24 | src/mcp_server/tools/join_graph.py:417 | P3 unary INDs only; key-less/nullable misjudged; 1:1 extension tables dropped by PK-subset filter | none (declared FK finds it) | MISSING-TRIGGER |
| 25 | src/mcp_server/tools/join_graph.py:496 | P3 one filtered query per surviving pair | none | MISSING-TRIGGER |
| 26 | src/mcp_server/tools/join_graph.py:534 | P3 containment is the only score; coincidental overlap-1.0 IND survives; dirty genuine FK can be cut | classifier on non-name features trained on confirmed FKs | after Part 3 |
| 27 | src/mcp_server/tools/join_graph.py:623 | P3 with no origin config, no value-based edges are proposed (no name guess) | none | MISSING-TRIGGER |
| 28 | src/mcp_server/tools/join_graph.py:750 | P7 pointer/junction tables read in full into memory; one reference per FK column | relationship-based duplicate detection over the edges | a non-empty hierarchy on the real origins |
| 29 | src/mcp_server/tools/resolve_entities.py:891 | P15 pairs below the exclude bar not retained (lower committed bar needs a stage retry); band width is policy | Possible Match as persisted revocable state | Part 3 |
| 30 | src/mcp_server/tools/resolve_entities.py:956 | P15 changed cluster id derived from new membership, not the previous run's id | none | MISSING-TRIGGER |
| 31 | src/mcp_server/tools/resolve_entities.py:1087 | P5 a typo inside every shared token no longer co-blocks (phonetic key removed) | none | MISSING-TRIGGER |
| 32 | src/mcp_server/tools/resolve_entities.py:1189 | P5 meta-blocking lossy by design; CJK name is one token | character n-gram keys (CJK); progressive resolution | partial: progressive "if a run is ever time-boxed"; n-gram has none |
| 33 | src/mcp_server/tools/resolve_entities.py:1913 | P14 four levels cannot separate "two rare tokens" from "five common" | continuous text weight | once this version is measured |
| 34 | src/mcp_server/tools/resolve_entities.py:2604 | P6 identifier classifier weak with few evident duplicates | report D-26 full classifier | the corpus (Part 3) |
| 35 | src/mcp_server/tools/resolve_entities.py:2896 | P8 LLM `p` is uncalibrated; band reads it as is | calibration against a labelled sample (P13 / 2.17) | MISSING-TRIGGER (names an open item, no condition) |
| 36 | src/mcp_server/tools/resolve_entities.py:3206 | P20 two strong wrong edges into one node form a real triangle; names not consulted | correlation clustering | flagged clusters prove to be real wrong merges |
| 37 | src/mcp_server/tools/resolve_entities.py:3226 | P20 WNP drops a true edge below the mean at both ends | none | MISSING-TRIGGER |
| 38 | src/mcp_server/tools/resolve_entities.py:3274 | P20 pendant member split off a clique | support over 2-hop paths | MISSING-TRIGGER (path only) |
| 39 | src/mcp_server/tools/resolve_entities.py:3300 | P20 1:1 reduction skips 3+ sources and skips silently-logged when scipy missing | none | MISSING-TRIGGER |
| 40 | tests/eval/score.py:30 `pre` | flat 10% name-block share heuristic | real block-size estimate | a legitimate dataset trips it |
| 41 | tests/eval/score.py:458 | P19 blocking recall unmeasured without labels; harness has no caller | PC/PQ against a HITL-labelled sample; wire into `main()` | first run with such decisions (wiring: Part 4) |

MISSING-TRIGGER rows: 1, 2, 7, 9, 11, 12, 15, 17, 19, 20, 22, 23, 24, 25, 27, 30, 31, 35, 37, 38, 39 = **21** (2 of them `pre`: 7, 11; 4 are path-only: 11, 15, 35, 38). Row 32 is partial and not in the 21.
Several MISSING rows are stances with no deferral (22-25, 27, 31, 37): they are accepted ceilings, not debts, but they still lack the sentence saying so.

##### 1b. Strict-xfail ceilings in tests/part1

27 `xfail(strict=True)` markers in tests/part1 (AST count, parametrized cases count once). **10 carry "ceiling" or "waiver" in their reason:**

| test | ceiling (reason, abridged) | trigger |
|---|---|---|
| test_p10_attack.py:110 | F4 (lead waiver r2): run-relative proposals miss uniform badness (all entities ~45% NULL or one cluster) pass; operator sees values at the gate | none stated |
| test_p10_attack.py:844 | r3 (waiver): nameless doc with a name column no sibling has (`email`) judged as second naming style | none stated |
| test_p10_attack.py:877 | r3 LOW-MEDIUM (waiver): coverage width ignores carried fields the mapper never listed (sample cap, pinned profile) | none stated |
| test_p10_attack.py:929 | r3 LOW (waiver): `nickname` outranks a populated `email`; users table with sparse nickname is refused | none stated |
| test_p20_attack.py:99 | WNP recall trade: 0.4% weight wobble splits a sparse chain | none stated |
| test_p20_attack.py:130 | decision c: every-member double bridge, WNP prunes every true edge | P16 flag is the only guard |
| test_p20_attack.py:551 | two wrong edges into one node form a real triangle | none stated |
| test_p4_attack.py:198 | F6: role depends on column order when several minimal keys exist | none stated |
| test_p4_attack.py:570 | F10: pre-existing same-text dimension row duplicated | none stated |
| test_p9_attack.py:320 | P9 two live instances last-writer-wins | "Part 3 trigger" |

The other 17 strict xfails are defects or limitations the tests still fence without that wording (P15 F1-F4 x4, P16 x3, P20 r4 x2, P4 F2/F4/F7 x4, P8 NaN, P9 remembered keep_separate, P18 option B, P19 D6). Not counted as ceilings, but several (P15 F1-F4, P20 r4 splits) are open defects, not accepted trade-offs.

##### 1c. Deferrals recorded in HANDOFF.md (not already a `ponytail:` row)

| source | deferral | trigger |
|---|---|---|
| P3 row + escalation | LIVE RISK: dense `company.companyid` makes the coincidence rule drop `staff.main_entity_id -> company.companyid`; `tests/part1/baseline_schema.py` still calls the old `_find_candidate_edges` signature | LOCAL_VERIFY P3 |
| P2 row | WNP loss 4-19% vs uncapped accepted; block filtering omitted (ratio fitted) | none |
| P5 row | runtime ~quadratic (55k records about 90 s) | none |
| P6 row | short codes one edit apart look exclusive; mailbox + rare token leaves 2-3 review items; `possibly_related` has no consumer | none |
| P7 row | BRANCH_OF never derived; nothing downstream consumes typed edges; no `entity_tables` feeds every same-key junction | none |
| P8 row | LLM `p` uncalibrated; about 40x more calls than batches | P13 / 2.17 |
| P9 row | remembered keep_separate not honoured if run 2 scores >= auto_match; `decisions_from_memory` only in `resolution_stats` | none |
| P12 row + escalation | value patterns not implemented; `dw_load_id`/`ingestion_time` missed; pinned pre-P12 profile reused verbatim can map a metadata column | Phase 5 / profile owner |
| P14 row + escalation | initials and middle initials score lower; 0.90/0.70 cut-offs inherited from difflib, not recalibrated | restored ground truth (LOCAL_VERIFY P14) |
| P15 row + escalation | F1-F4 (keep+accept audit, lowered bar sealed by kept cluster, later keep after merge, raised bar not persisted); band width policy | Phase 5 decision |
| P16 row | 1-char junk name masks a fused cluster; two-token reorder flagged (5-7% of generated clusters); quadratic past about 10k names; "half a holder name" ratio is hand-set (debt: derive from data); empty run_id self-compares; one unreproduced xdist flake; lone fused cluster unflagged | none |
| P17 row | whole entity withheld on one absent mapped column | Part 3 (row 4) |
| P18 row + escalation | `field_priorities` has no API/UI surface (config-only); longest changes non-text winners | none |
| P20 row | **measured cost of decision c:** no name cut, bridged chain/tree entities over-merge (planted FP 826-9896 vs parent 31-79); jittered K4 splits in 3.1% of draws (parent 0%); `one_to_one` has no caller; `min_cohesion` unused in rebuild | local run shows P16-flagged clusters are real wrong merges |
| Escalation | `tests/part1/` is NOT run by `check.py test`; CI sees only unit + integration | Phase 5 decision |
| Escalation | P1 `ambiguous_splits` returned by `map_fields_impl` but dropped by `map_fields_node` | not stated |
| Notifications (Ph 3, 4) | `_split_weakest_link`, cohesion branch, `min_cohesion` unreachable from build/rebuild (about -60 lines); `entities_at_worst` duplicates `entities_violating` (touches Gates.tsx); `sql_lineage.py` has no caller | Phase 5 audit |
| Notifications (Ph 1, 2) | discover_schemas `run_kwargs`, `_named_like` no-FK fallback, `_key_info` schema-cache reuse, `_materialise_dimensions` guard duplicate: reviewed small, kept | none |
| Other (found here) | `scipy` is imported by the P20 `one_to_one` path (resolve_entities.py about 3309) but is declared in no manifest in the repo (grep of toml/txt/cfg: no hit); the marker calls it "optional extras" | not stated |

#### 2. Ponytail gain

Scoreboard from the skill, as printed. **These are the published benchmark medians (5 everyday tasks, 3 models), not numbers for this repo.** The skill forbids a per-repo savings figure because no unbuilt baseline exists.

```
  ponytail gain                     benchmark median · 5 tasks · 3 models

  Lines of code   no-skill  ████████████████████  100%
                  ponytail  ██▌·················    6–20%   ▼ 80–94%
  Cost            no-skill  ████████████████████  100%
                  ponytail  █████▌··············   23–53%  ▼ 47–77%
  Speed           ponytail  ▸ 3–6× faster

  This repo:  /ponytail-debt  (shortcuts you deferred)
              /ponytail-audit (what's still cuttable)
```

##### This branch's own measured numbers (git, origin/main...HEAD)

| measure | value |
|---|---|
| `-- src` | 28 files changed, 4347 insertions(+), 927 deletions(-) (net +3420) |
| `-- tests` | 55 files changed, 19754 insertions(+), 417 deletions(-); tests/part1 alone is 39 files, +18829 / -1 |
| `-- tests` outside tests/part1 | +925 / -416 |
| whole branch | 139 files, +26992 / -1388 (docs + scripts: 4 files, +216 / -40) |
| `pyproject.toml` dependency diff | **none**: empty diff against origin/main (no requirements/package/lock file changed either) |
| ponytail markers | 41 in src/tests (4 on main, 37 added); 37 added lines in the diff |
| debt | 21 of 41 markers lack a trigger; 10 strict-xfail ceilings of 27 strict xfails |

Caveat: `git diff --shortstat` counts lines, not value; tests/part1 is 4.3 times the src change and is not run by `check.py test`.

## Phase 5 lane C — docs updated

### Phase 5 lane C: docs/architecture update

`check.py docs` passes. Only docs/architecture/*.md changed (no INDEX link needed; overview.md TOC got a 6b entry). No file:line citations added.

#### File -> change
- data_pipeline.md
  - §7: EM paragraph fixed (m = EM, field-level u = sampled random pairs, P13; new "why u is not from EM" and IDF token-name comparison, P14).
  - §7 Blocking rewritten: no comparison budget; token blocking (P5) -> IDF block filtering + purging -> WNP meta-blocking; `_MAX_CANDIDATE_PAIRS` is an OOM guard raising `CandidatePairBudgetExceeded`; stats counters; loss is measured (P19), not assumed (P2).
  - §7 Clustering rewritten: confident edges only -> WNP -> conflict repair -> triangle support (+ optional 1:1); cohesion cut no longer in the main path; names are a flag, not a cut (P20).
  - §7 borderline pairs: band goes to the matches gate with `band_summary`, `pair_decisions`, `band_thresholds`, `rebuild_band_clusters` (P15); decision memory, env-var driven (P9).
  - §8: `completeness` -> `longest` (alias kept), text-only; higher priority number wins, `field_priorities`; total-order `first_seen`; `numeric_disagreement` in the report (P18).
  - NEW §6b: catalog/ETL metadata classification + 3 override layers (P12), value-based IND join graph (P3), table roles + relationship edges shown at the deps gate and never acted on (P4, P7).
  - §10: pre-load gate (P10), destination defect report (P17), run-report quality signals (P16).
- known_problems.md: §18.4 rewritten as resolved (budget gone, guard raises); §19.1 "truncate" -> "fail loudly"; §19.10 no longer says cohesion/average-linkage runs on every component; item 1 wording; idea 10 notes P9 only replays.
- orchestration_and_ui.md: matches-gate condition, new subsection "What the gates carry now" (deps/matches/final payloads and feedback fields), feedback route row, `run_config.metadata_overrides`.
- state_and_graph.md: new MergeState keys (`table_roles`, `relationship_edges`, `pre_load_thresholds`, `metadata_overrides`).
- overview.md: TOC 6b, gate descriptions (matches band, final approval), file map rows (metadata_classification, join_graph, cluster_quality, decision_memory).
- testing_and_decisions.md: decision-table rows replaced/added (u sampling, token blocking + WNP, clustering, band gate, pre-load gate, IND join graph, `longest`).

#### ADRs
- docs/decisions/0014-replace-budget-truncation-with-blocking-analysis.md: Consequences bullet "builtin engine's truncation-and-report behavior stays as-is" is now false (P2 removed truncation from the builtin engine). Partially superseded; not edited.
- 0004 line ~9 describes the old builtin as "comparison-budget truncation" (history, still accurate as history).

#### Could NOT verify / flags
- `decision_memory_path` and `quality_history_path` are read via `state.get(...)` but not declared in `MergeState`, so only the env vars (`MERGE_ENGINE_DECISION_MEMORY`, `MERGE_ENGINE_QUALITY_HISTORY`) are reliable. The docs say so. The lead may want to declare them.
- Figures quoted (711,670 pairs, 40/40, 4-19 % WNP loss, 20k records in 39 s) come from HANDOFF.md, not re-run.
- The §19.10 runtime claim is marked "not re-measured on the new path".
- Pre-existing stale line-number citations in untouched sections (e.g. overview.md `resolve_entities.py:1091`) pass the linter but are likely wrong; untouched.
- docs/architecture/llm_and_profiles.md not changed. P8's one-pair-per-call LLM judging could warrant a line there; it was outside the item list.
