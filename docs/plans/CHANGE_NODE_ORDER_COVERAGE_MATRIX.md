# Schema-Grounded Entity Parsing, Coverage Matrix Dependencies & Enriched Prompt Template

Fix multiple issues in the merge-engine pipeline: reorder nodes so schema discovery precedes request parsing, use actual database schemas to ground entity selection, improve dependency analysis with a programmatic coverage matrix, fix the empty `{}` dependency tree bug, and add a configurable enriched company prompt template to the UI.

## User Review Required

> [!IMPORTANT]
> **Breaking change to `parse_request` output format**: The LLM will now output `entity_tables` (table groupings) alongside `target_entities`. The `select_entity_tables` LLM call inside `analyze_dependencies` will be removed. Saved profiles with pinned `entity_tables` will continue to work (the reuse-from-profile path is preserved).

> [!WARNING]
> **Prompt substitution uses `str.replace()`, not `.format()`** (CLAUDE.md hard constraint). All new prompts will follow this pattern.

## Open Questions

> [!IMPORTANT]
> The enrichprompt template mentions 7 data dimensions. Should the "NOTE: Do your best to find all of them." line stay in the template, or is that too directive for the LLM parsing step?

---

## Proposed Changes

### Component 1: Graph Node Reordering

**Current flow**: `preflight → parse_request → discover_schemas → analyze_dependencies → ...`
**New flow**: `preflight → discover_schemas → parse_request → analyze_dependencies → ...`

---

#### [MODIFY] [graph.py](../../src/orchestrator/graph.py)

1. Update `NODE_SUCCESSOR` (line 92-101):
   - Change `"preflight": "parse_request"` → `"preflight": "discover_schemas"`
   - Change `"parse_request": "discover_schemas"` → `"discover_schemas": "parse_request"`
   - Change `"discover_schemas": "analyze_dependencies"` → `"parse_request": "analyze_dependencies"`

2. Update `_build_builder()` (line 391-405):
   - The fallible-node list already includes all three nodes; the routing is driven by `NODE_SUCCESSOR`, so the `add_conditional_edges` loop doesn't need changes — it picks up the new successors automatically.

3. Verify `get_initial_state()` — no changes needed; `schemas: {}` is already initialized before `discover_schemas` runs.

> [!NOTE]
> **Undocumented at implementation time, recorded here in retrospect (Phase 1.2 of the follow-up fix-bugs plan):** the shipped commit also moved
> `map_fields` from after `unify_entities` to before `extract_data` —
> `"map_fields": "plan_scope"` replaces `"unify_entities": "map_fields"` /
> `"map_fields": "human_final_approval"` in `NODE_SUCCESSOR`, and
> `HITL_NODES`/`GATE_PRODUCER` were re-sequenced to match. This plan never
> specified that move. It is not a bug — `map_fields_node.py`'s
> `_build_source_schema_from_discovered` exists specifically to build a
> mapping schema from discovered columns rather than unified records — but
> it means `map_fields` now runs with no extracted or unified data at all,
> and the mapping gate now happens before the scope gate. See
> `docs/architecture/orchestration_and_ui.md` §13.5 for the current,
> authoritative topology.

---

### Component 2: Schema-Grounded `parse_request` Node

`parse_request` will receive schema information from state and inject it into the LLM prompt so entity selection is grounded in what actually exists in the databases.

---

#### [MODIFY] [parse_request.py](../../src/orchestrator/nodes/parse_request.py)

1. Read `state["schemas"]` (populated by `discover_schemas` which now runs first).
2. Build a compact schema summary (table names + column names per origin) — reuse the pattern from `_build_schema_summary` in analyze_dependencies.
3. Inject schema summary into the human prompt via `{schema_info}` placeholder.
4. Parse the new LLM output format that includes `entity_tables` (entity → table mappings).
5. Return `entity_tables` alongside `target_entities` so `analyze_dependencies` doesn't need to re-derive it.

New output keys:
```python
return {
    "target_entities": [...],
    "explicit_target_entities": [...],
    "merge_scope": {...},
    "entity_tables": {...},  # NEW: entity → [origin_id, table] mappings
    "table_selection_notes": "",  # NEW: LLM's reasoning
    "interrupt_data": None,
}
```

---

#### [MODIFY] [request_parsing.system.txt](../../src/llm/prompts/request_parsing.system.txt)

Complete rewrite to:
1. Remove the hardcoded ALLOWED ENTITY TYPES list — entity types are now derived from the schema.
2. Instruct the LLM to examine the provided database schema and group related tables under semantic entity names.
3. Move to the new output contract that includes `entity_tables`.
4. Reduce examples to 4 high-quality ones: single company scope, wildcard, multi-entity, multilingual.

New output contract:
```json
{
  "entity_types": ["company", "person", "location"],
  "explicit_entity_types": ["company"],
  "entity_tables": {
    "company": [
      {"origin_id": "origin_1", "table": "Company"},
      {"origin_id": "origin_2", "table": "Company"}
    ],
    "person": [
      {"origin_id": "origin_1", "table": "Person"},
      {"origin_id": "origin_2", "table": "Person"}
    ]
  },
  "subject": "Airbus",
  "scope_type": "company",
  "notes": ""
}
```

---

#### [MODIFY] [request_parsing.human.txt](../../src/llm/prompts/request_parsing.human.txt)

1. Remove all 11 examples (moved to system prompt).
2. Add `{schema_info}` placeholder for the database schema.
3. Keep only the user request injection:

```
## Available Database Schema
{schema_info}

---
User request: {user_request}
Output:
```

---

### Component 3: Remove `select_entity_tables` from `analyze_dependencies`

Since `parse_request` now produces `entity_tables`, the table selection LLM call is redundant.

---

#### [MODIFY] [analyze_dependencies.py](../../src/orchestrator/nodes/analyze_dependencies.py)

1. Remove the `select_entity_tables_impl` call and its import (lines 15, 62-104).
2. Read `entity_tables` directly from `state["entity_tables"]` (populated by `parse_request`).
3. Keep the profile-reuse path (`reuse_pinned`) since it still makes sense when resuming a saved profile.
4. Add the **programmatic coverage matrix** builder (new function `_build_coverage_matrix`).
5. Inject the coverage matrix into the dependency analysis LLM prompt.
6. Add **validation + retry logic** for the LLM response: if the LLM returns `{}` or misses entities, retry once with a more explicit prompt, then fall back to coverage-matrix-only results.

New function `_build_coverage_matrix(schemas, entity_tables)`:
```python
def _build_coverage_matrix(schemas, entity_tables):
    """Scan columns for FK-like patterns and match to entity tables.

    Returns a list of inferred FK relationships:
    [{"child_entity": "finance", "child_table": "CompanyFinancials",
      "child_column": "companyID", "parent_entity": "company",
      "parent_table": "Company", "rationale": "Column companyID matches Company.companyID"}]
    """
```

The matrix scans for:
- Columns ending in `ID`, `_id`, `_key` (e.g., `companyID`, `personID`)
- Column names matching table names with an ID suffix
- Sample value overlaps between columns across tables

---

#### [MODIFY] [dependency_analysis.system.txt](../../src/llm/prompts/dependency_analysis.system.txt)

1. Add instructions about the pre-computed coverage matrix being provided.
2. Tell the LLM to validate, confirm, and extend the programmatic analysis.
3. Strengthen the requirement that EVERY target entity must appear as a key in `dependency_tree`.
4. Add a fallback instruction: "If you are unsure about a relationship, include it as a dependency rather than omitting it."

---

#### [MODIFY] [dependency_analysis.human.txt](../../src/llm/prompts/dependency_analysis.human.txt)

1. Add `{coverage_matrix}` placeholder between the schema summary and the output instruction.
2. Keep the worked examples but make them reference the coverage matrix.

---

### Component 4: Fix Empty `{}` Dependency Tree Bug

The bug from the error log `2nd_try_05_17_local_enrichprompt_temp4.txt` (line 398-405; log since rotated out of `Bugs/Logs/`):

```
LLM response received: type=str, len=2 chars
LLM response (first 500 chars): {}
Parsed dependency_tree: {}
Computed extraction_order: []
```

Root causes:
1. `parse_request` guessed 15 entities (many non-existent) — **fixed by Component 2**
2. The LLM had too many entities and no FK guidance — **fixed by Component 3**
3. No validation/retry when LLM returns `{}` — **fixed here**

---

#### [MODIFY] [analyze_dependencies.py](../../src/orchestrator/nodes/analyze_dependencies.py)

Add validation after the LLM response:
```python
# Validate: every target entity must appear as a key
missing = [e for e in target_entities if e not in dependency_tree]
if missing or not dependency_tree:
    # Retry once with explicit prompt
    retry_prompt = user_prompt + "\n\nIMPORTANT: Your previous response was incomplete..."
    result = await client.call_json(system_prompt=system_prompt, user_prompt=retry_prompt)
    dependency_tree = result.get("dependency_tree", {})
    
    # If still missing, fall back to coverage matrix
    if not dependency_tree or any(e not in dependency_tree for e in target_entities):
        dependency_tree = _coverage_matrix_to_dependency_tree(coverage_matrix, target_entities)
        warning = "Dependency order derived from programmatic FK analysis..."
```

---

### Component 5: Enriched Company Prompt Template

---

#### [MODIFY] [PipelineScreen.tsx](../../src/ui/frontend/src/components/PipelineScreen.tsx)

Update the existing `applyCompanyPreset()` function (line 222-230) to use the enriched prompt template:

```typescript
function applyCompanyPreset() {
    const name = presetCompany.trim();
    if (!name) return;
    setRequest(
      `Please conduct a comprehensive and indepth data extraction of ${name}, covering all of the following dimensions:\n` +
      `NOTE: Do your best to find all of them.\n\n` +
      `1. Basic Company Information: Legal structure, registered address and operational headquarters, establishment history, shareholding structure and stock symbol;\n\n` +
      `2. Personnel and Organization: Board members, executive committee, and senior executives (including CEO, CEOs of various business divisions, CFO, etc.), total number of employees worldwide and the educational and professional backgrounds of all individuals;\n\n` +
      `3. Business and Products: All business areas and delivery data;\n\n` +
      `4. Finance and Funding: Revenue and profit data, funding history and major investors;\n\n` +
      `5. Technology and Innovation: Core technology directions and digital transformation;\n\n` +
      `6. Global Presence: Main operational locations and international layout;\n\n` +
      `7. Contact Information: Official company & individuals phone number and website.`,
    );
    setFullRemerge(true);
}
```

Also update the card title and hint text to reflect the enriched prompt:
- Title: "Comprehensive company extraction"
- Hint: "Type a company name to generate a detailed extraction request covering company info, personnel, products, financials, technology, locations, and contacts."

---

### Component 6: Test Updates

---

#### [MODIFY] [test_parse_request_node.py](../../tests/unit/test_parse_request_node.py)

1. Update test fixtures to include `schemas` in the state dict (since `parse_request` now reads it).
2. Update expected outputs to include `entity_tables` key.
3. Verify that schema info is passed to the LLM call.

---

#### [MODIFY] [test_analyze_dependencies_node.py](../../tests/unit/test_analyze_dependencies_node.py)

1. Remove tests for `select_entity_tables_impl` integration (it's no longer called from this node).
2. Add tests for `_build_coverage_matrix` function.
3. Add test for validation + retry logic when LLM returns `{}`.
4. Add test for fallback to coverage-matrix-only results.

---

#### [MODIFY] [test_graph_workflow.py](../../tests/integration/test_graph_workflow.py)

1. Update the expected node ordering: `discover_schemas` before `parse_request`.
2. Verify `NODE_SUCCESSOR` reflects the new graph topology.

---

### Component 7: TODO.md Update

---

#### [MODIFY] [TODO.md](../../TODO.md)

Add a new Phase section documenting these changes with checkbox tracking.

---

### Component 8: Remediation — Complete Dependency Tree Key Validation

---

#### [MODIFY] [analyze_dependencies.py](../../src/orchestrator/nodes/analyze_dependencies.py)

Update `_parse_llm_tree` helper to validate that EVERY target entity is present as a key in `raw_tree`:

```python
def _parse_llm_tree(res: Any) -> dict[str, list[str]] | None:
    if not isinstance(res, dict) or not res:
        return None
    raw_tree = res.get("dependency_tree")
    if not isinstance(raw_tree, dict) or not raw_tree:
        return None
    # Ensure every target entity was explicitly returned as a key by the LLM.
    # If the LLM returns a partial tree omitting any entity, reject it so
    # the retry and programmatic coverage matrix fallback trigger.
    if any(e not in raw_tree for e in target_entities):
        return None
    tree = {}
    for e in target_entities:
        deps = raw_tree.get(e, [])
        tree[e] = list(deps) if isinstance(deps, list) else []
    return tree
```

---

#### [MODIFY] [test_analyze_dependencies_node.py](../../tests/unit/test_analyze_dependencies_node.py)

Add unit test `test_partial_dependency_tree_omitting_entities_retries_and_falls_back()` to verify that an LLM output omitting one or more `target_entities` triggers the retry mechanism and falls back to the coverage matrix.


---

## Verification Plan

### Automated Tests
```bash
python -m pytest tests/unit/test_parse_request_node.py -v
python -m pytest tests/unit/test_analyze_dependencies_node.py -v
python -m pytest tests/unit/test_discover_schemas_node.py -v
python -m pytest tests/integration/test_graph_workflow.py -v
python -m pytest tests/unit/test_graph_timing.py -v
python scripts/check.py all
```

### Manual Verification
- User runs the app with the enriched prompt template against their ClickHouse databases to verify:
  1. `discover_schemas` runs before `parse_request` in the pipeline
  2. `parse_request` returns schema-grounded entity types and table mappings
  3. `analyze_dependencies` produces a non-empty dependency tree with coverage matrix
  4. The "Build request" button in the UI generates the enriched prompt with the company name
