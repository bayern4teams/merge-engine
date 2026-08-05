# TODO — `feat/planner-swarm` (B2)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05. Nothing in this branch
has started yet. **Merges last in the integration order (after B5, B3, B1, B4)
— do not start implementation until told this branch is next.**

## B2.1 — The planning subgraph

- [ ] `src/orchestrator/planning/` scaffolded with `create_swarm([...])` and
      four agents (`SchemaExplorer`, `EntityPlanner`, `TableSelector`,
      `ScopePlanner`), each with a `create_handoff_tool` to its listed
      targets. **Live verification:** unit test drives the swarm on a
      synthetic schema and confirms handoffs occur in a plausible order (not
      just that the graph compiles).
- [ ] `SchemaExplorer` queries the real origin catalog + FK graph.
      **Live verification:** run against real ClickHouse origin1/origin2,
      confirm it correctly reports which tables exist and which don't
      (e.g. correctly reports no `product` table for Boeing).
- [ ] `EntityPlanner` derives entity closure from the FK graph, not the
      fixed enum. **Live verification:** on the real Boeing schema, confirm
      it proposes entities beyond the original 3 (company/person/product).
- [ ] The full loop (EntityPlanner proposes `product` → SchemaExplorer
      reports absence + surfaces `CompanyFinancials`/`CompanyTechnographics`/
      `CompanyRelation` → EntityPlanner revises) reproduced live against the
      real Boeing origin data.

## B2.2 — The contract boundary

- [ ] Subgraph emits exactly `target_entities`, `explicit_target_entities`,
      `merge_scope`, `dependency_tree`, `extraction_order`, `entity_tables`,
      `table_selection_notes`, `scope_plan` — same keys, same shapes as the
      current sequential path. **Live verification:** run both
      `planning.engine: sequential` and `planning.engine: swarm` on the same
      input, diff the resulting state dict's keys/types (not values, which
      should differ) — confirm no new/missing/reshaped keys.
- [ ] `human_review_deps` and `human_review_scope` interrupt payloads
      unchanged in shape. **Live verification:** trigger both gates under
      the swarm path, confirm `Gates.tsx` renders them with zero code
      changes needed on the frontend.

## B2.3 — State isolation

- [ ] `active_agent` / swarm `messages` never appear in the SQLite
      checkpoint. **Live verification:** inspect a real checkpoint after a
      swarm-planned run (direct SQLite query against the checkpoint file,
      not the app's own status display) and confirm no swarm-internal keys
      leaked into `MergeState`.

## B2.4 — Bounded loop

- [ ] Hard handoff cap and wall-clock budget enforced.
      **Live verification:** seed a pathological case designed to ping-pong
      (e.g. an ambiguous subject with no clear entity closure) and confirm
      the planner falls back to deterministic nodes within the budget,
      rather than spinning.
- [ ] Fallback reason recorded in `table_selection_notes` and visible at the
      deps gate. **Live verification:** trigger the fallback, confirm the
      note appears in the real gate payload.

## B2.5 — Default off

- [ ] `planning.engine` config flag added, default `sequential`.
      **Live verification:** run the existing test suite with no config
      change and confirm zero swarm code executes (e.g. via a coverage
      check or an explicit assertion that `SchemaExplorer` etc. are never
      invoked).

## B2.6 — Entity vocabulary widening

- [ ] Vocabulary/alias groups extended for whatever the FK-derived closure
      produces. **Live verification:** `pytest tests/unit/test_entity_aliases.py`
      still passes (one alias group per canonical type, no alias in two
      groups) after the widening.

## Acceptance gates (branch-level, all required before merge-back)

- [ ] Boeing under the swarm planner reaches ≥ 9 entity families at the
      `human_review_deps` gate (v1 baseline: 3) — verified by reading the
      real gate payload from a live run, not a mock.
- [ ] No `product`-style hard error anywhere in `extract_data` for an
      inferred-but-absent entity — it warns, never escalates.
- [ ] Parity improvement over 12.0% reported honestly with the actual
      number, via `scripts/diagnostics/compare_to_golden.py`.
- [ ] `planning.engine: sequential` run is bit-identical to the current
      `main` behaviour (diff the full result state).
- [ ] Airbus and Lockheed both run end-to-end under the swarm planner.
- [ ] `python scripts/check.py all` green.
