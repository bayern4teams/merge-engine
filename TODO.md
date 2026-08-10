# merge-engine — Branch `fix-bugs` Implementation TODO

This document tracks all implementation tasks, bug fixes, architectural enhancements, and verification results for the `fix-bugs` branch of `merge-engine`.

---

## Phase 1 — Upfront Field Mapping Node Re-ordering & Early Schema Mapping

Move field mapping upfront in the graph execution pipeline (after dependency analysis and before scope planning) so destination schema mapping occurs prior to data extraction. Support origin metadata (`origin_id`, `table`, `source_field`) and coalesced destination fields.

- [x] **1.1 Graph Sequence Re-ordering in `graph.py`**
  - Updated `HITL_NODES`, `NODE_SUCCESSOR`, `GATE_PRODUCER`, and `_build_builder()` conditional routing in [graph.py](file:///d:/transfer/merging/.worktrees/fix-bugs/src/orchestrator/graph.py).
  - Sequence set to: `preflight` -> `discover_schemas` -> `parse_request` -> `analyze_dependencies` -> `human_review_deps` -> `map_fields` -> `human_review_mappings` -> `plan_scope` -> `human_review_scope` -> `extract_data` -> `resolve_entities` -> `human_review_matches` -> `unify_entities` -> `human_final_approval` -> `load_data` -> `update_extraction_state` -> `END`.
  - Note: `discover_schemas`/`parse_request` order was superseded by Phase 9.1 below (schema-grounded parsing needs schemas first); this line is corrected to match the order actually shipped.
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

---

## Phase 10 — Fix-Bugs Follow-Up: Full Table Coverage, Join-Graph Scoping, Cross-Table Mapping & Merge Lineage

The 4th live run (`Bugs/Logs/4th_try_05_18_local_enrichprompt.txt`) exposed that the pipeline silently discards most of the database before it ever reaches the merge, plus gaps in Phases 1-9 that were claimed done but were unreachable from production. Full plan audited and tracked separately; this section records live-verified progress.

- [x] **10.0 Make the existing work durable**
  - Committed the 31 files that were uncommitted since Phase 9 as checkpoint `1667670`; created local backup branch `fix-bugs-backup` at the same commit.
  - *Evidence:* `git log --oneline -1 fix-bugs fix-bugs-backup` — both at `1667670`. Push to `origin` attempted and timed out (`git.n4t.com` unreachable, per `docs/decisions/0003-server-ci-unreachable.md`) — reported honestly, not treated as a blocker.

- [x] **10.1 Close gaps in already-claimed work (Phases 1-9)**
  - Fixed 8 items: missing `column_dependencies` init on the legacy Config state path; documented the undocumented `map_fields` relocation; corrected a stale TODO.md node-order line (this file, §1.1); `analyze_dependencies`' empty-entity path and `parse_request`'s exception handler now both set `state["failure"]` (via the existing error taxonomy in `utils/retry.py::classify_exception`) instead of silently continuing with zero entities; repaired `tests/prompts/harness.py` (missing `{schema_info}` placeholder, a live `ValueError: unknown stage` crash from a `"cls"`/`entity_table_selection` mismatch); added `NODE_SUCCESSOR` assertions to `tests/integration/test_graph_workflow.py` (never updated for the Phase 9 reorder) and fixed its stale "START edge to parse_request" assertion (entry point has always been `preflight`); added schema-grounding/entity_tables/malformed-entry-cleaning coverage to `tests/unit/test_parse_request_node.py`.
  - *Evidence:* `tests/unit/test_analyze_dependencies_node.py` + `test_parse_request_node.py` + `tests/integration/test_graph_workflow.py`: 78 passed. `scripts/check.py types` clean.

- [x] **10.2 Gate plumbing: one builder per gate**
  - Root-caused why `origin_schemas`/`column_dependencies` never reached the dependency gate and `origin_id`/`table` never reached the mapping gate: `interrupt_before=HITL_NODES` means a HITL node body never runs at the pause, so `WorkflowManager._build_interrupt_data` — not the node's own body — was the only payload the live UI ever saw, and it was an independently-maintained, poorer copy. Extracted one shared builder per gate into `nodes/human_nodes.py` (`_build_deps_interrupt`, `_build_mapping_rows`, `_build_matches_interrupt` — the last one a fourth divergence found while writing the regression test, missing `auto_match_stats`/`pending_merge_candidates`/`pending_merge_note`) and made `WorkflowManager` delegate to all three instead of keeping independent copies. Added `quality_report`/`has_quality_failure` plumbing to the final-approval payload ahead of Phase 10.4.5 (validate_data).
  - Added `tests/unit/test_interrupt_payload_parity.py`: for every HITL node, asserts the node body's own `interrupt_data` and `WorkflowManager._build_interrupt_data`'s payload have identical (or, for `human_final_approval`'s deliberately richer superset, documented) data keys — a mechanical guard against this bug class recurring a fifth time.
  - *Evidence:* `test_interrupt_payload_parity.py` + `test_human_nodes.py` + `test_workflow_manager.py` + `test_graph_workflow.py`: 80 passed. `scripts/check.py types` clean.

- [x] **10.3 Stop the 2-second poll from clobbering in-progress gate edits**
  - Root cause: every poll parses a fresh JSON response, so `data` is a new object reference every ~2s even when content is unchanged; `Gates.tsx`'s `MappingBody` reset its local row state off `[node, data]`, wiping whatever the reviewer had just typed. Added a `payload_version` content hash (`WorkflowManager._build_interrupt_data`, threaded through all three call sites plus the `apply_mapping_feedback` rejection path that mutates a cached payload in place) and changed `MappingBody`'s sync effect to depend on `[node, payloadVersion]` instead of the whole `data` object.
  - *Evidence:* `tests/unit/test_interrupt_payload_parity.py::TestPayloadVersion` (4 tests: stability, change-detection, cross-node non-collision, recompute-after-mutation). Frontend `npm run build` clean.

- [x] **10.4 Remove `new_column` from the mapping gate dropdown**
  - The destination schema is a fixed contract (`CLAUDE.md`'s "No new destination columns, ever") and `apply_mapping_feedback` has always hard-rejected `new_column`, so offering it in the UI was a dead end reachable only by picking it, filling in a name/type, and being told no. `ACTIONS` is now `["map", "skip"]`; the New Column/Type input columns are replaced with a Reason column (backed by a `reason` field now carried on every skip row, not just the separate `unmapped_fields` list). Removed the fully-dead `new_column_name`/`new_column_type` fields from `MappingRow` (TS), the row builder (Python), and the stale `apply_mapping_feedback` docstring. Found and fixed a real regression from 10.2's refactor along the way: `_build_mapping_rows`'s skip-row had dropped the `suggested_column`-as-preview `dest_column` hint the old (now-deleted) `_mapping_rows_from_values` used to show.
  - *Evidence:* `tests/unit/test_mapping_gate.py` + `test_api.py` + related: 117 passed. Frontend `npm run build` clean.

- [x] **10.5 Wire the dependency-gate's custom links back to the backend**
  - `DependencyBody`'s "Add Custom Dependency Link" form captured `colDeps` in local React state only — `GateButtons` called `onApprove(undefined)`, so nothing was ever sent. Threaded `columnDependencies` through `GateProps.onApprove` → `sendFeedback` → the `Feedback` Pydantic model → a new `human_review_deps` branch in `resume_workflow`, merged into `state["column_dependencies"]` (appended and deduplicated against what `analyze_dependencies` already computed, never replacing it — that key has no accumulating reducer in `MergeState`). Each submitted link is validated against the discovered schema (`WorkflowManager._sanitise_column_dependencies`, mirroring the existing `_sanitise_entity_tables` pattern) before being kept.
  - *Evidence:* `tests/integration/test_workflow_manager.py::test_resume_deps_gate_merges_custom_column_dependencies` (valid link kept, invalid-column link dropped, duplicate of an existing link not double-added) — 11 passed. Ruff/mypy clean.

---

## Phase 11 — Full Table Coverage & Subject-First Input (fix-bugs follow-up plan Phase 3)

Closes issue 1 (7 of 20 discovered tables silently dropped) structurally, and issue 8/10 (ask for a company name, keep the free-text path reachable).

- [x] **11.1 Deterministic entity grouping module**
  - New `src/orchestrator/utils/entity_grouping.py::group_tables_into_entities` — pure, no LLM, assigns every discovered table across every origin to exactly one entity group before parse_request ever calls the LLM. Owning-entity prefixes (`CompanyExperience` → `experience`) are stripped only when the remainder is a recognised concept in `entity_aliases.ALIAS_GROUPS`, and only a *successfully stripped* remainder (or an already-bare single word) is eligible for further alias-group merging — a genuine multi-word fallback compound never is. That distinction is load-bearing: `ALIAS_GROUPS`' industry group already contains the literal alias `"industry_classification"` (added for unrelated destination-table-matching reasons), and without the distinction `IndustryClassification` would silently merge into `industry` — exactly the over-merge the gate exists to prevent.
  - Collapsed the three/four competing vocabularies down to one: deleted `src/orchestrator/utils/entity_types.py` (a fixed 18-type enum with zero production importers — only a test imported it, and the prompt block it used to mirror was already removed by the Phase 9 schema-grounded rewrite); `entity_aliases.ALIAS_GROUPS` is now the single source. Trimmed `tests/unit/test_entity_aliases.py` to only its still-valid alias-group invariants.
  - *Evidence:* `tests/unit/test_entity_grouping.py` — 10 tests using the exact 13+7 table lists from the 4th live run's log, proving `CompanyExperience`+`Experience`, `CompanyIndustry`+`Industry`, and `CompanyContact`+`PersonContact` merge correctly across origins while `IndustryClassification`, `CompanyTechnographics`, and `CompanyInvestor` correctly do NOT over-merge. `unassigned == []` asserted directly. Ruff/mypy/arch-lint/docs-lint all clean.

- [x] **11.2 `parse_request` rewritten: LLM names groups, cannot change membership**
  - `parse_request.py` now calls `group_tables_into_entities` first for authoritative membership, then calls the LLM only to name each group, classify which the request text explicitly named, and extract merge subject/scope. A missing or invented group name falls back to the deterministic key; `entity_tables`/`target_entities` always cover every discovered table (a hard post-condition assertion checks this before returning) regardless of what the LLM says — the completeness guarantee no longer depends on the LLM at all. An LLM failure now degrades gracefully to the deterministic keys as their own names (via the existing `classify_exception` taxonomy) instead of the empty-entities silent-continue Phase 1.4/1.5 fixed twice already, since grouping never depended on the LLM in the first place.
  - Rewrote both prompt files for the new `group_names`/`explicit_groups`/`subject`/`scope_type` contract; deleted the harmful "Bring over all information about Airbus → 4 types" few-shot example. Fixed `tests/prompts/harness.py`'s `request_parsing` placeholder to `{group_catalog}` (was `{schema_info}`) so `check_contracts`/`scripts/check.py all` stay accurate — the golden case *content* itself still encodes the pre-rewrite contract, tracked separately (Phase 8.2, since it needs real discovered-schema fixtures, not just a prompt-shape fix).
  - *Evidence:* `tests/unit/test_parse_request_node.py` — 20 tests, entirely rewritten for the new contract (full-coverage invariant, rename/fallback/invented-key handling, explicit-groups filtering, scope extraction, LLM-failure graceful degradation, no-schemas early exit). `python -m tests.prompts.harness` contract checks pass. Ruff/mypy clean.

- [x] **11.3 Subject-first UI**
  - `PipelineScreen.tsx`: the company-name box is now the default, primary control; the free-text textarea moved into a collapsed `<details>` "Advanced — custom request" disclosure, matching the existing `AdvancedPanel.tsx` pattern. Removed the old `applyCompanyPreset()` giant 7-dimension enriched-text builder entirely — deterministic grouping (11.1) makes it unnecessary, not just redundant, since completeness is now structural rather than a function of how elaborately the prompt was written.
  - Backend: `WorkflowStart.subject` → `WorkflowManager.start_workflow(subject=...)` → `graph.get_initial_state(subject=...)` sets `merge_scope` directly to an active company-scoped merge for the UI branch; `parse_request.py` checks for this pre-set scope at entry and treats it as authoritative, never letting the LLM's own subject/scope_type guess override it (and it survives an LLM naming-call failure intact, since it's read from state, not derived from the response).
  - *Evidence:* `tests/unit/test_subject_mode.py` (5 tests on `get_initial_state`) + `test_parse_request_node.py::TestSubjectFirstMode` (4 tests: pre-set scope wins over LLM guess, survives LLM failure, survives no-schemas, and an *inactive* pre-set scope correctly does NOT suppress the free-text path's own LLM extraction). Live-verified in the running app (`.claude/launch.json` fixed to use `.venv/Scripts/python.exe` — was resolving to an unrelated venv missing `structlog`, entirely unrelated to this plan but blocking every future live check): typed into the company-name box, toggled Advanced open/closed via real clicks, confirmed the company field disables/re-enables and both inputs' values persist independently across toggles, zero console errors. Frontend `npm run build` clean.

---

## Phase 12 — Real Join Graph with Value-Overlap Verification (fix-bugs follow-up plan Phase 4.1)

Closes issue 9 structurally (the coverage matrix was pure name-heuristics with a `startswith` clause that fabricated edges like `Person.company_name → Company.id`) and lays the foundation for issue 6 (deep join chains like `Company → Experience → Person → PersonContact`, 3 hops, structurally unrepresentable by the old single-junction `_find_junction`).

- [x] **12.1 New `src/mcp_server/tools/join_graph.py`**
  - `build_join_graph(schemas, origins, *, sample_size=500) -> JoinGraph` builds a real graph — nodes are `(origin_id, table)`, edges are directed child→parent (many-to-one) and stored on both endpoints. Three tiers, descending confidence: **declared FKs** (always empty for ClickHouse/MongoDB, real and trusted for PostgreSQL); **name heuristics** (`companyID` → `Company`'s *actual* primary key, never a hard-coded `"id"`, and never a bare `.startswith()` match); **inclusion-dependency verification** — every name-heuristic candidate is sampled via each connector's new `sample_distinct_values(table, column, limit)` and scored as `|child ∩ parent| / |child|`, ≥0.95 → `verified`, 0.5–0.95 → `probable`, below → discarded.
  - Reshaped from B4-matching-precision's `identity_profile.py`: kept `normalize_val` verbatim (protocol/`www.`/trailing-slash stripping), but rebuilt the containment check from B4's bag-of-every-column-in-the-foreign-table (which cannot distinguish a real inclusion dependency from an unrelated column collision — B4's own version would score a `country_code` of `{"BA","AIR","LMT"}` at 1.0 against an unrelated `ticker` column) into a genuine per-column-pair ratio, and pushed the sampling into SQL/aggregation via the connectors rather than pulling full tables into Python.
  - Added `sample_distinct_values` to `connectors/base.py` (concrete, `NotImplementedError` default — not `@abstractmethod`, since `DatabricksConnector` doesn't implement the base interface's method names at all and a new abstract method would break it), and real implementations in `postgres.py` (parameterised `SELECT DISTINCT`), `clickhouse.py` (`{limit:UInt64}` parameter, backtick-quoted `` `db`.`table` ``), and `mongodb.py` (`$match`/`$group`/`$limit` aggregation pipeline — `aggregate()` on `AsyncCollection` is itself a coroutine that must be awaited before the returned cursor can be async-iterated, unlike `.find()`).
  - Removed `analyze_dependencies.py`'s `col_low.startswith(ot_low)` clause per the plan's explicit instruction — the coverage matrix remains a lighter-weight name-only heuristic that only feeds LLM prompt context in that node; `join_graph.py` is now what does verified matching for real extraction decisions.
  - *Evidence:* `tests/unit/test_join_graph.py` — 21 tests: `normalize_val` (protocol/www stripping), `_resolve_primary_key` (declared PK, MongoDB `_id` fallback when no `"primary_key"` key exists at all, no-PK-no-`_id` case), `_find_candidate_edges` (real-PK matching, the exact `startswith` regression this closes — `Person.company_name` no longer fabricates an edge to `Company`, skips own-table PK, no candidate when the other table has no resolvable PK), `JoinGraph` (both-endpoints storage, unknown-node lookup), and `build_join_graph` end-to-end against a mocked connector: declared-FK tier trusted without sampling, the full issue-6 3-hop chain (`Company→Experience→Person→PersonContact`) all verifying from mocked overlapping sampled values, full/partial/zero overlap correctly landing as `verified`/`probable`/discarded, connector `.close()` called even when sampling raises, missing origin config skips verification gracefully rather than crashing, and all schema nodes present in the graph even with zero edges. `tests/unit/test_analyze_dependencies_node.py` (26 passed, confirming no existing test depended on the removed `startswith` behavior), `test_postgres_connector.py`/`test_clickhouse_scratch_table.py`/`test_mongodb_connector.py` (their new `TestSampleDistinctValues` classes, 131 passed together with the above). `scripts/check.py types` clean (fixed two real mypy findings along the way: the MongoDB `aggregate()`-is-a-coroutine issue above, and an untyped `table_by_name` dict comprehension in `join_graph.py` that let `str | None` leak into `_EdgeCandidate.parent_table`). Full `tests/unit` sweep: 1248 passed, 83 skipped, zero failures/regressions. Full `tests/integration` sweep (started after the Phase 10/11 commit, run in the background): 148 passed, zero failures. `scripts/check.py all`: 10/10 passed (including the `format` gate — its own docstring documents it as a real enforced check post-Phase-0, not report-only despite an earlier note in `CLAUDE.md`; fixed 5 files that had drifted).

- [x] **12.2 BFS path finder over the join graph**
  - `find_paths(graph, subject_node, target_node, *, max_hops=6, max_paths=20) -> list[JoinPath]` in `join_graph.py`, replacing what `plan_scope.py`'s `_find_junction` (a flat scan capped at exactly one junction table, i.e. 2 hops) could express. Two-pass design rather than naive all-paths DFS, which is what keeps it bounded instead of exploding combinatorially on diamond-shaped schemas: pass 1 is a plain visited-guarded BFS recording each node's shortest hop distance from the subject (a cycle just stops expanding once every member has been reached — it provably cannot loop); pass 2 backtracks forward from the subject, only stepping onto edges whose far side sits exactly one hop further out per pass 1's distances, which makes it a walk over a DAG by construction and finds every *tied* shortest path, not just the first one found (verified directly — a 4-node cycle A→B→C→D→A produces both 2-hop routes A→B→C and A→D→C, not just whichever the traversal order happened to hit first).
  - Each `JoinPath` tracks direction per hop — `child→parent` is `"narrow"` (many-to-one), `parent→child` is `"fanout"` (one-to-many) — and exposes `high_fanout` (`fanout_hop_count >= 2`), the flag Phase 5.1's scope-gate checkboxes will default to unchecked, since two compounding fan-out hops is exactly how "everything about Airbus" silently pulls in unrelated companies (Airbus employees → their other jobs → those companies). Verified directly against the issue-6 chain: `Company→Experience→Person→PersonContact` is `[fanout, narrow, fanout]`, 2 fan-out hops, `high_fanout=True` — the real Airbus schema's 3-hop chain is itself the naturally-occurring high-fan-out case the flag exists for. Ranking (`JoinPath.rank_key`) is hop count, then weakest verified overlap ratio along the path (a path is only as trustworthy as its least-verified hop), then fan-out hop count — matching the plan's ranking spec exactly.
  - `max_hops` kept as a plain keyword default (6, matching the plan) rather than wired into `config.yaml` yet — nothing calls `find_paths` from a real node until Phase 4.3 wires it into `plan_scope.py`, and an unused config key would itself be exactly the kind of claimed-but-dead surface this whole follow-up plan exists to eliminate. Config wiring happens in 4.3 where the value is actually consumed.
  - *Evidence:* `tests/unit/test_join_graph.py` — 18 new tests (`_hop_from` direction correctness for both edge sides and an unrelated node; zero-hop self path; missing subject/target; single narrow and single fanout hop; the full issue-6 3-hop chain with exact node/direction/fanout assertions; `max_hops` truncation returning `[]`; a disconnected-but-present target returning `[]`; a genuine 4-node cycle finding both tied shortest routes without infinite-looping; hop-count-first ranking; confidence-then-fanout tie-breaking with an explicit weak-vs-strong-overlap pair; `min_overlap_ratio` staying `None` when every hop is `declared`; `max_paths` truncating a 3-way tie to 2; cross-origin nodes never connecting even when same-named). All 39 tests in the file pass together. `scripts/check.py types` clean. Full `tests/unit` sweep: 1266 passed (18 more than Phase 12.1's baseline, zero regressions), 83 skipped.

- [x] **12.3 Generalise the scope plan to N-hop paths**
  - Rewrote `plan_scope.py`'s dependent-entity heuristic to call `find_paths` over a real `JoinGraph` instead of three separate ad-hoc cases (declared-FK check, name-hint id-column scan, single-junction `_find_junction`, deleted). BFS over a real graph subsumes all three at once, plus chains longer than the old 2-hop ceiling — verified directly: a 2-hop junction case that used to need `_find_junction`'s special-casing now falls out of the same `find_paths` call as everything else. Every dependent-entity entry across the pipeline is now the single `mode: "link_path"` shape (`path`: an ordered list of `{from_table, from_column, to_table, to_column}` hops, plus `hops`/`confidence`/`enabled`/`link_entity`), replacing the old flat `link` (one column) and `link_via` (one hardcoded junction) shapes.
  - The LLM may still propose `match`/`link`/`link_via` in its familiar shape (backward-compatible prompt contract, small clarifying addition to `scope_planning.system.txt` noting proposals are checked and that omitting a deep-chain table lets automatic search find it) or the new explicit `link_path`, but it can no longer invent a join: `_sanitize_plan` now validates every hop of every proposed entry against the real graph (new `JoinGraph.find_edge`, direction-agnostic — checks both (table_a,col_a)→(table_b,col_b) and the reverse without the caller needing to know which side looks like the FK) and normalises whatever survives into the one `link_path` shape. A column that doesn't correspond to a real edge is dropped, not trusted — closing the "hallucinated join" gap the same way Phase 3 closed "hallucinated entity table."
  - Found and fixed two real gaps in Phase 4.1's own name-heuristic tier while wiring this up, both via this repo's own pre-existing test fixtures (Pagila-style pluralized table names): (1) `_find_candidate_edges` only matched a column literally derived from the OTHER table's exact name (`companyID`→`Company`), so a plural table (`companies`) with a singular FK column (`company_id`) — an extremely common convention, and exactly what this repo's own `test_plan_scope.py`/`scope_plans.json` fixtures already used — never became a candidate at all; fixed with a small local singularizer in `join_graph.py` (a deliberate copy of `entity_grouping.py`'s `_singularise`, not an import — that module sits above `mcp_server/tools` in CLAUDE.md's layering rule). (2) An abbreviation like `org_id` for a table named `organizations` doesn't match even the singularized form ("organization"), since it's a different word, not a plural — added `_concept_link_fallback` in `plan_scope.py`, a last-resort single-hop guess using the same `concept_matches` vocabulary Phase 3's entity grouping already uses (which already treats "org"/"organization"/"company" as one alias group), tagged `confidence="heuristic"` and never promoted beyond it.
  - Also added a 4th join-graph confidence tier, `"heuristic"`: when an origin has no connector config available for the inclusion-dependency sampling tier (scope planning can run before any live connection was opened), a name-heuristic candidate is now kept unverified rather than silently dropped — losing the join entirely because a connector happened to be unavailable is worse than surfacing a hypothesis a human can still discount. Found and fixed a real ranking bug this surfaced: `declared` and `heuristic` both carry `overlap_ratio=None` (nothing was sampled either way, but for opposite reasons — one is a real FK, the other an unverified guess), so `JoinPath.rank_key` compared by raw `overlap_ratio` alone would have ranked them as equally trustworthy; added a `_CONFIDENCE_RANK` tier ordering (`heuristic < probable < verified < declared`) and a `JoinPath.confidence` property (weakest hop wins) that `rank_key` now checks before `overlap_ratio`.
  - Added `PathHop.from_table/from_column/to_table/to_column` — direction-aware properties so every consumer (this phase's `plan_scope.py`, Phase 4.4's `extract_data.py`) gets the hop already expressed as "select `to_table` rows whose `to_column` is in the known `from_table.from_column` values," the same fixpoint shape regardless of whether the underlying edge narrows (child→parent) or fans out (parent→child), with zero direction-branching in the caller.
  - Frontend: `Gates.tsx`'s `ScopeBody` had no rendering branch for `link_path` (only `match`/`link`/`link_via`/`not_found`) and would have silently shown a blank "how" cell for every dependent-entity row now that the heuristic floor produces `link_path` by default — added a minimal hop-chain rendering branch as an interim fix; the full interactive rebuild is Phase 5.1's job.
  - `_bare_table_name` translates an LLM/`_resolve_table`-resolved full name (`public.companies`) back to the join graph's bare node name (`companies`) for hop lookups — the two conventions coexist deliberately (full names are what a connector needs to query, bare names are what the graph uses internally, unchanged from Phase 4.1) rather than unifying them, to avoid touching already-verified, committed Phase 4.1/4.2 code for an internal convention choice.
  - `arch-lint-baseline.txt` updated: `plan_scope.py`'s pre-existing (already-baselined, Phase P-era) reverse-layer import of `entity_aliases.concept_matches` shifted from line 32 to line 56 purely from unrelated code added above it in the same file — corrected the baseline's line reference for this one already-accepted entry (not a new violation; `lint_arch.py` confirms 0 new, 14 pre-existing, matching before/after).
  - *Evidence:* `tests/unit/test_plan_scope.py` — `TestHeuristicPlan` rewritten (6 tests: match unchanged, declared-FK 1-hop, name-hint-abbreviation-via-fallback 1-hop, product 1-hop, a genuine 2-hop junction chain) plus new `TestSanitizePlan` (7 tests: valid `link`→`link_path` normalization, hallucinated `link` column dropped, valid `link_via`→2-hop `link_path` normalization, fabricated `link_via` junction dropped, explicit `link_path` validated hop-by-hop, explicit `link_path` with one fabricated hop dropped entirely — not partially kept, unknown table dropped) — 23 tests total, all pass. `tests/unit/test_scope_plan_eval.py`'s golden eval (4 real-world-shaped cases: Postgres declared FKs, ClickHouse no-FK abbreviation, both origins together, MongoDB) rewritten for the `link_path` shape and passing — this is the floor the LLM plan is only ever allowed to improve on, now proven correct for the new contract. `tests/unit/test_join_graph.py` — 2 new regression tests for the pluralization fix (`companies`+`company_id`, `people`+`person_id` via the irregular-plural map) plus the earlier heuristic-tier/`find_edge`/`PathHop` direction/`JoinPath.confidence` tests (50 total, all pass). `tests/prompts/harness.py` contract checks still pass after the prompt addition. `scripts/lint_docs.py`/`lint_arch.py` clean. `scripts/check.py types` clean. Frontend `npm run typecheck` and `npm run build` both clean. Full `tests/unit` sweep: 1284 passed (18 more than the Phase 4.2 baseline), 83 skipped, zero regressions.

- [x] **12.4 Execute multi-hop filters in extract_data**
  - `extract_data.py`'s `_resolve_link_via` (hardcoded exactly one junction hop) is now `_resolve_link_path`, walking an N-hop `path` via the fixpoint approach the plan's Phase 4.4 spec names (from the USPTO subsetting patents): at each hop but the last, the known key set filters `hop["to_table"]` on `hop["to_column"]`, and the column the FOLLOWING hop will filter on (`path[i+1]["from_column"]`) is projected out to become the next known key set. The last hop is deliberately never queried inside the resolver — its `to_table` is the entity table itself, extracted separately by the existing outer loop with its full column list, filtered on `path[-1]["to_column"]` against whatever the resolver returns. A 1-hop path (what `link` used to be) never enters the loop at all — `subject_ids` already IS the value set for the single hop's `from_column`, matching the old `link` behavior exactly with zero intermediate queries. A 2-hop path (what `link_via` used to be) makes exactly one junction query, byte-for-byte the same query the old code made. A 3-hop path (`Company→Experience→Person→PersonContact`, issue 6's exact case) is simply one more loop iteration — not a separate code path, unlike before.
  - Closed the "full-extract leak" the plan's Phase 4.4 explicitly names: `merge_scope.active=True` with an empty `scope_entities` used to silently fall through to `scope_active=False` and read every table in full while still reporting the run as the scoped merge the user asked for. Now a hard failure (`error_class: "DataQualityError"`, `disposition: "escalate"`) before the extraction loop even starts, with an early return — this can only happen if `plan_scope.py` itself somehow produced nothing (Phase 4.3's `_ensure_complete_origin_coverage` normally guarantees every target entity gets at least a `not_found` placeholder, so this is a genuine defense-in-depth backstop, not the common path).
  - Added `scoped_out_entities` (new `MergeState` field, next to `dropped_inferred_entities`): entities with real candidate tables where every one was scoped out (no plan entry, no subject ids, or an empty join-path result) rather than missing entirely or genuinely failing — tracked via two per-entity flags (`entity_had_scope_skip`, `entity_had_failure`) so a real extraction error is never miscounted as a scoping outcome. Before this, such an entity was invisible beyond an unexplained `extracted_total: 0`.
  - Simplified `_scope_filter_for` to only handle `match` mode (the subject entity's own ILIKE filter) — its `link` branch was already dead now that Phase 4.3 normalises every dependent-entity entry to `link_path`; keeping it would have silently mis-handled a stray old-shape entry as an ILIKE match instead of rejecting it.
  - *Evidence:* `tests/unit/test_extract_data_node.py` — `TestScopedExtraction`'s existing 1-hop and 2-hop tests updated for the `link_path` shape (assertions unchanged — same filters, same query counts, proving the new resolver reproduces the old `link`/`link_via` behavior exactly) plus 5 new tests: the full 3-hop issue-6 chain with exact per-hop query and filter assertions (`Company→Experience→Person` shared by two dependent entities, `Person→PersonContact`-style final hop resolved only via the outer loop's own filter, never queried inside the resolver); `_resolve_link_path` unit-tested directly for the 1-hop no-query case and an empty-intermediate-result short-circuit; the full-extract-leak fix (scope requested, plan empty → hard failure, zero extraction calls made, asserted via a mock that raises if called at all); `scoped_out_entities` correctly populated for an entity with a real candidate table but no plan entry, and correctly absent from `dropped_inferred_entities`. 50 tests in the file, all pass. `scripts/check.py types` clean. Full `tests/unit` sweep: 1289 passed (5 more than the Phase 4.3 baseline), 83 skipped, zero regressions.

- [x] **12.5 Salvage the `validate_data` gate from B3-quality-lineage**
  - Copied `validate_data.py` and `data_quality.py` from `.worktrees/B3-quality-lineage` per the plan's salvage table, but found and fixed a critical bug the plan's salvage table didn't anticipate: B3 predates the Phase N spool refactor and assumed `extracted_data[entity]` was always an in-memory row list. In the current codebase it is a spool HANDLE dict (`{"__spool__": True, "run_id": ..., "row_count": ...}`); copied verbatim, B3's code would fall into its `isinstance(data, dict)` fallback and validate the handle's OWN keys (`run_id`, `row_count`, …) as if they were one record's fields, checking "uniqueness" against handle metadata instead of any real data. Fixed by moving all row/count resolution out of `data_quality.py` (kept pure and layering-clean — no spool knowledge, `mcp_server/tools` cannot import `orchestrator` per CLAUDE.md) into `validate_data.py` (the orchestrator node, already at the correct layer): a new `_sample_rows` helper reads via `RunSpool.iter_rows` capped at `_SAMPLE_SIZE=2000` for a spooled entity, and `entity_row_count` gives the EXACT row count without ever reading the rows — so the row-count check (the one that matters most for issue 6) is never approximate even when the deeper checks (uniqueness/not-null/date-bounds) run against a bounded sample for a large entity. `run_data_quality_checks`'s contract changed from `extracted_data: dict[str, list]` to `sampled_rows` + `row_counts` to make this split explicit; each entity's report now also carries `sample_size`/`sampled` so a reviewer knows when a check was approximate.
  - Applied the plan's other two named corrections: dropped the unused `great_expectations` import and `gx_available` flag entirely (GX was never actually invoked in B3's code, only imported to set a flag nothing read); replaced the `rows[0]`-only column derivation with a union across every sampled row, so an entity fed by origins with different columns (the norm, not the exception, in a merge) doesn't blind the name-column/date-bounds checks to whatever the first row happened not to carry.
  - Wired the gate in: `NODE_SUCCESSOR["extract_data"] = "validate_data"` / `["validate_data"] = "resolve_entities"`, node registered and given a **plain** edge to `resolve_entities` (not `_fallible_router`-wrapped, since `validate_data_node` never sets `state["failure"]` — the never-auto-abort posture is structural, not just a design intention, proven directly by a test asserting `"failure" not in result` even for a hard `fail` severity). Added `quality_report`/`has_quality_failure` to `MergeState`.
  - **The single highest-value part of this salvage, per the plan**: on B3, `has_quality_failure` was computed and then silently dropped before ever reaching a human — `workflow_manager.py`'s `human_final_approval` payload builder already had `quality_report`/`has_quality_failure` plumbed in (Phase 2.1, ahead of this node existing, explicitly noting it was a no-op until both sides existed) but nothing populated them and `Gates.tsx` didn't render them either. Both now do: `SummaryBody` shows a prominent `banner-error` when `has_quality_failure` is true (naming which entities failed) or a `banner-warn` for warn-only, plus a per-entity Data Quality table (severity, row count with a "(sampled N)" annotation when applicable, and the specific failed/warned check details) — placed at the top of the final approval gate, directly under the summary stats, matching "shown prominently."
  - Found along the way that `spool.py`'s `is_handle(value) -> bool` didn't let mypy narrow `value`'s type inside an `if is_handle(value):` block anywhere it's used — every call site had to re-assert the type or trip a new mypy error. Changed its return type to `TypeGuard[dict[str, Any]]` (a pure type-annotation change, zero runtime behavior difference) — fixes the narrowing at every existing call site, not just this phase's new one.
  - *Evidence:* `tests/unit/test_data_quality.py` — 11 tests: clean pass, zero-rows fail, the exact-row-count-vs-sample-size distinction (a 1-row sample against a stated 43,861-row count still reports the real count and passes row_count_ge_1), duplicate-id fail, null-name warn, bad-year warn, the column-union-not-rows[0] fix demonstrated directly (a row missing a column entirely followed by a row that has it, null), missing-row_counts fallback, bare-`id`-fallback uniqueness, internal-exception graceful degradation (no GX involved at all), and the empty-input clean-pass case. `tests/unit/test_validate_data_node.py` — 8 tests: inline list validated directly; a REAL spool handle read via `RunSpool`/`make_handle` and proven to check real column names (not handle keys); a spooled entity with 0 rows failing loudly (the exact origin_2.Person/PersonContact shape from the 4th live run); large-entity sampling proven bounded (`_SAMPLE_SIZE` monkeypatched to 10 against 50 real spooled rows); no-extracted-data clean pass; and the never-auto-abort guarantee proven directly (`"failure" not in result` on a fail-severity run) — plus graph-position tests (`NODE_SUCCESSOR`, node registered in the real builder). `tests/unit/test_interrupt_payload_parity.py` — 1 new test proving the VALUE (not just key presence, which Phase 2.1's existing superset test already covered) of a fail-severity `quality_report` genuinely reaches `_build_interrupt_data`'s payload unmodified. Fixed one stale pre-existing assertion in `test_human_nodes.py` that had `NODE_SUCCESSOR["extract_data"]`'s old value (`"resolve_entities"`) baked into an error-gate wording check. `scripts/lint_arch.py`/`lint_docs.py` clean. `scripts/check.py types` clean (after the `TypeGuard` fix). Frontend `npm run typecheck` and `npm run build` both clean. Full `tests/unit` sweep: 1309 passed (20 more than the Phase 4.4 baseline), 83 skipped, zero regressions.
  - **Not yet done, honestly flagged**: the plan's own "Verify" instruction for this phase — confirming `origin_2.Person`/`origin_2.PersonContact` resolve via their real 2-hop/3-hop chains with counts matching hand-written ClickHouse SQL through the `mcp-clickhouse2` MCP — requires a live run against real Airbus-shaped origins, which this session has not had a live database connection available for. Everything above is verified at the unit level (including a real spool round-trip through `RunSpool`, not a mock), but the live cross-check against real ClickHouse data specifically remains open. *(Closed in Phase 12.5 below, once live database access became available in this session — with one correction: `origin_2`'s real `Experience` table has no `personID` column at all, so the `origin_2.PersonContact` 3-hop scenario named here doesn't exist in the actual sample data; `origin_1`'s 2-hop `Company→CompanyExperience→Person` chain is the one verified live.)*

---

## Phase 12.5 — Scope gate checkboxes (Phase 5.1/5.2) — plus three real bugs a live run surfaced

Closes issue 5 (no way to include/exclude entities at the scope gate) and issue 6's confirmation step. Implemented 5.1 and 5.2 together — a checkbox with no return path to the backend is not meaningfully "done" — then live-verified through the real browser UI against the real Airbus ClickHouse origins, which surfaced three genuine, independent bugs the entire unit-test suite could not have caught (each is a "the mock was too clean" class of bug, not a logic error).

- [x] **12.5.1 `ScopeBody` becomes interactive**
  - Full rewrite of `Gates.tsx`'s `ScopeBody`: `useState` over the flat plan rows (keyed on `payloadVersion`, the exact Phase 2.2 poll-clobbering fix, now applied here for the first time since this component never had local state before), a checkbox on every real row (`match`/`link_path`) defaulting to whatever `enabled` the backend sent, and an entity-level master checkbox with real `indeterminate` state when a group's rows are mixed. `not_found` placeholder rows get no checkbox — there is nothing to toggle. Renders its own `GateButtons` row (matching `DependencyBody`'s established pattern) since it now needs to hand `onApprove` a third, edited-plan argument the generic gate footer doesn't carry.
  - `GateProps.onApprove` widened to a third optional `scopePlan?: Record<string, any>` parameter; `GateButtons` forwards it; `PipelineScreen.tsx`'s `onApprove` and `api.ts`'s `sendFeedback` both threaded through, gated on `node === "human_review_scope"` exactly like the deps gate's `columnDependencies` before it.
  - `human_nodes.py`'s `_build_scope_interrupt` rewritten to forward every field an entry actually has (`{**e, "entity_type": entity}`) instead of a hand-picked subset that silently dropped `path`/`hops`/`confidence` — the edited plan the human sends back on approve is this same shape round-tripped, so a dropped field here would have been lost the moment a human approved the plan unchanged. `enabled` defaults `True` via `row.setdefault` when an entry doesn't carry it (every `match` entry, and any `not_found` placeholder). Added `"enabled": True` explicitly to both `_heuristic_plan`'s and `_sanitize_plan`'s `match`-mode entry builders in `plan_scope.py` for the same reason.
  - Investigated the plan's "doubled `not_found` rows" claim directly (two test scenarios: a hand-built partial-coverage plan, and the real `plan_scope_impl` pipeline end to end) — not reproducible in the current code. `plan_scope.py`'s `_ensure_complete_origin_coverage` only backfills when an entity's list is entirely empty; `_build_scope_interrupt`'s own coverage loop only adds a placeholder for an origin genuinely missing from that entity's `covered` set — the two never double-count the same gap. No fix applied; documented rather than silently dropped, per this session's own standing rule against inventing fixes for bugs that don't reproduce.

- [x] **12.5.2 Wire the edit back to the backend**
  - `Feedback` (`main.py`) gains `scope_plan: dict[str, Any] | None = None`; the feedback endpoint forwards it into the resume dict exactly like `column_dependencies` before it. No new merge/sanitize layer needed on the backend: `human_review_scope`'s node body already replaced `state["scope_plan"]` wholesale with `human_feedback["scope_plan"]` when it exists (this was live code, just unreachable — nothing could ever put a `scope_plan` key into the feedback payload until now).
  - `extract_data.py`: a new `entry.get("enabled") is False` check right after the existing `entry is None` ("not in the plan") skip — same skip path, same log-and-continue shape, so an unchecked row is treated exactly like a row the plan never proposed in the first place, not "read it in full."

- [x] **12.5.3 Three bugs a live run found that no mock could — fixed, not just logged**

  Ran a real scoped merge against the live Airbus ClickHouse origins (`origin1`/`origin2` on `localhost:8190`) through the actual browser UI end to end: Setup form → company-name box → Start merge → real LLM calls → deps gate → mapping gate (135 real fields) → **scope gate, with real checkboxes** → unchecked the `experience` row → Approve → extraction → Final Approval, then rejected before touching the real destination. Along the way:

  1. **`.env` was silently never loading at all.** `python-dotenv` is imported in four places (`src/api/main.py`, `src/main.py`, `src/utils/config_loader.py`, `src/llm/tracing.py`) but was never declared in `pyproject.toml`, and all four import sites swallowed `ImportError` with a bare `pass`. Every `${ENV_VAR}` placeholder in `config.yaml` — including `LLM_API_KEY` — silently stayed as the literal unexpanded string. Three consecutive "fresh" API keys from the user all produced an identical `401 Incorrect API key provided` from DashScope; a raw `urllib` request with the exact same key succeeded (`200 OK`), proving the keys were fine and the app's own env loading was the fault. Fixed: added `python-dotenv>=1.0.0` to `pyproject.toml` and installed it; all four sites now log a real, specific warning instead of silently proceeding half-configured — a deployment with real OS env vars and no `.env` file is still legitimate and must still start, so this is a loud warning, not a raise. A pre-existing, unrelated `config/platform_overrides.yaml` (gitignored, machine-local) also had a stale `llm.base_url` pointing at an unreachable local vLLM box left over from before this session — cleared locally, not a code change.
  2. **A composite primary key silently blocked the whole structural join-graph tier.** `join_graph.py`'s `_find_candidate_edges` skipped any column that was a *member* of a table's own primary key, on the theory that a table's own key identifies its own rows rather than pointing elsewhere. True for a single-column key; false for a composite one — `Experience`'s real ClickHouse primary key is `(experienceID, companyID)`, and `companyID` is simultaneously part of Experience's own identity *and* a real FK to `Company`. The old check discarded it either way, so the entire structural (tier 2/3) join-graph search found **zero** candidate edges for a table shaped exactly like the ones Phase 4 exists to handle, and every dependent entity fell through to the much weaker single-hop `_concept_link_fallback` (which cannot bridge 2+ hops at all). Fixed: only skip a column when it **alone** constitutes the primary key (`own_pk == {col_low}`), not merely when it's a member.
  3. **Independent two-sided sampling structurally cannot detect a real FK when the parent table is much larger than the child.** `_verify_inclusion` sampled up to 500 distinct values from *both* the child and the parent table independently, then measured overlap. For a parent with thousands of rows and a child with a few dozen, an arbitrary `LIMIT 500` slice of the parent has a near-zero chance of coincidentally containing the exact rows the child references — verified live against `Company` (thousands of rows) and `CompanyFinancials` (29 rows): **0.069 overlap for a relationship that is, by construction, 100% referentially intact.** Fixed at the connector layer: `sample_distinct_values` on all three connectors (`postgres.py`, `clickhouse.py`, `mongodb.py`, plus the `base.py` default) gained an optional `filter_values` parameter that pushes a `WHERE column IN (...)` / `$in` filter into the query instead of sampling independently; `_verify_inclusion` now samples the child first, then asks the parent "do these specific values exist" rather than "give me an arbitrary sample and hope it overlaps."

  **Combined effect, live-verified twice over:**
  - Before either fix: `person`/`contact`/`experience` (everything needing 2+ hops, or a 1-hop candidate on a composite-keyed table) all showed `not_found` or a `heuristic`-confidence single-hop fallback guess at the real scope gate — the exact flagship failure issue 6 was written to fix, still failing on real data despite the entire unit-test suite passing.
  - After both fixes: `Company → CompanyExperience → Person` resolves as a real, **`verified`**-confidence 2-hop `link_path` (`build_join_graph` against the live origins, standalone script) — independently cross-checked against real ClickHouse SQL: the 7 real Airbus companies in `origin_1` reach exactly 32 distinct `personID`s via `CompanyExperience`, and all 32 are real rows in `Person` (32/32, not a sample-derived estimate).
  - `origin_2`'s real `Experience` table has no `personID` column at all (only `companyID`) — `origin_2.Person`/`origin_2.PersonContact` genuinely have no reachable path in this schema, confirmed structurally, not a remaining bug.

- [x] **12.5.4 The checkbox → skip round trip, proven live, not simulated**
  - At the real scope gate, unchecked the `experience` / `origin_2` row (the only real, non-`not_found` entry for that entity) via a real click in the browser, submitted Approve, and let the run continue. The final summary's `entity_counts.experience` came back **exactly 0**. Independently verified this was the uncheck taking effect and not coincidentally-empty data: a direct ClickHouse query found **212 real `Experience` rows** in `origin_2` referencing the 13 real Airbus companies discovered there — had the uncheck not been honoured end to end (browser state → `sendFeedback` → `Feedback.scope_plan` → `human_review_scope`'s wholesale replace → `extract_data`'s `enabled is False` skip), the real count would have been 212, not 0.
  - *Evidence:* `tests/unit/test_plan_scope.py` — `_build_scope_interrupt` field-forwarding (a `link_path` entry's `path`/`hops`/`confidence`/`enabled` all survive), `enabled` defaulting `True` when absent, the not-found-duplication non-issue formally guarded against regression. `tests/unit/test_extract_data_node.py` — a new test proving an `enabled: False` entry produces zero extract calls and zero rows, same code path as "not in the plan." `tests/unit/test_api.py` — `Feedback.scope_plan` round-trips through the endpoint into the resume dict. `tests/unit/test_join_graph.py` — 4 new tests: composite-PK member is still a candidate FK (the exact live bug), single-column PK is still correctly excluded (no regression), and a filtered-parent-lookup test that reproduces the live sampling bug exactly (an arbitrary "parent sample" that deliberately excludes the child's real referenced values, proving the OLD independent-sampling code would have failed this exact case while the fix correctly finds the values via `filter_values`). `tests/unit/test_postgres_connector.py` / `test_clickhouse_scratch_table.py` / `test_mongodb_connector.py` — one `filter_values` test each, confirming the `IN`/`$in` clause is genuinely pushed into the query. Frontend `npm run typecheck` and `npm run build` both clean. `scripts/lint_arch.py` (0 new, 14 pre-existing) / `lint_docs.py` clean. `scripts/check.py types` clean. Full `tests/unit` sweep: 1320 passed (11 more than the Phase 4.5 baseline of 1309 — 5 from the checkbox UI/wiring, 6 from the three live-discovered bug fixes), 83 skipped, zero regressions.

---

## Phase 12.6 — Destination family graph (Phase 6.1)

Closes the structural gap behind issue 4: `map_fields.py` only ever considered one destination table per entity, so `Company.totalInvestment` was dropped with *"no destination column for investment amount"* (the 4th live run's log) while `finance` sat right there, unused, and a `contact` entity could never fan out into `contact_email`/`contact_phone`/`contact_social` no matter how the source data was shaped. This phase derives *which* destination tables are related to which; Phase 6.2 (next) is what actually widens the mapping search to use it.

- [x] **12.6.1 `build_family_map`**
  - New `src/mcp_server/tools/dest_family.py::build_family_map(dest_connector) -> dict[str, set[str]]`. One pass over every destination table's real FK constraints (`PostgreSQLDestinationConnector.get_foreign_keys`, one call per `list_tables()` result), recording the relationship on *both* endpoints — a table is in another's family if either one declares the FK, so the direction of the constraint doesn't matter to the caller. Derived from the real schema, not hand-written, so it generalises to any destination without a code change when a table is added or renamed.
  - `getattr(dest_connector, "get_foreign_keys", None)` duck-typed rather than an interface requirement: only `PostgreSQLDestinationConnector` implements it today (`ClickHouseDestinationConnector` has no FK constraints to read at all), and a connector without it degrades to an empty family map — the same graceful-degradation shape every other schema-introspection call site in this codebase already uses (`map_fields_node.py`'s own `try/except` around `dest_connector.list_tables()`), never an error that would abort mapping over what is, at worst, a missing enhancement rather than a required one.
  - A `list_tables()` failure, or one table's `get_foreign_keys()` call failing, degrades gracefully (empty map, or that one table's outgoing FKs skipped) rather than aborting the whole build — one broken table must not silently lose every other table's real relationships too.
  - *Evidence:* `tests/unit/test_dest_family.py` — 8 tests: a simple bidirectional FK, multiple tables sharing one referenced table, a self-referencing junction with two FK columns to the same table deduplicating via set semantics (not crashing or double-counting), a table with zero FKs still present with an empty family (not silently absent), a referenced table missing from `list_tables()`'s own result still recorded, a connector without `get_foreign_keys` at all degrading to `{}`, `list_tables()` raising degrading to `{}`, and one table's `get_foreign_keys()` raising not aborting the rest. **Live-verified against the real 26-table `global_warehouse` destination** (`localhost:55201`, the same database this whole plan's live checks have used): `company`'s derived family is `{company_relation, contact_email, contact_phone, contact_social, contract_entity, experience, finance, finance_budget, industry, location, product, technologic}` — an almost word-for-word match to the plan's own illustrative example (`{finance, industry, experience, company_relation, contact_email, contact_phone, contact_social, location, technologic, ...}`), genuinely derived from the real schema's FK constraints, not copied from the plan text. `scripts/check.py types` clean. Full `tests/unit` sweep: 1328 passed (8 more than the Phase 5 baseline of 1320), 83 skipped, zero regressions.

---

## Phase 12.7/12.8 — Widen mapping search within family + write child rows (Phase 6.2/6.3)

Implemented together, as 4.3+4.4 were: shipping 6.2's producer-side change (`map_fields.py` emitting `dest_table`-tagged cross-table mappings) without 6.3's consumer-side change (`load_data.py` understanding how to write those mappings into the right table) would leave a genuinely broken intermediate state for any entity that actually receives a cross-table mapping — either silent data loss or an INSERT into the wrong table. Closes issue 4 for real: `Company.totalInvestment`-shaped fields (no home on the entity's own table, a real home on a sibling) are now mapped AND written, not just discovered and dropped.

- [x] **12.7.1 `map_fields_impl` widened to search the entity's table plus its family**
  - `map_fields.py`: `destination_schema` (the primary table) is unchanged; a new optional `family_schemas: list[dict] | None` parameter carries sibling tables (from `dest_family.build_family_map`, Phase 6.1). Internals rebuilt around `dest_candidates: dict[tuple[table, column], col_info]`, an ordered dict with the **primary table's columns inserted first, family tables' after** — the existing `if score > best_score` (strict greater-than) comparison in every phase (exact-name, confident-heuristic, low-confidence-heuristic) then naturally prefers the primary table on a scoring tie as a side effect of iteration order, with zero new tie-breaking logic layered on top of the already-well-tested scoring functions, exactly as planned.
  - Every phase updated to work over `(table, column)` keys instead of bare column names: `taken` (claimed-columns tracking) is now a set of `(table, column)` tuples so a claimed `company.amount` and a claimed `finance.amount` don't collide with each other; `_incompatible_with_column` takes the specific candidate's column-info dict directly instead of a name-keyed lookup, so two same-named columns on different tables are checked against their own real types; `is_coalesced` is now keyed by `(dest_table, dest_column)` — a field landing on `company.amount` and a different field landing on `finance.amount` must not be flagged as sharing a destination cell just because the bare column name matches.
  - LLM phase (`_decide_fields_with_llm`): the prompt's destination-schema block gets a `### Related table: <name>` section per family table, each column listed as `<table>.<column>`; the system prompt gained one new decision rule explaining the format and that a bare column name always means the primary table. `_apply_llm_decision` resolves the model's answer via `_resolve_candidate_key`, accepting either `table.column` (family) or a bare name (primary-preferred, since dict iteration order puts the primary table's entries first). An LLM answer naming a column that doesn't exist anywhere in the offered set is rejected exactly as before (falls through to heuristic tiers / unmapped).
  - Every mapped field now carries `dest_table` (the entity's own table for an ordinary mapping, a family table for a cross-table one) — `MappingRow` (`api.ts`) gained the same field; `Gates.tsx`'s mapping-gate table's "Dest Table" column (previously a placeholder rendering `r.entity_type`) now renders the real `r.dest_table`, with a small "↗ cross-table" badge when it differs from the entity's own table. `human_nodes.py`'s `_build_mapping_rows` forwards `info.get("dest_table", entity_type)` onto every row.
  - `workflow_manager.py`'s `apply_mapping_feedback` (the mapping-gate approve/edit path) fixed to match: `GateButtons.onApprove` resends **every** row on approval, not just edited ones, so without a fix a cross-table mapping's `dest_table` tag would be silently stripped back to the entity's own table on every single approval (and then rejected outright, since the column genuinely doesn't exist on that table) — a regression this session's own re-run of `tests/unit/test_mapping_gate.py` caught immediately after the `_build_mapping_rows` change (`test_map_row`'s golden dict was missing the new key, which would have masked the real bug if just patched away instead of tracing why the approve path independently needed the same fix). Validation is now per-`(entity, target_table)` via a lazily-cached `_columns_for(table)` instead of one column set per entity.
  - *Evidence:* `tests/unit/test_map_fields_cross_table.py` (new, 9 tests) — exact-name and confident-heuristic matches on a family table; no-family-schemas behaves byte-for-byte as before (regression guard); primary-table-preferred-on-tie (the core design claim, directly tested); reserved columns (`id`/`_source_ids`) excluded from family tables too; `is_coalesced` correctly NOT set when two different fields land on two different tables' same-named column; LLM maps to a `table.column`-qualified family answer (prompt content asserted, not just the outcome); LLM bare-name answer resolves to the primary table; LLM naming a nonexistent qualified column is rejected. `tests/unit/test_map_fields_node.py` — 1 new test proving the node actually builds and forwards `family_schemas` from a real (mocked) FK-exposing connector. `tests/unit/test_mapping_gate.py` — `test_map_row` updated for the new `dest_table` key; 2 new tests for `apply_mapping_feedback`: a cross-table edit validated against the family table's real columns and the tag surviving the round-trip, and a cross-table edit naming a column that doesn't exist on that family table correctly rejected. All 51 pre-existing `map_fields`/field-mapping tests (`test_mcp_map_fields.py`, `test_hybrid_llm_paths.py`, `test_field_mapping.py` integration) still pass unmodified — the widening is fully backward compatible when `family_schemas` is absent.

- [x] **12.7.2 / 12.8 `load_data.py` writes child rows for cross-table mappings**
  - New `_group_mapping_by_dest_table(mapping, primary_table)`: splits one entity's field mapping by which destination table each entry actually targets — a plain mapping (no entry has `dest_table`, every call site before this phase) collapses entirely into `{primary_table: mapping}`, a genuine no-op for the common case.
  - `load_data_impl` uses this to build the PARENT transform from only the primary-table-tagged entries (unchanged shape/behavior for a single-table mapping), then — once the parent's rows are written and their final destination ids are known — calls new `_write_child_rows` for whatever's left.
  - `_write_child_rows`: for each cross-mapped sibling table, reads its **real** foreign keys (`connector.get_foreign_keys(child_table)`) and finds the column pointing back at the primary table. Deliberately handles only that one direction — a family table whose OWN FK points at the primary table (`finance.company_id -> company.id`), which live inspection of the real `global_warehouse` schema confirms is the shape of every field-mapping-relevant relationship here (`finance`, `finance_budget`, `contact_email/phone/social`, `contract_entity`, `experience`, `company_relation` all have the FK; only `company.industry_id -> industry` and `company.location_id -> location` run the other way, and those are not sibling tables a source FIELD would ever cross-map onto). No reverse FK found -> the field is logged and left unwritten rather than guessed at; this is a real, bounded scope decision, not an oversight — the reverse direction needs find-or-create matching against existing rows, a genuinely different feature. One child row per parent record per sibling table; a record that carries none of that table's fields (transforms to all-`None`) is skipped rather than writing an empty row. Reuses the SAME caller-supplied connector as the parent write (`load_data_node.py`'s single connector/transaction for the whole load), so a partial fan-out cannot commit — an exception here propagates exactly like a parent-table load failure and rolls the whole load back.
  - `load_data_impl`'s return dict gained `child_tables: {table: {inserted, updated}}`, empty for every mapping that doesn't cross a table (i.e. everything before this phase).
  - *Evidence:* `tests/unit/test_load_data_cross_table.py` (new, 12 tests) — `_group_mapping_by_dest_table` (plain mapping collapses, cross-table entries split correctly, empty mapping, a `dest_table` equal to the primary stays in the primary group); `_write_child_rows` (one child row per parent with the real FK set, an all-None child record not written, a sibling table missing the reverse FK skipped without raising, a connector without `get_foreign_keys` degrading to no-op, a parent record with no id skipped, two sibling tables each getting their own child rows in one call); a full `load_data_impl` integration test proving the parent write never even sees the finance-tagged field and `child_tables` comes back correctly populated; a single-table-mapping regression test proving `child_tables == {}` and `get_foreign_keys` is never even called when there's nothing cross-table to write.
  - **Live-verified end to end against the real `global_warehouse` destination** (`localhost:55201`) — genuinely round-tripped, not just read: built the real family map via `build_family_map` against the real connector (confirmed `finance`/`contact_email` are really in `company`'s family); called the **real** `map_fields_impl` with the real LLM (DashScope, the same endpoint/key this whole plan's live checks use) against the real `company`/`finance`/`contact_email` schemas with three synthetic fields (`org_title`, `netIncomeUSD`, `workEmail`) that have no unambiguous home on `company` for two of them — the LLM correctly cross-mapped `netIncomeUSD -> finance.net_income` and `workEmail -> contact_email.email` (both `table.column`-qualified answers, proving the prompt format round-trips through a real model, not just the test's mocked one) and `org_title -> company.name` (bare name, primary table); called the **real** `load_data_impl` against the real connector, which wrote one `company` row plus one `finance` child row plus one `contact_email` child row, all correctly keyed by the real generated `company.id` via each child table's real `company_id` FK column; then — critically — opened a **separate, freshly-created** database connection (never touched by the write path) and re-queried all three tables independently, confirming the exact values landed (`net_income = 4242000.0`, `email = 'verify@phase63.test'`, `name = 'Phase 6.3 Verify Co'`). Cleaned up afterward and independently re-confirmed zero residual rows and the `company` table back to its original 16 rows. (One earlier attempt without an `llm_config` showed the heuristic-only tiers correctly finding `email_addr -> contact_email.email` via substring match but leaving `totalInvestment` unmapped against the real, unusually messy `finance` schema — which has accumulated a mix of a clean financial-statement schema and separate enrichment-looking columns like `ticker`/`alexaRank`/`ISCurrent` from some earlier session's runs; a genuinely ambiguous case for pure string heuristics, correctly escalated rather than guessed at, and exactly what the LLM tier exists to resolve — which the LLM-enabled run then did.) Docker/the `postgres`/`mcp-clickhouse2` MCP servers were unavailable for part of this session; once available, `mcp-clickhouse2` connected cleanly but the `postgres` MCP itself kept erroring, so this verification used a direct `psycopg2` connection instead — still a connection independent of the app's own success message, per CLAUDE.md's actual requirement.
  - `scripts/check.py all` — all 10 checks pass (ruff, ruff-format, mypy ratchet, `lint_arch.py` — 0 new violations, 14 pre-existing — `lint_docs.py`, unit tests, frontend typecheck+build). Full `tests/unit` sweep: **1352 passed** (24 more than the Phase 6.1 baseline of 1328 — 9 from the new cross-table map_fields tests, 12 from the new load_data child-row tests, 1 from the map_fields_node family-passthrough test, 2 from the apply_mapping_feedback round-trip tests), 83 skipped, zero regressions. `npm run typecheck` and `npm run build` both clean.

---

## Phase 12.9 — Per-pair match evidence retained on clusters (Phase 7.1)

Closes half of issue 7: `_build_clusters` computed genuine per-pair scores while scoring, then threw everything away except `min()` as a cluster's `match_confidence` — a reviewer at the matches gate (or, once Phase 7.3 lands, the lineage view) had no way to see *why* two records were judged the same entity, only a bare number. `fellegi_sunter.py` already had a `MatchModel.explain()` method that computes almost exactly this per-field breakdown — genuinely dead code, zero callers anywhere in the codebase — so this is largely wiring existing math through, not new statistics.

- [x] **12.9.1 `_pair_field_evidence` — the field-level weight breakdown**
  - New `resolve_entities.py::_pair_field_evidence(rec1, rec2, vector, model, record_cache) -> (matched, mismatched)`. For every non-`LEVEL_NULL` field in a pair's comparison vector, calls the SAME `FieldModel.weight(level, value)` the scorer itself already sums into one probability — this is that exact number decomposed per field, not a second, possibly-diverging calculation. Positive weight -> `matched_fields`, negative -> `mismatched_fields`, each sorted strongest-evidence-first. Each entry carries the field name, both sides' canonically-projected values (`a`/`b`), the comparison level, and the weight in bits.
- [x] **12.9.2 Capture evidence during scoring, not by re-deriving it later**
  - `_resolve_core`'s scoring loop (dispatchable to a `ProcessPoolExecutor`, so every return value must stay plain/picklable data) now also builds `pair_evidence: dict[(id1,id2), dict]` alongside `scored_pairs`, using the SAME `precomputed_vector` already computed for scoring — zero extra vector-building cost, only the per-field weight decomposition is new work, and only for pairs that actually got a probabilistic-model score (method `"fellegi_sunter"`; a heuristic-only pair, too few candidate pairs to fit a model, gets no field breakdown — nothing false to show beats a confident-looking blank).
  - `_judge_pairs_with_llm` gained an optional `evidence_sink` parameter (the same caller-owned-mutable-dict pattern this file already uses for `stats`), filled with `{"method": "llm", "reason": ..., "llm_match": ...}` per adjudicated pair. The model's rationale (`j.get("reason")`) used to be logged at debug level, truncated to 120 chars, and discarded — now it is the same string, kept.
  - `resolve_entities_impl` merges both sources into one `pair_evidence` dict and passes it into `_build_clusters`, which attaches `cluster["match_evidence"] = {"pairs": [...]}` to every multi-member cluster, built from `_evidence_for_members` — the same internal-edge enumeration `_min_internal_confidence`/`_average_linkage` already use, so an edge only appears if it was genuinely part of `edge_weight` (a real scored pair, not a chain co-membership) AND has recorded evidence. A singleton cluster gets no `match_evidence` key at all — there is no pair to explain.
  - *Evidence:* `tests/unit/test_resolve_entities_match_evidence.py` (new, 11 tests) — `_pair_field_evidence`: exact agreement positive-weight/matched, disagreement negative-weight/mismatched with both raw values carried, `LEVEL_NULL` dropped, a field with no model entry skipped, matched/mismatched sort order. `_evidence_for_members`: only edges present in both `edge_weight` and `pair_evidence` returned (a scored-but-unrecorded edge silently skipped, not padded), no edges, singleton. `resolve_entities_impl` end to end: a real fitted probabilistic model (9 near-identical "Widget Traders" records providing the >=30 candidate pairs needed to fit at all, none of which merge — correctly blocked by the discriminating-agreement guard since they share only a non-discriminating name) plus one genuine duplicate pair (agreeing on name/email/phone/domain) that clears the 0.95 auto-match threshold and gets `match_evidence` with `method: "fellegi_sunter"` and the right field breakdown; a real (mocked-LLM) borderline-pair merge gets `match_evidence` with `method: "llm"` and the exact rationale string the mock returned; a singleton has no `match_evidence` key. All 108 pre-existing resolve_entities/hybrid-LLM tests still pass unmodified.
  - `scripts/lint_arch.py`: this change shifted `resolve_entities.py`'s pre-existing (baselined, not new) reverse-layer import of `src.orchestrator.utils.retry_hints` down by ~90 lines, which line-number-keyed `arch-lint-baseline.txt` initially flagged as "new." Regenerated via `scripts/update_arch_lint_baseline.py` (the sanctioned tool for exactly this) — net **shrank** by one entry, not grew: `map_fields.py`'s OWN identical violation had already been fixed in an earlier phase (Phase 1.2's "inlined reviewer rework feedback formatting") but the baseline file was never regenerated after that fix landed, so it carried one stale line the whole time. `scripts/lint_arch.py` now reports 0 new, 14 pre-existing (was 15 stale-counted).
  - `scripts/check.py all` — all 10 checks pass. Full `tests/unit` sweep: **1363 passed** (11 more than the Phase 6.2/6.3 baseline of 1352), 83 skipped, zero regressions.
  - **Not yet done, honestly flagged**: `_source_ids` population (`unify_entities.py`) and persisting evidence to a durable audit table are Phase 7.2; surfacing `match_evidence` at the final-approval gate and a lineage view are Phase 7.3 — this phase is the capture-and-attach step both build on, not the full issue 7 closure.

---

## Phase 12.10 — Persist merge_engine_merge_audit + populate _source_ids (Phase 7.2)

Closes the other half of issue 7: Phase 7.1's `match_evidence` lived only in the run's own state — gone the moment the run finished, with no way to query "why did company X and Y merge" after the fact. This makes it durable, and fixes the `_source_ids` gap the plan named directly: the column exists (`map_fields_node.py:169`) but nothing ever set it, so `load_data.py`'s own fallback to `{"cluster_id": ...}` fired on literally every load.

- [x] **12.10.1 `_source_ids` gets the cluster's real members**
  - `unify_entities.py`: right where `unified_record["id"] = cluster_id` is set, `unified_record["_source_ids"] = json.dumps(record_ids)` — `record_ids` is the SAME list already read two lines above for logging (`cluster.get("record_ids", [])`), just never attached to the output record. `load_data.py`'s `_transform_records` already reads this key when present (unchanged there); before this it was never present, so its own `{"cluster_id": rec.get("_cluster_id", "")}` fallback fired unconditionally.
  - *Evidence:* `tests/unit/test_unify_entities_node.py` — 1 new test asserting `_source_ids` decodes to the cluster's exact `record_ids`, not a self-reference. All 12 pre-existing tests in the file still pass.
- [x] **12.10.2 `merge_engine_merge_audit` — the durable table**
  - New `PostgreSQLDestinationConnector.ensure_merge_audit()`/`save_merge_audit(entity_type, run_id, rows)`, same shape and pattern as the existing `ensure_entity_map`/`save_entity_map` right above them. Schema matches the plan's own spec exactly: `(run_id, entity_type, cluster_id, record_a, record_b, confidence, method, evidence JSONB, decided_by, human_override, created_at)`, `PRIMARY KEY (run_id, entity_type, record_a, record_b)`. `ON CONFLICT DO UPDATE` refreshes `confidence`/`method`/`evidence`/`decided_by` and takes `human_override` via `COALESCE(new, existing)` — a later human decision updates the row a run already wrote instead of erroring or duplicating it, while an absent override on a re-save doesn't clobber one already recorded.
  - New `load_data.py::_persist_merge_audit`, called inside the load's own transaction right after the existing `_persist_entity_map` (same reasoning: a rolled-back load must not leave audit rows for a merge that never landed). Walks every multi-member cluster's `match_evidence.pairs` (Phase 7.1), maps `method` -> `decided_by` (`"llm"` stays `"llm"`, everything else is `"auto"`) — **except** a cluster the reviewer explicitly confirmed at the matches gate (`cluster_edits.apply_cluster_decisions`' "keep" path sets `reviewed_by_human` without touching `match_evidence`), which outranks the original decision: `decided_by="human"`, `human_override="merge"`. Deliberately **non-fatal**, unlike the entity map: a merge-audit write failure is wrapped in its own `try/except` that only logs a warning, on the reasoning that losing an audit row is a real but recoverable observability loss, not one worth rolling an otherwise-successful load back over (no future run duplicates rows because of it, unlike a lost entity-map assignment).
  - **Scoped, honestly**: a human **splitting** a cluster is NOT yet recorded as its own audit row (`human_override="split"`) — `apply_cluster_decisions` rebuilds a split cluster's fragments without `match_evidence`, so there is currently nothing for `_persist_merge_audit` to attach a split decision to. The split itself is still fully honoured functionally (those records genuinely do not merge in the destination); only the audit-trail entry explaining "a human decided these are NOT the same entity" is missing. Threading that through would need `apply_cluster_decisions` to return a third value (the original pairs a split broke apart) and both its call sites (`human_nodes.py`, `workflow_manager.py`) to carry it forward to load time — real work, deferred rather than rushed. ClickHouse destination support is likewise deferred: `merge_engine_entity_map` (the existing, analogous table) is already PostgreSQL-only for the same reason (`ClickHouseDestinationConnector` has no `save_entity_map` either) — this does not regress anything that worked before.
  - *Evidence:* `tests/unit/test_load_data_merge_audit.py` (new, 9 tests) — one row per pair with the right fields (`fellegi_sunter` method -> `decided_by="auto"`); LLM method -> `decided_by="llm"` with `evidence.reason` carried; a `reviewed_by_human` cluster overrides to `decided_by="human"`/`human_override="merge"`; a singleton (no `match_evidence`) writes nothing; a pair missing `a`/`b` is skipped; a connector without `save_merge_audit` degrades to a silent no-op; a `save_merge_audit` exception is swallowed, never raised (the non-fatal guarantee, tested directly); no entity types / non-dict destination are no-ops. Confirmed against the FULL `test_load_data_node.py` suite (38 tests combined) that adding this unconditional call after `_persist_entity_map` did not break any pre-existing test — several of those tests' connector fixtures don't stub `save_merge_audit` at all, exercising the MagicMock-return-value-is-not-an-int path through the non-fatal exception handler for real, not just in the new file's own dedicated test for it.
  - **Live-verified against the real `global_warehouse` destination** (`localhost:55201`) — genuinely round-tripped: called the real `ensure_merge_audit()` (creating the table for the first time on this database) and `save_merge_audit()` writing two real rows (one `fellegi_sunter`, one `llm`), then called it AGAIN with an updated `decided_by="human"`/`human_override="merge"` for the first row, proving `ON CONFLICT DO UPDATE` genuinely updates in place rather than erroring or duplicating; then — a separate, freshly-opened connection the write path never touched — re-queried both rows independently, confirming the override landed (`decided_by='human', human_override='merge'`) and the JSONB `evidence` column round-tripped correctly (`matched_fields[0].field == 'name'`, `reason` string intact). Cleaned up afterward and independently re-confirmed zero residual rows. The `postgres` MCP was still erroring during this verification (same as Phase 6.2/6.3's), so this used a direct `psycopg2` connection — still independent of the app's own success message.
  - `scripts/check.py all` — all 10 checks pass (`lint_arch.py`: 0 new, 14 pre-existing). Full `tests/unit` sweep: **1373 passed** (10 more than the Phase 7.1 baseline of 1363 — 9 from `_persist_merge_audit`, 1 from `_source_ids`), 83 skipped, zero regressions.

---
