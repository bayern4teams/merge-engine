# TODO — `feat/quality-lineage` (B3)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05. Nothing in this branch
has started yet. **Merges second in the integration order (right after B5).**

## B3.1 — Great Expectations gate

- [ ] `NODE_SPECS` registry built (minimal form, this branch's owned files
      only) — or, if hand-wired instead, that choice documented here
      explicitly. **Live verification:** `validate_data` appears in a real
      graph run's node sequence between `extract_data` and
      `resolve_entities`.
- [ ] `tools/data_quality.py` implements per-entity expectations:
      uniqueness on `unique_id_fields`, not-null on detected name column
      (warn), bounds on `*_year`/`*_date` fields (warn), `row_count >= 1`
      (hard fail). **Live verification:** run against real extracted Boeing
      data, confirm expectations evaluate correctly against known-good data
      (no false fails).
- [ ] GX wrapped in try/except, degrading to "validation unavailable" on
      failure rather than crashing. **Live verification:** force a GX
      internal error (e.g. malformed expectation config) and confirm the
      run still completes with a degraded quality report, not a crash.
- [ ] `fail` severity flagged prominently at `human_final_approval`, never
      auto-aborts. **Live verification:** seed a real 0-row entity and a
      duplicate unique-id, confirm both surface as `fail` at the final gate
      and the run still reaches it.
- [ ] Confirmed distinct from `preflight.py`'s structural checks — no
      duplicated logic. **Live verification:** code review diff against
      `preflight.py`'s existing checks (read-only reference, not edited).

## B3.2 — Column-level lineage

- [ ] `orchestrator/utils/lineage.py::build_lineage` joins field mappings
      with origin table provenance from spool rows (`_origin_id`,
      `_origin_table`, `_origin_id_row`). **Live verification:** run
      against real Boeing spool data, confirm at least one multi-origin
      destination column's lineage correctly lists both `origin1` and
      `origin2` source fields.
- [ ] `_conflicts` data from `unify_entity_group.py:236` preserved into the
      lineage output instead of being discarded in `_transform_records`.
      **Live verification:** trigger a real field conflict during a Boeing
      run, confirm the winning-rule/losing-value data appears in the
      lineage output, not just in transient in-memory state.
- [ ] Known limitation documented (not silently worked around): `_source_ids`
      only carries `cluster_id`, not real origin row ids; `merge_engine_entity_map`
      is PostgreSQL-only.
- [ ] `sqlglot` enrichment attempted; shipped only if it doesn't fight
      parameterised SQL, with an explicit note either way.
      **Live verification:** if shipped, run against a real mapped query and
      confirm the sqlglot-derived lineage agrees with the mapping-derived
      lineage on at least one column.

## B3.3 — UI

- [ ] Quality summary panel added to `Gates.tsx`'s `SummaryBody`.
      **Live verification:** real Boeing run's quality report renders
      correctly in the browser (screenshot or DOM read).
- [ ] Collapsible lineage section added, read-only, no editor controls.
      **Live verification:** same real run, lineage section renders and
      expands/collapses correctly.

## Acceptance gates (branch-level, all required before merge-back)

- [ ] A real Boeing run report carries non-empty `data_quality` and
      `lineage` keys (direct inspection of `logs/run_reports/<id>.json`,
      not the app's own success message).
- [ ] Seeded duplicate unique-id + 0-row entity both produce `fail`
      severity at the final gate; run still reaches the gate.
- [ ] Lineage traces at least one multi-origin destination column back to
      both origins, and flags at least one dropped/created-column case.
- [ ] Graph-wiring test confirms `validate_data`'s position and that it
      never interrupts (`_derive_skipped_gates` and every router unaffected).
- [ ] `python scripts/check.py all` green.
