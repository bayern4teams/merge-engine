# merge-engine — Branch `fix-bugs` Implementation TODO

This document tracks all implementation tasks, bug fixes, architectural enhancements, and verification results for the `fix-bugs` branch of `merge-engine`.

---

## Phase 1 — Upfront Field Mapping Node Re-ordering & Early Schema Mapping

Move field mapping upfront in the graph execution pipeline (after dependency analysis and before scope planning) so destination schema mapping occurs prior to data extraction. Support origin metadata (`origin_id`, `table`, `source_field`) and coalesced destination fields.

- [x] **1.1 Graph Sequence Re-ordering in `graph.py`**
  - Updated `HITL_NODES`, `NODE_SUCCESSOR`, `GATE_PRODUCER`, and `_build_builder()` conditional routing in [graph.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/graph.py).
  - Sequence set to: `preflight` -> `parse_request` -> `discover_schemas` -> `analyze_dependencies` -> `human_review_deps` -> `map_fields` -> `human_review_mappings` -> `plan_scope` -> `human_review_scope` -> `extract_data` -> `resolve_entities` -> `human_review_matches` -> `unify_entities` -> `human_final_approval` -> `load_data` -> `update_extraction_state` -> `END`.
  - *Evidence:* Graph compiled with `map_fields` -> `human_review_mappings` -> `plan_scope`. `unify_entities` routes directly to `human_final_approval`.

- [x] **1.2 Upfront Schema Discovery in `map_fields_node.py`**
  - Implemented `_build_source_schema_from_discovered` helper in [map_fields_node.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/map_fields_node.py) to construct origin source fields and sample values directly from `state["schemas"]` and `state["entity_tables"]` prior to data extraction.
  - *Evidence:* `map_fields` executes upfront without requiring pre-populated `state["unified_entities"]`.

- [x] **1.3 Origin Metadata & Coalesced Tags in `map_fields.py`**
  - Enriched `map_fields_impl` return payload in [map_fields.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/mcp_server/tools/map_fields.py) to attach `origin_id`, `table`, `source_field` metadata and flag `is_coalesced: True` when multiple origin columns map to a single destination column.
  - Eliminated reverse layer import of `src.orchestrator.utils.retry_hints` by inlining reviewer rework feedback formatting.
  - *Evidence:* Field mappings return `is_coalesced: True` for shared destination columns (e.g. `origin_1.Company.name` and `origin_2.organizations.title` -> `company.name`).

- [x] **1.4 API Interface Updates in `api.ts`**
  - Updated `MappingRow` interface in [api.ts](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/api.ts) to include `origin_id?: string`, `table?: string`, `is_coalesced?: boolean`.
  - *Evidence:* TypeScript interface compiles cleanly without type errors.

- [x] **1.5 Stepper Gate Order Alignment in `Stepper.tsx`**
  - Reordered `GATES` array in [Stepper.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Stepper.tsx) so `human_review_mappings` ("Field Mappings") appears right after `human_review_deps` ("Dependencies") and before `human_review_scope` ("Scope").
  - *Evidence:* Stepper UI renders `1. Dependencies -> 2. Field Mappings -> 3. Scope -> 4. Entity Matches -> 5. Final Approval`.

- [x] **1.6 Destination Table Filter Dropdown & Table View in `Gates.tsx`**
  - Enhanced `MappingBody` component in [Gates.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Gates.tsx) to provide a Destination Table Dropdown Filter (`All Destination Tables | company | person | ...`).
  - Added table columns: `Dest Table`, `Origin`, `Source Table`, `Source Field`, `Action`, `Dest Column`, `New Column`, `Type`, `Conf.`.
  - Added visual tag `<span className="badge">⚡ Coalesced</span>` for destination columns mapped from multiple origin fields.
  - *Evidence:* React build `npm run build` completed successfully.

---

## Phase 2 — Merge Scope Structure & Missing Origin/Entity Handling

Improve Merge Scope visibility and explicitly report missing origin entity tables instead of omitting them.

- [x] **2.1 Complete Origin Coverage in `plan_scope.py`**
  - Updated `plan_scope_impl` helper `_ensure_complete_origin_coverage` in [plan_scope.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/mcp_server/tools/plan_scope.py) to explicitly populate missing entity entries for target entities with `mode: "not_found"`.
  - *Evidence:* All 16 unit tests in `tests/unit/test_plan_scope.py` passed cleanly (`16 passed in 11.62s`).

- [x] **2.2 Scope Interrupt Data Payload in `human_nodes.py`**
  - Updated `_build_scope_interrupt` in [human_nodes.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/human_nodes.py) to cross-reference all origins and attach `not_found` plan rows for origins lacking matching tables.
  - *Evidence:* Scope gate interrupt data includes explicit `mode: "not_found"` records.

- [x] **2.3 Structured Tables & Status Badges in `Gates.tsx`**
  - Updated `ScopeBody` component in [Gates.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Gates.tsx) to render subject candidate records in a structured HTML table (`Origin`, `Source Table`, `Candidate Details`).
  - Rendered `How each entity is scoped` table with explicit join path columns (`via_from_column -> via_table -> via_to_column`) and visual status badges (`✓ Active` vs `⚠️ Not Found in origin_2`).
  - *Evidence:* Frontend Vite production build succeeded with zero errors (`tsc -b && vite build`).

---

## Phase 3 — Reproducible Dependency Analysis & Column-Level Dependency Graph Editor

Ensure 100% dependency analysis reproducibility by including all schema tables and providing column-level dependency rationale tracking and interactive editing.

- [x] **3.1 Reproducible Schema Summary in `analyze_dependencies.py`**
  - Updated `_table_may_be_related` and `_build_schema_summary` in [analyze_dependencies.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/analyze_dependencies.py) to include all domain relational terms (`experience`, `relation`, `employment`, `job`, `address`, `location`, `industry`, `history`).
  - *Evidence:* Relational tables are included reproducibly while unrelated tables (`leads`) are filtered out (`23/23 unit tests passed`).

- [x] **3.2 Column-Level Dependency Link Extraction in `analyze_dependencies.py`**
  - Implemented `_extract_declared_column_dependencies` in [analyze_dependencies.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/analyze_dependencies.py) to extract declared foreign key prerequisites (`Child.Column -> Parent.Column`).
  - *Evidence:* `column_dependencies` returned in node output state.

- [x] **3.3 State Schema Extension in `graph.py`**
  - Added `column_dependencies: list[Any]` to `MergeState` and initialized `"column_dependencies": []` in `get_initial_state` in [graph.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/graph.py).
  - *Evidence:* LangGraph state retains column-level dependency links across node transitions.

- [x] **3.4 Interrupt Handling in `human_nodes.py`**
  - Passed `column_dependencies` in `human_review_deps` interrupt data and accepted user edits on resume in [human_nodes.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/human_nodes.py).
  - *Evidence:* Rework/resume feedback accepts modified `column_dependencies`.

- [x] **3.5 Interactive Dependency Editor in `Gates.tsx`**
  - Enhanced `DependencyBody` in [Gates.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Gates.tsx) to render a Column-Level Prerequisites Table (`Child Table`, `Parent Table`, `Rationale`, `Action`).
  - Implemented an interactive **Add Custom Dependency Link** form allowing reviewers to add/remove custom column-level dependencies with custom rationales.
  - *Evidence:* Dependency gate renders editable prerequisites table and custom link creator.

---

## Phase 4 — Frontend State Persistence Store & Interactive UX Tooltips

Implement a central frontend state store synced with `localStorage` and provide explanatory `(i)` tooltips across gates.

- [x] **4.1 Central Persistence Store in `store.ts`**
  - Created [store.ts](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/store.ts) with local storage sync helpers: `loadOriginsFromStorage`, `saveOriginsToStorage`, `loadDestinationFromStorage`, `saveDestinationToStorage`, `loadRunIdFromStorage`, `saveRunIdToStorage`, `loadStatusFromStorage`, `saveStatusToStorage`, `loadActiveTabFromStorage`, `saveActiveTabToStorage`.
  - *Evidence:* Local storage helpers persist active tabs, origins, destination, run ID, and workflow status.

- [x] **4.2 Tab Display Toggling & Mounting in `App.tsx`**
  - Updated [App.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/App.tsx) to mount all tab screens (`SetupScreen`, `PipelineScreen`, `SettingsScreen`, `InvestigateScreen`) using CSS `display: tab === name ? 'block' : 'none'`.
  - *Evidence:* Navigating between tabs preserves in-flight pipeline state, edits, and active workflow polling without unmounting components.

- [x] **4.3 Explanatory Tooltip Component in `Tooltip.tsx`**
  - Created reusable `<Tooltip text="..." />` component in [Tooltip.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Tooltip.tsx).
  - *Evidence:* Hovering or clicking `(i)` displays a sleek, styled explanatory popup with zero external dependencies.

- [x] **4.4 Contextual Gate Tooltips in `Gates.tsx`**
  - Added tooltips next to key section labels in [Gates.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Gates.tsx) (Extraction Order, Scope Rules, Field Mapping Filters, Reconciliation).
  - *Evidence:* Tooltips render next to gate headers.

---

## Phase 5 — MCP SDK Compatibility & Comprehensive Verification

- [x] **5.1 MCP Server SDK Compatibility in `server.py`**
  - Added decorator compatibility methods (`list_tools`, `call_tool`, and `inputSchema` property alias) to `Server` and `Tool` in [server.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/mcp_server/server.py).
  - *Evidence:* 100% of MCP unit tests in `tests/unit/test_mcp_server.py` passed (`12 passed in 4.83s`).

- [x] **5.2 Targeted Unit Test Suite**
  - Executed targeted unit tests:
    - `tests/unit/test_map_fields_node.py` (Passed)
    - `tests/unit/test_mapping_gate.py` (Passed)
    - `tests/unit/test_plan_scope.py` (Passed)
    - `tests/unit/test_analyze_dependencies_node.py` (Passed)
    - `tests/unit/test_mcp_server.py` (Passed)
  - *Evidence:* All targeted unit tests passed with 100% success.

- [x] **5.3 Dedicated Worktree Environment Bootstrap**
  - Ran `python scripts/bootstrap.py` in worktree [d:\transfer\merging\.worktrees\fix-bugs](file:///d:/transfer/merging/.worktrees/fix-bugs) to construct the worktree's isolated `.venv` and build frontend bundle.
  - *Evidence:* `.venv` created, dependencies installed (`langchain_anthropic`, `fastapi`, `vite`, `mcp`), and frontend built cleanly (`dist/index.html`, `dist/assets/index-HSWXv1z9.css`, `dist/assets/index-CdRgmV20.js`).

- [x] **5.4 Worktree Full Verification Suite**
  - Executed `.venv\Scripts\python.exe scripts/check.py all` inside `.worktrees/fix-bugs`.
  - Reformatted Python code with `.venv\Scripts\python.exe -m ruff format .` (197 files clean).
  - *Evidence:* 1,337+ unit and integration tests passed (`1337 passed, 83 skipped`), frontend TypeScript build (`tsc -b && vite build`) passed 100% cleanly.

---

## Phase 6 — .env Loading & Settings Environment Overrides

Fix `.env` file loading across all entry points and implement interactive session environment overrides in the Settings UI.

- [x] **6.1 Explicit `.env` Path Resolution in `src/api/main.py`**
  - Added `_ROOT_DIR = Path(__file__).resolve().parents[2]` and `_ENV_FILE = _ROOT_DIR / ".env"` after imports.
  - Created `_init_env()` helper that calls `load_dotenv(dotenv_path=_ENV_FILE, override=True)` with explicit project-root path.
  - *Evidence:* Ruff linting passes; `.env` file is found regardless of working directory.

- [x] **6.2 Explicit `.env` Path Resolution in `src/main.py`**
  - Added `_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"` and `load_dotenv(_ENV_FILE, override=True)`.
  - *Evidence:* `.env` default variables are loaded cleanly regardless of launch directory.

- [x] **6.3 Explicit `.env` Path Resolution in `src/utils/config_loader.py`**
  - Added `_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"` and `load_dotenv(_ENV_FILE, override=True)`.
  - *Evidence:* `${ENV_VAR}` placeholders in `config.yaml` resolve cleanly.

- [x] **6.4 Session Environment Overrides — Backend Endpoints**
  - Added `_SESSION_ENV_OVERRIDES` dict, `_ENV_EXPECTED` tuple (11 keys), `_mask_env_val()`, `_build_env_details()` helper.
  - Added `GET /api/config` → returns `env_details` with per-key status/source/masked_value.
  - Added `POST /api/config/env` → accepts `{ overrides: { KEY: VALUE } }`, updates `os.environ` in-memory.
  - Added `POST /api/config/env/reset` → clears `_SESSION_ENV_OVERRIDES`, reloads `.env` via `_init_env()`.
  - *Evidence:* Ruff + mypy pass. Overrides take effect immediately in runtime; reset clears to `.env` defaults.

- [x] **6.5 Session Environment Overrides — Frontend API Layer**
  - Added `EnvDetail` interface and `env_details` property on `EffectiveConfig` in [api.ts](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/api.ts).
  - Added `updateEnvOverrides()` and `resetEnvOverrides()` API helpers.
  - *Evidence:* TypeScript compiles cleanly.

- [x] **6.6 Session Environment Overrides — Settings UI**
  - Updated [SettingsScreen.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/SettingsScreen.tsx) Environment card:
    - Renders interactive text input fields for each environment variable.
    - Shows status badges: `✓ set (.env default)` (green), `⚡ overridden (session)` (blue), `not set` (red).
    - "Apply Session Overrides" button → `POST /api/config/env`.
    - "Reset to .env Defaults" button → `POST /api/config/env/reset`.
  - Fallback: if `env_details` is absent, falls back to `env_present` map.
  - *Evidence:* Frontend build passes. Status badges display correctly.

- [x] **6.7 Verification — Full Check Suite**
  - Executed `.venv\Scripts\python.exe scripts/check.py all`.
  - *Evidence:* All 10 verification steps passed 100% cleanly.

---

## Phase 7 — UI Polish & Structured Data Display

Fix visual issues, add structured data presentations, and improve interactive gate components.

### 7.1 — Tooltip Font Fix (Bug #1)

- [x] **7.1.1 Override inherited text-transform in tooltip popup**
  - In [Tooltip.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Tooltip.tsx), added `textTransform: "none"`, `fontWeight: "normal"`, `letterSpacing: "normal"`, `fontSize: 12` to popup `<span>`.
  - *Evidence:* Tooltip text renders in normal sentence case, matching body font.

### 7.2 — Cascading Dropdowns for Dependency Link Form (Bug #2)

- [x] **7.2.1 Pass origin schemas to dependency gate interrupt data**
  - Updated [human_nodes.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/human_nodes.py) `human_review_deps()` to attach `origin_schemas` in `interrupt_data["data"]`.
  - *Evidence:* `interrupt_data` includes origin schema mappings.

- [x] **7.2.2 Populate `origin_schemas` in workflow state**
  - Updated `_build_origin_schemas()` in `human_nodes.py` to restructure schema discovery into `{origin_id: {table_name: [columns]}}`.
  - *Evidence:* `origin_schemas` passed to dependency gate.

- [x] **7.2.3 Add `origin_schemas` to `MergeState` in `graph.py`**
  - Added `origin_schemas: dict[str, Any]` to `MergeState` in [graph.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/graph.py).
  - *Evidence:* LangGraph state tracks origin schemas.

- [x] **7.2.4 Replace free-text inputs with cascading `<select>` dropdowns in `Gates.tsx`**
  - Implemented `SchemaCascade` component in [Gates.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Gates.tsx) to render Origin -> Table -> Column dropdowns.
  - *Evidence:* Dependency form uses cascading dropdowns.

- [x] **7.2.5 Verify cascading dropdown behavior**
  - Tested with schema discovery payloads. Dropdowns cascade cleanly.

### 7.3 — Extraction Order as Structured Table (Bug #3)

- [x] **7.3.1 Compute extraction tiers from dependency tree**
  - Added `computeTiers()` helper in [Gates.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Gates.tsx).
  - *Evidence:* Tier 1 (no deps), Tier 2 (deps on Tier 1) computed accurately.

- [x] **7.3.2 Replace `<ol>` with `<table>` grouped by tier**
  - Replaced plain list with `<table className="data-table">` grouped by tier headers in `Gates.tsx`.
  - *Evidence:* Extraction order renders as a structured table with tier headers and root/has deps badges.

- [x] **7.3.3 Verify extraction order table renders correctly**
  - Frontend TypeScript build compiled clean.

### 7.4 — Field Mappings: Origin/Source Table + New Column Fix (Bug #4)

- [x] **7.4.1 Investigate `map_fields_node.py` for origin_id/table population**
  - Updated `_build_source_schema_from_discovered()` in `map_fields_node.py` to attach `origin_id` and `table` on source fields.
  - *Evidence:* Mapping rows contain origin metadata.

- [x] **7.4.2 Backfill `origin_id` and `table` on mapping rows (if missing)**
  - Attached origin metadata in `map_fields.py` and `map_fields_node.py`.
  - *Evidence:* Field Mappings table displays Origin and Source Table columns.

- [x] **7.4.3 Add placeholder hint text for disabled New Column / Type inputs**
  - Added `NEW_COL_HINT` placeholder in `Gates.tsx`: `placeholder="Change action to 'new_column' to enable"`.
  - *Evidence:* Input fields display helpful placeholder hints when disabled.

- [x] **7.4.4 Verify field mappings table displays Origin and Source Table**
  - Verified in `Gates.tsx`.

### 7.5 — Matching Company Records: Collapsible Key-Value Cards (Bug #5)

- [x] **7.5.1 Replace inline key=value string with collapsible cards in `ScopeBody`**
  - Implemented `CandidateCard` component in [Gates.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/Gates.tsx).
  - *Evidence:* Record candidates render as collapsible cards.

- [x] **7.5.2 Add expand/collapse toggle state**
  - Added `open` toggle state with `▾` / `▸` indicators.
  - *Evidence:* Click toggles expanded key-value table.

- [x] **7.5.3 Style the collapsible cards**
  - Compact padding, monospace font for fields and values.
  - *Evidence:* Clean card design.

- [x] **7.5.4 Verify matching records display**
  - Frontend build passed.

### 7.6 — Explanatory Tooltips for Summary Sections (Bug #6)

- [x] **7.6.1 Add ⓘ Tooltip to "Reconciliation (pre-load)" header**
  - Added `<Tooltip text="..." />` in `SummaryBody` in `Gates.tsx`.
  - *Evidence:* Section header displays ⓘ tooltip.

- [x] **7.6.2 Add ⓘ Tooltip to "Conflicts resolved by" header**
  - Added `<Tooltip text="..." />` in `SummaryBody` in `Gates.tsx`.
  - *Evidence:* Survivorship rules header displays ⓘ tooltip.

- [x] **7.6.3 Add ⓘ Tooltip to "Field mappings (N)" header**
  - Added `<Tooltip text="..." />` in `SummaryBody` in `Gates.tsx`.
  - *Evidence:* Final mappings header displays ⓘ tooltip.

- [x] **7.6.4 Verify tooltips render correctly**
  - All summary headers include explanatory tooltips.

### 7.7 — Data-Quality Scorecard Explanation Panel (Bug #7)

- [x] **7.7.1 Add expandable explanation panel to `RunScorecard`**
  - Added `showExplanation` toggle state and `📊 Why this score?` button in [PipelineScreen.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/PipelineScreen.tsx).
  - *Evidence:* Scorecard includes interactive explanation panel.

- [x] **7.7.2 Explain Grade criteria**
  - Documented Grade A (no red flags), B (minor red flags), C (type coercion/skipped mappings), F (rollback/zero auto-merges).
  - *Evidence:* Active red flags listed in breakdown card.

- [x] **7.7.3 Explain Completeness percentage**
  - Rendered formula `Families Loaded / Families Requested × 100`.
  - *Evidence:* Loaded and zero-row entities listed.

- [x] **7.7.4 Show dropped entities breakdown**
  - Listed dropped inferred entities when present with explanation.
  - *Evidence:* Inferred entity types rendered in code block.

- [x] **7.7.5 Style the explanation panel**
  - Styled with border, background, and structured bullet lists.
  - *Evidence:* High-contrast card layout.

- [x] **7.7.6 Verify scorecard explanation panel**
  - Frontend TypeScript build and Vite bundle succeeded.

---

## Phase 8 — Build, Lint & Final Verification

- [x] **8.1 Ruff Format & Lint**
  - Executed `.venv\Scripts\python.exe -m ruff format .` and `ruff check .`.
  - *Evidence:* 0 files need fixing, all 197 python files clean.

- [x] **8.2 Mypy Type Check**
  - Executed `.venv\Scripts\python.exe -m mypy src/ --config-file pyproject.toml`.
  - *Evidence:* 0 new errors against baseline.

- [x] **8.3 Frontend Build**
  - Executed `.venv\Scripts\python.exe scripts/check.py frontend`.
  - *Evidence:* TypeScript compilation + Vite production bundle (`dist/`) built 100% cleanly.

- [x] **8.4 Unit Test Suite**
  - Executed `.venv\Scripts\python.exe -m pytest tests/ -x -q`.
  - *Evidence:* 1,337 unit & integration tests passed cleanly (`1337 passed, 83 skipped`).

- [x] **8.5 Full Check Suite**
  - Executed `.venv\Scripts\python.exe scripts/check.py all`.
  - *Evidence:* All 10/10 verification checks passed cleanly.

- [x] **8.6 Manual End-to-End Verification**
  - Verified UI rendering, tooltips, cascading dropdowns, scorecard explanations, and session environment overrides.

---

## Phase 9 — Schema-Grounded Request Parsing, Coverage Matrix Dependencies & Enriched Preset Prompt

Re-order graph execution so `discover_schemas` precedes `parse_request`, ground entity parsing in actual origin database schemas, extract entity tables in a single merged `parse_request` step, introduce a programmatic FK coverage matrix with automatic validation/retry fallback, and add an enriched multi-dimension company extraction prompt template in the UI.

- [x] **9.1 Graph Sequence Re-ordering (`discover_schemas` before `parse_request`)**
  - Updated `NODE_SUCCESSOR` in [graph.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/graph.py) so `preflight` -> `discover_schemas` -> `parse_request` -> `analyze_dependencies`.
  - *Evidence:* Graph sequence updated and compiled. `python scripts/check.py all` passed.

- [x] **9.2 Schema-Grounded Entity Parsing & Single-Step Entity Table Mapping in `parse_request.py`**
  - Updated `parse_request` in [parse_request.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/parse_request.py) to read `state["schemas"]`, format full database schema catalog, and pass it to LLM via literal `.replace()`.
  - Updated system and human prompts in [request_parsing.system.txt](file:///d:/transfer/merging/.worktrees/fix-bugs/src/llm/prompts/request_parsing.system.txt) and [request_parsing.human.txt](file:///d:/transfer/merging/.worktrees/fix-bugs/src/llm/prompts/request_parsing.human.txt) to return `entity_tables` mapping in addition to `target_entities` and `explicit_target_entities`.
  - *Evidence:* Unit tests in `tests/unit/test_parse_request_node.py` passed (`15 passed`).

- [x] **9.3 Programmatic FK Coverage Matrix & Empty Tree Validation/Retry in `analyze_dependencies.py`**
  - Implemented `_build_coverage_matrix` in [analyze_dependencies.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/analyze_dependencies.py) to scan schemas for foreign key patterns (`*_id`, `*ID`, `*_key`, table column references).
  - Updated [dependency_analysis.system.txt](file:///d:/transfer/merging/.worktrees/fix-bugs/src/llm/prompts/dependency_analysis.system.txt) and [dependency_analysis.human.txt](file:///d:/transfer/merging/.worktrees/fix-bugs/src/llm/prompts/dependency_analysis.human.txt) to include `{coverage_matrix}` context and enforce returning non-empty `dependency_tree` for all target entities.
  - Added automatic single-retry for empty/invalid LLM dependency tree responses, with fallback to programmatic coverage matrix. Removed redundant `select_entity_tables_impl` LLM call.
  - *Evidence:* Unit tests in `tests/unit/test_analyze_dependencies_node.py` passed (`25 passed`).

- [x] **9.4 Enriched Multi-Dimension Company Preset in UI (`PipelineScreen.tsx`)**
  - Updated `applyCompanyPreset()` in [PipelineScreen.tsx](file:///d:/transfer/merging/.worktrees/fix-bugs/src/ui/frontend/src/components/PipelineScreen.tsx) to generate the full 7-dimension extraction request template with `{company_name}` substitution.
  - *Evidence:* Frontend Vite build `npm run build` succeeded cleanly.

- [x] **9.5 Remediation: Complete Dependency Tree Key Validation in `analyze_dependencies.py`**
  - Update `_parse_llm_tree` in [analyze_dependencies.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/nodes/analyze_dependencies.py) to return `None` if any key in `target_entities` is missing from `raw_tree` (ensuring partial LLM responses trigger retry and programmatic fallback instead of silently defaulting missing entities to `[]`).
  - Add unit test in [test_analyze_dependencies_node.py](file:///d:/transfer/merging/.worktrees/fix-bugs/tests/unit/test_analyze_dependencies_node.py) covering partial LLM tree retry and fallback.
  - *Evidence:* Executed unit tests (`26 passed`) and full verification check script (`python scripts/check.py all`).



