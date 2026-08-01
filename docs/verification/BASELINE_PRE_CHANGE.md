# Pre-change baseline (2026-07-30)

Captured before any of remediations 1-6 (see `merge-engine-remediation-1-to-6` in
project memory and the plan at `groovy-booping-finch.md`) touch a single line of
`src/`. This is what "no net loss" and "no precision regression" in later phases are
measured against.

## Environment note (affects every command below)

`pytest` in this `na180` env auto-loads the `pytest-playwright` plugin, which
conflicts with `pytest-asyncio` 1.4.0's own event-loop runner: every async test fails
with `RuntimeError: Runner.run() cannot be called from a running event loop`
(`pytest_playwright/pytest_playwright.py:112` wraps `runtest` around
`pytest_asyncio/plugin.py`'s own runner). This is a pre-existing environment issue,
unrelated to the merge-engine's own code or to this remediation effort — confirmed by
re-running a small async-test subset with the plugin disabled and getting a clean
pass. **Every `pytest` invocation in this effort must pass `-p no:playwright`.**
Also pass `--basetemp` to a scratch dir — the default `%TEMP%\pytest-of-<user>`
symlink cleanup throws `PermissionError: [WinError 5] Access is denied` on Windows
during teardown (cosmetic, doesn't affect results, but pollutes the exit code/output).

Reference command:
```bash
python -m pytest -q -p no:playwright --basetemp=<scratch-dir>
```

## 1. Full test suite

```
13 failed, 1083 passed, 83 skipped, 1 warning, 20 errors in 156.06s
```

(README's checked-in claim of "908 passed / 83 skipped, 0 failed" is stale — the
repo has grown since; 1083 passing is the real current baseline.)

**Pre-existing failures/errors (not caused by, and out of scope for, this
remediation — fixes 1-6 don't touch any of these areas):**
- `tests/integration/test_config_resolution.py` (4 failed) — env-var config
  resolution edge cases.
- `tests/integration/test_edge_cases.py` (2 failed) — MongoDB cursor pagination /
  nested-id flattening.
- `tests/integration/test_human_in_the_loop.py` (2 failed) — error-handler retry
  edge cases.
- `tests/integration/test_load_data.py` (3 failed) — connector-failure integration
  cases (**note**: this file lives in the same area Phase 4 modifies — re-check
  after Phase 4 in case the fix incidentally touches these).
- `tests/integration/test_workflow_manager.py` (2 failed) — resume/no-graph-stream
  edge cases.
- `tests/integration/test_boeing_full_pipeline.py` (4 errors) — needs live
  ClickHouse/Postgres, not available at collection time in this run.
- `tests/integration/test_full_pipeline_manual.py` (14 errors) — same, needs live
  services.
- `tests/test_gradio_playwright.py` (1 error) — Gradio was fully removed in the
  React migration (see `merge-engine-frontend-react-migration` in project memory);
  this test file is dead and expected to error.

**Gate for Phases 1-6:** the full suite must show **no net loss** vs 1083 passing —
new failures introduced by my changes are not acceptable; these 33 pre-existing
failures/errors are not something this plan fixes, but must not silently grow either.

## 2. Matcher precision/recall (`python -m tests.eval.score`)

```
Golden set: 560 labelled pairs (200 same entity, 360 different)

heuristic only:              precision 1.0000  recall 0.8850  F1 0.9390
Fellegi-Sunter + term freq:   precision 1.0000  recall 1.0000  F1 1.0000
  TP=200 FP=0 TN=100 FN=0, 46.4% routed to review

clustering (400 distinct people)        -> 400 clusters [OK], 0 folded
clustering (100 people x 2 systems)     -> 100 clusters [OK], 100 folded correctly
blocking recall                          -> 1.0000 (480/19900 candidate pairs, 2.4%)
clustering (300 people, no unique id)    -> 300 clusters [OK], 0 folded
```

**Hard gate for Phase 3** (the scoped-merge matcher fix): precision must stay
**1.0000** — recall/F1 may improve (that's the point of the deterministic-id-field
fix), but zero false positives is non-negotiable per
`merge-engine-matching-precision` in project memory ("pairwise precision alone is
not evidence of safety" — still re-run the full harness, not just spot checks).

## 3. Prompt contract harness (`python -m tests.prompts.harness`, offline)

```
Contract checks OK for: request_parsing, dependency_analysis, entity_resolution, field_mapping
```

Only 4 of the 6 stages are covered (`scope_planning` and `entity_table_selection`
have no golden files and aren't in `_STAGES`/`_REQUIRED_PLACEHOLDERS` — see
`tests/prompts/harness.py:36-45`). Phase 2.1 closes this gap before any prompt gets
rewritten, specifically so a dropped `{table_catalog}`/`{subject}` placeholder in
those two stages can't ship silently (prompt substitution is literal `str.replace()` —
a missing placeholder is a silent no-op, not an error).

The last **live** scorecard on file is `tests/prompts/scorecard_latest.txt`
(model `openai/qwen3.6-27b`, all 25 cases across the 4 covered stages passing) — this
is the reference for the Phase 2.3 gate ("`--live` must score >= this").

## Golden baselines (row-level, for Phase 7 diffing)

Regenerated 2026-07-30 into `global_warehouse_golden` (PostgreSQL :55201, schemas
`boeing`/`airbus`/`lockheed`), confirmed deterministic (two fresh runs each, byte-for-byte
identical table counts):

| Subject | Total rows | Notes |
|---|---|---|
| Boeing | **3,427** | Corrected from the originally recorded 3,185 — the original script had a hash-randomization non-determinism bug (see `merge-engine-boeing-manual-merge` in project memory); fixed and now reproducible. |
| Airbus | **2,649** | Matches originally recorded baseline exactly. |
| Lockheed | **555** | Matches originally recorded baseline exactly. |

These are the row-level references `scripts/diagnostics/compare_to_golden.py` diffs
Phase 7's app runs against.
