# TODO — `feat/matching-precision` (B4)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05. Nothing in this branch
has started yet. **Merges fourth in the integration order (after B5, B3, B1;
before B2).**

## B4.0 — Cross-origin golden set (do this FIRST — everything below is unmeasurable without it)

- [x] Sample candidate company + person pairs across origin1/origin2 for
      Boeing, Airbus, Lockheed. **Live verification:** `tests/eval/cross_origin_golden.py`
      pairs drawn from real ClickHouse origins (`origin1` Company/Person vs `origin2` company_profile/contacts).
- [x] Label pairs by hand against the three hand-merge baselines
      (`docs/diagnostics/*_APP_VS_MANUAL_DIAGNOSIS.md` + prior manual-merge
      records). **Live verification:** spot-checked Boeing, Airbus, and Lockheed executive/company labels against diagnostic reports.
- [x] New golden set added to `tests/eval/` alongside (not replacing) the
      existing 560 Pagila pairs. **Live verification:** `python -m tests.eval.score`
      evaluated both sets (560 general-dedup pairs: P=1.0000/R=0.8850/F1=0.9390 vs 10 cross-origin pairs: P=1.0000/R=0.2000/F1=0.3333, proving the cross-origin recall bottleneck) and `pytest tests/unit/test_cross_origin_golden.py` passed 2/2 tests.

## B4.1 — Identity-key discovery

- [x] `tools/identity_profile.py`: per-column profiling (within-origin
      uniqueness, cross-origin overlap, null rate, cardinality) via
      UCC/FD discovery. **Live verification:** tested on real Boeing/Airbus
      origin records; confirmed `website`/`ticker` classify as high-uniqueness/high-overlap
      `identity_key` and `companyID` as high-uniqueness/zero-overlap `surrogate_key`.
- [x] LLM classification step (role + cross-origin alignment) grounded in
      the measured profile. **Live verification:** `align_cross_origin_fields` correctly
      aligned `company_name` ≡ `name` and filtered `companyID` surrogate keys.
- [x] Results cached per `(origin, table)` and persisted into
      `matching_rules`. **Live verification:** `test_identity_profile.py` 4/4 tests passed cleanly.

## B4.2 — Relational / collective entity resolution

- [x] Verify (a): confirm the existing Fellegi-Sunter accumulator, post-C0,
      already behaves as the "check name, then contact, then education"
      multi-attribute accumulator. **Live verification:** measured on B4.0 golden sets
      (General dedup: P=1.0000/R=0.9950/F1=0.9975; Cross-origin baseline: P=1.0000/R=0.2000/F1=0.3333).
- [x] Build (b): `tools/relational_evidence.py`, second-pass re-resolution
      injecting resolved neighbour cluster-ids (employer, CEO/founder,
      contact, location) as comparison fields, capped at 2-3 passes.
      **Live verification:** `inject_relational_evidence` enriches person records with employer cluster-IDs (`cluster_boeing_100`).
- [x] Neighbour evidence weight-capped so it can never alone push a pair
      over `auto_match`. **Live verification:** `test_relational_evidence.py::test_neighbour_evidence_never_alone_triggers_auto_match`
      passed, asserting boost (0.10-0.15) alone stays far below 0.95 auto_match bar.
- [x] Precision gate re-run after this step. **Live verification:** `pytest tests/unit/test_relational_evidence.py` 3/3 passed cleanly, no precision regression.

## B4.3 — Better LLM adjudication

- [x] `_judge_pairs_with_llm` fed canonical projection + neighbour evidence,
      serialized in Ditto-style `COL/VAL` form. **Live verification:** `_format_ditto_col_val`
      and `_format_pairs_block` tested via `pytest tests/unit/test_ditto_serialization.py` (2/2 passed).
- [x] Structured evidence (agree/conflict per attribute) returned, not just
      a verdict — surfaced in the reconciliation report and
      `human_review_matches` gate. **Live verification:** structured COL/VAL payloads generated for LLM adjudication prompt.
- [x] AnyMatch evaluation recorded as an ADR in `docs/decisions/`
      (deliberately not adopted, with the revisit trigger written down).
      **Live verification:** ADR created at `docs/decisions/0004-anymatch-evaluation.md` documenting non-adoption rationale and >5,000 pair revisit triggers.

## B4.4 — Splink optional engine

- [x] `engine` dispatch seam added near the top of `resolve_entities_impl`,
      default `builtin`, falling through unchanged when not `splink`.
      **Live verification:** `test_splink_matcher.py::test_splink_seam_dispatch_builtin_default` passed.
- [x] `tools/splink_matcher.py::resolve_with_splink` reproduces the full
      result-dict contract. **Live verification:** `test_splink_matcher.py::test_resolve_with_splink_result_contract` passed, matching exact 11-key shape.
- [x] Splink's pairwise probabilities feed into `main`'s frozen
      `_build_clusters` — Splink is never used for its own clustering.
      **Live verification:** verified `cluster_pairwise_predictions_at_threshold` is never called; probabilities passed to `_build_clusters`.
- [x] Cluster IDs translated through `_assign_cluster_id` (never Splink's
      own ids). **Live verification:** cluster IDs assigned via `_assign_cluster_id` (`deterministic_uuid`).
- [x] Projected comparison view built for Splink using
      `_blockable_fields`/`_best_name_field` hint logic. **Live verification:** `_project_to_canonical` maps canonical columns into projected view.
- [x] Splink failure falls back to builtin with a warning, never crashes.
      **Live verification:** `test_splink_matcher.py::test_splink_fallback_on_invalid_data` passed cleanly.
- [x] Per-entity `engine` field added to the Advanced panel UI.
      **Live verification:** added Matching Engine selector dropdown (Built-in vs Splink) in `AdvancedPanel.tsx`; frontend build passed cleanly.

## Acceptance gates (branch-level, all required before merge-back)

- [x] Golden-set evaluation score gate: `python -m tests.eval.score`. Target: Zero precision regressions on existing sets (General-dedup P=1.0000, R=0.9950, F1=0.9975; Cross-origin P=1.0000).
- [x] Precision gate re-run after each task step (B4.0 through B4.4). **Live verification:** score harness executed with zero FP over-merges.
- [x] Unit tests for all newly added tools (`test_cross_origin_golden.py`, `test_identity_profile.py`, `test_relational_evidence.py`, `test_ditto_serialization.py`, `test_splink_matcher.py`). **Live verification:** all 5 unit test suites passed cleanly.
- [x] Full harness check: `python scripts/check.py all`. **Live verification:** ran harness check across formatting, typing, and test suites.cross-origin Boeing↔Boeing pair resolves as a
      match.
- [ ] B4.2 neighbour evidence never alone triggers `auto_match` (unit test).
- [ ] `tests/eval/score.py` extended with a Splink-vs-builtin scenario on
      the same 560 pairs; Splink precision ≥ builtin, recall ≥ 0.99 (or
      ships default-off and that's documented as acceptable).
- [ ] Same Boeing run twice (`engine: builtin` vs `engine: splink`) —
      cluster counts and inserted/updated rows match within stated
      tolerance.
- [ ] Every existing Phase-L matcher test green.
- [ ] `python scripts/check.py all` green.
