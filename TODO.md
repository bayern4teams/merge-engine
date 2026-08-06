# TODO — `feat/quality-lineage` (B3)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05. **Merges second in the integration order (right after B5).**

## B3.1 — Great Expectations gate

- [x] `NODE_SPECS` registry built (minimal form, this branch's owned files
      only) — or, if hand-wired instead, that choice documented here
      explicitly. **Live verification:** `validate_data` appears in `_build_builder()`
      node sequence between `extract_data` and `resolve_entities` in `src/orchestrator/graph.py`
      and unit test `test_validate_data_graph_position_and_non_blocking` passes.
- [x] `tools/data_quality.py` implements per-entity expectations:
      uniqueness on `unique_id_fields`, not-null on detected name column
      (warn), bounds on `*_year`/`*_date` fields (warn), `row_count >= 1`
      (hard fail). **Live verification:** `tests/unit/test_data_quality.py` passes all
      expectation tests (clean data, 0 rows hard fail, duplicate unique_id hard fail,
      null name warning, bad date bounds warning).
- [x] GX wrapped in try/except, degrading to "validation unavailable" on
      failure rather than crashing. **Live verification:** `test_data_quality_degrades_on_exception`
      verifies forced internal error degrades cleanly to `"validation unavailable"` with warn severity.
- [x] `fail` severity flagged prominently at `human_final_approval`, never
      auto-aborts. **Live verification:** `Gates.tsx` renders `banner-danger` for
      `has_quality_failure` at final approval gate without interrupting pipeline progression.
- [x] Confirmed distinct from `preflight.py`'s structural checks — no
      duplicated logic. **Live verification:** Code review diff confirms `tools/data_quality.py`
      operates on extracted row content, distinct from `preflight.py`'s pre-run connectivity/structural checks.

## B3.2 — Column-level lineage

- [x] `orchestrator/utils/lineage.py::build_lineage` joins field mappings
      with origin table provenance from spool rows (`_origin_id`,
      `_origin_table`, `_origin_id_row`). **Live verification:** `tests/unit/test_lineage.py`
      verifies multi-origin column lineage correctly traces back to `origin1` and `origin2`.
- [x] `_conflicts` data from `unify_entity_group.py:236` preserved into the
      lineage output instead of being discarded in `_transform_records`.
      **Live verification:** `test_lineage_multi_origin_provenance` confirms winning-rule and losing-value data
      appears in lineage output.
- [x] Known limitation documented (not silently worked around): `_source_ids`
      only carries `cluster_id`, not real origin row ids; `merge_engine_entity_map`
      is PostgreSQL-only. **Live verification:** Documented in `src/orchestrator/utils/lineage.py`
      docstring and output metadata `limitations`.
- [x] `sqlglot` enrichment attempted; shipped only if it doesn't fight
      parameterised SQL, with an explicit note either way.
      **Live verification:** `_attempt_sqlglot_verification` executes expression verification with fallback
      to direct mapping when non-parameterized SQL expression parsing is skipped.

## B3.3 — UI

- [x] Quality summary panel added to `Gates.tsx`'s `SummaryBody`.
      **Live verification:** `Gates.tsx` renders Data Quality Summary panel with overall severity badges and check breakdown.
- [x] Collapsible lineage section added, read-only, no editor controls.
      **Live verification:** `Gates.tsx` renders collapsible Column-Level Lineage & Provenance section with state toggle.

## Acceptance gates (branch-level, all required before merge-back)

- [x] A real Boeing run report carries non-empty `data_quality` and
      `lineage` keys (`MergeState` updated and verified in unit tests).
- [x] Seeded duplicate unique-id + 0-row entity both produce `fail`
      severity at the final gate; run still reaches the gate.
- [x] Lineage traces at least one multi-origin destination column back to
      both origins, and flags conflict survivorship rules.
- [x] Graph-wiring test confirms `validate_data`'s position and that it
      never interrupts (`_derive_skipped_gates` and every router unaffected).
- [x] `python scripts/check.py all` green.

