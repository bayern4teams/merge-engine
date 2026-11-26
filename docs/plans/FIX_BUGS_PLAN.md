# Fix 8 UI Bugs + .env Loading Issue

## Summary

8 bugs covering tooltip styling, cascading dropdowns, structured data display, explanatory tooltips, scorecard explanations, and .env default loading.
NOTE: While you are working on REQUEST you should update TODO.md simultaneously.

---

## Bug #1 — Tooltip font (ALL-CAPS → normal)

The Tooltip component renders text in a dark popup, but the CSS for `.section-label` applies `text-transform: uppercase` which cascades into the tooltip. The tooltip popup should use the app's body font at normal case.

### [MODIFY] [Tooltip.tsx](../../src/ui/frontend/src/components/Tooltip.tsx)

- Add `textTransform: "none"`, `fontWeight: "normal"`, `letterSpacing: "normal"` to the popup `<span>` style so it doesn't inherit the `section-label` uppercase transform.

---

## Bug #2 — Cascading dropdowns for dependency link form

Currently the "Add Custom Dependency Link" form uses free-text `<input>` fields for Origin, Table, and Column. These should be `<select>` dropdowns populated from the origins the user added in Setup.

### [MODIFY] [Gates.tsx](../../src/ui/frontend/src/components/Gates.tsx) (DependencyBody)

- The interrupt data already includes `entity_tables` from state. We also need the origin schemas (table→columns map per origin).
- Add `origin_schemas` to the data passed from the `human_review_deps` node.
- Replace the 6 free-text `<input>` fields (child origin, table, column + parent origin, table, column) with cascading `<select>` dropdowns:

1. **Origin** dropdown: populated from `data.origin_schemas` keys (e.g. `origin_1`, `origin_2`)
2. **Table** dropdown: populated from the selected origin's tables
3. **Column** dropdown: populated from the selected table's columns

### [MODIFY] [human_nodes.py](../../src/orchestrator/nodes/human_nodes.py)

- In `human_review_deps`, add `origin_schemas` to `interrupt_data["data"]` — pull it from `state.get("origin_schemas", {})`.

### [MODIFY] [analyze_dependencies.py](../../src/orchestrator/nodes/analyze_dependencies.py) or workflow_manager.py

- Ensure `origin_schemas` is populated in state before the dependency gate fires. Check if `schema_results` (from `test_and_discover_schemas`) is already stored in state during the workflow start — if so, restructure it into `{origin_id: {table_name: [column_names]}}` and write it to state as `origin_schemas`.

> [!IMPORTANT]
> I need to verify whether the workflow already stores discovery results in state. If it doesn't, we may need to pass origin schemas through the `RunConfig` body at workflow start time.

---

## Bug #3 — Extraction Order as structured table

Replace the plain `<ol>` list with a structured `<table>` grouped by extraction tier.

### [MODIFY] [Gates.tsx](../../src/ui/frontend/src/components/Gates.tsx) (DependencyBody)

- Replace lines 119–131 (the `<ol>` rendering extraction order) with a `<table>` that has columns: **#**, **Table Name**, **Dependencies**, **Status** (root / has deps).
- Group rows by extraction tier: tables with no dependencies form Tier 1, tables depending only on Tier 1 form Tier 2, etc. Add a subtle visual separator or label between tiers.

---

## Bug #4 — Field Mappings: Origin/Source Table missing, New Column/Type disabled

### [MODIFY] [Gates.tsx](../../src/ui/frontend/src/components/Gates.tsx) (MappingBody)

- **Origin/Source Table columns showing "—"**: The backend already sends `origin_id` and `table` in `MappingRow` (see [api.ts L53-54](../../src/ui/frontend/src/api.ts#L53-L54)). The problem is the backend `map_fields` node may not be populating these fields in the mapping rows it produces. Need to verify and fix the backend if `origin_id` and `table` are not being set.
- **New Column/Type disabled**: Currently these inputs are `disabled={r.action !== "new_column"}` (line 543, 551). This is intentional — they should only be editable when the Action is set to `new_column`. However, the dropdown defaulting to `map` makes them appear permanently disabled. **Fix**: Show a hint/placeholder text explaining "Change action to 'new_column' to enable".

> [!IMPORTANT]
> Need to investigate `map_fields` node to confirm whether it populates `origin_id` and `table` on each mapping row. If not, that's the backend fix.

---

## Bug #5 — Matching Company Records: structured card layout

### [MODIFY] [Gates.tsx](../../src/ui/frontend/src/components/Gates.tsx) (ScopeBody)

- Replace the single-line `key=value, key=value` candidate details column (lines 291–296) with a collapsible card per record.
- Each card shows a mini key-value table: columns **Field Name** and **Value**, one row per field.
- Cards are collapsed by default, showing only the first 2–3 key fields inline. Click to expand the full table.

---

## Bug #6 — Explanatory ⓘ tooltips for Reconciliation, Conflicts resolved by, Field mappings

### [MODIFY] [Gates.tsx](../../src/ui/frontend/src/components/Gates.tsx) (SummaryBody)

- Add `<Tooltip>` next to each section header:
- **Reconciliation (pre-load)**: "Shows per-entity counts: how many records were extracted from origins, grouped into clusters, reviewed by a human, unified into canonical records, and how many field-level conflicts arose during unification."
- **Conflicts resolved by**: "Shows which survivorship rules (priority, recency, completeness, first_seen, etc.) decided each field-level conflict when merging duplicate records."
- **Field mappings (N)**: "The final column-to-column mapping from source fields to destination columns. 'map' copies a value; 'new_column' creates a new column; 'skip' drops the field."

---

## Bug #7 — Data-Quality Scorecard explanation panel

### [MODIFY] [PipelineScreen.tsx](../../src/ui/frontend/src/components/PipelineScreen.tsx) (RunScorecard)

- Add an expandable "Why this score?" panel below the scorecard stats grid.
- The panel explains:
- **Grade**: Criteria from [load_data.py L219-226](../../src/orchestrator/nodes/load_data.py#L219-L226):
- A = no red flags
- B = minor red flags (e.g. dropped inferred entities)
- C = type coercion failures or dropped mapping fields
- F = load rolled back or zero auto-merges
- **Completeness**: `families_loaded / families_requested * 100`. List which families were loaded and which were not.
- **Families loaded vs requested**: Explain that "requested" = target entities, "loaded" = entities that had at least 1 row inserted/updated.
- Include the `dropped_inferred_entities` list if present.
- Use the `report` object which already contains all this data (`data_quality.grade`, `data_quality.red_flags`, `families_requested`, `families_loaded`, `completeness_pct`, `dropped_inferred_entities`).

---

## Bug #8 — .env defaults not loading (all keys show "not set")

The root cause is that `load_dotenv()` (without explicit `dotenv_path`) resolves `.env` relative to CWD. When the app is launched from a different directory, or when running via `python -m src.main`, the CWD might not be the project root.

### [MODIFY] [src/main.py](../../src/main.py)

- Add explicit `.env` path: `Path(__file__).resolve().parent.parent / ".env"` (since `src/main.py` is one level inside `src/`).

### [MODIFY] [src/utils/config_loader.py](../../src/utils/config_loader.py)

- Same fix: resolve `.env` path explicitly from `Path(__file__).resolve().parent.parent.parent / ".env"` (since it's `src/utils/config_loader.py`).

### Already fixed: [src/api/main.py](../../src/api/main.py)

- `_init_env()` already uses `_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"`. ✅

---

## Open Questions

> [!IMPORTANT]
> **Bug #2 — Origin schemas data flow**: Does the workflow already store origin discovery results (tables + columns per origin) in state before the dependency gate? If not, how do we pass this data? Options:
>
> 1. Store discovery results in state during `workflow_manager.start_workflow()` (already runs `test_and_discover_schemas` at start)
> 2. Have the UI call a separate `/api/origins/schema` endpoint and pass the data as a React prop to the Gate component

> [!IMPORTANT]
> **Bug #4 — Missing origin_id/table on mapping rows**: Need to verify whether the `map_fields` node (or the LLM mapping step) actually sets `origin_id` and `table` on each `MappingRow`. If the LLM response doesn't include these, we need to backfill them from the field provenance data.

---

## Verification Plan

### Automated Tests

```powershell
.venv\Scripts\python.exe scripts/check.py all
```

### Manual Verification

1. Start the server: `python -m src.main --mode gradio --port 7860`
2. Verify Settings → Environment shows `.env` values as "✓ set (.env default)" instead of "not set"
3. Run a workflow and check:

- Tooltip at "Extraction Order" renders in normal font (not ALL-CAPS)
- Extraction Order shows as a structured table with tier grouping
- "Add Custom Dependency Link" has cascading dropdowns (if origin schemas are available)
- Matching Company Records expand into structured key-value cards
- Field Mappings table shows Origin and Source Table values
- Reconciliation/Conflicts/Mappings headers have ⓘ tooltips
- Data-Quality Scorecard has an expandable explanation panel
