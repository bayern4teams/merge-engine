# CLAUDE.md — map, not a style guide

Every line below is traceable to a real past failure or a hard constraint.
Depth lives in `docs/` — start at [docs/INDEX.md](docs/INDEX.md). This file
just tells you where to look and what not to redo.

## Do this first, before anything else

```bash
python scripts/bootstrap.py          # venv, deps, frontend, git hooks, graphs
```

`git clone` gives you **none** of the tooling below. `.git/hooks/` is never
cloned — git excludes hooks so that cloning a repo cannot execute its code —
and there is no post-clone hook to hang anything on. This repo was found in
exactly that state: `.git/hooks/` empty, so all 13 configured hooks, including
the four called "mechanically enforced" further down, had never run once.

`bootstrap.py` is the one command that fixes that. It installs the pre-commit
**and** post-commit hooks and builds all three graphs (~8s, no LLM, no key).

**Every session, before reading any code, check the graphs exist:**

```bash
ls graphify-out/graph.json graft/ >/dev/null 2>&1 || python scripts/refresh_graphs.py
```

If they are missing, build them — do not shrug and start grepping. A missing
graph is an 8-second fix; skipping it means reading the whole repo the slow way
and leaving the next agent to do the same. The graphs are gitignored local
caches, so "not there" is the normal state of a fresh clone, not a bug.

## Code lookup order — stop at the first tier that answers

**graphify, graft and codebase-memory-mcp are this repo's primary tools for
understanding code. Reading source is the fallback, not the default.**

Four tiers. **If a tier answers the question, stop.** Do not re-derive from a
lower tier a fact a higher one already gave you; reading source to confirm
what the graph just told you is the slow path these tools exist to replace.

1. **graphify** — `graphify query "<question>"` (BFS over the graph),
   `graphify path "A" "B"` (shortest path between two nodes),
   `graphify explain "X"`, `graphify affected "X"` (reverse traversal: what
   breaks if X changes), `graphify god-nodes` (architectural hubs).
   Graph lives at `graphify-out/graph.json`.
2. **graft** — `graft ask "<query>"` returns ranked nodes with exact
   `file:line`; `graft skeleton <file>` is the cheapest view of one file's API
   surface. Per-file cards in `graft/` (greppable — see `.ignore`).
3. **codebase-memory-mcp** — `search_graph`, `trace_path`, `get_code_snippet`,
   `query_graph`, `get_architecture`. Call `check_index_coverage` on any file
   you rely on; clean coverage is not proof of completeness.
4. **Raw source** — grep and read. Last resort, and the only tier that is
   always current, so use it to confirm anything from 1-3 that looks wrong.

Tiers 1-3 are caches. They can be stale or plain mistaken, and a confident
wrong answer from a graph is worse than no answer: when a claim from any tier
contradicts what the code does, the code wins and the graph needs rebuilding.

**Never the LLM paths** — `graphify extract --mode deep`, `graphify label`,
`graphify cluster-only` without `--no-label`, `graft build --deep`. They spend
model calls for what tiers 1-3 already answer for free.

The three graphs are gitignored local caches, never committed:
`scripts/bootstrap.py` builds them on a fresh clone, and the `refresh-graphs`
post-commit hook rebuilds them in the background after every commit
(`scripts/refresh_graphs.py`, ~8s, no API key, log in `.tmp/`). Missing or
stale? Run `python scripts/refresh_graphs.py` — do not quietly drop to tier 4
and leave it stale for whoever comes next.

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

`python scripts/check.py all` — 11 stages, in this order: `lint`, `format`
(ruff + ruff-format, report-only — see the script's own docstring), `arch`
(`lint_arch.py`), `docs` (`lint_docs.py`), `portability`, `guards` (the custom
pre-commit hooks below), `types` (the mypy ratchet), `frontend`
(typecheck+build), `test` (unit **and** integration), `eval`, `prompts`. If it
passes locally and fails in CI, that is a bug in `check.py`, not a discrepancy
to work around.

CI runs exactly this command on GitHub Actions
(`.github/workflows/ci.yml`), green since 2026-09-22. ADR
`docs/decisions/0003-server-ci-unreachable.md` records why the original
`git.n4t.com` server-side CI was abandoned; GitHub is the replacement, not a
gap. Do not assume `pre-commit` is enforcing anything on its own: `.git/hooks/`
is never cloned, so until someone runs `python scripts/bootstrap.py` it is
empty and *no* hook runs. This repo sat in exactly that state — which is why
`check.py guards` exists, so CI enforces the same rules regardless.

## Rules that are mechanically enforced, not just written down

A prompt instruction is a suggestion; only code and a lint rule are a rule
(this is why BUG A happened — a prompt telling the LLM "prefer a new column"
was obeyed, and the destination schema grew from 14 columns to 43).

The rules below run on every commit *and* in CI — `check.py guards` invokes
them, so `--no-verify` does not slip past them.

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

**A terminal status of `completed` is not evidence of anything.** Measured
2026-09-24 on the enrichment corpus: one run finished `completed` having
discarded 9,556,382 candidate pairs unexamined, left 78 of 99 `company.name`
values NULL, and produced zero field mappings for 8 of 16 entities. No stage
can fail a run on its *results* — only on an exception — so the only way to
know a run was good is to score it against something independent. Write the
scorer before the run; a scorer written afterwards gets fitted to the result.

## HITL resume trap

`interrupt_before=[...]` means a HITL node's own body never runs on resume —
only the router does. See `docs/architecture/orchestration_and_ui.md` before
touching gate logic.

## Branch discipline

`fix-v3` was merged into `main` on 2026-09-23; the five `feat/*`/`chore/*`
branches (B1-B5) are still open. They exist because a strict file-ownership map keeps six
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

## Ponytail — use it, and use all of it

Ponytail (<https://github.com/dietrichgebert/ponytail>) is installed as a
plugin and is active by default. It is the standing brief for *how much* to
build: stop at the first rung that works, delete before adding, and never
scaffold for a "later" that can scaffold for itself. This repo is the
cautionary tale — 750k lines of generated JSON were tracked in git, a
connector nobody could reach shipped with 549 lines of tests, and four rules
were declared "mechanically enforced" while running nowhere.

All six commands are available; they are not interchangeable:

| Command | Use it when |
|---|---|
| `/ponytail` | Set or restate the intensity (`lite`/`full`/`ultra`). Default is `full`. |
| `/ponytail-review` | Reviewing a diff or PR for over-building before it lands. |
| `/ponytail-audit` | Hunting dead code, unused deps, duplicated helpers across the tree. |
| `/ponytail-debt` | Cataloguing what was deliberately deferred, and what triggers paying it. |
| `/ponytail-gain` | Measuring what a simplification actually bought — lines, deps, runtime. |
| `/ponytail-help` | When unsure which of the above applies. |

Pair `/ponytail-audit` with the graph tiers above: the graph finds what is
unreferenced, the audit decides whether it should exist at all. Neither
conclusion is safe alone — `affected "X"` returning nothing means the *graph*
sees no caller, not that there is none. Confirm with grep before deleting.

## Part 1 implementation (cloud, multi-agent)

If told to implement the technical report's Part 1: read
[docs/plans/PART1_IMPLEMENTATION_PLAN.md](docs/plans/PART1_IMPLEMENTATION_PLAN.md) and
`docs/plans/part1/HANDOFF.md` first. Branch `feat/part1-fixes` only; the plan, not this
file, defines roles, phases and the approval protocol.

## Where to look next

`docs/INDEX.md` for the full map. `TODO.md` for live task status (changes far
more often than anything in `docs/`, kept separate on purpose).

<!-- OPENWIKI:START -->

## OpenWiki

See [AGENTS.md](AGENTS.md) for OpenWiki agent instructions.

<!-- OPENWIKI:END -->
