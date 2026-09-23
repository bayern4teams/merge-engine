# CLAUDE.md — map, not a style guide

Every line below is traceable to a real past failure or a hard constraint.
Depth lives in `docs/` — start at [docs/INDEX.md](docs/INDEX.md). This file
just tells you where to look and what not to redo.

## Hard constraints

- Work only inside this repo root. Never touch parent folders.
- Python: any 3.11 environment satisfying `pyproject.toml` — venv or conda,
  the hard constraint is the *version*, not the tool. `python
  scripts/bootstrap.py` sets one up from scratch on any platform.
- App: `python -m src.main --mode gradio --port 7860` (flag name is legacy;
  it serves FastAPI + the React SPA — `--mode both` is broken).
- Exactly one destination may be configured at a time.
- A fresh clone has **no UI** until `src/ui/frontend/dist/` is built
  (`npm install && npm run build`, or `python scripts/check.py frontend`) —
  `src/api/main.py` silently degrades to an `{"status": "api-only"}` stub
  without it. `dist/` is gitignored on purpose; never commit it.

## The one verification command

`python scripts/check.py all` — ruff, ruff-format (report-only, see the
script's own docstring), the mypy ratchet, `lint_arch.py`, `lint_docs.py`,
unit tests, frontend typecheck+build. If it passes locally and fails in CI,
that is a bug in `check.py`, not a discrepancy to work around. Server-side CI
is not yet wired up — `git.n4t.com` is unreachable
(`docs/decisions/0003-server-ci-unreachable.md`); `pre-commit` is the only
enforcement that actually runs today.

## Rules that are mechanically enforced, not just written down

A prompt instruction is a suggestion; only code and a lint rule are a rule
(this is why BUG A happened — a prompt telling the LLM "prefer a new column"
was obeyed, and the destination schema grew from 14 columns to 43).

- **Prompt substitution is literal `str.replace()`, never `.format()`**
  (`src/llm/__init__.py`). Prompts contain literal JSON braces; `.format()`
  raises, and a lost placeholder token fails *silently*. Enforced by the
  `no-str-format-on-prompts` pre-commit hook.
- **No module-level mutable state in `src/mcp_server/tools/`.** Two
  concurrent entity resolutions would cross-contaminate each other's stats —
  this is exactly what `_LAST_BLOCKING_STATS` did before it was tracked.
  Return state through the function's result dict instead. Enforced by
  `scripts/lint_arch.py`, ratcheted against `arch-lint-baseline.txt` for the
  pre-existing cases (shrink-only — never hand-edit the baseline to hide a
  new one).
- **Layering**: `src/api` → `src/orchestrator` → `src/mcp_server/tools` →
  `src/mcp_server/connectors` → `src/utils`. No reverse import. `src/llm` may
  be imported from below `api` but must not import upward. Enforced by
  `scripts/lint_arch.py`.
- **No bare `except Exception: pass`.** `_get_workflow_summary` once
  swallowed every failure into `{}`, silently. Use
  `contextlib.suppress(Exception)` if the suppression is truly intentional,
  or handle the error. Enforced by the `no-new-bare-except-pass` pre-commit
  hook (ratchets on block *count*, immune to reformatting churn).
- **No new destination columns, ever.** The destination schema is a fixed
  contract; an unmappable source field is recorded in `unmapped_fields`, never
  invented into a column. `map_fields_impl(allow_schema_changes=False)` is the
  gate; `_apply_schema_changes` is a hard-refusal backstop if it's ever
  bypassed.
- **`sample_databases/` must never be committed** (6 GB of dumps nearly were).
  Enforced by `no-sample-databases-commit`.
- **A bare `*.json`/`*.csv`/`*.tsv` line in `.gitignore` is forbidden** — one
  silently untracked `src/ui/frontend/package.json`, breaking every fresh
  clone's `npm install`; `*.csv` then did it again to
  `tests/eval/pagila_staff.csv`, degrading the eval set to a 2-row stub whose
  3 distinct names hung `check.py eval` (and every CI run) forever. Enforced
  by `no-blanket-data-ignore`.

## Live verification is mandatory, per task

Check a box only after running the thing against the real app/DBs and
confirming with an *independent* check — direct SQL, log inspection, a
regenerated report — never the app's own success message. The matcher once
reported `uncompared_pairs = 0` while having silently dropped 465 pairs; the
app said everything was fine.

## HITL resume trap

`interrupt_before=[...]` means a HITL node's own body never runs on resume —
only the router does. See `docs/architecture/orchestration_and_ui.md` before
touching gate logic.

## Branch discipline (once `feat/*`/`chore/*` branches exist)

Five branches (B1-B5) exist because a strict file-ownership map keeps six
parallel workstreams from producing six-way merge conflicts in `graph.py`,
`resolve_entities.py`, and `pyproject.toml`. That only holds if every agent
obeys it mechanically, not by memory:

- **When told to implement a specific branch, `git checkout` it and stay
  there.** Never `git checkout main` or another branch mid-task, never
  merge/rebase/delete a branch, unless explicitly told to. Coming back to
  `main` to "just fix one thing" is exactly how the ownership map breaks.
- **That branch's own `Branch_Plan.md` and `TODO.md`, at its root, are the
  authoritative spec — read them first, not this file or the root
  `TODO.md`.** They contain that branch's plan section verbatim, so they are
  self-contained by design.
- **Only modify files in that branch's Owns column** (the table in its
  `Branch_Plan.md`). A file outside it is a request back to `main`, not
  something to edit directly — say so instead of reaching across the line.
- **Update that branch's `TODO.md` live** as you verify each item, in the
  same style the root `TODO.md` already uses (checkbox + live-verification
  evidence, never checked from the app's own success message).
- `docs/plans/ONLY_PLAN_TO_IMPLEMENT.md` is the full programme (all five
  branches, cross-branch rules R1-R11, integration order) — read it for
  context on how your branch fits the whole, but your branch's own
  `Branch_Plan.md` is what you implement against.

## Where to look next

`docs/INDEX.md` for the full map. `TODO.md` for live task status (changes far
more often than anything in `docs/`, kept separate on purpose).

<!-- OPENWIKI:START -->

## OpenWiki

See [AGENTS.md](AGENTS.md) for OpenWiki agent instructions.

<!-- OPENWIKI:END -->
