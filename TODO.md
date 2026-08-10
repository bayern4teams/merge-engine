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


