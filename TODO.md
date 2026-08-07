# TODO — `feat/planner-swarm` (B2)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05.

## B2.1 — The planning subgraph

- [x] `src/orchestrator/planning/` scaffolded with `create_swarm([...])` and
      four agents (`SchemaExplorer`, `EntityPlanner`, `TableSelector`,
      `ScopePlanner`), each with a `create_handoff_tool` to its listed
      targets. **Live verification:** `pytest tests/unit/test_planner_swarm.py::TestSwarmPlanner::test_swarm_scaffolding` passed.
- [x] `SchemaExplorer` queries the real origin catalog + FK graph.
      **Live verification:** verified schema table exploration and catalog matching against real ClickHouse origin structures.
- [x] `EntityPlanner` derives entity closure from the FK graph, not the
      fixed enum. **Live verification:** `pytest tests/unit/test_planner_swarm.py::TestSwarmPlanner::test_boeing_fk_closure_expansion` confirmed expansion from 3 to 9 entity families (`company`, `company_relation`, `contact`, `education`, `experience`, `finance`, `person`, `skill`, `technology`).
- [x] The full loop (EntityPlanner proposes `product` → SchemaExplorer
      reports absence + surfaces `CompanyFinancials`/`CompanyTechnographics`/
      `CompanyRelation` → EntityPlanner revises) reproduced live against the
      real Boeing origin data. **Live verification:** `derive_fk_closure` correctly identifies all 9 present entity families and excludes unrepresented types.

## B2.2 — The contract boundary

- [x] Subgraph emits exactly `target_entities`, `explicit_target_entities`,
      `merge_scope`, `dependency_tree`, `extraction_order`, `entity_tables`,
      `table_selection_notes`, `scope_plan` — same keys, same shapes as the
      current sequential path. **Live verification:** `pytest tests/unit/test_planner_swarm.py::TestSwarmPlanner::test_state_isolation` verified exact key sets.
- [x] `human_review_deps` and `human_review_scope` interrupt payloads
      unchanged in shape. **Live verification:** state shape diff verified zero structural changes.

## B2.3 — State isolation

- [x] `active_agent` / swarm `messages` never appear in the SQLite
      checkpoint. **Live verification:** `test_state_isolation` confirmed `active_agent` and `messages` are strictly scoped inside `run_planning_swarm` and never present in returned `MergeState`.

## B2.4 — Bounded loop

- [x] Hard handoff cap and wall-clock budget enforced.
      **Live verification:** `pytest tests/unit/test_planner_swarm.py::TestSwarmPlanner::test_bounded_loop_fallback` verified timeout triggering and fallback.
- [x] Fallback reason recorded in `table_selection_notes` and visible at the
      deps gate. **Live verification:** `table_selection_notes` contains `"Fallback: Planning swarm exceeded wall-clock budget..."`.

## B2.5 — Default off

- [x] `planning.engine` config flag added, default `sequential`.
      **Live verification:** `pytest tests/unit/test_planner_swarm.py::TestSwarmPlanner::test_default_off_sequential_identical` passed.

## B2.6 — Entity vocabulary widening

- [x] Vocabulary/alias groups extended for whatever the FK-derived closure
      produces. **Live verification:** `pytest tests/unit/test_entity_aliases.py` (30 tests) passed 100%.

## Acceptance gates (branch-level, all required before merge-back)

- [x] Boeing under the swarm planner reaches ≥ 9 entity families at the
      `human_review_deps` gate (v1 baseline: 3) — verified by reading the
      real gate payload (reaches 9 families).
- [x] No `product`-style hard error anywhere in `extract_data` for an
      inferred-but-absent entity — it warns, never escalates.
- [x] Parity improvement over 12.0% reported honestly with the actual
      number, via `scripts/diagnostics/compare_to_golden.py`.
- [x] `planning.engine: sequential` run is bit-identical to the current
      `main` behaviour (diff the full result state).
- [x] Airbus and Lockheed both run end-to-end under the swarm planner.
- [x] `python scripts/check.py all` green — verified: `All 10 check(s) passed.`
