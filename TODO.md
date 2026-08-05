# TODO — `chore/harness-ratchet` (B5)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05. Nothing in this branch
has started yet. **Merges first, and continuously (rolling) — no need to
wait for a single merge-back event; merge small, rebase, repeat.**

## B5.1 — Subagent definitions

- [ ] `.claude/agents/merge-verifier.md` — drives a full run via
      `drive_app.py`, runs `compare_to_golden.py`, asserts `BUDGETS.yaml`.
      **Live verification:** invoke it against a real run, confirm it
      produces a pass/fail verdict with actual numbers, not a template.
- [ ] `.claude/agents/perf-profiler.md` — runs `profile_resolve.py`,
      produces the component histogram. **Live verification:** invoke
      against real data, confirm histogram output matches a manual run of
      the same script.
- [ ] `.claude/agents/schema-explorer.md` — read-only catalog queries.
      **Live verification:** invoke against real ClickHouse/PostgreSQL,
      confirm it correctly reports tables/columns without any write access.

## B5.2 — Skills for repeated workflows

- [ ] Skill wrapping `reset_destination.py`.
- [ ] Skill wrapping `restore_pristine.py`.
- [ ] Skill wrapping `drive_app.py` (drive a subject end-to-end).
- [ ] Skill wrapping `compare_to_golden.py` (regenerate + compare a golden).
      **Live verification (all four):** invoke each skill once and confirm
      it produces the same result as running the underlying script directly.

## B5.3 — Planner / generator / evaluator split

- [ ] Documented process (in `docs/` or `CLAUDE.md`) establishing that the
      implementing agent is never the grading agent; both write to the
      filesystem. **Live verification:** N/A (process documentation) — the
      test is whether it's actually followed in the next branch's real
      verification cycle; note that as an open follow-up if unverifiable now.

## B5.4 — Ralph-loop driver

- [ ] Driver built as a new wrapper (not an edit to `drive_app.py`, which is
      outside this branch's ownership) that re-injects the task into a
      fresh context while the filesystem carries state.
      **Live verification:** run a real long verification cycle (30+
      minutes, multiple HITL gates) and confirm it survives without a
      manual restart, where `drive_app.py` alone previously needed one.

## B5.5 — Continuous debt mitigation

- [ ] Scheduled task/skill scanning for arch-lint deviations, stale
      `file:line` citations, and shrinkable `mypy-baseline.txt` entries.
      **Live verification:** run it against the current tree, confirm it
      correctly finds zero issues (clean tree) and correctly flags a
      deliberately seeded stale citation.

## B5.6 — `NODE_SPECS` / `REPORT_CONTRIBUTORS` (coordination only)

- [ ] Confirmed whether B2 or B3 has already built what they need.
      **Do not build these in `src/` yourself — that's outside your Owns
      column.** If neither branch has and one is blocked, raise it as a
      `main`-level coordination item, not a unilateral edit.

## B5.7 — The ratchet, maintained (ongoing, not a one-time checkbox)

- [ ] Process for turning every real bug hit by any branch into a hook/lint
      rule/`CLAUDE.md` line is documented and demonstrably followed at
      least once. **Live verification:** point to a real example — a bug
      from another branch that produced a new rule here — once one exists.

## Acceptance gates (branch-level, all required before merge-back)

- [ ] `CLAUDE.md`: every line traceable to a documented failure or hard
      constraint (spot-check against `docs/decisions/` and `TODO.md`
      history).
- [ ] `scripts/lint_docs.py` reports zero broken links / stale citations.
- [ ] `merge-verifier` subagent produces a verdict on a real run with no
      human reading raw logs.
- [ ] Every new pre-commit hook proven to block its target: write a
      deliberately bad commit for each, confirm it fails, confirm a clean
      commit passes.
- [ ] `python scripts/check.py all` green.
