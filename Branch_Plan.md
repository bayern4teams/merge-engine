# Branch Plan — `chore/harness-ratchet` (B5)

Copied verbatim from [docs/plans/ONLY_PLAN_TO_IMPLEMENT.md](docs/plans/ONLY_PLAN_TO_IMPLEMENT.md)
(the full programme) at branch-creation time, 2026-08-05, from `main` commit
`e269a5f`. This file is self-contained — you should not need to check out
`main` or another branch to know what to do. If something here conflicts
with the source plan later, the source plan on `main` is the fallback of
last resort, but this file is what you implement against.

**Branch discipline (see `CLAUDE.md` on `main` — copied here for
self-containment):** once you are implementing this branch, `git checkout
chore/harness-ratchet` and stay there. Never `git checkout main` or another
branch mid-task, never merge/rebase/delete a branch, unless explicitly told
to by the user. Only modify files in the Owns column below — a file outside
it is a request back to `main`, not something to edit directly. Update this
branch's own `TODO.md` live, in the same style the root `TODO.md` on `main`
uses (checkbox + live-verification evidence, never checked from the app's
own success message).

---

## Ownership

| Owns (exclusive write) | Never touches |
|---|---|
| `CLAUDE.md`, `AGENTS.md`, `docs/**`, `.claude/**`, `scripts/lint_*.py`, `scripts/check.py`, `.pre-commit-config.yaml`, CI config | all of `src/` |

You may **read** any file in the repo, including all of `src/`. You may only
**modify** the docs/tooling/config files listed above. This is the widest
read surface of any branch (you need to see all of `src/` to write accurate
docs and lint rules about it) and the narrowest write surface — never touch
`src/` even to fix something that looks trivially wrong; flag it instead.

---

## Goal

The second tier of harness engineering — the parts of the agent-harness
literature that go beyond Phase 0's baseline. **Short-lived and rolling**:
merge to `main` early and often (target: weekly, or whenever a discrete
piece is done), because everything here makes the other four branches
faster and delaying it wastes that.

## Resources for this branch

- Claude Code subagents docs: https://docs.claude.com/en/docs/claude-code/sub-agents
- Claude Code skills/slash-commands docs: https://docs.claude.com/en/docs/claude-code/slash-commands
- Addy Osmani, "Agent Harness Engineering" (referenced throughout the full
  plan for the structural-enforcement principle — "telling agents 'don't do
  X' in a markdown file is a suggestion; making X trigger a build failure is
  a rule"): search for the original post if a direct link isn't already
  saved in `docs/decisions/`
- OpenAI "Harness Engineering" writeup (same — the "success is silent,
  failure is verbose" and "small continuous payments rather than periodic
  reckoning" principles both come from this source)

## Task breakdown

**B5.1 — Subagent definitions in `.claude/agents/`** (committed, not local-only, so
every branch and worktree gets them). Per the literature, isolated context per
subagent with the filesystem as the coordination surface:
- `merge-verifier` — drives a full run via `scripts/diagnostics/drive_app.py`, runs
  `scripts/diagnostics/compare_to_golden.py`, asserts `docs/verification/BUDGETS.yaml`,
  reports pass/fail with numbers.
- `perf-profiler` — runs `scripts/diagnostics/profile_resolve.py` and
  produces the component histogram.
- `schema-explorer` — read-only origin/destination catalog queries; the same
  capability B2's swarm agent needs, usable interactively.

**B5.2 — Skills for the repeated workflows.** Each of these was done manually many
times during v1: reset destination to pristine; drive a subject end-to-end;
regenerate a golden; compare to golden; restore after a failed run. `scripts/` has
the code (`reset_destination.py`, `restore_pristine.py`, `drive_app.py`,
`compare_to_golden.py`, all under `scripts/diagnostics/`); wrap them as skills so
they're discoverable. (These scripts themselves are outside your ownership if they
live under `scripts/diagnostics/` rather than `scripts/lint_*.py`/`scripts/check.py`
— wrapping them as a skill is a docs/config-layer task, not an edit to the scripts.)

**B5.3 — Planner / generator / evaluator split.** The literature is explicit that
self-evaluation is biased — agents overestimate their own work.
`scripts/diagnostics/compare_to_golden.py` is already an independent evaluator;
formalize the split so the agent that *implements* a change is never the one that
*grades* it, and both write to the filesystem rather than to each other's context.
This is a documentation/process task (how agents are briefed), not a code change.

**B5.4 — Ralph-loop driver for long runs.** A full Boeing verification is 30–90
minutes across multiple HITL gates and repeatedly exceeded `drive_app.py`'s fixed
iteration budget during v1, forcing manual restarts. Build a driver that re-injects
the original task into a fresh context while the filesystem carries the state. If
this requires changes to `drive_app.py` itself (under `scripts/diagnostics/`, not
`scripts/lint_*.py`/`check.py`), that's outside your Owns column — either scope
this as a new wrapper script that doesn't modify `drive_app.py`, or flag the need
back to `main`.

**B5.5 — Continuous debt mitigation (OpenAI principle #4).** Rather than periodic
refactoring sprints, a scheduled background task that scans for architecture-lint
deviations, stale `file:line` citations in `docs/`, and `mypy-baseline.txt` entries
that could now be removed — and opens small PRs. *"Small continuous payments rather
than periodic reckoning."*

**B5.6 — Build the `NODE_SPECS` / `REPORT_CONTRIBUTORS` registries** *if neither B2
nor B3 has built the specific version each needs by the time you get here*. Part
1.6.D's re-audit found these were part of the original §0.8 design but never
actually built — `graph.py` still wires all 17 nodes by hand and
`build_reconciliation_report` is still one function body. **These registries live
in `src/orchestrator/`, which is outside your Owns column** — if B2/B3 haven't
built what they need and it's blocking them, this is coordination work (raise it,
help design it), not something to implement yourself in `src/`. If a general-purpose
version genuinely needs building and no other branch owns the relevant files at
that moment, that's a `main`-level decision, not a unilateral B5 edit.

**B5.7 — The ratchet, maintained.** Every time any branch hits a real failure, a
corresponding hook, lint rule, or `CLAUDE.md` line is added here — and **every
existing line stays traceable to the failure that caused it.**

## Acceptance gates

- `CLAUDE.md` has every line traceable to a documented failure or hard constraint.
- `scripts/lint_docs.py` reports zero broken links / stale citations across `docs/`.
- The `merge-verifier` subagent produces a verdict on a real run without a human
  reading logs.
- Every new hook is proven to actually block its target (write a deliberately bad
  commit, watch it fail).

---

## Cross-branch rules (verbatim — apply to every branch)

**R1 — One verification command.** `python scripts/check.py all` must pass before
every commit that leaves your machine. Server CI runs the identical command. If it
passes locally and fails in CI, that's a bug in `check.py` — fix it, don't work
around it. **You own `check.py` itself — if it's wrong, you're the branch that
fixes it, for everyone.**

**R2 — Ownership is exclusive.** You may read any file; you may modify only files in
your branch's Owns column. Needing a change outside it is a request to `main`, not
an edit. Ownership violations are the #1 source of six-way conflicts. **This is also
the CLAUDE.md "Branch discipline" rule — `git checkout` your branch and stay there.**
**You own `CLAUDE.md` itself, where this rule is written — keep it accurate as the
branch structure evolves, but do not use that ownership to unilaterally change what
the rule means for the other four branches.**

**R3 — Extend through registries, never through shared function bodies.** New state
key → your own `state/<feature>.py` fragment plus one line in the `MergeState` base
list. New node → one row in `NODE_SPECS` (build it if it doesn't exist yet for your
owned files). New report section → one `REPORT_CONTRIBUTORS` registration (same
caveat). If you find yourself editing a shared function body with no registry
available, build the minimal registry first rather than reaching into the shared
body. (This rule governs `src/` changes other branches make — you enforce it via
`scripts/lint_arch.py`, which you own, but you do not make the `src/` changes
yourself.)

**R4 — Never edit `pyproject.toml` or `requirements.txt`.** Every dependency you need
was pre-declared as an extras group in Phase 0.1. If a new tooling dependency is
needed for a skill/subagent (not a runtime dependency), confirm it belongs in
`requirements-dev.txt` rather than the runtime extras, and note that this file's
ownership should be confirmed with `main` before editing if it's ambiguous.

**R5 — Rebase on `main` immediately after every merge-back**, before writing another
line. Serialized integration only works if everyone re-baselines at once. **As the
rolling/continuous branch, you will do this more often than any other branch —
merge early and often, per your own Goal section above.**

**R6 — New features ship default-off.** Splink, the swarm planner, and any
performance strategy change land behind a config flag whose default reproduces
current behaviour. A branch that cannot be turned off cannot be merged safely.

**R7 — Output contracts are frozen.** Prompt output keys, `resolve_entities_impl`'s
result dict, the six HITL interrupt payloads, and `MergeState`'s existing keys do not
change shape without a `main`-level decision recorded in `docs/decisions/`. **You
own `docs/decisions/` — this is where such decisions get recorded, including ones
you help other branches document.**

**R8 — House style is mechanical.** One file per node; typed `MergeState`;
`logger.debug("ENTER …")` / `"EXIT …"` in every node; tests co-located in
`tests/unit/test_*.py`; golden evals in `tests/eval/`. `scripts/lint_arch.py`
enforces the parts that can be enforced — if it doesn't complain, you're consistent.
**You own `lint_arch.py` — this is your enforcement mechanism.**

**R9 — Live verification per task, not per phase.** A checkbox goes green only after
the thing ran against the real app and DBs and an *independent* check confirmed it —
direct SQL, log inspection, a regenerated report. Never the app's own success
message. If it can't be verified live, leave it unchecked and say why.

**R10 — Report numbers, not adjectives.** "Faster", "better", "more accurate" are not
results. Every claim carries a before/after number measured against
`docs/verification/BASELINE_V1.md`.

**R11 — Every failure becomes a rule.** When you hit a real bug, open a `B5` item for
the hook/lint/doc line that would have prevented it. This is the ratchet; it is the
single highest-leverage habit in the whole plan. **This is literally this branch's
job — B5.7 above.**

---

## Where this fits

Integration order (from the full plan): **B5 → B3 → B1 → B4 → B2**. This
branch (B5) merges **first, and continuously** — pure tooling with zero
`src/` conflict surface, so there's no reason to hold it back. Unlike the
other four branches, you don't wait for a single merge-back event: merge
small, rebase, repeat.

Full programme context, the other four branches' specs, and the honest
scope note about priority: `docs/plans/ONLY_PLAN_TO_IMPLEMENT.md` on `main`.
