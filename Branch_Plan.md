# Branch Plan — `feat/dspy-prompt-layer` (B1)

Copied verbatim from [docs/plans/ONLY_PLAN_TO_IMPLEMENT.md](docs/plans/ONLY_PLAN_TO_IMPLEMENT.md)
(the full programme) at branch-creation time, 2026-08-05, from `main` commit
`e269a5f`. This file is self-contained — you should not need to check out
`main` or another branch to know what to do. If something here conflicts
with the source plan later, the source plan on `main` is the fallback of
last resort, but this file is what you implement against.

**Branch discipline (see `CLAUDE.md` on `main` — copied here for
self-containment):** once you are implementing this branch, `git checkout
feat/dspy-prompt-layer` and stay there. Never `git checkout main` or another
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
| `src/llm/**`, `src/llm/prompts/**`, `tests/prompts/**` | `src/orchestrator/**`, `src/mcp_server/**` (call sites change only via the adapter's stable API) |

You may **read** any file in the repo. You may only **modify** files under
`src/llm/`, `src/llm/prompts/`, and `tests/prompts/`. If a task seems to
require touching `src/orchestrator/` or `src/mcp_server/`, stop — that is a
request back to `main`, not something to do here. The one place other code
may need to change is a call site importing `LLMClient` or the DSPy adapter,
and that must happen through the adapter's existing stable API, not by
reaching into orchestrator/mcp_server internals.

---

## Goal

Replace two hand-tuned prompt stages with DSPy programs that can be
*optimized against a metric* instead of hand-edited, without losing any of the
production plumbing wrapped around `LLMClient`.

## Resources for this branch

- DSPy docs: https://dspy.ai/
- DSPy `BaseLM` reference (for the adapter): https://dspy.ai/api/models/LM/
- `BootstrapFewShot` / `MIPROv2` optimizer docs: https://dspy.ai/learn/optimization/optimizers/
- DSPy GitHub (issues/examples are often more current than the docs site for a
  fast-moving library): https://github.com/stanfordnlp/dspy

## Task breakdown

**B1.1 — The `dspy.LM` adapter (do this before touching any stage).** DSPy's default
LM knows nothing about this project's four load-bearing behaviours. Subclass
`dspy.BaseLM` (verify the exact base class against the installed DSPy version) so
`forward()` delegates to the existing `LLMClient` and preserves **all four**:
- the quota-fallback chain (`_advance_model` / `_is_quota_error`, `client.py:102-118`,
  walking `config.llm.fallback_models`' nine models);
- `SEMANTIC_CACHE` (`cost.py:219`) — and make caching happen *after* a
  successful parse, not before (the Phase 0 §0.8 fix already landed on
  `main` — confirm it's still true rather than re-introducing the bug);
- `COST_LEDGER` (`cost.py:220`), partitioned by `run_id`;
- `trace_config()` Langfuse callback injection (`client.py:206`).

Unit-test the adapter against the existing `TestLLMClientModelFallback` suite shape.
Note: those tests need `"cache_enabled": False` because `SEMANTIC_CACHE` is a
process-wide global that cross-pollutes tests.

**B1.2 — Signatures for the two pilot stages.**
- `request_parsing` — outputs `entity_types`, `explicit_entity_types`, `subject`,
  `scope_type`. Golden: 14 cases.
- `field_mapping` — outputs a **bare, dynamically-keyed object** (`{source_field:
  {action, ...}}`), which does not fit a fixed DSPy signature. Reshape to
  `list[FieldDecision]` inside the module and convert back at the boundary, so
  `map_fields.py:492-495`'s filtering contract is unchanged. Golden: 8 cases.

**Both output contracts must stay byte-identical in key names.** The 18-value entity
vocabulary in `request_parsing.system.txt:3-22` is mirrored by hand in
`src/orchestrator/utils/entity_types.py` and guarded by
`tests/unit/test_entity_aliases.py` — a DSPy rewrite must keep them in sync or that
test fails, which is exactly what it's there for. (You may not edit
`entity_types.py` — it's outside your ownership — so if the vocabulary needs
to change, that's a `main`-level request, not something to do here.)

**B1.3 — Metrics from the existing scorer.** `tests/prompts/harness.py:172-282`
already implements per-stage scoring (set equality on `entity_types`; per-field
`action` + `dest_column` equality for mappings). Lift those into DSPy metric
functions. **Do not invent new metrics** — the whole point is comparability with
`scorecard_latest.txt`.

**B1.4 — Optimize, honestly.** 14 and 8 cases support `BootstrapFewShot`
(documented as viable from ~10 examples). They do **not** support `MIPROv2`, which
wants 200+ examples and 40+ trials — say so rather than running it on thin data.
Path: `LabeledFewShot` baseline → `BootstrapFewShot` → grow the golden sets → then
reconsider `MIPROv2`/`GEPA`. Save compiled programs with `.save()` (plain-text JSON)
and **check the artifacts into the repo** — a compiled program is a build output the
run depends on, and an unreproducible optimizer run is worse than a hand-written
prompt.

**B1.5 — Fix the harness drift found during planning.**
`tests/prompts/harness.py:100-166` (`_build_prompt`) hand-mirrors production's
`.replace()` chains and has **already drifted**: it omits the appended user-request
suffix that `select_entity_tables.py:203-204` really sends, and it applies no retry
hints. DSPy removes the need for that mirror entirely for converted stages — for the
four not yet converted, make the harness import the production prompt builder rather
than reimplementing it. (`select_entity_tables.py` is outside your ownership —
this task only changes how the harness *calls* it, not the file itself.)

**B1.6 — Keep every heuristic floor.** All six stages degrade to a non-LLM fallback
(heuristic table selection, heuristic scope plan, `_compute_heuristic_confidence`,
`_heuristic_match`). Those floors are what keep a run alive when the LLM is down and
they are the natural DSPy baseline. Do not remove them. (These floors live outside
`src/llm/` — you are not modifying them, just relying on their contract staying
stable, which R7 already guarantees.)

## Acceptance gates

- `python -m tests.prompts.harness --live` scores **≥ the regenerated
  `docs/verification/BASELINE_V1.md` scorecard** on both converted stages, with the other four
  unchanged.
- The adapter test suite proves quota fallback, cache, ledger and tracing all still
  fire — each asserted independently.
- A full Boeing run completes with parity ≥ 12.0%.
- The four unconverted stages are provably untouched (byte-identical `.txt` files).

---

## Cross-branch rules (verbatim — apply to every branch)

**R1 — One verification command.** `python scripts/check.py all` must pass before
every commit that leaves your machine. Server CI runs the identical command. If it
passes locally and fails in CI, that's a bug in `check.py` — fix it, don't work
around it.

**R2 — Ownership is exclusive.** You may read any file; you may modify only files in
your branch's Owns column. Needing a change outside it is a request to `main`, not
an edit. Ownership violations are the #1 source of six-way conflicts. **This is also
the CLAUDE.md "Branch discipline" rule — `git checkout` your branch and stay there.**

**R3 — Extend through registries, never through shared function bodies.** New state
key → your own `state/<feature>.py` fragment plus one line in the `MergeState` base
list. New node → one row in `NODE_SPECS` (build it if it doesn't exist yet for your
owned files). New report section → one `REPORT_CONTRIBUTORS` registration (same
caveat). If you find yourself editing a shared function body with no registry
available, build the minimal registry first rather than reaching into the shared
body. (Note: as of Part 1.6.D's re-audit, `NODE_SPECS` and `REPORT_CONTRIBUTORS`
were never actually built — this branch doesn't need either, but B2/B3/B5 do; don't
assume they exist if you ever read code that references them.)

**R4 — Never edit `pyproject.toml` or `requirements.txt`.** Every dependency you need
was pre-declared as an extras group in Phase 0.1 (`dspy`, `splink`, `dq`, `lineage`,
`swarm`, `perf`). If you need one that wasn't, that's a request to `main`.

**R5 — Rebase on `main` immediately after every merge-back**, before writing another
line. Serialized integration only works if everyone re-baselines at once.

**R6 — New features ship default-off.** Splink, the swarm planner, and any
performance strategy change land behind a config flag whose default reproduces
current behaviour. A branch that cannot be turned off cannot be merged safely.

**R7 — Output contracts are frozen.** Prompt output keys, `resolve_entities_impl`'s
result dict, the six HITL interrupt payloads, and `MergeState`'s existing keys do not
change shape without a `main`-level decision recorded in `docs/decisions/`. The
frontend and `WorkflowManager` both depend on these and neither is your file.

**R8 — House style is mechanical.** One file per node; typed `MergeState`;
`logger.debug("ENTER …")` / `"EXIT …"` in every node; tests co-located in
`tests/unit/test_*.py`; golden evals in `tests/eval/`. `scripts/lint_arch.py`
enforces the parts that can be enforced — if it doesn't complain, you're consistent.

**R9 — Live verification per task, not per phase.** A checkbox goes green only after
the thing ran against the real app and DBs and an *independent* check confirmed it —
direct SQL, log inspection, a regenerated report. Never the app's own success
message. If it can't be verified live, leave it unchecked and say why.

**R10 — Report numbers, not adjectives.** "Faster", "better", "more accurate" are not
results. Every claim carries a before/after number measured against
`docs/verification/BASELINE_V1.md`.

**R11 — Every failure becomes a rule.** When you hit a real bug, open a `B5` item for
the hook/lint/doc line that would have prevented it. This is the ratchet; it is the
single highest-leverage habit in the whole plan.

---

## Where this fits

Integration order (from the full plan): **B5 → B3 → B1 → B4 → B2**. This
branch (B1) merges third — after B5 (tooling, near-zero conflict) and B3
(new files, near-zero conflict), before B4 and B2 (which touch more of the
shared surface). Rebase on `main` immediately after B5 and B3 land, and
again after you merge, per R5.

Full programme context, the other four branches' specs, and the honest
scope note about priority: `docs/plans/ONLY_PLAN_TO_IMPLEMENT.md` on `main`.
