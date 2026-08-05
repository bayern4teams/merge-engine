# Branch Plan — `feat/quality-lineage` (B3)

Copied verbatim from [docs/plans/ONLY_PLAN_TO_IMPLEMENT.md](docs/plans/ONLY_PLAN_TO_IMPLEMENT.md)
(the full programme) at branch-creation time, 2026-08-05, from `main` commit
`e269a5f`. This file is self-contained — you should not need to check out
`main` or another branch to know what to do. If something here conflicts
with the source plan later, the source plan on `main` is the fallback of
last resort, but this file is what you implement against.

**Branch discipline (see `CLAUDE.md` on `main` — copied here for
self-containment):** once you are implementing this branch, `git checkout
feat/quality-lineage` and stay there. Never `git checkout main` or another
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
| new `nodes/validate_data.py`, `tools/data_quality.py`, `orchestrator/utils/lineage.py`, `state/quality.py`, `state/lineage.py`, `Gates.tsx` quality/lineage panels | `tools/resolve_entities.py`, `src/llm/**` |

You may **read** any file in the repo. You may only **modify**/**create**
the files named above. `tools/resolve_entities.py` and everything under
`src/llm/` belong to `main`/B4 and B1 respectively — do not touch them even
if a quality check would be convenient to add there.

---

## Goal

Phase Q's Q.2 and Q.3, together in one branch because both extend the
reconciliation report and would otherwise conflict with each other.

## Resources for this branch

- Great Expectations docs: https://docs.greatexpectations.io/docs/
- GX Core Python API (expectation suites, validation): https://docs.greatexpectations.io/docs/core/introduction/
- `sqlglot` (optional SQL-lineage enrichment): https://sqlglot.com/sqlglot.html
- `sqlglot` lineage module specifically: https://sqlglot.com/sqlglot/lineage.html

## Task breakdown

**B3.1 — Q.2, Great Expectations gate.** New node `validate_data` between
`extract_data` and `resolve_entities` (register via 0.8's `NODE_SPECS` table, not by
editing `graph.py` by hand — **see the `NODE_SPECS`-does-not-exist note below**:
build the minimal registry this node needs, or wire it by hand and document
that choice). Logic in `tools/data_quality.py` so the node stays thin.
Per-entity expectations: uniqueness on declared `unique_id_fields`; not-null on the
detected name column (warn); sane bounds on `*_year`/`*_date` (warn); and
`row_count >= 1` as a **hard fail** — that one would have caught the watermark-loss
bug from an earlier phase at exactly the right place. Emit `quality_report` with
`severity ∈ {"warn","fail"}`.

**Behaviour on failure: report and flag, never auto-abort.** A `fail` sets a flag
shown prominently at the existing `human_final_approval` gate; the human decides.
This matches the project's "fail loudly, human decides" posture and avoids adding a
seventh gate. Wrap GX in try/except — a GX failure degrades to "validation
unavailable", never crashes a run.

Note this is *row-content* validation after extraction, distinct from
`nodes/preflight.py`'s pre-run structural checks (reachability, writability, LLM
liveness). Do not duplicate preflight. (`preflight.py` is outside your ownership.)

**B3.2 — Q.3, column-level lineage.** `orchestrator/utils/lineage.py::build_lineage`
joins `field_mappings[entity]` (source_field → dest column) with, per source field,
the set of `(origin_id, origin_table)` that actually carried it — derivable from the
spool rows, which already carry `_origin_id`/`_origin_table`/`_origin_id_row`.

**The highest-value piece is already computed and thrown away:** `_conflicts` from
`unify_entity_group.py:236` records, per conflicted field,
`{field, values:[{origin, value}], selected_origin, selected_value, winning_rule}` —
that *is* column-level lineage — and `_transform_records`
(`tools/load_data.py:208`) discards it. Preserve it into the lineage output.
(`unify_entity_group.py` and `load_data.py` are outside your ownership — read them
to find the discarded data, but the change needed is in your own `lineage.py`
consuming what's already returned, not editing those files. If preserving the data
genuinely requires a change inside either file, that's a request to `main`.)

`sqlglot`-based SQL-expression lineage is an **optional enrichment** used to
*validate* the mapping-derived lineage, not replace it. If it proves awkward over
parameterised SQL, ship without it and say so — the mapping-derived piece is the
user-visible win.

**Record the known limitation** rather than papering over it: the destination row's
`_source_ids` currently only carries `[{"cluster_id": …}]`
(`tools/load_data.py:242`), not real origin row ids, and the durable
`merge_engine_entity_map` exists **only on a PostgreSQL destination**
(`postgresql_destination.py:766`; ClickHouse has no `save_entity_map`).

**B3.3 — UI.** A quality summary panel and a collapsible lineage section in
`Gates.tsx`'s `SummaryBody` (`:314`). Read-only; no editor. (`Gates.tsx` itself is
shared — you own only the new quality/lineage panels within it, per the ownership
table; keep the diff scoped to additive panel code, not a rewrite of the file.)

**Note on `NODE_SPECS` and `REPORT_CONTRIBUTORS`:** Part 1.6.D's re-audit found
both registries were part of the original §0.8 design but were **never actually
built** — `graph.py` still wires all 17 nodes with inline `add_node`/
`add_conditional_edges` calls, and `build_reconciliation_report` (`load_data.py`)
is still one function body. `quality_report` and `lineage` need to land in the
reconciliation report and `validate_data` needs to land in the graph; either build
the minimal registries as a small, self-contained refactor first (this branch is
well-positioned to do it, since it's the first to need both), or add the keys/node
directly and document that choice explicitly in this file's status notes — do not
silently assume either registry exists.

## Acceptance gates

- A Boeing run report carries non-empty `data_quality` and `lineage` keys.
- Deliberately seed one duplicate unique-id and one 0-row entity; prove `fail`
  severity surfaces at the final gate and that the run still reaches the gate.
- Lineage traces at least one multi-origin destination column back to both
  `origin1` and `origin2` source fields, and flags a created column.
- Graph-wiring tests confirm `validate_data` sits between extract and resolve and
  that `_derive_skipped_gates` and every router are unaffected — this node is **not**
  a HITL gate and must not interrupt.

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
owned files — see this branch's own note above, since you're the first to need it).
New report section → one `REPORT_CONTRIBUTORS` registration (same caveat). If you
find yourself editing a shared function body with no registry available, build the
minimal registry first rather than reaching into the shared body.

**R4 — Never edit `pyproject.toml` or `requirements.txt`.** Every dependency you need
was pre-declared as an extras group in Phase 0.1 (`dspy`, `splink`, `dq`, `lineage`,
`swarm`, `perf`). Great Expectations is the `dq` extra, `sqlglot` is the `lineage`
extra. If you need one that wasn't pre-declared, that's a request to `main`.

**R5 — Rebase on `main` immediately after every merge-back**, before writing another
line. Serialized integration only works if everyone re-baselines at once.

**R6 — New features ship default-off.** Splink, the swarm planner, and any
performance strategy change land behind a config flag whose default reproduces
current behaviour. A branch that cannot be turned off cannot be merged safely.
(`validate_data` is additive and never interrupts, so it does not strictly need a
flag — but if the GX dependency proves heavy or flaky, consider one anyway.)

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
branch (B3) merges **second** — right after B5 (tooling, near-zero conflict).
B3 itself is near-zero conflict too (new files plus registry entries). Rebase
on `main` immediately after B5 lands, and again after you merge, per R5.

Full programme context, the other four branches' specs, and the honest
scope note about priority: `docs/plans/ONLY_PLAN_TO_IMPLEMENT.md` on `main`.
