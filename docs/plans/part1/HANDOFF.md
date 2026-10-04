# Part 1 handoff — the only memory that survives a session

Overwrite this file at every commit. A fresh session reads it first, then
[the plan](../PART1_IMPLEMENTATION_PLAN.md). Git is the memory; a session's context is not.

**Lock:** none · **Autonomy:** dryrun · **Last heartbeat:** n/a

(Lock/heartbeat protocol: plan §7. A session sets `Lock: <ISO time>`, pushes it, and refreshes it at every commit; a lock older than 75 min is stale. `Autonomy` is `off`, `dryrun` (a tick only records what works and exits) or `unattended-through-phase-5` (authorises the supervisor to chain phases). Only the user changes it.)

**Branch:** `feat/part1-fixes` · **Current phase:** 0 done (see [PHASE0_REPORT.md](PHASE0_REPORT.md)); cloud re-checks #1 and #2 done (see [CLOUD_RECHECK.md](CLOUD_RECHECK.md), [CLOUD_RECHECK2.md](CLOUD_RECHECK2.md)) · **Last usage reading:** 5-hour window `allowed_warning`, resets 12:00Z, at 2026-10-04 10:28Z via cloud `get_session` (no percentage available in the cloud)

| item | phase | state | commit | lead verdict |
|---|---|---|---|---|
| P1 | 1 | todo | | |
| P2 | 1 | todo | | |
| P3 | 2 | todo | | |
| P4 | 2 | todo | | |
| P5 | 2 | todo | | |
| P6 | 3 | todo | | |
| P7 | 3 | todo | | |
| P8 | 3 | todo | | |
| P9 | 3 | todo | | |
| P10 | 4 | todo | | |
| P11 | 1 | todo | | |
| P12 | 1 | todo | | |
| P13 | 1 | todo | | |
| P14 | 1 | todo | | |
| P15 | 3 | todo | | |
| P16 | 4 | todo | | |
| P17 | 3 | todo | | |
| P18 | 1 | todo | | |
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
Dry run passed (skills, MCP tools, push, gh, setup line; graft's npm download is flaky and a real
tick recovers it with step 4). **Phase 1, step 1:** derive acceptance for P2, P1, P18, P13 with
`part1-verifier` agents, then implement per the lane plan below. Runs only while `Autonomy` is
`unattended-through-phase-5` (set by the user).

## Open escalations (worker/verifier results that passed only written tests or one case)
none
