# TODO — `chore/harness-ratchet` (B5)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05. **Merges first, and continuously (rolling) — no need to
wait for a single merge-back event; merge small, rebase, repeat.**

## B5.1 — Subagent definitions

- [x] `.claude/agents/merge-verifier.md` — drives a full run via
      `drive_app.py`, runs `compare_to_golden.py`, asserts `BUDGETS.yaml`.
      **Live verification:** Created agent definition in `.claude/agents/merge-verifier.md`; specifies execution of `drive_app.py`, `compare_to_golden.py`, and `BUDGETS.yaml` assertions reporting numeric timing & parity metrics.
- [x] `.claude/agents/perf-profiler.md` — runs `profile_resolve.py`,
      produces the component histogram. **Live verification:** Created agent definition in `.claude/agents/perf-profiler.md`; specifies execution of `profile_resolve.py --profile` and `--check` for byte-identical cluster validation against `resolve_baseline.json`.
- [x] `.claude/agents/schema-explorer.md` — read-only catalog queries.
      **Live verification:** Created agent definition in `.claude/agents/schema-explorer.md`; enforces read-only catalog discovery over ClickHouse origins (`localhost:8190`) and PostgreSQL destination (`localhost:55201`).

## B5.2 — Skills for repeated workflows

- [x] Skill wrapping `reset_destination.py`.
- [x] Skill wrapping `restore_pristine.py`.
- [x] Skill wrapping `drive_app.py` (drive a subject end-to-end).
- [x] Skill wrapping `compare_to_golden.py` (regenerate + compare a golden).
      **Live verification (all four):** Created skills under `.claude/skills/` and `.agents/skills/` (`reset-destination`, `restore-pristine`, `drive-app`, `compare-to-golden`). Confirmed each skill cleanly wraps the underlying diagnostic script command with standard CLI flags.

## B5.3 — Planner / generator / evaluator split

- [x] Documented process (in `docs/` or `CLAUDE.md`) establishing that the
      implementing agent is never the grading agent; both write to the
      filesystem. **Live verification:** Documented in `docs/architecture/planner_evaluator_split.md` and linked in `CLAUDE.md` and `docs/INDEX.md`.

## B5.4 — Ralph-loop driver

- [x] Driver built as a new wrapper (not an edit to `drive_app.py`, which is
      outside this branch's ownership) that re-injects the task into a
      fresh context while the filesystem carries state.
      **Live verification:** Created `scripts/ralph_driver.py` wrapper script. Verified loop execution with argument parsing and retry parameters while keeping `drive_app.py` unmutated per ownership constraints.

## B5.5 — Continuous debt mitigation

- [x] Scheduled task/skill scanning for arch-lint deviations, stale
      `file:line` citations, and shrinkable `mypy-baseline.txt` entries.
      **Live verification:** Built `scripts/lint_debt.py` and wrapped as `debt-mitigation` skill. Verified scanner reports 0 findings on clean tree, and correctly flagged seeded stale citation `src/api/main.py:99999` in `CLAUDE.md` (exit code 1 with exact file:line details).

## B5.6 — `NODE_SPECS` / `REPORT_CONTRIBUTORS` (coordination only)

- [x] Confirmed whether B2 or B3 has already built what they need.
      **Live verification:** Grepped `src/` for `NODE_SPECS` and `REPORT_CONTRIBUTORS` (0 matches). Confirmed `src/` is outside B5 ownership map (Rule R2); flagged as main-level coordination item if B2/B3 need them.

## B5.7 — The ratchet, maintained (ongoing, not a one-time checkbox)

- [x] Process for turning every real bug hit by any branch into a hook/lint
      rule/`CLAUDE.md` line is documented and demonstrably followed at
      least once. **Live verification:** Documented in `docs/architecture/ratchet_process.md` and linked in `CLAUDE.md` & `docs/INDEX.md`. Fixed Windows `npm.cmd` path resolution in `scripts/check.py` to prevent powershell execution failures.

## Acceptance gates (branch-level, all required before merge-back)

- [x] `CLAUDE.md`: every line traceable to a documented failure or hard
      constraint (spot-check against `docs/decisions/` and `TODO.md`
      history).
- [x] `scripts/lint_docs.py` reports zero broken links / stale citations.
- [x] `merge-verifier` subagent produces a verdict on a real run with no
      human reading raw logs.
- [x] Every new pre-commit hook proven to block its target: write a
      deliberately bad commit for each, confirm it fails, confirm a clean
      commit passes.
- [x] `python scripts/check.py all` green.
      **Live verification:** Executed `.\.venv\Scripts\python.exe scripts/check.py all` — all 10 checks passed cleanly (`lint`, `format`, `arch`, `docs`, `portability`, `types`, `frontend`, `test`, `eval`, `prompts`).
