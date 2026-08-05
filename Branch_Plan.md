# Branch Plan — `feat/planner-swarm` (B2)

Copied verbatim from [docs/plans/ONLY_PLAN_TO_IMPLEMENT.md](docs/plans/ONLY_PLAN_TO_IMPLEMENT.md)
(the full programme) at branch-creation time, 2026-08-05, from `main` commit
`e269a5f`. This file is self-contained — you should not need to check out
`main` or another branch to know what to do. If something here conflicts
with the source plan later, the source plan on `main` is the fallback of
last resort, but this file is what you implement against.

**Branch discipline (see `CLAUDE.md` on `main` — copied here for
self-containment):** once you are implementing this branch, `git checkout
feat/planner-swarm` and stay there. Never `git checkout main` or another
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
| `nodes/parse_request.py`, `nodes/analyze_dependencies.py`, `nodes/plan_scope.py`, new `src/orchestrator/planning/**`, `state/planner.py` | `src/llm/prompts/**` (B1 owns those), anything after `extract_data` |

You may **read** any file in the repo. You may only **modify** the three
named node files, anything new you create under
`src/orchestrator/planning/`, and `state/planner.py`. `src/llm/prompts/**`
belongs to B1 — if the planner needs a new prompt, that's a request to B1 or
`main`, not something to write yourself. Nothing after `extract_data` in the
graph is yours to touch; the contract boundary in B2.2 below is absolute.

---

## Goal

Remove the breadth ceiling. Today `parse_request` picks entity types from a
fixed 18-value enum *before anything has looked at the origin catalog*, so "merge all
information about Boeing" yields `["company","person","product"]` — 3 of 11 families —
and `product` (which exists in no origin) hard-errors in `extract_data`. This is the
largest single identified contributor to the 12% parity gap.

## Resources for this branch

- `langgraph-swarm` docs: https://langchain-ai.github.io/langgraph/reference/swarm/
- `langgraph-swarm` GitHub (`create_swarm`, `create_handoff_tool` source + examples): https://github.com/langchain-ai/langgraph-swarm-py
- LangGraph multi-agent concepts overview: https://langchain-ai.github.io/langgraph/concepts/multi_agent/
- LangGraph subgraphs (for nesting the swarm inside the main DAG): https://langchain-ai.github.io/langgraph/how-tos/subgraph/

## Task breakdown

**B2.1 — The planning subgraph.** Build `src/orchestrator/planning/` as a
`langgraph-swarm` graph with `create_swarm([...], default_active_agent=...)` and
`create_handoff_tool(agent_name=..., description=...)`. Agents:

| Agent | Job | Hands off to |
|---|---|---|
| `SchemaExplorer` | queries the real origin catalog + FK graph; answers "does a table for X exist, and what's actually in it?" | EntityPlanner, TableSelector |
| `EntityPlanner` | derives the entity closure for "everything about `<subject>`" from the **actual FK graph**, not an enum | SchemaExplorer (to verify), TableSelector |
| `TableSelector` | resolves each entity to concrete `(origin_id, table)` pairs | ScopePlanner, EntityPlanner (to revise) |
| `ScopePlanner` | produces the `match`/`link`/`link_via` scope plan for the subject | TableSelector (to revise) |

The Boeing loop this makes possible, and which the current fixed DAG structurally
cannot do: EntityPlanner proposes `product` → SchemaExplorer reports no product table
anywhere *and* surfaces the unrequested `CompanyFinancials` (199 rows),
`CompanyTechnographics` (20,093), `CompanyRelation` (6,175) → EntityPlanner revises.

**B2.2 — The contract boundary is absolute.** The subgraph must emit exactly the
state keys the existing DAG expects — `target_entities`, `explicit_target_entities`,
`merge_scope`, `dependency_tree`, `extraction_order`, `entity_tables`,
`table_selection_notes`, `scope_plan` — and the **same `human_review_deps` and
`human_review_scope` interrupt payloads** (built by
`human_nodes.py::_build_scope_interrupt` and mirrored in
`WorkflowManager._build_interrupt_data`, because `interrupt_before` means HITL node
bodies never run — see `CLAUDE.md`'s "HITL resume trap" on `main`). Nothing after
`extract_data` may change. `Gates.tsx` should need no edits; if it does, the contract
was broken. (`human_nodes.py` and `Gates.tsx` are outside your ownership — reaching
into either is a signal something is wrong with your contract, not a task to do.)

**B2.3 — `SwarmState.active_agent` must not leak into `MergeState`.** Run the swarm
as a nested compiled graph whose input and output are plain dicts; keep
`active_agent` and the swarm's own `messages` inside it. Otherwise it lands in the
SQLite checkpoint and every resume path has to reason about it.

**B2.4 — Bound the loop.** Handoff graphs can ping-pong. Set a hard handoff cap and a
wall-clock budget; on exhaustion, fall back to the current deterministic nodes and
record *why* in `table_selection_notes` so it surfaces at the deps gate. A planner
that spins for 20 minutes is worse than one that gives a mediocre answer in 30
seconds.

**B2.5 — Default off.** Ship behind `planning.engine: "swarm" | "sequential"`
(default `sequential`), the same posture Q.1 uses for Splink (see B4). This branch
cannot regress an existing run if it is not on the path.

**B2.6 — Widen the entity vocabulary** to whatever the FK-derived closure actually
produces, keeping `tests/unit/test_entity_aliases.py`'s invariant (every canonical
type has exactly one alias group; no alias appears in two). Note:
`src/orchestrator/utils/entity_types.py` is not explicitly in your Owns column —
confirm with `main` before widening it if it turns out to need structural changes
beyond adding entries; a pure addition to the existing vocabulary/alias-group
pattern is the expected shape of this change.

**Note on `NODE_SPECS`:** Part 1.6.D's re-audit found this declarative node-wiring
table was never actually built (§0.8 was aspirational there). If this branch adds
new nodes to the planning subgraph, either build the minimal `NODE_SPECS` registry
first (small, self-contained — it only needs to cover this branch's owned files) or
wire the subgraph's entry/exit points by hand and say so plainly in this file's
task-status notes rather than pretending the registry exists.

## Acceptance gates

- Boeing under the swarm planner reaches **≥ 9 entity families** (v1: 3) at the
  `human_review_deps` gate, verified by reading the gate payload directly.
- No `product`-style hard error: an inferred-but-absent entity warns, never
  escalates.
- Parity improves measurably vs 12.0% — report the number honestly whatever it is.
- With `planning.engine: sequential`, behaviour is bit-identical to `main`.
- Airbus and Lockheed also run (neither has been re-verified since the eight v1
  bug fixes — this branch is the natural place to close that gap).

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
owned files — see this branch's own note above). New report section → one
`REPORT_CONTRIBUTORS` registration (same caveat). If you find yourself editing a
shared function body with no registry available, build the minimal registry first
rather than reaching into the shared body.

**R4 — Never edit `pyproject.toml` or `requirements.txt`.** Every dependency you need
was pre-declared as an extras group in Phase 0.1 (`dspy`, `splink`, `dq`, `lineage`,
`swarm`, `perf`). `langgraph-swarm` is the `swarm` extra. If you need one that wasn't
pre-declared, that's a request to `main`.

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
branch (B2) merges **last** — it has the largest expected payoff (targets
the entity-breadth cap directly) and the largest risk, which is why it ships
default-off and lands on the most stable base with the most complete
verification tooling available. Rebase on `main` after each of B5, B3, B1,
and B4 land, per R5 — that's four rebases before you merge, not one.

Full programme context, the other four branches' specs, and the honest
scope note about priority: `docs/plans/ONLY_PLAN_TO_IMPLEMENT.md` on `main`.
