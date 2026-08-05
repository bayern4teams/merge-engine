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

- [ ] Sample candidate company + person pairs across origin1/origin2 for
      Boeing, Airbus, Lockheed. **Live verification:** pairs drawn from the
      real ClickHouse origins, not synthetic data.
- [ ] Label pairs by hand against the three hand-merge baselines
      (`docs/diagnostics/*_APP_VS_MANUAL_DIAGNOSIS.md` + prior manual-merge
      records). **Live verification:** spot-check a sample of labels against
      the golden hand-merge tables directly.
- [ ] New golden set added to `tests/eval/` alongside (not replacing) the
      existing 560 Pagila pairs. **Live verification:** `pytest
      tests/eval/` runs both sets without interference.

## B4.1 — Identity-key discovery

- [ ] `tools/identity_profile.py`: per-column profiling (within-origin
      uniqueness, cross-origin overlap, null rate, cardinality) via
      `desbordante` UCC/FD discovery. **Live verification:** run against
      real origin1/origin2 Boeing schemas, confirm `website`/`ticker`-shaped
      columns correctly classify as high-uniqueness/high-overlap and
      `companyID`-shaped columns as high-uniqueness/zero-overlap.
- [ ] LLM classification step (role + cross-origin alignment) grounded in
      the measured profile. **Live verification:** confirm
      `origin1.entityName ≡ origin2.name` and `origin1.fullName ≡
      legal_name` are correctly proposed on real data.
- [ ] Results cached per `(origin, table)` and persisted into
      `matching_rules`. **Live verification:** re-run twice, confirm second
      run reuses the cache (no duplicate LLM calls — check cost ledger).

## B4.2 — Relational / collective entity resolution

- [ ] Verify (a): confirm the existing Fellegi-Sunter accumulator, post-C0,
      already behaves as the "check name, then contact, then education"
      multi-attribute accumulator. **Live verification:** measure on real
      data before building anything new — report the actual precision/recall
      this alone achieves on the B4.0 golden set.
- [ ] Build (b): `tools/relational_evidence.py`, second-pass re-resolution
      injecting resolved neighbour cluster-ids (employer, CEO/founder,
      contact, location) as comparison fields, capped at 2-3 passes.
      **Live verification:** on real Boeing data, confirm a person's
      resolved employer-cluster overlap measurably shifts pair scores.
- [ ] Neighbour evidence weight-capped so it can never alone push a pair
      over `auto_match`. **Live verification:** unit test asserting this
      directly, not by inspection.
- [ ] Precision gate re-run after this step. **Live verification:**
      `python -m tests.eval.score` on both golden sets, no regression.

## B4.3 — Better LLM adjudication

- [ ] `_judge_pairs_with_llm` fed canonical projection + neighbour evidence,
      serialized in Ditto-style `COL/VAL` form. **Live verification:**
      inspect real LLM adjudication payloads for a borderline Boeing pair,
      confirm the full evidence (not a 4-field intersection) reaches the
      model.
- [ ] Structured evidence (agree/conflict per attribute) returned, not just
      a verdict — surfaced in the reconciliation report and
      `human_review_matches` gate. **Live verification:** trigger the gate
      on a real run, confirm structured evidence renders.
- [ ] AnyMatch evaluation recorded as an ADR in `docs/decisions/`
      (deliberately not adopted, with the revisit trigger written down).
      **Live verification:** ADR file exists and is linked from
      `docs/INDEX.md` (request the INDEX.md edit from B5/`main` if that
      registry isn't yours to touch — confirm ownership before editing it
      yourself).

## B4.4 — Splink optional engine

- [ ] `engine` dispatch seam added near the top of `resolve_entities_impl`,
      default `builtin`, falling through unchanged when not `splink`.
      **Live verification:** confirm a run with no `matching_rules.engine`
      set is byte-identical to pre-branch `main` behaviour.
- [ ] `tools/splink_matcher.py::resolve_with_splink` reproduces the full
      result-dict contract. **Live verification:** run on real data, diff
      the returned dict's keys/shape against the builtin's.
- [ ] Splink's pairwise probabilities feed into `main`'s frozen
      `_build_clusters` — Splink is never used for its own clustering.
      **Live verification:** code review + test confirming
      `cluster_pairwise_predictions_at_threshold` is never called.
- [ ] Cluster IDs translated through `_assign_cluster_id` (never Splink's
      own ids). **Live verification:** run the same input twice under
      `engine: splink`, confirm identical cluster ids both times (no
      duplication of destination rows).
- [ ] Projected comparison view built for Splink using
      `_blockable_fields`/`_best_name_field` hint logic.
- [ ] Splink failure falls back to builtin with a warning, never crashes.
      **Live verification:** force a Splink import/runtime failure, confirm
      graceful fallback.
- [ ] Per-entity `engine` field added to the Advanced panel UI.
      **Live verification:** select `splink` in the real browser UI,
      confirm it reaches `matching_rules` in the started run.

## Acceptance gates (branch-level, all required before merge-back)

- [ ] `python -m tests.eval.score`: no precision regression on the original
      560 pairs at every step (B4.1/B4.2/B4.3/B4.4 each gated separately).
- [ ] Cross-origin golden set (B4.0): precision/recall/F1 reported before
      and after each step; cross-origin Boeing↔Boeing pair resolves as a
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
