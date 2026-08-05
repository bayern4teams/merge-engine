# docs/ — navigation index

Phase 0 Sec 0.4 restructured `docs/` from a flat folder into five purpose-built
directories. This file is the map; `scripts/lint_docs.py` (wired into
`scripts/check.py docs`) mechanically checks that every link below, and every
`path/to/file.py:NNN` citation anywhere under `docs/` and in `CLAUDE.md`,
still resolves — so this index cannot silently rot.

## architecture/ — how the system works today

Split from the old root `TECHNICAL_README.md`, by subsystem, in reading order:

1. [overview.md](architecture/overview.md) — file map, mental model, table of contents for the rest
2. [state_and_graph.md](architecture/state_and_graph.md) — `MergeState`, the LangGraph wiring, the error taxonomy
3. [data_pipeline.md](architecture/data_pipeline.md) — DuckDB spool, connectors/keyset pagination, entity resolution, unification, field mapping, loading
4. [llm_and_profiles.md](architecture/llm_and_profiles.md) — the LLM client/prompts/cache/cost ledger, run profiles
5. [orchestration_and_ui.md](architecture/orchestration_and_ui.md) — WorkflowManager, the FastAPI + React surface, observability
6. [testing_and_decisions.md](architecture/testing_and_decisions.md) — testing strategy, index of design decisions
7. [known_problems.md](architecture/known_problems.md) — known problems, future risks, ideas worth building next

## decisions/ — ADRs, one per locked decision

- [0003-server-ci-unreachable.md](decisions/0003-server-ci-unreachable.md) — why server-side CI is undone (`git.n4t.com` unreachable) and the revisit trigger

## plans/ — the plans this repo is executing against

- [ONLY_PLAN_TO_IMPLEMENT.md](plans/ONLY_PLAN_TO_IMPLEMENT.md) — the
  harness-foundation plan: Phase 0, Part 1.5, and Part 1.6 (all three ✅
  complete, live-verified — status banner at the top of the file), then the
  `BRANCH CREATION GATE` (passed 2026-08-05), then the full spec for the five
  parallel branches (B1-B5) plus the cross-branch rules (R1-R11) and
  integration order. This is the *design* record — why things are shaped the
  way they are. `TODO.md` is the *execution* record — what actually ran,
  against what data, with what result; when the two disagree, `TODO.md`
  wins. Each branch's own `Branch_Plan.md` (at that branch's root, not on
  `main`) contains its section of this file copied verbatim, so a branch
  never needs to check out `main` to read its own spec.
- [PHASE_Q_PLAN.md](plans/PHASE_Q_PLAN.md) — Splink/GX/lineage research, superseded in scheduling by the plan above but still the source of truth for *why* those features are shaped the way they are

## verification/ — recorded proof, not prose claims

- [BASELINE_V1.md](verification/BASELINE_V1.md) — the authoritative Phase 0 +
  Part 1.5 exit baseline every later branch measures "no regression"
  against: full test suite, matcher precision/recall, prompt-contract
  harness, and three successive live Boeing runs (pre-fix, post-A/B.1/C,
  post-D.5-D.7+B.3+C.6)
- [BUDGETS.yaml](verification/BUDGETS.yaml) — the machine-checkable
  performance contract `drive_app.py --assert-budgets` asserts against
- [SCORING_PROFILE.md](verification/SCORING_PROFILE.md) — Part 1.5 §A's
  cProfile breakdown of `resolve_entities`'s scoring hot path (what A.1-A.6
  actually fixed)
- [PROCESS_POOL_PROFILE.md](verification/PROCESS_POOL_PROFILE.md) — Part
  1.5 B.3's process-pool dispatch measurements (pickling cost, per-entity
  wall time under concurrent dispatch)
- [CLUSTERING_PROFILE.md](verification/CLUSTERING_PROFILE.md) — live D.1 profiling: clustering is not the throughput bottleneck
- [BASELINE_PRE_CHANGE.md](verification/BASELINE_PRE_CHANGE.md) — state of the app before the Phase 0 correctness fixes
- `boeing_diff.json`, `resolve_baseline.json` — raw diff/baseline output backing the above

## diagnostics/ — what is measurably wrong and by how much

- [BOEING_APP_VS_MANUAL_DIAGNOSIS.md](diagnostics/BOEING_APP_VS_MANUAL_DIAGNOSIS.md), [AIRBUS_...](diagnostics/AIRBUS_APP_VS_MANUAL_DIAGNOSIS.md), [LOCKHEED_...](diagnostics/LOCKHEED_APP_VS_MANUAL_DIAGNOSIS.md) — app output vs. the hand-merge golden, per subject
- [BOEING_SPEED_REPORT_2026-08-04.md](diagnostics/BOEING_SPEED_REPORT_2026-08-04.md) — the three live-measured Boeing run timings that Part 1.5 was scoped from, plus the corrected C0a pool-growth attribution (see its own Context section)
- [PERFORMANCE_BOTTLENECKS.md](diagnostics/PERFORMANCE_BOTTLENECKS.md) — the original measured throughput findings (partly superseded — see `CLUSTERING_PROFILE.md` above)

## Root-level docs not moved here

- `CLAUDE.md` / `AGENTS.md` — the map into this index, read on every session; kept at repo root because that's where every agent harness looks for it first.
- `README.md` — human-facing project overview and setup instructions.
- `User_Guide.md` — end-user walkthrough of the UI.
- `TODO.md` — the live, continuously-updated task list. Not moved into `docs/plans/` because it changes far more often than anything else here and mixing that churn into a directory of stable reference docs would bury the signal.
