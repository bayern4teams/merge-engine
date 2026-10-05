# Part 1 handoff — the only memory that survives a session

Overwrite this file at every commit. A fresh session reads it first, then
[the plan](../PART1_IMPLEMENTATION_PLAN.md). Git is the memory; a session's context is not.

**Lock:** 2026-10-05T09:20:15Z · **Autonomy:** unattended-through-phase-5 · **Last heartbeat:** 2026-10-05T09:20:15Z

(Lock/heartbeat protocol: plan §7. A session sets `Lock: <ISO time>`, pushes it, and refreshes it at every commit; a lock older than 75 min is stale. `Autonomy` is `off`, `dryrun` (a tick only records what works and exits) or `unattended-through-phase-5` (authorises the supervisor to chain phases). Only the user changes it.)

**Branch:** `feat/part1-fixes` · **Current phase:** 1 done, 2 next (Phase 0: [PHASE0_REPORT.md](PHASE0_REPORT.md)); cloud re-checks #1 and #2 done (see [CLOUD_RECHECK.md](CLOUD_RECHECK.md), [CLOUD_RECHECK2.md](CLOUD_RECHECK2.md)) · **Last usage reading:** 5-hour window `allowed_warning`, resets 12:00Z, at 2026-10-04 10:28Z via cloud `get_session` (no percentage available in the cloud)

| item | phase | state | commit | lead verdict |
|---|---|---|---|---|
| P1 | 1 | unverified-live | (this commit) | ACCEPT r2: 63 acceptance + 2 attack tests pass; 0 of 73 names regressed on a 50-column schema; debt: 1-letter vocabulary words |
| P2 | 1 | unverified-live | 5431075 | ACCEPT after attack F1-F3,F5 fixed: 40/40 planted (was 31/40), uncompared 0 (was 389,200), guard raises and fails the run; F4 WNP loss 4-19 % vs uncapped = accepted ceiling; block filtering omitted (ratio r is fitted, §2.4) |
| P3 | 2 | unverified-live | (this commit) | ACCEPT r2 (worker, lead diff review): 34/34 acceptance; value-based INDs, RHS-uniqueness (profile_column or complete sample), PK-subset filter, binomial pre-prune (miss < 1/pairs), coincidence test vs the parent's density over the child's range, cliff; decoy rejected at caps 50/500; cost 0-6,084 filtered queries vs 439,886 naive. Ceilings: FK into a DENSE surrogate sequence not inferred (declared FKs cover it); 1:1 extension tables dropped; lone low-overlap string edge stays `probable` (no floor); Mongo key-less tables |
| P4 | 2 | unverified-live | (this commit) | ACCEPT r2 (worker, lead diff review): 37/37 acceptance; attack r1 REJECT (mapper Phase 1b took primary-column fields `Industry`->family table, routed child tables, self-FK label) -> r2: primary column wins, dimension = table the primary references and that does not reference back (FK info now passed by map_fields_node), label never FK/PK/taken/non-text; 43/44 attack (a3 strict xfail = ceiling: self-FK label when the caller gives no FK info; real path passes FK info). Roles via minimal UCC from sampled rows, proposed at the deps gate (MergeState.table_roles, lead), never acted on. Ceilings: weak proposals noisy, one minimal key (column order), a NULL FK erases a role, pre-existing dimension row with same text duplicated, case variants are distinct rows, no-FK-info fallback reads column names |
| P5 | 2 | unverified-live | (this commit) | ACCEPT r3 (lead item): 19/19 acceptance. Attack r1: realistic company corpus raised the guard, CJK lost; r2: typo'd-surname persons PC 0.965 -> 0.522 (asymmetric per-record filter). r3: token blocking (Unicode) -> local-IDF block filtering (geometric mean, chosen by measurement — disclosed) with the either-end union rule -> purging (dominance over all other blocks, or IDF < 1 bit, coverage by kept blocks) -> node-centric WNP; guard counts retained pairs. Attacker corpora: persons 6.8k PC 0.985 (old 0.965 full-name / 0.785 split), companies 2k PC 1.000 (old 0.995), 20k PC 0.999 in 39 s; lead 6.4k persons 326/400 (old 296). Ceilings: CJK one-token names, runtime ~quadratic (55k ≈ 90 s) |
| P6 | 3 | todo | | |
| P7 | 3 | todo | | |
| P8 | 3 | todo | | |
| P9 | 3 | todo | | |
| P10 | 4 | todo | | |
| P11 | 1 | unverified-live | (this commit) | ACCEPT on lead review (10-line Semaphore(_LLM_CONCURRENCY) around the existing gather; attack step replaced by line review — deviation, trivial diff): 25/25 acceptance incl. bound 1/2/4 peak tests; report's "serial" was stale, the defect was the missing bound |
| P12 | 1 | unverified-live | (this commit) | ACCEPT: 25/26 acceptance (4c documented skip); attack: generic audit/ETL/DBMS vocabulary (report sanctions name patterns), no corpus literal, overrides run>origin>default, corpus rows unchanged; ceilings: value patterns not implemented, dw_load_id/ingestion_time missed; per-run layer threaded via RunConfig.metadata_overrides (lead) |
| P13 | 1 | unverified-live | (this commit) | ACCEPT r2: 17/17 acceptance (lead adapted fit() to pass population_pairs) + consistency test (precision@0.5 1.0 / 0 FP vs old 0.97-0.99 / 1-2 FP on 3 seeds); prior rescaled to random-pair population (lead deviation); call site samples 3000 pairs from records sorted by id, seed 0 |
| P14 | 1 | unverified-live | (this commit) | ACCEPT r2 (lead item): 24/24 acceptance. Attack r1 rejected binary-token cosine (typos -> DISAGREE, person recall@0.7 0.85 -> 0.26). r2 = IDF-weighted symmetric Monge-Elkan over `_seq_ratio` (no new constant); text path only when both values have letters + a space. e2e synthetic: company recall@0.7 0.497 -> 0.611, @0.95 0.250 -> 0.296, precision 1.0; person @0.95 0.577 -> 0.552 and @0.7 0.845 -> 0.552 = knife edge (old truth pairs sat at 0.705, new m(strong) 0.52 vs 0.60 puts them at ~0.67). Debt: initials ("j. smith") weak -> disagree, middle initial strong -> weak; 0.90/0.70 cut-offs carried over from difflib, not recalibrated |
| P15 | 3 | todo | | |
| P16 | 4 | todo | | |
| P17 | 3 | todo | | |
| P18 | 1 | unverified-live | (this commit) | ACCEPT: 54/55 acceptance (3b = rejected option B, strict xfail); attack follow-ups applied (comment reworded: longest is text-only and changes non-text winners — sanctioned by report ③; disagreement needs spread>0 and finite values). ③ = rename + `completeness` alias |
| P19 | 3 | todo | | |
| P20 | 3 | todo | | |

States: `todo` · `in-progress` · `accepted-cloud` (lead accepted, cloud-verifiable parts proven)
· `unverified-live` (accepted but needs the real databases — see LOCAL_VERIFY.md) · `blocked`.

## Locked file-ownership table (Phase 0.5)

Derived with `graphify affected` on each item's named functions (log: `.tmp/phase0/affected.txt`,
local only), then confirmed by reading the call sites. `affected` lists *callers*; the table
lists files an item must **edit**. Paths are under `src/` unless stated. Abbreviations:
`T/` = `mcp_server/tools/`, `N/` = `orchestrator/nodes/`.

| item | owns (edits) | read-only blast radius (callers seen by `affected`) |
|---|---|---|
| P1 | `T/map_fields.py` (`_tokenize_field`, `_token_overlap`, `_heuristic_match`) | `T/schema_matching.py` |
| P2 | `T/resolve_entities.py` (`_generate_candidate_pairs`, `_MAX_COMPARISONS`) | — |
| P3 | `T/join_graph.py` (`_find_candidate_edges`, `_verify_inclusion`, `build_join_graph`), `N/analyze_dependencies.py` | `T/plan_scope.py`, `N/plan_scope.py`, `orchestrator/graph.py` |
| P4 | junction test: `T/plan_scope.py` + `N/analyze_dependencies.py`; dimension rows: `T/map_fields.py`, `N/map_fields_node.py`, `N/load_data.py`; weak-entity proposal: `N/human_nodes.py` (`_build_deps_interrupt`) | `T/join_graph.py` |
| P5 | `T/resolve_entities.py` (`_generate_candidate_pairs`, `_blocking_keys_for`, `_pick_blocking_field`, `_blockable_fields`, `_phonetic_key`) | — |
| P6 | `T/resolve_entities.py` (`_has_conflicting_identity`, `_deterministic_conflict`, `_has_discriminating_agreement`, `_compute_heuristic_confidence`, `_score_pair`) | — |
| P7 | `T/resolve_entities.py` (`build_relationship_index`, `_shared_neighbors_beyond_common_parent`), `N/resolve_entities.py` (`_relationship_index_for_companies` — selects tables by the `company_relation` concept name), `T/join_graph.py` (self-INDs) | `orchestrator/graph.py` |
| P8 | `T/resolve_entities.py` (`_judge_pairs_with_llm`, `_format_pairs_block`, `_MAX_LLM_PAIRS`, `_LLM_CHUNK_SIZE`) | `N/resolve_entities.py`, `scripts/diagnostics/profile_resolve.py` |
| P9 | new decision-memory module (file store) + its hook in `N/human_nodes.py` and the gate router in `orchestrator/graph.py` | — |
| P10 | `N/load_data.py` (pre-load invariants), `N/human_nodes.py` (`human_final_approval`), `orchestrator/graph.py` (gate wiring) | — |
| P11 | `N/map_fields_node.py` (`map_fields`) | — |
| P12 | `N/discover_schemas.py`, `T/discover_schema.py`, `N/map_fields_node.py`, `N/resolve_entities.py` (record preparation for matching), override config | must **not** edit `N/load_data.py` in Phase 1 (see flag 3) |
| P13 | `T/fellegi_sunter.py` (`estimate_model`) **and its call site** `T/resolve_entities.py` `_fit_match_model` | `scripts/splink/*` |
| P14 | `T/fellegi_sunter.py` (`build_term_frequencies` + new `build_token_document_frequencies`), `T/resolve_entities.py` (`_comparison_level`, `_fit_match_model`) | `scripts/splink/*` |
| P15 | `T/resolve_entities.py` (`_pending_merge_candidates`, `_thresholds_for`), `N/resolve_entities.py`, `N/human_nodes.py` (`_build_matches_interrupt`, `human_review_matches`) | `T/splink_matcher.py` (imports the band constants), `T/__init__.py`, `mcp_server/server.py` |
| P16 | new label-free quality module + `N/load_data.py` (`build_reconciliation_report`) | — |
| P17 | `N/load_data.py` (`_load_all_entities`, report), `N/human_nodes.py` (pre-load gate payload) | — |
| P18 | `T/unify_entity_group.py`, `T/survivorship.py`, `N/unify_entities.py`; sub-fix ⑤ adds one section to `N/load_data.py` `build_reconciliation_report` | `orchestrator/graph.py`, `api/main.py` + `orchestrator/workflow_manager.py` (document the policy names; edit only if ③'s rename needs the alias there) |
| P19 | `tests/eval/score.py` + new tests (PC/PQ only where ground truth exists); read-only on `T/resolve_entities.py` | — |
| P20 | `T/resolve_entities.py` (`_build_clusters`, `_partition_component`, `_split_weakest_link`) + the name-variation helper (see flag 5) | `T/splink_matcher.py` |

### Flags — item pairs that share a file

1. **`T/resolve_entities.py`** (2.7k lines): P2, P5, P6, P7, P8, P13 (call site), P14, P15, P20.
   Expected; it is the lead's single serial chain (plan §3.3).
2. **Unexpected: P13 is not confined to `fellegi_sunter.py`.** `estimate_model` receives only
   candidate-pair comparison vectors (`fellegi_sunter.py:182`, called from `_fit_match_model`,
   `resolve_entities.py:1437`). Sampling `u` from the unblocked cross product needs vectors for
   random record pairs, built by `_build_comparison_vector` in `resolve_entities.py`. Lane D
   therefore cannot be disjoint as planned. **Resolution:** the worker owns only
   `fellegi_sunter.py` (`estimate_model` accepts the sampled vectors as an optional argument
   and uses them for `u`); the lead makes the call-site change inside the serial chain.
3. **Unexpected: `N/load_data.py` is touched by five items** — P18 ⑤ (Phase 1), P4 (Phase 2),
   P17 (Phase 3), P16 and P10 (Phase 4). One owner per phase: P18's lane in Phase 1, lane E in
   Phase 2, P17's lane in Phase 3, then P16 → P10 serially. P12 reaches projection exclusion
   through the mapping (an excluded column is never mapped, so never projected); if that proves
   insufficient it asks the lead instead of editing `load_data.py`.
4. **Unexpected: `N/human_nodes.py` and `orchestrator/graph.py` are shared in Phase 3** by P15
   (lead), P9 and P17 (side lanes), and later P10. **Resolution:** P9's side lane owns only the
   new decision-memory module and its tests; P17's side lane owns `load_data.py` only. Both
   gate hooks in `human_nodes.py`/`graph.py` are applied by the lead after P15 is accepted.
   Anyone touching them reads `docs/architecture/orchestration_and_ui.md` first (plan §2.6).
5. **Dependency inversion: P20 ↔ P16.** P20's coherence split uses P16's name-variation rate,
   but the DAG puts P16 *after* P20. **Resolution:** P20 introduces the name-variation function
   (in the new quality module, not in `resolve_entities.py`); P16 reuses it.
6. `T/fellegi_sunter.py` (P13 → P14), `T/join_graph.py` (P3 → P7), `T/map_fields.py` (P1 → P4),
   `N/map_fields_node.py` (P12 → P11 → P4), `N/resolve_entities.py` (P12 in Phase 1; P7, P15 in
   Phase 3) are each shared only across phases or inside one serial lane. No conflict.

## Lane plan (with the flags applied)

| phase | lead (serial) | worker lanes (≤ 2 concurrent, disjoint files) |
|---|---|---|
| 1 | P2 → P13 call site → P14 (`T/resolve_entities.py`, `T/fellegi_sunter.py` after P13) | **B** P1 → P12 → P11 (`T/map_fields.py`, `N/discover_schemas.py`, `T/discover_schema.py`, `N/map_fields_node.py`, `N/resolve_entities.py`) · **C** P18 (`T/unify_entity_group.py`, `T/survivorship.py`, `N/unify_entities.py`, `N/load_data.py`) · **D** P13 (`T/fellegi_sunter.py` only) |
| 2 | P5 (needs P2 accepted) | **E** P3 → P4 (`T/join_graph.py`, `N/analyze_dependencies.py`, `T/plan_scope.py`, `T/map_fields.py`, `N/map_fields_node.py`, `N/load_data.py`, `N/human_nodes.py`) — lead reviews line by line |
| 3 | P15 → P6 → P7 → P8 → P20, then the P9/P17 gate hooks | P9 (new module only) · P17 (`N/load_data.py` only) · P19 (`tests/eval/score.py`, tests) |
| 4 | P10 (after P16, P17, P18) | P16 (quality module + `N/load_data.py` report) |

## Next action
**Phase 2, step 2:** P3 and P5 accepted. P4 lane E worker implementing against `.handoff/P4.acceptance.md`
(owned files incl. `T/load_data.py` `_write_child_rows`); then attack (`part1-verifier`), lead line review,
then the Phase 2 gate (`check.py all`, `pytest tests/part1`, CI green, `/ponytail-review`). Runs only while
`Autonomy` is `unattended-through-phase-5`.
Supervisor tick 2026-10-05T08:53Z: graft not on PATH (optional tier 2; continuing with graphify + codebase-memory-mcp); `.venv` absent at session start, rebuilt by `bootstrap.py` (2m13s).
Phase 1 deviations (one line each, §3.2): the lead edited `N/resolve_entities.py` (lane B) for P2's
guard re-raise and pruned counts; the lead threaded `metadata_overrides` through `api/main.py` and
`orchestrator/graph.py` for P12; P11 accepted on lead line review (10-line diff) without a separate
attack agent. P13: prior rescaled to the random-pair population (lead decision, see verdict).

## Open escalations (worker/verifier results that passed only written tests or one case)
- `tests/part1/` is NOT run by `check.py test` (CI runs tests/unit + tests/integration only). The lead runs `pytest tests/part1` at every phase gate; folding it into CI is a Phase 5 decision.
- P1 `ambiguous_splits` is returned by `map_fields_impl` but dropped by `map_fields_node` — surface it in lane B (P11/P12).
- P18 `field_priorities` has no API/UI surface yet (config-only).
- P4 ownership extended by the lead: `src/mcp_server/tools/load_data.py` `_write_child_rows` (dimension find-or-create); no other lane touches it.
- P3 LIVE RISK: if `company.companyid` is a dense integer sequence, the coincidence rule drops the very `staff.main_entity_id -> company.companyid` edge the report targets (LOCAL_VERIFY P3). `tests/part1/baseline_schema.py` still calls the old `_find_candidate_edges` signature (script, not a test).
- P14: person-name initials/middle initials score lower than before (level debt above); the 0.90/0.70 level cut-offs were calibrated for difflib and are reused on the IDF similarity — revisit with the restored ground truth (LOCAL_VERIFY P14).
- P12: a mapping pinned in a saved profile before P12 is reused verbatim, so it can still map a metadata column (profile replay is not re-filtered). Decide in Phase 5 or with the profile owner.
- P13 side effect for P14/P6: Phase 0 P6 case a (same email, truth Match) on the model path moved 0.76 (review) -> 0.67 (excluded). Cause measured: consistent random-pair prior (-11 bits at n=156) vs two unique exact agreements (+6.04 bits each); the fixture's match count is fixed while n grows, so the prior shrinks as 1/n^2. Real wrinkle: `build_term_frequencies` counts the record's own value (u = df/N, not (df-1)/(N-1)), ~1 bit per exact field. P14 (token-grain TF) must decide this; P6's outcome-class tests on both paths are the gate.

## Notifications
- [PHASE 1 DONE] 2026-10-04: P1, P2, P11, P12, P13, P14, P18 accepted (all `unverified-live`, see LOCAL_VERIFY.md). `check.py all` green locally; CI green on the phase head; `/ponytail-review` of the phase diff: net -6 lines possible, deferred (discover_schemas `run_kwargs` kept for a dozen one-argument test stubs; `_names` str-or-list and the two metadata lookups too small to churn).
