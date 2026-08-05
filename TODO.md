# merge-engine — Implementation TODO

Approved plan: fix core pipeline bug, swap Langfuse→LangSmith tracing, seed test DBs, verify UI end-to-end.

Constraints:
- Work only inside `D:\transfer\merging`
- Always `conda activate na180` before running the app
- Exactly ONE destination configured at a time (any type — count limit, not type limit)

---

## Phase 0 — Seed test databases

- [x] 0.1 Verify Docker daemon is running and ports 5433/8123/9000 are free
- [x] 0.2 Start `postgres:17` container `merge-test-pg` on host port 5433 (user/pass: postgres/postgres)
- [x] 0.3 Create logical databases `crm_origin` and `main_dest` inside the Postgres container
- [x] 0.4 Start `clickhouse/clickhouse-server:latest` container `merge-test-ch` on ports 8123/9000
- [x] 0.5 Create database `hr_origin` inside ClickHouse
- [x] 0.6 Write `create_test_databases.py` — Postgres origin schema (companies, products, deals, people, founders, funding_rounds, locations, contacts, jobs, schools, skills)
- [x] 0.7 Write ClickHouse origin schema with DIFFERENT names for the same concepts (organizations, product_catalog, agreements, persons, org_founders, investment_rounds, sites, contact_points, employment_history, education_records, technologies)
- [x] 0.8 Write destination (`main_dest`) schema — unified naming, partially overlapping data
- [x] 0.9 Generate ~150 rows per table; ~30-40% of company/person entities shared across origins with DELIBERATELY DIFFERENT IDs
- [x] 0.10 Destination data: mix of overlapping-by-name records (different IDs again) + destination-only records
- [x] 0.11 Run seeding script under na180 env
- [x] 0.12 Verify row counts (~150/table) via direct queries against all 3 DBs
- [x] 0.13 Spot-check cross-origin ID mismatches for the same real-world entities

## Phase 1 — Run the app

- [x] 1.1 `conda activate na180 && python -m src.main --mode gradio --port 7860`
- [x] 1.2 Confirm no startup errors in terminal
- [x] 1.3 Confirm Gradio reachable at http://localhost:7860

## Phase 2 — Configuration Setup tab

- [x] 2.1 Add single-destination guard to `submit_destination` (block silent overwrite; require explicit change action)
- [x] 2.2 Playwright: fill Postgres origin credentials, blur → auto-connect fires, discovered databases populate
- [x] 2.3 Playwright: fill ClickHouse origin credentials, blur → auto-connect fires, discovered databases populate
- [x] 2.4 Playwright: click "➕ Add to Origins" for both origins, verify origins list
- [x] 2.5 Playwright: click "✅ Confirm Origins", verify state advances
- [x] 2.6 Playwright: fill destination credentials, blur → auto-connect, click "✅ Confirm Destination"
- [x] 2.7 Playwright: attempt second destination confirm → verify guard blocks/warns (single-destination rule)
- [x] 2.8 Playwright: verify both recent-connections dropdowns populate and selecting an entry refills fields
- [x] 2.9 Full app restart; verify recent connections persist from `config/recent_connections.json`

## Phase 3 — Pipeline tab: bug fixes, tracing swap, HITL verification

### Bug fixes
- [x] 3.1 Fix `parse_request.py:84` — read `entity_types` first (matches prompt contract), fall back to `entities`/`target_entities`/bare list
- [x] 3.2 Fix `main.py` — reset `langgraph_server_url` to `""` when `start_langgraph_server()` fails, so in-process WorkflowManager fallback triggers
- [x] 3.3 Empirically verify interrupt detection reports `interrupted` at HITL nodes — verified live 2026-07-19: in-process WorkflowManager path; all 4 HITL nodes reported `interrupted` via `poll_status` during a full run (LangGraph CLI server never became ready, so `server_manager.py`'s HTTP path stays exercised only by its unit tests)

### Langfuse → LangSmith swap
- [x] 3.4 Remove `_LangfuseContext` / `_get_langfuse_config` / `USE_LANGFUSE` from `src/llm/client.py`
- [x] 3.5 Remove dead `langfuse.callback.CallbackHandler` block from `src/orchestrator/workflow_manager.py`
- [x] 3.6 Add `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY=` (user fills), `LANGCHAIN_PROJECT=merge-engine` to `.env`; remove LANGFUSE_* placeholders
- [x] 3.7 Remove `langfuse:` block (with hardcoded keys) from `config/config.yaml`
- [x] 3.8 Update `requirements.txt` / `pyproject.toml`: remove langfuse, add langsmith
- [x] 3.9 Check config_loader.py for any langfuse config references and clean up

### Pipeline HITL testing (live browser-driven, 2026-07-19)
- [x] 3.10 Submit example merge prompt via "🚀 Start Merge"
- [x] 3.11 Verify pipeline advances past parse_request with non-empty `target_entities` (logs showed `['company', 'person']`)
- [x] 3.12 Verify analyze_deps makes its LLM call (no empty-entities early return; produced 22-table extraction order)
- [x] 3.13 Reach `human_review_deps` interrupt with non-empty dependency tree; Approve → resumes
- [x] 3.14 Reach `human_review_matches` — fired for the first time ever after seed-data fix (4 borderline company + 4 person pairs); Approve → resumes
- [x] 3.15 Reach `human_review_mappings` — fired after routing fix; Approve → resumes
- [x] 3.16 Reach `human_final_approval` (pauses correctly after adjacent-HITL resume fix); Approve → load_data runs → workflow completes
- [x] 3.17 Second run: test Reject — verified via headless WorkflowManager driver (same code path the UI buttons call): reject at `human_review_deps` → status `aborted` cleanly
- [x] 3.18 Test "⏹ Abort" mid-run — verified via headless driver: `abort_workflow` during `parse_request` → status `aborted`
- [x] 3.19 Poll timer updates status display — works, but with a caveat: poll ticks fire alongside UI events, so clicking Approve immediately after an interrupt can race the status update (first click syncs, second click submits)
- [x] 3.20 Verify merged data actually landed in `main_dest` with correctly mapped fields — final clean run (2026-07-19): 296 companies + 296 persons inserted; 150/table with mapped values from the Postgres origin (`company_name`→`name` etc.), 150/table with values in schema-extended columns from the ClickHouse origin (`org_title`, `sector`, …; 15 columns added, 0 failed), exactly 4 merged cross-origin clusters per entity (the seeded borderline pairs) carrying fields from both origins. Note: the LLM chose to add ClickHouse fields as new columns rather than map e.g. `org_title`→`name` — a mapping-quality choice, not a code bug; no data is dropped

### Tracing verification
- [x] 3.21 Confirm terminal DEBUG logs show ENTER/EXIT for every node in the run — verified in `logs/debug.log.bak-run2`: ENTER+EXIT present for parse_request, analyze_dependencies, discover_schemas, extract_data, resolve_entities, unify_entity_groups, map_fields, load_data_node, flag_origins_node, plus human-gate and connector/MCP-impl logging
- [ ] 3.22 Confirm trace spans for every node/LLM call appear in LangSmith UI (user checks their account after adding API key to `.env` — runs already attempt trace upload on every node/LLM call, currently 403 without a key, so spans will appear as soon as the key is set)

## Newly-discovered bugs (found & fixed during Phase 3 verification, 2026-07-19)

Crash bugs that prevented any merge from ever completing:
- [x] N.1 `unify_entity_group_impl` crashed on every cluster (`MCP server not initialized` ×600 in logs) — `_get_config().origins` was never initialized and `Config` has no `origins` field. Fixed: explicit `origins` parameter, caller passes `state["origins"]` (`src/mcp_server/tools/unify_entity_group.py`, `src/orchestrator/nodes/unify_entities.py`); removed identical dead `_get_config()` call in `map_fields.py`
- [x] N.2 ClickHouse `extract()` raised `UnboundLocalError` on every filtered extraction — dead leftover block referenced undefined `sql` (`src/mcp_server/connectors/clickhouse.py:188-190`, deleted)
- [x] N.3 ClickHouse `mark_imported` called nonexistent `client.execute_query(...)` — replaced with `client.command(sql, parameters=...)`; also fixed numeric substitution names (`{0:String}` → `{p0:String}`, ClickHouse requires identifiers)
- [x] N.4 Postgres `mark_imported` quoted `"public.companies"` as a single identifier — now quotes schema and table separately
- [x] N.5 `extract_data_impl()` called with unsupported `origin_id` kwarg — already fixed in uncommitted WIP (verified working)

HITL gates that could never fire:
- [x] N.6 `human_review_mappings` unreachable — three-part chain: `unapped_fields` typo, `has_unmapped_fields()` ignoring unmapped fields, dead confidence check in `human_nodes.py`. Fixed by threading a new `unmapped_fields` state key through `graph.py` / `map_fields_node.py` / `human_nodes.py`
- [x] N.7 `human_review_matches` never fired with seeded data — heuristic scores always fell outside the (0.70, 0.85) review band. Fixed in seed data: `match_test_ref` column with 4 company + 4 person deliberately borderline pairs (~0.7875 confidence, verified by direct calculation)
- [x] N.8 `human_final_approval` skipped on resume — adjacent HITL nodes (`human_review_mappings` → `human_final_approval`) meant `stream(None)` after `update_state(as_node=...)` executed the next gate as if resumed. Fixed in `workflow_manager.resume_workflow` (finalize instead of streaming when the pending node is a HITL gate) plus a node-level guard against stale feedback in `human_final_approval`

Load-path bugs:
- [x] N.9 `create_connector('postgresql')` unknown type — added `postgresql` alias to the connector registry
- [x] N.10 Schema extension called `extend_schema_batch` on the origin connector class (which lacks it) and wrongly awaited the sync method — `_apply_schema_changes` now uses `_get_destination_connector` (`src/orchestrator/nodes/load_data.py`)
- [x] N.13 `_transform_records` dropped every field flagged `requires_schema_change` ("not yet added") even though `_apply_schema_changes` adds those columns before load — so schema-extended columns were always NULL. Fixed in `src/mcp_server/tools/load_data.py`: fields are kept (with `dest_column or src_field` fallback for None dest_column); `load_batch` already intersects against the live table schema, so a failed column-add degrades to dropping just that column
- [x] N.12 Schema extension emitted invalid Postgres DDL — `ADD COLUMN ... STRING` (ClickHouse/LLM type names passed straight into `ALTER TABLE`). Added `_pg_ddl_type()` translator in `postgresql_destination.py` (String→TEXT, Int64→BIGINT, unwraps `Nullable(...)`, unknown→TEXT fallback) wired into `extend_schema` and `extend_schema_batch`
- [x] N.11 Double field-transformation nulled every renamed column (e.g. `company_name`→`name` was always NULL; only same-name columns survived) — `load_data_node` transformed records and then `load_data_impl` transformed them again. Fixed: node passes raw unified records; `load_data_impl` owns the single transform

Known design gap (documented, not fixed — out of scope):
- ~~`flag_origins_node` passes synthetic row indices...~~ **RESOLVED, see Phase 2 below.**
- Misc: `langsmith` added to `pyproject.toml` deps (was only in requirements.txt); stale port-5432 `origin_1` entry in `config/recent_connections.json` overwritten by live re-entry (now 5433)

## Phase 2 — Incremental, watermark-based extraction (end-user readiness pass, 2026-07-19)

Replaced the broken `_imported`-column/mark_imported design (origins never had that column, so the flag never worked and mark_imported also matched on synthetic row indices instead of real PKs). New design: origins are **read-only** — never written to. Incremental extraction state lives entirely in the destination's `merge_engine_extraction_state` table (one row per origin/table, not one per extracted row — O(number of tables), scales to millions of rows).

- [x] P2.1 PK + watermark-candidate discovery added to origin connectors (`postgres.py`, `clickhouse.py` `discover_schema`) via shared `src/mcp_server/connectors/watermark.py::detect_watermark_column` — timestamp-like column preferred (catches inserts+updates), monotonic single-column integer PK as fallback (insert-only capture)
- [x] P2.2 SQL-pushdown filtering: both origin connectors' `extract()` accept `watermark={"column","since","kind"}` and push `WHERE col > :since` down into the origin query, ordering by that column so the last row of the last batch carries the new high-water mark
- [x] P2.3 `merge_engine_extraction_state` tracking table added to both destination connectors (`ensure_extraction_state`/`get_extraction_state`/`update_extraction_state`)
- [x] P2.4 `extract_data.py` node wired to look up tracked watermark per table, pass it into extraction, and record the observed high-water mark in new state field `extraction_watermarks`
- [x] P2.5 `flag_origins_node` replaced by `update_extraction_state_node` — writes one UPSERT per tracked table after load; `mark_imported` removed entirely from `BaseConnector`, origin connectors, the `mark_imported` MCP tool/server registration, and the `_imported`-column filter path in `extract_data.py`
- [x] P2.6 `run_id` (workflow thread_id) threaded through `MergeState` → tracking table, for lineage
- [x] P2.7 Live verification: re-seeded DBs, ran the full pipeline twice via the headless `WorkflowManager` driver. Run 1 populated `merge_engine_extraction_state` with real per-table watermarks (e.g. `origin_1/public.companies` → `company_id`/numeric/`1149`). Run 2's debug log shows every table's extraction hitting its stored watermark and returning **0 rows** (`Extracted 0 rows from origin_1:public.companies for entity 'companies'`) — proof of incremental pushdown, not a full re-scan. Confirmed no `_imported` column and no writes on either origin database throughout.
- [x] P2.8 Unit tests updated/added: watermark discovery (postgres connector schema tests), `update_extraction_state_node` (replaces old `TestFlagOriginsNode`), stale `mark_imported`-related tests removed across `test_postgres_connector.py`, `test_connectors.py`, `test_connectors_base.py`, `test_mcp_server.py`, `test_mcp_extract_data.py`; `tests/unit/test_mcp_mark_imported.py` deleted. Full suite: 683 passed, 0 failed.

## Phase 3 — Prompt engineering: restructure, hybrid LLM, test harness (2026-07-19)

- [x] P3.1 Prompt convention: every stage is a `<name>.system.txt` + `<name>.human.txt` pair in `src/llm/prompts/`; `PROMPT_TEMPLATES` loader updated; old single-file prompts removed
- [x] P3.2 `call_json` retries once on malformed JSON (re-invokes with the parse error appended); `response_format={"type":"json_object"}` requested when `llm.structured_output: true` (silent fallback)
- [x] P3.3 `request_parsing` rewritten: strict `{"entity_types": [...]}` contract, few-shot examples incl. Persian/mixed-language and negations; dead `filters`/`sync_mode`/`deduplicate` keys dropped
- [x] P3.4 `dependency_analysis` rewritten: FK-reasoning rules + worked examples; LLM-failure fallback now surfaces a visible warning in the deps review gate instead of a silent flat tree
- [x] P3.5 Entity-resolution heuristic upgraded: token-set Jaccard + SequenceMatcher, blocking, real `_MAX_COMPARISONS` enforcement
- [x] P3.6 Hybrid LLM entity resolution: borderline pairs (0.70–0.95) judged in one batched call per entity type (`entity_resolution.system/human.txt`); LLM failure keeps heuristic scores
- [x] P3.7 Hybrid LLM field mapping: sub-0.85 fields go to the LLM in one call per entity type with sample values + live destination schema; output = map/new_column/skip per field; failure degrades to heuristics
- [x] P3.8 `_map_type_to_databricks` → `_map_type_generic` (Postgres/ClickHouse-appropriate types)
- [x] P3.9 Prompt harness `tests/prompts/harness.py` + golden cases; `--live` scorecard hit targets (request parsing 10/10, mapping org_title→name etc., resolution 4/4); offline contract checks re-verified green 2026-07-19

## Phase 4 — UI race fix + editable mappings gate (2026-07-19)

- [x] P4.1 `submit_feedback` re-fetches live `get_workflow_status` (poll-once/retry-once/visible-warning) — two-click Approve quirk gone
- [x] P4.2 `human_review_mappings` interrupt payload carries unified `mapping_rows` (remaps + new-column proposals + skip candidates)
- [x] P4.3 `mapping_panel` is an editable `gr.Dataframe` (entity_type, source_field, action map|new_column|skip, dest_column, new_column_name, new_column_type, confidence)
- [x] P4.4 Edits applied in `resume_workflow` via `apply_mapping_feedback` (HITL node bodies never run on resume); validated against live destination schema; invalid edits re-interrupt with an inline error
- [x] P4.5 `pending_schema_changes` reducer changed from `operator.add` to plain replace so cancelled proposals can be removed
- [x] P4.6 Unit tests: `tests/unit/test_mapping_gate.py` (15 tests)

## Phase 5 — Persistence across restarts (2026-07-19)

- [x] P5.1 `langgraph-checkpoint-sqlite` added to requirements/pyproject
- [x] P5.2 SQLite checkpointer at `data/checkpoints.db` — `_LoopSafeSqliteSaver` bridges async checkpoint methods to sync via `asyncio.to_thread` (graph runs use `astream` on per-run daemon-thread loops; a loop-bound AsyncSqliteSaver would break)
- [x] P5.3 `get_workflow_status` rehydrates unknown threads from `graph.get_state` (interrupted/completed/failed derived from `snapshot.next`)
- [x] P5.4 `data/` gitignored; graceful in-memory fallback when SQLite unavailable
- [x] P5.5 Unit tests: `tests/unit/test_workflow_persistence.py` (7 tests)

## Phase 6 — Reconciliation report + conflict tiebreaks (2026-07-19)

- [x] P6.1 `build_reconciliation_report`: per-entity extracted-per-origin/total, clusters, auto vs human-reviewed, unified records, conflicts (per-field + per-winning-rule), rows inserted/updated, new columns, errors
- [x] P6.2 Report shown in `human_final_approval` payload (pre-load preview) and written to `logs/run_reports/<run_id>.json` post-load
- [x] P6.3 Conflict tiebreak chain in `unify_entity_group.py`: priority → completeness → recency (`updated_at`-like field) → first_seen; each conflict records its `winning_rule`; `origins` shadowing bug fixed
- [x] P6.4 Unit tests: `tests/unit/test_conflict_tiebreaks.py` (17 tests)
- [ ] FUTURE WORK (documented, not built): rapidfuzz/embedding matching; per-field survivorship config; schema-drift detection between runs; multi-destination fan-out; partitioned/parallel extraction for very large tables

## Phase 7 — Final verification (in progress 2026-07-19)

Bugs found & fixed during verification:
- [x] P7.1 `SqliteSaver does not support async methods` on first run — fixed with `_LoopSafeSqliteSaver` async bridge (see P5.2)
- [x] P7.2 `company` entity extracted 0 rows — `_table_matches_entity` used an ad hoc substring heuristic that matched neither "companies" reliably nor "organizations" at all; now delegates to the shared `entity_aliases.concept_matches` + token fallback for satellite tables (suppressed when the table itself is a known concept, so `org_founders` can't leak into "company")
- [x] P7.3 Cross-origin entity resolution could never match (`matched=0`): blocking picked ONE global name field (pg `company_name`), dropping all ch records into the fallback block, and `_compute_heuristic_confidence` only compared common field names. Fixed: per-record best-name blocking + identifier-field second blocking pass (union, deduped) + cross-field name similarity (exact name → 0.96 auto-match; fuzzy capped at 0.93 → review band). Verified: 300 records → 250 clusters, exactly the 50 seeded cross-origin overlaps auto-merged, per entity
- [x] P7.3b Resolution scale fixes found on the next run: (a) ref-hint "number" matched `tel_number` and country-code first-token blocking lumped a whole origin into one block (10k truncated pairs) — "number" hint removed + oversized ref blocks (> 25 records) skipped as non-discriminative; (b) 820 borderline pairs went to the LLM in ONE giant call — now capped at 200 pairs/entity (closest-to-match first, rest keep heuristic scores), chunked 40/call, 4 chunks in parallel (`asyncio.gather` + semaphore); resolution wall time ~12 min → ~2 min per entity
- [x] P7.3c Loader bugs surfaced by the first completed run: (a) many-to-one mappings (`company_name` AND `org_title` → `name`) clobbered the mapped value with the absent origin's None — `_transform_records` now coalesces; (b) `person_id → id` mapping left `id` NULL for ch-only records (NOT NULL PK violation; whole person load failed) — null `id` now backfilled from `_cluster_id`/UUID, same as the unmapped case. LLM mapping quality itself confirmed excellent: `org_title→name`, `sector→industry`, `name_full→name`, `mail_address→email`, `tel_number→phone`, `year_of_birth→birth_year`, `sex→gender`; internal PKs skipped

Verification steps:
- [x] P7.4 Clean run 1 (wf_eb081b71, 2026-07-19): 150 rows extracted per table from all 4 origin tables; 250 unified records per entity loaded (405 total incl. 155 pre-seeded, 0 NULL names); exactly 50 cross-origin clusters merged per entity; 4 conflicts per entity (match_test_ref, winning_rule=priority); reconciliation report at `logs/run_reports/wf_eb081b71.json` matches direct DB counts; errors=[]
- [x] P7.5 Run 2 incremental proof (wf_e5a13a01): all 4 tables pushed `WHERE "<col>" > :since` down with a correctly typed numeric watermark and extracted 0 rows with **zero** extraction errors; run completed cleanly. (First attempt wf_88fb7f5b had LOOKED clean but the Postgres zeros were a masked DataError — TEXT `last_value` hit asyncpg's typed binds as a string. Numeric watermarks are now re-typed in the `extract_data` node before pushdown, with a unit test pinning the coercion.)
- [x] P7.6 Restart-mid-interrupt persistence proof (wf_d9b1a3ce): process 1 started a workflow and hard-exited (`os._exit`) at `human_review_deps`; a fresh process rehydrated the thread from `data/checkpoints.db` (`status=interrupted`, correct gate), approved, and the workflow resumed through to `human_final_approval`
- [x] P7.7 UI verification (2026-07-20, runs wf_c1f4a3c5 / wf_443c99d5 driven live in the Gradio UI via the Browser pane):
  - Single-click Approve confirmed at `human_review_deps`, `human_review_mappings`, and `human_final_approval` — every gate resumed on ONE click (`submit_feedback ENTER → EXIT (resumed)` in logs), no two-click race.
  - **New bug found & fixed during this verification**: the 2s `gr.Timer` poll re-sent `gr.update(value=rows)` to the editable mapping Dataframe on every tick, clobbering the reviewer's in-flight cell edits. `poll_status` now pushes the table value only on the FIRST render of the gate (keyed on `thread_id:node` in `chat_state`), so edits persist until Approve; a validation-error re-interrupt keeps the user's edited rows on screen.
  - Mapping edit round-trip proven end-to-end (wf_443c99d5): dropped `companies.website` from the destination so the gate must fire; LLM proposed `new_column: website (TEXT)`; edited `new_column_name` to `website_url` in the Dataframe; edit survived poll ticks, Approve carried it through `apply_mapping_feedback` (final-approval payload showed `companies.website_url TEXT`), load ran `ALTER TABLE`, and `main_dest.companies.website_url` now exists with 150 populated rows (no `website` column).
  - Reject re-checked (wf_6b2ca419): one Reject click at the deps gate → workflow aborted cleanly. Mid-flight Abort re-checked (wf_d3850f20): top ⏹ Abort on an in-flight workflow → aborted cleanly.
  - LLM switched to `qwen3.6-flash` (same DashScope key; prior model's quota exhausted) — verified live across parse/deps/resolution/mapping calls in wf_443c99d5.
  - Known limitation (documented, by design): resetting `merge_engine_extraction_state` and re-running from scratch re-inserts previously merged clusters under new UUIDs (multi-record cluster ids are not deterministic across runs). Incremental operation with intact watermarks does not hit this.
- [x] P7.8 Full `tests/unit` green after ALL Phase 7 fixes incl. the P7.7 mapping-panel no-clobber fix: 742 passed, 84 skipped, 0 failed (2026-07-20)
- [x] P7.9 README section 12 added (watermark tracking, prompt conventions, persistence, mapping gate, reconciliation); memory file updated

## Phase 8 — Scale & precision audit (2026-07-20)

Re-tested the "high precision" claim specifically against the millions-of-rows bar (all prior testing was at ~150 rows/table) and expanded the adversarial LLM test set:

- [x] P8.1 Live prompt harness re-run on `qwen3.6-flash`: 25/25 original golden cases, 0 malformed JSON, faster than the prior model. Added 8 new adversarial cases (nickname/formal-name equivalence, company renamed post-merger with identical tax ID, a **conflicting-identifier-beats-name-similarity** case designed to catch a model over-trusting surface similarity, cryptic 2-letter field abbreviations, a genuinely-unmappable field forced to `new_column`) — all 36/36 pass, 0 malformed JSON.
- [x] P8.2 Audited the 84 skipped unit tests: all deliberate (databricks + postgresql_destination connector tests excluded per prior user request; one dead test for a removed function). No real coverage gap hiding behind the skip count.
- [ ] P8.3 **Found, not yet fixed**: `postgres.py`/`clickhouse.py` extraction batches paginate with `OFFSET/LIMIT` and run a full `COUNT(*)` on every batch — both degrade badly on multi-million-row tables (offset scan-and-discard cost grows with depth; repeated full-table counts). `extract_data.py` also accumulates every row of an entity type into one in-memory Python list before resolution starts, which then gets checkpointed to SQLite on every graph step. None of this was reachable at 150-row test scale. Fix plan (keyset pagination reusing the existing watermark column, hoist the count, stream/chunk instead of full accumulation) written into the plan file — **deferred pending explicit user go-ahead, per the user's requested execution order (UI first).**
- [x] P8.4 **Found and root-caused, fix deferred alongside P8.3**: cluster IDs are `uuid.uuid4().hex` (random) at `resolve_entities.py:138,694`, and `load_data.py:220` uses that same id as the destination row's primary key when no better id exists — so re-running the pipeline against unchanged data creates duplicate destination rows instead of updating them. Deterministic fix (hash of sorted source record ids) identified, not yet applied.
- [x] P8.5 LangSmith tracing confirmed broken: every live call returns `403 Forbidden` from `api.smith.langchain.com` despite `LANGCHAIN_API_KEY` being set — the key itself is invalid/unauthorized, not a wiring bug. Needs a fresh key from the user.

## Phase 9 — Gradio wizard UI rework (2026-07-20)

User feedback: the Merge Pipeline tab was "the worst frontend," with 4 competing status/log surfaces, all 4 HITL gates stacked in one always-open accordion with generic identically-styled buttons, and reviewers unable to tell what they were actually being asked to verify (entity-match review showed raw internal record ids like `origin_1:companies:42`, not the actual field values that would let a human judge a match).

- [x] P9.1 Consolidated status: `status_output` was silently showing the thread id under a "Status" label (`poll_status`'s 7th return value was never actually a status string) — replaced with a real human-readable status line (`_get_status_html`, e.g. "⏸ Awaiting your review — Review Dependency Tree"). Removed the dead `chat_output` "Activity Log" textbox (filled once by `start_workflow`, never updated again). Defined the `.progress-step`/`.event-log` CSS classes that were already referenced in the HTML but never styled.
- [x] P9.2 Real 4-step wizard indicator (`_get_gate_stepper_html`) built from the same `node`/`title` fields `_build_interrupt_data` already produces — done/current/pending states, hand-built HTML+CSS (Gradio has no native stepper). Each HITL gate's panel + Approve/Reject buttons now live inside its own `gr.Group(visible=...)` — previously only the panel content toggled visibility while the Approve/Reject buttons for ALL 4 gates stayed visible/clickable at all times regardless of which gate was actually active.
- [x] P9.3 Entity-match review now shows actual field values, not opaque ids: `_build_interrupt_data`'s `human_review_matches` branch cross-references `extracted_data` by the same `_origin_id:_origin_table:_origin_id_row` key used elsewhere and attaches a `records: [{record_id, fields}]` list per cluster; `render_match_reviewer` shows each candidate record as a card of real field key/values with a plain-language framing sentence, instead of `Cluster: abc123 / Records: id1, id2`.
- [x] P9.4 Approve buttons switched from `variant="stop"` (red — backwards for an approve action) to a dedicated `.btn-approve` green gradient class; Reject/Abort correctly stay `variant="stop"`.
- [x] P9.5 Recent-connection dropdown labels unified into one function (`_conn_label`, used by both `_get_recent_display` and `_find_recent_item` so they can't drift) with a type icon (🐘/⚡/🍃) prefix.
- [x] P9.6 Output-tuple contract for `poll_status`/`submit_feedback`/`abort_workflow`/`start_workflow` rewritten (8→13 values for the poll/feedback trio; `start_workflow` now returns 6) with a shared `_blank_gate_result` helper for every idle/transitional return path, so the tuple shape can only drift in one place. Verified: existing correctness fixes preserved — the mapping-gate poll-clobber guard, the live-status re-fetch in `submit_feedback` (approve-race fix), single-destination guard.
- [x] P9.7 Verified two ways: (a) direct Python-level invocation of `poll_status` against a real interrupted workflow (`wf_59ba59b3`) confirmed correct output for both `human_review_deps` (stepper step 1/4 current, only `dep_group` visible) and `human_final_approval` (skipped gates 2-3 correctly shown as done, only `summary_group` visible); (b) live in the Browser pane — single active gate card, green Approve button confirmed via computed style, dropdown icons rendered, Approve click correctly resumed the backend workflow end-to-end. (The Browser preview pane's `gr.Timer` client updates were intermittently throttled in the background between tool calls — confirmed via server-side log timestamps to be tab-backgrounding behavior, not a code defect; direct-invocation testing was used to get a reliable signal alongside the live check.)
- [x] P9.8 14 stale unit tests updated for the new contracts (not reverted): `TestStartWorkflow`/`TestPollStatus` in `test_gradio_app.py` (6→13-value tuples, validation messages now go through `_system_messages` instead of a returned chat transcript), `TestRenderMatchReviewer` in `test_match_reviewer.py` and `test_ui_components.py` (new field-value-cards content, no more raw `Cluster:`/`Records:` labels). Full suite green: 742 passed, 84 skipped, 0 failed.

**Per the user's explicit instruction this session: Phase 8 (P8.3/P8.4 fixes) is written up but deliberately NOT implemented yet — waiting for explicit go-ahead before starting scale/precision work now that the UI rework is done.**

## Phase E — Execution-mode fix, Studio integration, reliability hardening (2026-07-20)

User-reported failures: a real 2-origin merge timed out after ~300s with no status feedback; LangGraph Studio showed "Failed to fetch"; ClickHouse `hr_origin` discovery logged SYNTAX_ERROR. All root-caused and fixed:

- [x] E.1 **300s timeout root cause**: installing `langgraph-cli` (for Studio) made `main.py`'s auto-started dev server come up, which flipped the UI from the proven in-process `WorkflowManager` into `LangGraphServerManager` HTTP mode, where `threads.create()` hung the SDK's default 300s read timeout. Fixed: `main.py` still auto-starts `langgraph dev` for Studio/tracing, but never passes its URL to `create_app` — the merge always executes in-process. Verified live: workflow starts instantly (`wf_` thread id), reaches `human_review_deps` in ~25s.
- [x] E.2 **Dev-server pipe deadlock** (why Studio said "Failed to fetch"): `langgraph dev` was spawned with unread `subprocess.PIPE`s — once the ~64KB buffer filled, the server blocked mid-write and stopped serving HTTP entirely. Fixed: stdout/stderr → `logs/langgraph_dev.log`, child env forces UTF-8 (CLI emoji output crashes under cp1252 when stdout is a file), added `--no-browser --allow-blocking` (graph nodes use sync DB drivers). Also: ready-wait 30s→60s (cold start is slow on Windows), and timeout/shutdown now kill the full process TREE (`taskkill /T`) — plain terminate() leaves an orphaned multiprocessing worker holding :2024, which blocks every later start.
- [x] E.3 `server_manager.py` SDK client timeout (5,15,15,5) — any future HTTP-mode use fails in seconds with a clean error instead of a 5-minute hang.
- [x] E.4 `start_workflow` UI call site wrapped in try/except — failures now surface as a red status/event-log message instead of a raw Gradio traceback with frozen lights.
- [x] E.5 ClickHouse discovery (`db_discovery.py`): SQLAlchemy-style `:db`/`:tbl` binds → clickhouse-connect server-side `{db:String}`/`{tbl:String}`; plus two latent bugs the syntax error was masking — `result_columns[0]` iterated as if rows (would have produced first-CHARACTER-of-name table lists) → `result_rows`, and the count query now backtick-quotes identifiers. Verified live: `hr_origin` lists all 11 tables with columns/row counts.
- [x] E.6 `load_origin_from_recent` never returned the saved connection's TYPE, so selecting the ClickHouse recent entry auto-tested with the postgres driver against :8123 (timeout). Now returns type first, mirroring `load_destination_from_recent`. Verified live.
- [x] E.7 **New: page-refresh workflow adoption.** A browser refresh created a fresh Gradio session with empty `chat_state`, permanently orphaning the still-running workflow from the UI. Added `adopt_active_workflow` wired to `app.load`: a fresh session adopts the most recent `list_active_workflows()` entry and immediately polls, restoring status + active gate. (`get_workflow_status` already falls back to `rehydrate_workflow` for app restarts.)
- [x] E.8 Unit tests re-run after all changes: 742 passed, 84 skipped, 0 failed.
- [ ] E.9 LangSmith tracing still blocked by the invalid API key (P8.5): verified again this session — `api.smith.langchain.com` returns 403 for the configured key. **User action needed: generate a fresh key at smith.langchain.com → Settings → API Keys and replace `LANGCHAIN_API_KEY` in `.env`.** Studio graph visualization works without it (browser → localhost:2024 directly).

## Phase F — HITL gate transparency & log clarity (2026-07-20)

A real user-driven merge run surfaced gaps prior "verified" passes missed: the dependency gate showed only a generic sentence (no findings), only 2 of 4 gates appeared, the activity log was flooded with repeated `[ERROR]/[WARN] Checkpoint` lines, and LangSmith/Studio still looked broken. User bar: **no gate may be approved without showing what was found.** All root-caused via 3 parallel investigations and fixed:

- [x] F.1 **`gr.HTML`-component-vs-string bug** (direct cause of the blank dependency gate): `render_dependency_editor`/`render_match_reviewer`/`render_final_summary`/`render_mapping_reviewer` returned a `gr.HTML(...)` *instance*, but `poll_status` passes them as `gr.update(value=...)`, which expects a plain string. All four now return `str`. UI-component tests updated from the `.value`/`hasattr(value)` contract to the string contract. Verified live: dependency gate now renders the real extraction order + dependency matrix.
- [x] F.2 **Mapping gate showed only the schema-change subset.** New shared `WorkflowManager._mapping_rows_from_values()` emits a row for EVERY field in `field_mappings` (auto-mapped included). Final-approval payload also carries `field_mappings_rows` so the complete table is visible even when the mapping gate auto-skips. Verified live: full 13-field table shown at both the mapping gate and final approval.
- [x] F.3 **Auto-match/auto-exclude counts surfaced.** New `resolution_stats` MergeState key (per-entity `auto_matched`/`auto_excluded`/`llm_scored`, `operator.or_`-merged, populated in the resolve node) → `WorkflowManager._auto_match_stats()` → shown on the match reviewer banner and the final summary. Verified live: "50 auto-matched (≥85%), 1095 auto-excluded (<70%), 140 LLM-judged."
- [x] F.4 **Auto-skipped gates now visibly distinct.** `_derive_skipped_gates()` re-applies the graph's routing predicates over checkpoint values (+ a `visited_gates` list) to compute `{gate: reason}`, returned by `get_workflow_status` as `skipped_gates`. `_get_gate_stepper_html` renders them as `⏭ <label> (auto)` (dashed-blue `.gate-skipped` + reason tooltip), and the final summary has an "Automatic decisions (review gates skipped)" section. Verified live: stepper showed `✓Dependencies ⏭Entity Matches (auto) ✓Field Mappings 4Final Approval`.
- [x] F.5 **Poll-tick log spam fixed.** `poll_status` logged the same checkpoint/status strings every 2s tick at ERROR/WARN; now deduped via `chat_state["last_status_log"]`/`["last_gate_log"]`, and an open gate logs at INFO ("Review required at checkpoint" was mislabeled — an interrupt is expected state, not an error). Verified live: each checkpoint logs once per gate. (Minor residual: the auto-skip reason logs once per *subsequent* gate, ~twice total per run — not per-tick.)
- [x] F.6/F.7 **Studio "Failed to fetch" was transient**, not a bug: `langgraph dev` was simply not running when checked (clean shutdown in `logs/langgraph_dev.log`). With the app up, `http://127.0.0.1:2024/ok` and `/docs` both return 200. No repo-level `--host`/CORS config exists; chase that only if it reproduces against a confirmed-alive server.
- [x] F.8 Full unit suite green after all Phase F changes: **742 passed, 84 skipped, 0 failed.** Live end-to-end run loaded companies into `main_dest` and created the new `org_key` column (schema-change mapping round-tripped).
- [ ] F.9 **LangSmith LLM-span visibility still blocked by E.9's invalid key** (NOT a code bug — every call already goes through traced `ChatOpenAI/ChatAnthropic.ainvoke`). Same user action as E.9: replace `LANGCHAIN_API_KEY` in `.env`, then a live run will show nested prompt/response spans.
- [ ] F.10 Not yet seen live: the match reviewer panel with low-confidence clusters (this seed data yields all matches ≥0.85, so the match gate auto-skips). The renderer + `auto_stats` banner are unit-tested and the code path is exercised, but a live low-confidence run would be stronger proof — tune seed data if a full 4-gate live walkthrough is wanted.

## Phase G — Replace Gradio with FastAPI + React frontend (2026-07-20)

User: "gradio is driving me crazy" — wanted a minimal, simple, visually good custom frontend. Decided (via AskUserQuestion): **FastAPI backend + React (Vite/TS) frontend, same wizard flow, fully replace Gradio.** The backend was already framework-agnostic (`WorkflowManager`/`db_discovery` have zero gradio coupling), so this was a pure presentation-layer swap.

- [x] G.1 **FastAPI backend** (`src/api/main.py`, new): thin JSON wrappers — `POST /api/connections/test`, `GET`/`POST /api/connections/recent`, `POST /api/workflow/start`, `GET /api/workflow/{id}/status`, `POST /api/workflow/{id}/feedback`, `POST /api/workflow/{id}/abort`, `GET /api/workflow/active`. Status endpoint returns the raw `_build_interrupt_data` dict (React renders it). Serves the built React bundle via `StaticFiles` so it's one process/one port. Recent-connection persistence relocated to gradio-free `src/ui/connections.py`.
- [x] G.2 **React frontend** (`src/ui/frontend/`, new — Vite + React + TS): two screens mirroring the old tabs — Setup (origin/destination forms with on-blur discovery, recent-connection dropdowns, single-destination lock) and Pipeline (request box, 4-step stepper, one active gate card at a time, 2s status poll, activity log). Hand-rolled design-token CSS, small components (`ConnectionForm`, `Stepper`, `Gates`, `EventLog`, `SetupScreen`, `PipelineScreen`). `npm run build` → `dist/` served by FastAPI.
- [x] G.3 **Preserved every correctness fix**: mapping-edit round-trip (`mapping_edits` shape unchanged, first-render-only reset guard via `useEffect([node])`), approve-race guard (feedback endpoint re-fetches live status with retry before resuming), single-destination lock, auto-skip stepper visibility + reasons, event-log dedup (logs only on genuine status/gate transitions, not per poll tick).
- [x] G.4 **Cut over + removed Gradio**: `src/main.py` now runs `uvicorn` serving the FastAPI app (workflow still executes in-process); deleted `gradio_app.py`, the 4 `render_*` component files, `origin_loader.py`, and their gradio-specific tests; removed `gradio` from `requirements.txt`/`pyproject.toml` (added `fastapi`/`uvicorn`); rewrote `src/ui/__init__.py`. Runtime verified importable with gradio blocked. Added `tests/unit/test_api.py` (12 endpoint tests). **Unit suite: 625 passed, 83 skipped, 0 failed.**
- [x] G.5 **Verified live** in the Browser pane against the seeded DBs (crm_origin PG + hr_origin CH → main_dest PG): configured both origins (auto-discovery worked) + destination (single-dest lock confirmed), ran a full merge — dependency gate showed the real extraction order, matches + mappings gates auto-skipped with the stepper showing `✓Dependencies ⏭Entity Matches (auto) ⏭Field Mappings (auto) 4Final Approval` + reason banners ("50 auto-matched, 921 auto-excluded", "all 13 fields mapped"), final approval showed the full 13-field mapping table + reconciliation, approved through, **755 companies + 579 persons landed in main_dest**. Activity log: 13 lines total across the whole run (no per-tick spam).
- [ ] G.6 Not seen live this run: the **mapping-edit round-trip through the new UI** (the mapping gate auto-skipped — non-deterministic, same as Phase F's non-mapping runs). The editable `MappingBody` table + `mapping_edits` submission are implemented and the backend path (`apply_mapping_feedback`) is unit-tested (`test_mapping_gate.py`); a run where the LLM proposes a schema change would exercise it end-to-end.

## Phase H — Replace LangSmith with self-hosted Langfuse (2026-07-21)

User: "migrate from langsmith to langfuse — it is local and all control is on my hand, doesn't have these connection errors. Delete everything related to langsmith. Create project + public/private keys, integrate completely, check every tracing + node + HITL. No excuse is acceptable." LangSmith's key had 403'd since Phase E (E.9/F.9, never resolved) and required reaching api.smith.langchain.com. Decisions (via AskUserQuestion): remove `langgraph dev`/Studio too (Studio UI is smith.langchain.com-hosted), run Langfuse as a standalone docker-compose stack, auto-bootstrap keys.

- [x] H.1 **Self-hosted Langfuse stack** (`docker/langfuse-compose.yml`, `docker/langfuse.env` [gitignored]): official v3 6-container stack (postgres/clickhouse/redis/minio/web/worker), based on upstream compose but with postgres/redis/clickhouse host-port publishes REMOVED (they collide with delta_db:5432, delta_redis:6379, merge-test-ch:8123/9000 — reached internal-network-only instead). Only langfuse-web:3000 + minio:9090 exposed. `LANGFUSE_INIT_*` vars auto-create org/project/admin-user/API-key pair on first boot — no manual signup. Verified: all 6 containers healthy, `http://localhost:3000` up, bootstrapped keys authenticate (`GET /api/public/projects` → the `merge-engine` project; bad key → 401).
- [x] H.2 **Tracing wired in** (`src/llm/tracing.py` new): one shared `langfuse.langchain.CallbackHandler` (v4 SDK, global-client model, `load_dotenv` before Langfuse import). `trace_config(session_id, tags)` returns a RunnableConfig fragment (empty dict when unconfigured → safe no-op). `client.py` passes it to all 3 `ainvoke` sites (every `LLMClient` call → a generation span). `workflow_manager.py` binds the same handler + `langfuse_session_id=thread_id` into the run config built at workflow creation — reused by both the start (`astream(initial_state)`) and every HITL resume (`astream(None)`), so a run's whole lifecycle lands in one Langfuse session with a span per node. Smoke-tested: a traced call landed with correct session + tags.
- [x] H.3 **Deleted all LangSmith** : `src/main.py` lost `start_langgraph_server`/`_wait_for_server`/`_kill_process_tree` + `--start-langgraph`/`--langgraph-port` args + the main() startup/cleanup blocks (no more langgraph dev/Studio). `.env`/`.env.example`: `LANGCHAIN_*` → `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST`. `requirements.txt`/`pyproject.toml`: dropped `langsmith`, added `langfuse>=3.0.0`. `config/config.yaml` tracing comment block rewritten for Langfuse. Deleted orphaned `src/orchestrator/server_manager.py` (langgraph-server HTTP client, unused after dev-server removal) + its dead manual test `tests/integration/test_full_pipeline.py`. Harness docstring updated + `flush()` added for the short-lived script. Verified: no `LANGCHAIN`/`LANGSMITH` env remains, LangChain `tracing_is_enabled()` == False (zero cloud calls; the `langsmith` lib stays only as langchain's inert transitive dep), Langfuse handler active on localhost. Fixed 1 test mock (`test_batch_call` ainvoke now accepts `config`). **Unit suite: 624 passed, 83 skipped, 0 failed.**
- [x] H.4 **Verified live end-to-end** in the Browser pane (crm_origin PG + hr_origin CH → main_dest PG): configured both origins + destination, started a merge, HITL dialog + Approve/Reject + status tracker + stepper all live, walked dependency gate → (matches/mappings auto-skipped, shown as `⏭(auto)` with reason tooltips) → final approval, **755 companies + 579 persons landed in main_dest**, UI reached "✅ completed". Langfuse (session `wf_272b5e68`): 3 root `LangGraph` traces (initial + 2 HITL resumes, all tagged merge-engine), CHAIN spans for **every** node (parse_request, discover_schemas, analyze_dependencies, extract_data, resolve_entities, map_fields, unify_entities, load_data + routing nodes), and `ChatOpenAI`/qwen3.6-flash GENERATION spans with full prompt/completion content + token usage (input/output/reasoning). "Langfuse tracing enabled" logged on first LLM call; app boots with no langgraph-dev/LangSmith.
- [ ] H.5 Not exercised live (same non-determinism as F.10/G.6): mapping-edit round-trip + low-confidence match gate (this seed auto-skips both). Backend paths unit-tested; a schema-change/low-confidence run would exercise them.

## Phase I — UI/UX polish on the React frontend (2026-07-21)

User asked for a few lightweight, still-simple ("just for test") UX improvements while reviewing the new frontend. Chosen (via AskUserQuestion): inline field validation, dark mode toggle, collapsible/persistent event log.

- [x] I.1 **Inline field validation** (`ConnectionForm.tsx`): required fields (host/user/database) now show a per-field red outline + "Required" message directly under the field on blur when empty, instead of only the page-level banner. `errors` state cleared on valid input or when a recent connection is loaded. Verified live (dark mode): blurring empty Host → `.field.invalid` class + "Required" text + red border (`var(--red)`).
- [x] I.2 **Dark mode toggle** (`App.tsx` + `styles.css`): 🌙/☀️ button in the header (`margin-left:auto`), sets `data-theme` on `<html>`, persisted in `localStorage["me-theme"]`. Added `:root[data-theme="dark"]` override block reusing the existing design-token variable names. **Also fixed a latent CSS bug**: the base `input,select` rule used the `border` shorthand with `var(--border-strong)`, which didn't re-resolve on theme change — split into `border-width/style/color` longhands so inputs pick up the dark border correctly. Verified live: toggling flips body bg (`#f6f7f9`↔`#0f1115`), cards, inputs, and accent colors; choice persists in localStorage.
- [x] I.3 **Collapsible/persistent event log** (`EventLog.tsx` + `styles.css`): the activity log is now a collapsible drawer with a caret + line-count badge header; open/closed state persisted in `localStorage["me-log-open"]` (read in the `useState` initializer) so it survives gate transitions and reloads. Log data still lives in `PipelineScreen` state (unchanged), so collapsing never loses lines. Verified live during an active run: collapse hides the log (caret closed, badge still shows count, localStorage=false), re-expand restores it (localStorage=true).
- [x] I.4 Frontend rebuilt (`npm run build`, tsc clean) and served by FastAPI. Python unit suite unaffected (Phase I is TS/CSS only) — last green run 624/83/0 from Phase H. **Note:** getComputedStyle in the Browser-pane renderer returns stale var()-derived border colors until a reflow is forced — the CSS is correct (confirmed after `offsetHeight` reflow), this is a readback quirk, not a bug.

## Phase J — Validation against real-world third-party databases (2026-07-21)

User: "go and search web and find for me 3 different databases ... check our app with those new databases ... check all output information and schema and data". Until now every test ran against seed data **we** generated, which shares our own assumptions. Three independent, publicly published datasets were used instead — one per supported engine.

### J.1 Databases stood up and manually verified before touching the app
- [x] **Pagila** (PostgreSQL) — official PG sample DB (github.com/devrimgunduz/pagila), loaded into new container `real-test-pg` (port **5434**, postgres/postgres), database `pagila`. 22 base tables + 7 views. Verified directly via psql: film 1000, actor 200, customer 599, payment 16049 (partitioned), rental 16044, city 600, staff 1500, store 500; joins work (top renter ELEANOR HUNT, 46 rentals); **0 orphan rentals** (referential integrity intact). Note: current master pagila is larger than the classic version (500 stores / 1500 staff) — deliberately, for stress testing.
- [x] **Cell Towers** (ClickHouse) — official ClickHouse tutorial dataset, loaded from `datasets.clickhouse.com/cell_towers.csv.xz` straight over `url()` into `cell_towers_db.cell_towers` on the existing `merge-test-ch`. **43,276,150 rows**, 14 typed columns (incl. an `Enum8` radio type). Verified: radio breakdown sums exactly to the total (UMTS 20,686,487 / LTE 12,101,148 / GSM 9,931,304 / CDMA 556,344 / NR 867). A 5,000-row `cell_towers_sample` table was derived for pipeline-speed testing.
- [x] **sample_mflix** (MongoDB) — official MongoDB Atlas sample dataset (JSON dumps via the neelabalan mirror), loaded with `mongoimport` into new container `real-test-mongo` (port 27017, admin/mongopass, auth enabled). movies 23,539 / comments 50,304 / theaters 1,564 / users 185, 0 import failures. Verified nested-document integrity via mongosh (e.g. "The Great Train Robbery" with nested `imdb`, `tomatoes`, `awards` sub-documents and array fields).
- [x] All three confirmed reachable **through the app's own `test_and_discover_schemas`** before any UI work — correct databases, tables/collections, per-column types and row counts for all three engines.

### J.2 Bugs found by real data (all fixed; all previously invisible to seed data)
- [x] **J.2a MongoDB origins were entirely broken in the pipeline.** `MongoDBConnector._ensure_client` demanded a pre-built `connection.uri`, but the UI/API supply host/port/user/password — so every Mongo origin failed schema discovery with "MongoDB connection URI is required", while the Setup tab's discovery (which builds its own URI) appeared to work. Fixed to build the URI from host/port/user/password (URL-encoded via `quote_plus`) and to accept both flat and nested config shapes, matching the convention already used in `postgres.py`. 2 new unit tests.
- [x] **J.2b Connectors were never closed — crashed the pipeline on HITL resume.** `discover_schema` / `extract_data` / `extend_schema` created connectors and never called `close()`. The workflow manager creates and closes a **fresh event loop per run and per resume**, so orphaned asyncpg/Mongo sockets from gate 1 killed the loop at gate 2: `WinError 995` -> `ConnectionResetError` -> `asyncio.InvalidStateError: invalid state`, surfacing in the UI as "Workflow failed" right after the first Approve. Fixed with `try/finally: await connector.close()` at all three sites.
- [x] **J.2c BSON types broke LangGraph checkpointing.** Mongo `ObjectId` (and any other BSON-only type) flowed into graph state via `_flatten_doc`, and the msgpack checkpointer rejected it: `Type is not msgpack serializable: ObjectId`. Fixed with a `_to_serializable` coercion at the connector boundary (recurses lists/dicts, keeps primitives + datetime, stringifies anything else).
- [x] **J.2d The pipeline could not load into an empty destination.** Loading only ever ran `ALTER TABLE ADD COLUMN`; nothing ever issued `CREATE TABLE`. Every prior "success" relied on `main_dest` already being seeded by us. Against a genuinely fresh database it failed with `relation "public.person" does not exist`. Added `PostgreSQLDestinationConnector.ensure_table` (`CREATE TABLE IF NOT EXISTS <t> (id TEXT PRIMARY KEY)`; remaining columns still arrive via schema extension), called before schema extension and before load, guarded with `hasattr` so other destination types are unaffected.
- [x] **J.2e Watermarks advanced even when the load failed — silent data loss.** `update_extraction_state_node` persisted watermarks unconditionally, so after J.2d's failed load the next run saw `staff_id > 1499`, extracted **0 rows**, and reported a clean success having loaded nothing; those rows would never have been loaded again. Fixed: `load_data_node` now records `failed_entities`, exposed as `load_failed_entities` on the reconciliation report; the state node refuses to advance any watermark when a load failed, and logs why. Verified live — the retry re-extracted all 1500 rows.
- [x] **J.2f Entity resolution silently destroyed 299 distinct people (precision bug).** `_NAME_FIELD_HINTS` matches `"name"` as a *substring*, so on Pagila's staff table `first_name` was selected as "the entity name". `_cross_name_similarity` returns **0.96** for an exact match — above the 0.95 auto-merge threshold — so four different people named Rory (different last names, **different emails**) were auto-merged into one record **with no human review**. The source has 1500 people with 1500 distinct emails and exactly ONE genuine duplicate; only 1201 rows landed. Two fixes: (1) `_PARTIAL_NAME_FIELDS` — a match on a partial name (`first_name` / `last_name` / `username` / ...) scores 0.93, landing it in the LLM/human review band instead of auto-merging; (2) `_has_conflicting_identity` — records whose unique identifiers (`email` / `tax_id` / `vat` / `ssn`) are both present and **differ** are capped below auto-merge regardless of name similarity. Result: auto-merges went 359 -> **0**, and all four Rorys are preserved.

### J.3 Verified live end-to-end (Pagila PG + sample_mflix Mongo -> empty `movies_dest` PG)
- [x] Both origins discovered in the UI with correct tables/collections and row counts; destination locked; merge driven through **every** HITL gate in the Browser pane.
- [x] **Mapping-edit round-trip finally exercised live** — closes the long-standing G.6 / H.5 gap. The Field Mappings gate fired with all 11 real Pagila columns; two rows edited in the UI (`email` -> new column `contact_email`, `password` -> `skip`); both survived to the final-approval summary (10 mappings, no `password`) **and into the physical destination schema**: `person` has `contact_email` and has no `password` column.
- [x] Final load: table auto-created, 8 columns added, **1357 rows inserted, 0 failed, 0 errors**; watermark advanced only after the successful load.
- [x] Data verified directly in `movies_dest`: 1357 rows / 1357 distinct ids / 1357 distinct emails / 0 nulls in name+email; real Pagila values. Previously-broken case now correct — all 4 distinct `Rory` people present (was 1).
- [x] Match stats after the precision fix: **0 auto-matched, 200 auto-excluded, 200 judged by the LLM** (was 359 blind auto-merges).
- [x] Langfuse traced the whole run (session `wf_786d239b`): 3 root `LangGraph` traces across the initial run + HITL resumes, with `ChatOpenAI` / qwen3.6-flash GENERATION spans.
- [x] **Unit suite: 626 passed, 83 skipped, 0 failed** (+2 new MongoDB connector tests).

### J.4 Known remaining gaps (honest)
- [ ] **Entity/table candidate selection only picked ONE source table.** For entity `person` the pipeline chose `public.staff` alone — it never considered Pagila's `customer` table or Mongo's `users` collection, despite the request naming all three and both origins being discovered correctly. So this run was effectively single-origin. This is the largest remaining gap, and it is *not* a regression from J.2 — it is how candidate matching currently behaves.
- [ ] Over-merging is reduced but not eliminated: 1357 clusters from 1500 records where only 1 genuine duplicate exists (~142 pairs still merged by LLM judgment on shared first names). The heuristic no longer auto-merges them, but LLM adjudication is not yet tuned for this partial-name case.
- [ ] The 43M-row ClickHouse table was loaded and verified but **not** run through the pipeline (only the 5k sample table exists for that) — the scale test remains Phase A work.

## Phase K — HITL gates that actually gate (2026-07-21)

User asked for every backend improvement worth making (algorithms, precision, scale,
LLM usage, exception handling, reject/customisation behaviour). Planning surfaced that
the human gates were largely decorative, so this phase fixes that before anything else.
Confirmed with the user: over-merging is the worse error; engineer for 10M+ rows; use
the LLM more at the metadata layer; order = correctness → precision → resilience →
scale → customisation. Phases L/M/N/O are planned and gated on explicit go-ahead.

### K.1 Reject at the final gate no longer writes
- [x] **Reject was a complete no-op at all four gates.** The graph edged past every gate
  unconditionally and `load_data_node` never read `load_approval`, so rejecting the final
  approval set `load_approval=False` and then loaded anyway. Added an approval guard in
  `load_data_node` (anything other than a literal `True` writes nothing, emits a
  `load_rejected` report) plus a conditional edge on the gate. Guard and routing are
  independent on purpose — a mis-route can't turn a rejection into a write.

### K.2 Four real decisions per gate
- [x] `_gate_router` conditional edges after every HITL node: `approve`/`edit` continue,
  `retry` routes back to the producing node (`analyze_dependencies` / `resolve_entities` /
  `map_fields`) with the user's free-text reason injected into that node's prompt via the
  new `src/orchestrator/utils/retry_hints.py`, `reject`/`abort` end the run. Legacy
  `{"approved": bool}` payloads still work. `gate_retry_counts` bounds retries at 3 so a
  loop can't run forever. API `Feedback` model widened (`decision`, `reason`,
  `cluster_decisions`, `entity_tables`) and validates the verb.
- [x] Frontend no longer calls `abortWorkflow` right after rejecting — that used to mask
  the no-op by showing "aborted" while the graph carried on loading.

### K.3 Match-gate decisions are applied
- [x] `human_review_matches` received `cluster_decisions` and discarded them
  ("Process decisions: keep, split, or merge clusters" was a comment, not code). New
  `src/orchestrator/utils/cluster_edits.py` implements keep/split, including partial
  splits via `groups` with unplaced records surviving on their own. An unrecognised
  action splits rather than merges, because an unintended merge is unrecoverable.

### K.4 Deterministic cluster ids — re-runs stop duplicating
- [x] `generate_uuid()` per cluster meant re-running unchanged data inserted a whole new
  set of destination rows (the cluster id is the destination PK). Replaced with
  `deterministic_uuid()` (SHA-256 over entity + sorted member ids, stamped to pass
  `validate_uuid`).
- [x] **Prerequisite found and fixed:** `_origin_id_row` was a batch ordinal that restarts
  at 0 every run, so record ids — and therefore cluster ids — were never stable across
  runs regardless. Now derived from the table's primary key (`_stable_row_key`), with the
  ordinal only as a last resort for tables that have none.

### K.5 Multi-table entity selection (closes the J.4 headline gap)
- [x] Selection was `ALIAS_GROUPS`, 11 hardcoded synonym sets — and `_build_schema_summary`
  pre-filtered the LLM's view with the same weak name test, so `customer` and `users` were
  never even shown to it. New `select_entity_tables` tool + prompt pair gives the LLM the
  **full unfiltered table catalog** (names, columns, row counts — metadata only, O(schema)
  not O(rows)) and asks which tables hold each entity. The alias heuristic is now the
  floor, never the ceiling; invented table names are dropped with a warning; bare names
  resolve to their canonical qualified form so a table can't be extracted twice.
- [x] The selection is shown at the dependency gate and is editable — edits are validated
  against the discovered schema on resume.
- [x] **Verified live: `person` now draws from 4 tables (staff 1500 + actor 200 +
  customer 599 + mongo users 185 = 2484 rows), where it previously used `public.staff`
  alone.**

### K.6 Two silent-success bugs found while verifying
- [x] **A failed load still reported `completed`.** `load_failed_entities` was already in
  the reconciliation report but nothing read it. The workflow now reports `failed` with
  the entity list and a note that watermarks were not advanced.
- [x] **A run that parsed no entities reported `completed`.** Surfaced when the LLM
  provider returned 403 (free quota exhausted): `parse_request` swallowed it, every later
  stage no-opped, and the run went green having written nothing. Now reports `failed`
  with the first underlying error.
- [x] **Type clash across merged origins.** Exposed by K.5: Pagila's `staff.active` is
  BOOLEAN and `customer.active` is INTEGER, and both now feed one `person.active` column —
  Postgres refuses the implicit cast and the entire entity load failed. Added
  `_coerce_to_column_type` in the Postgres destination connector (bool/int/float/text,
  passes unconvertible values through so the database can still reject bad data).

### K — Verification
- [x] Unit suite **659 passed / 83 skipped / 0 failed** (was 626; +33 new tests in
  `test_phase_k_gates.py` and `test_select_entity_tables.py`).
- [x] Live against Pagila + sample_mflix into a **freshly created empty** `phase_k_dest`:
  reject at the dependency gate → status `rejected`, zero tables created; approve all
  gates then reject at final approval → status `rejected`, still zero tables; K.5 selected
  staff + customer + users + actor.
- [x] Load layer verified directly against the real destination: mixed BOOLEAN/INTEGER/
  string `active` values all load (5 rows), and an identical second load gives
  **0 inserted / 5 updated, 5 → 5 rows** — the K.4 idempotency proof.
- [ ] **Not verified: a full approved end-to-end run after the type-coercion fix.** The
  LLM provider (DashScope qwen3.6-flash) returned `AllocationQuota.FreeTierOnly` 403
  mid-verification, so runs 3 and 4 could not parse a request. Needs a working LLM key to
  finish; every non-LLM part of that path is verified above.

### K — Still open (Phase L work, not regressions)
- [ ] Borderline pairs are still unioned in `_build_clusters`, so two different people
  named Rory scoring 0.93 merge with no review. Confirmed live. This is plan finding #8
  and is what Phase L's repair + cohesion cut addresses; thresholds must not move before
  the L.4 evaluation harness exists.

## Phase L — Matching precision rebuilt on measurement (2026-07-21)

LLM model switched to `qwen3.6-35b-a3b` at the user's request (the previous
`qwen3.6-flash` free tier was exhausted), which also unblocked the last Phase K
check. Phase L's rule: **no threshold moves without a before/after number.**

### L.4 Evaluation harness — built FIRST so everything else is measurable
- [x] `tests/eval/` scores the matcher against 560 labelled pairs built from the
  real Pagila staff table (`pagila_staff.csv`, 1500 rows / 1500 distinct emails, so
  distinct rows are provably distinct people). Labels are true *by construction*:
  MATCH pairs are a row plus a corrupted copy of itself (typo, nickname, reformatted
  email, missing field, case change); NON-MATCH pairs are different rows, biased
  toward the hard cases — 248 first names in that table are shared.
- [x] Reports precision/recall/F1 for the **auto-merge** decision specifically,
  since that is the one that happens with no human review. Pairs routed to review
  are counted separately: they are the system correctly saying "not sure".
- [x] Also measures **cluster-level** damage and **blocking recall**, because
  pairwise precision can read 1.0000 while clustering still destroys records.

### The baseline it exposed
Pairwise precision was already 1.0000 — but end-to-end, **26 of 400 distinct real
people were destroyed** (400 records -> 374 clusters), including three different
people named Lynn chained into one entity. The defect was never the pair scorer; it
was that `_build_clusters` unioned *borderline* pairs — evidence the system had
itself flagged as uncertain.

### L.3 Clustering: stop merging on uncertainty, then repair and cut
- [x] Borderline pairs are no longer unioned. They are surfaced at the matches gate.
- [x] **Conflict repair**: a component containing two records with disagreeing
  unique identifiers (email/tax_id/...) is split — they cannot be one entity.
- [x] **Cohesion cut**: average-linkage inside each surviving component, cut below
  `_MIN_COHESION`, so a weak chain A-B-C breaks instead of fusing three entities on
  two mediocre edges.
- [x] Measured: **400 distinct people -> 400 clusters (was 374)**, and 100 people
  duplicated across two systems still collapse correctly to 100.

### L.1 Fellegi-Sunter scoring with term-frequency adjustment
- [x] New `src/mcp_server/tools/fellegi_sunter.py`. Per-field comparison levels
  (exact/strong/weak/disagree/null) -> m/u probabilities -> summed log-odds match
  weight. **Term-frequency adjustment** makes agreement on a rare value far stronger
  evidence than on a common one — the distinction the old averaged-similarity score
  could not express, and the direct cause of the shared-first-name merges.
- [x] m/u estimated **unsupervised via EM** over the candidate pairs, with priors
  that keep a cold start sane. No labelled training data needed.
- [x] Measured: **recall 0.8850 -> 1.0000, precision stays 1.0000, F1 0.9390 ->
  1.0000.** The 23 same-entity pairs the heuristic couldn't settle (nicknames,
  missing emails) are now caught, because agreement on a rare email carries weight.
- [x] **Found while testing:** on very few pairs, EM has nothing to learn and the
  priors dominate — a genuine borderline pair scored 0.002 where the heuristic
  correctly said 0.82. Gated behind `_MIN_PAIRS_FOR_MODEL = 30`; below that the
  heuristic is simply the better estimator.

### L.2 Blocking: multi-rule, phonetic, purged, and honest about shortfalls
- [x] Four independent rules unioned per field (exact whole value, phonetic code,
  first token, email local part) instead of one key, so a single typo no longer
  excludes a record from consideration entirely.
- [x] Compact Soundex (`_phonetic_key`) groups Smith/Smyth and Katherine/Catherine —
  variants exact-match blocking misses completely. No new dependency.
- [x] **Block purging**: approximate keys (phonetic, first token, partial names,
  identifier prefixes) shared by more than `_MAX_REF_BLOCK` records are dropped as
  non-discriminative. An exact match on a *complete* value (full name, email) is
  never purged — but an exact match on *part* of a name is, because a block of every
  Smith in a million-row table is 50 billion pairs of almost no value.
- [x] `_MAX_COMPARISONS` no longer silently truncates mid-loop. Blocks are processed
  smallest (most discriminating) first, and any shortfall is **reported** as
  `uncompared_pairs` in the resolution result rather than buried in a log warning.
- [x] Measured: **blocking recall 1.0000 at 2.4% of all-pairs cost.**
- [x] **Regression caught by the existing suite:** my first version prefixed block
  keys with the field name, which silently broke cross-origin blocking
  (`company_name` vs `org_title` no longer co-blocked). Keys are now field-agnostic.

### Idempotency under incremental extraction (finishes K.4)
- [x] The Phase K live re-run went **1935 -> 1953 rows**. Root cause: watermarks
  exist for the three Postgres tables but **not** for the Mongo collection, so run 2
  re-extracted only those 185 users and clustered them *alone*. Different membership
  -> different derived id -> 18 new rows. **Deterministic ids are only stable while
  cluster membership is, and incremental extraction changes membership.**
- [x] Fixed with a persistent `merge_engine_entity_map` table in the destination
  (source record -> cluster id). Resolution reuses a previously assigned id when any
  member has one; only genuinely new entities derive a fresh id. Written after a
  successful load, per entity, skipping entities that failed.

### L — Verification
- [x] Unit suite **677 passed / 83 skipped / 0 failed** (was 659; +18 Phase L tests).
- [x] Two pre-existing tests asserted the OLD contract — that an unadjudicated
  borderline pair gets merged and flagged. That is exactly the behaviour that
  destroyed 26 of 400 people; both were rewritten to assert the new contract.

### L.6 A second over-merge found by the live run (not by the unit tests)
- [x] The first Phase L live run merged **25 pairs of different Pagila actors**
  (ANGELA HUDSON + ANGELA WITHERSPOON, GRETA KEITEL + GRETA MALDEN, ...). In
  isolation those 200 actors resolve perfectly — 199 clusters, only the genuine
  SUSAN DAVIS duplicate — so the eval set never caught it.
- [x] Root cause: the `actor` table has **no email and no unique identifier at
  all**, and Pagila stamps every row with the same `last_update`. Two actors then
  agree on a first name and on a value identical across the whole table, and the
  accumulated weight crossed the auto-merge threshold on evidence that identifies
  nobody. Term-frequency alone did not stop it because that stamp is not uniformly
  frequent once actor rows are pooled with staff and customer rows.
- [x] Fix: `_has_discriminating_agreement` — an auto-merge now requires agreement on
  at least one field that is not merely part of a name AND whose agreed value is held
  by at most 5% of records. Agreement on *every* part of a name still counts, scored
  by the product of the parts' frequencies: "Rory" identifies 4 people, "Rory Ledner"
  identifies one.
- [x] First attempt was too strict — it treated first+last name agreement as
  non-discriminating and **recall fell 1.0000 -> 0.8000**. Caught by the harness
  before it shipped; the frequency-product rule restored recall to 1.0000.
- [x] The case is now in the eval set permanently ("300 people with NO unique id").

### Uncertain pairs are no longer invisible
- [x] Not merging an uncertain pair created a new blind spot: such pairs form no
  multi-record cluster, so the matches gate had nothing to show and **auto-skipped** —
  the reviewer never learned the system had hesitated. `resolve_entities` now returns
  `pending_merge_candidates`, the gate fires for them, and they are rendered with full
  field values.
- [x] `uncompared_pairs` (blocking budget shortfall) is threaded into
  `resolution_stats` and the reconciliation report, so "N pairs were never compared"
  is visible at the final-approval gate rather than only in a log line.

### L — Final measured state
| measure | before Phase L | after |
|---|---|---|
| pairwise precision | 1.0000 | 1.0000 |
| pairwise recall | 0.8850 | **1.0000** |
| pairwise F1 | 0.9390 | **1.0000** |
| 400 distinct real people | 374 clusters (26 destroyed) | **400 [OK]** |
| 100 people duplicated across 2 systems | 100 | 100 [OK] |
| 300 people with no unique id | 275 (25 destroyed) | **300 [OK]** |
| blocking recall | not measured | **1.0000** at 2.4% of all-pairs |
| unit suite | 659 | **683 passed / 83 skipped / 0 failed** |

### L — Final live verification (9/9, 2026-07-21)
- [x] Pagila (staff + actor + customer) + MongoDB sample_mflix -> freshly created
  empty `phase_k_dest`. All four HITL gates fired, including the matches gate.
- [x] **2484 source records -> 2483 destination rows = exactly ONE merge, and it is
  the one genuine duplicate in the data** (actor 101 + actor 110, both SUSAN DAVIS).
  Run 1 of Phase L produced 2457 (26 wrong merges); Phase K produced 1935 (549).
- [x] All four distinct people named Rory preserved. **Zero duplicate emails**, so
  the precision did not come at the cost of under-merging.
- [x] Re-run of identical data: **2483 -> 2483 rows.** Idempotent (was 1935 -> 1953).
- [x] Reject at the dependency gate and at final approval both end the run with
  status `rejected` and zero tables created.

---

## Phase M — Exception handling & transactional loads (COMPLETE, 2026-07-22)

Verified live: **30/30 checks** (17 in `verify_m.py`, 13 in `verify_m_gate.py`)
against real Pagila + sample_mflix into real PostgreSQL destinations.
Unit suite **734 passed / 83 skipped / 0 failed** (was 683).

### M.1 — Typed error taxonomy + per-node RetryPolicy
- [x] `MergeEngineError` base with a `disposition` (`retry` / `escalate` / `fail`)
  and `retryable`. `TransientError`, `PermanentError`, `DataQualityError`,
  `UserInputError` all still subclass `RuntimeError`, so existing handlers work.
- [x] `classify_exception()` maps raw driver exceptions onto the taxonomy by
  message. **Permanent markers beat transient ones**: "could not connect:
  permission denied" is a credentials problem, and retrying it three times only
  delays the message the user needs. Unrecognised errors are treated as
  permanent so they get read and fixed rather than looking like flakiness.
- [x] LangGraph's built-in `RetryPolicy(max_attempts=3, backoff_factor=2,
  jitter=True, retry_on=_is_transient)` attached to every node that touches a
  database or the LLM. Pure-compute nodes and HITL gates deliberately get none.

### M.2 — `handle_error` wired in as a real escalation gate
- [x] The orphaned node now has inbound edges from every fallible node and is a
  real HITL interrupt offering **retry / skip / abort**, with the failed step,
  the error, its class, the attempt count, and the last five errors.
- [x] The "skip" option spells out where the run resumes and that the merge will
  be incomplete — "skip" sounds harmless and is not.
- [x] `skip` is rejected with HTTP 422 at any non-error gate: it would read as
  approval of output nobody approved.
- [x] **Absent feedback aborts rather than continues.** The run is already known
  to be incomplete; "carry on" is the one default that can silently ship a
  wrong merge.
- [x] A skipped failure never reports a clean success — the status carries
  `warning: "Completed, but 1 step(s) were skipped after failing: …"`.

### M.3 — Preflight validation
- [x] New `preflight` node runs before everything: request non-empty, origins and
  destination configured, **every** origin reachable (all probed even after one
  fails, so one run reveals every broken connection), destination reachable,
  destination writable, LLM answering, disk space.
- [x] The write probe does a real `CREATE TABLE` + `INSERT` inside a transaction
  and **rolls back** — it exercises the exact permission a load needs while
  guaranteeing the destination is untouched.
- [x] The LLM probe makes a real call. An exhausted quota used to surface as an
  empty entity list and a run that reported success having written nothing.

### M.4 — Fail loudly (the most consequential fix in this phase)
- [x] `extract_data` used to `break` the batch loop on error, **keep the rows
  that had arrived**, and merge them as though the table were complete — a
  confident, wrong answer. Partial rows are now rolled out of the entity's row
  list, the watermark is not advanced, and the run stops.
- [x] An entity with no source table in any origin is now a failure, not a
  warning: continuing merges everything *except* what was asked for.
- [x] `discover_schemas` escalates when any origin's schema fails to load —
  that is how a two-origin request quietly became a one-origin one.

### M.5 — Transactional all-or-nothing loads
- [x] `PostgreSQLDestinationConnector.atomic()`: all nine commit points route
  through `_commit`/`_rollback`, which no-op inside a block. Every entity in a
  merge now lands in **one** transaction. PostgreSQL DDL is transactional, so
  `ensure_table`/ALTERs roll back with the data.
- [x] The entity map is written **inside** that transaction — if the load rolls
  back the map must too, or the next run reuses ids for rows that were never
  written and duplicates them.
- [x] A rolled-back load reports **every** attempted entity as not-loaded, not
  just the one that raised: listing only the culprit implies the others are
  safely in the database.
- [x] `dry_run` does the whole load and rolls it back, so the report shows real
  counts against the real schema with the destination untouched.
- [x] **ClickHouse deliberately does NOT pretend.** Its `atomic()` logs that the
  load is not atomic and refuses `dry_run` outright. A staging-table +
  `EXCHANGE TABLES` implementation is the real fix and is **not built** — see
  "Known gaps" below.

### M.6 — Live verification (30/30)
`verify_m.py` (17/17):
- [x] Unreachable origin -> run fails in **12s**, message names preflight,
  nothing written.
- [x] Read-only destination -> fails in **8s** naming the write permission,
  *before* any extraction. (Previously this failed at the very last step, after
  a full extract and merge.)
- [x] Healthy run still completes end-to-end through all four gates.
- [x] Injected mid-load failure (a `BEFORE INSERT` trigger that raises):
  status `failed`, **rows 2482 -> 2482 unchanged**, watermarks unchanged,
  entity map unchanged. Fault removed -> run completes, **no duplicate rows**.

`verify_m_gate.py` (13/13) — SELECT on `public.actor` revoked *while the run is
parked at the dependency gate*, i.e. a table readable when the run was planned
and not by the time it is read:
- [x] Run stops at `handle_error`, names `extract_data`, offers retry/skip/abort,
  quotes the unreadable table, and reports the discarded partial rows.
- [x] `abort` -> `failed`, nothing written. `retry` -> re-runs the node, fails
  again, returns to the gate, attempt counted. `skip` -> completes with the
  incompleteness warning.

### Two bugs this phase's own verification found
- **`disposition` was decorative.** Every failure escalated, so an unreachable
  database opened a gate whose "skip" button walked the user into a run that
  could not possibly work. Now `fail` ends the run and only `escalate` opens the
  gate.
- **`handle_error`'s body never runs.** `resume_workflow` calls
  `update_state(..., as_node=interrupt_node)`, which tells LangGraph the node
  already executed. The decision bookkeeping had to move into `resume_workflow`
  alongside the other gates' edit handling. (An isolated LangGraph test that
  omitted `as_node` was misleading on this point — the codebase's existing
  comment was right.)

### Known gaps after Phase M (not fixed, deliberately recorded)
- **The comparison budget still truncates, and it is now the binding
  constraint.** A 2,484-record merge generates **28,170 candidate pairs and
  compares 10,000** (`_MAX_COMPARISONS`). Phase L made this *visible*
  (`uncompared_pairs` is reported, was silent) but did not remove it. Because
  which pairs survive truncation depends on block iteration order, two runs of
  identical data produced **2483 and 2482 rows**. Resolution itself used no LLM
  (`llm_scored=0`), so this is not model non-determinism. **Phase N's
  SQL-over-DuckDB blocking is the fix.**
- **ClickHouse destination loads are not atomic** (see M.5).
- A table the user names in the request that the origin role cannot see is
  silently absent from the merge. Postgres only exposes tables the role holds a
  privilege on, so discovery never learns it exists. The dependency gate does
  show the selected tables, so a human can catch it — nothing warns them to look.

---

## Phase N — Scale (IN PROGRESS: N.1 + N.2 complete and verified)

Unit suite **780 passed / 83 skipped / 0 failed** (was 734).

### N.2 — Keyset pagination + count-once (COMPLETE, verified live 19/19)
- [x] `src/mcp_server/connectors/keyset.py`: cursors carry the *last row's key
  values* instead of an offset, so each batch seeks straight to its start.
  The key is the watermark **plus the primary key** — a watermark alone is
  rarely unique (Pagila repeats `last_update` across thousands of rows) and a
  batch boundary inside a run of equal values skips or repeats rows.
- [x] `COUNT(*)` now runs **once per table**, carried forward in the cursor.
  It used to run on every batch, which on a large table is a full scan per
  batch.
- [x] Postgres verified live: `public.rental` **16,044 rows read, 0 duplicates,
  0 missing**; batch 60 (24ms) is *faster* than batch 1 (107ms, which includes
  the one count).
- [x] ClickHouse verified live on the real **43,276,150-row** `cell_towers`:
  batch 1 8043ms → batch ~42 **1131ms**. Flat, not degrading.
- [x] Tables with neither watermark nor primary key still fall back to OFFSET
  and now **log a warning** naming the table, rather than silently degrading.

**Two real bugs found and fixed while doing this:**
- **Timestamp watermarks were broken outright on PostgreSQL.** `CAST(:wm_since
  AS TIMESTAMP)` makes asyncpg bind $1 *as a timestamp*, and it then rejects
  the string the tracking table stores — verified by running the old form
  directly. Only numeric watermarks ever worked; any table with a timestamp
  watermark failed on its second run. Fixed by pinning the parameter to text
  first (`CAST(CAST(:p AS TEXT) AS TIMESTAMPTZ)`).
- **MongoDB silently truncated every collection to one batch.** `next_cursor`
  was set only when `len(rows) == batch_size + 1`, but the loop capped `rows`
  at `batch_size`, so that was unreachable — and the projection excluded
  `_id`, the pagination key. `sample_mflix.comments` now reads **50,304
  documents across 101 batches**; before this it returned 500 and stopped.
  Also added the missing `.sort("_id", 1)`: `$gt` only means "next page" under
  a guaranteed order.

### N.1 — DuckDB spool (COMPLETE, verified live)
- [x] `src/orchestrator/spool.py`: extraction writes batches straight into
  `logs/spool/<run_id>.duckdb`; state carries `{entity: {row_count, ...}}`
  handles. Rows are JSON in one column — origins disagree about schema by
  definition, so a fixed table would need migrating per origin.
- [x] Accessors (`entity_rows`, `entity_row_count`, `entity_origin_counts`,
  `entity_records_by_id`) transparently handle a spool handle **or** a plain
  list, so callers that pass rows inline keep working.
- [x] Review gates fetch **only the records they display** instead of building
  a lookup of every extracted row — opening the matches gate used to cost as
  much as the extraction.
- [x] Per-origin reconciliation counts are computed in SQL
  (`{mflix_mongo: 185, pagila_pg: 2099}`), not by walking rows in Python.
- [x] Partial-table truncation (the Phase M rule) is enforced on the spool.
- [x] Spools are swept per run, and a run **parked at a gate keeps its spool**.
- [x] Live: run completes end-to-end, output unchanged, 1.26 MB spool file.

**Measured, not assumed:** `extracted_data` is now completely absent from the
checkpoint. But checkpoints are still ~6.8 MB per run, because the largest
channel is now **`unified_entities` (69.6%)** followed by **`entity_clusters`
(28.2%)**. N.1 removed one of the three; the "tiny checkpoints" claim needs
**N.4** as well. Reported here rather than claimed as done.

### N.3 — Blocking + scoring as SQL (NOT STARTED — and now known to be required)
Profiling the current Python path at 500/1000/2500 records:

| records | blocking | model fit | scoring |
|---|---|---|---|
| 500 | 0.03s | 1.10s | 1,720 pairs / 3.70s |
| 1000 | 0.24s | 7.34s | 6,883 pairs / 14.69s |
| 2500 | 0.26s | 10.38s | **10,000 pairs / 20.47s** (4,431 uncompared) |

**Scoring costs ~2ms per pair; blocking is nearly free.** So `_MAX_COMPARISONS
= 10000` exists because *scoring* is slow, and simply raising it is not an
option — the 28,170 pairs a 2,484-row merge generates would take ~58 seconds.
This is what makes two runs of identical data produce 2483 vs 2482 rows.
Set-based comparison in DuckDB is the fix, exactly as planned.

### N.4 / N.5 — not started
N.4 must move `unified_entities` and `entity_clusters` to the spool (they are
98% of what remains in the checkpoint). N.5 is the 1M / 43M-row proof.

### N.3 — Comparison budget (COMPLETE, verified live)
Profiling said the budget existed because *scoring* was slow, so the fix was
to make scoring fast rather than to rewrite it in SQL.

- [x] `_string_similarity` / `_tokens_match` / `_seq_ratio` / `_tokenize` are
  memoised. difflib was **90% of scoring time** (33.2s of 37.1s on a 1,500-
  record merge, 906,158 calls to `find_longest_match`). Candidate pairs are
  candidates *because* they share names and tokens, so the same two strings
  were being compared over and over. Keys are order-normalised (the
  comparisons are symmetric; difflib's autojunk, which would break that, only
  engages at 200+ characters). Caches are bounded.
- [x] **2.047 ms/pair -> 0.085 ms/pair (24x)**; model fit 10.4s -> 3.3s.
- [x] Eval harness **unchanged** — precision 1.0000 / recall 1.0000 / 400->400 /
  100->100 / 300->300 / blocking recall 1.0000. The cache changes no answer.
- [x] Budget raised 10,000 -> **250,000**, now a runaway guard rather than a
  tuning knob.
- [x] Truncation made reproducible: blocks are ordered by `(size, key)`, so
  equal-sized blocks no longer fall back on dict insertion order.
- [x] **Live: 28,170 candidate pairs compared, 0 uncompared** (was 10,000
  compared / 18,170 silently dropped on the same data).

### N.4 — Unified records + clusters moved to the spool (COMPLETE, verified live)
- [x] `unified_entities` and `entity_clusters` now live in the spool; state
  holds handles. The cluster handle carries `needs_review` / `multi_record`
  so the graph's routers and the review gates answer "is there anything to
  review" without reading every cluster back.
- [x] Loading pulls one entity's records at a time, so peak memory is one
  entity's merged output rather than all of them.
- [x] **Checkpoint per run: ~7 MB -> 392 KB (18x smaller)** for the same
  2,482-record merge. `extracted_data`, `unified_entities` and
  `entity_clusters` are all out of checkpointed state now.
- [x] Bug found and fixed while verifying: `_derive_skipped_gates` iterated
  the clusters container directly, which with handles yields *keys* — a live
  `'str' object has no attribute 'get'` failure at the final gate.

### Determinism: what is and is not reproducible (measured)
Two **full** runs of identical data into fresh destinations:

| | run A | run B |
|---|---|---|
| candidate pairs compared | 28,170 | 28,170 |
| clusters formed | 2,482 | 2,482 |
| destination rows | 2,482 | 2,482 |
| entity ids identical | **no** | |

Matching and clustering are fully deterministic. The ids differ because the
**LLM's field mapping varied between the runs**: run A kept `customer_id` as
its own column and derived all 2,482 ids; run B mapped `customer_id` -> `id`,
so exactly 599 rows (the customer count) kept source-shaped ids. The two runs
even produced *different destination schemas* (15 vs 13 columns).

This is not a matching bug — it is why **Phase O.4 (saved run profiles)**
matters: until the mapping is pinned, "re-run the same merge" is not a
reproducible operation across destinations. Within one destination it already
is: the entity map holds ids stable (2,482 -> 2,482, no duplicates).

### Still open
- **N.5** — 1M / 43M-row scale proof.
- **Phase O** — customisation, run profiles (O.4 now has a measured
  justification), conflict policy, LLM cost controls.

### N.5 — Scale proven against the real 43M-row table (COMPLETE)

Unit suite **904 passed / 83 skipped / 0 failed** (was 799).

- [x] **1M rows through the real extraction path**: 93.3s (10,716 rows/s),
  flatness ratio **1.00x** (first 5 batches avg 4.40s, last 5 avg 4.40s),
  peak RSS **262 MB**, spool 100 MB, and **1,000,000 extracted =
  1,000,000 spooled** — exact.
- [x] **The full 43,276,150-row `cell_towers` table** run to exhaustion and
  reconciled against a direct `SELECT count()`.

#### Three bugs N.5 found that no smaller test could

- [x] **Keyset pagination was dead on every connector.** `retry_on_transient`
  wrapped `extract` **without `functools.wraps`**, so
  `inspect.signature(connector.extract)` reported `(*args, **kwargs)` and
  `_accepts_pk_columns` returned False. `pk_columns` was therefore never
  passed and pagination silently fell back to OFFSET — on Postgres,
  ClickHouse and MongoDB alike. N.2's tests passed because they exercised
  `choose_keyset` directly and never the wiring. Fixed with `functools.wraps`;
  a regression test now asserts the decorated signature still advertises
  `pk_columns`.
- [x] **A non-unique keyset silently dropped rows.** The predicate was a
  strict `>`, which excludes every row sharing the batch boundary's key.
  Measured on a 300,000-row table keyed by three non-unique columns (144
  distinct values): **200,000 rows returned, 100,000 lost (33%), run reported
  success.** Fixed with `>=` plus a `boundary_seen` skip-count carried in the
  cursor. A first attempt at that still duplicated 3 of 16,044 Pagila rows,
  because equal-key rows come back in an unstable order between queries — so
  the ordering now carries a row-hash tiebreak (`cityHash64(*)` /
  `md5(row::text)`). Reconciled exactly on both engines afterwards.
- [x] **`executemany` into a DuckDB JSON column was ~1,300x too slow.** 50,000
  spool rows took **259 seconds** (~193 rows/s) — 43M rows would have been 62
  hours, and it presented as a hang rather than as slowness. Registering a
  pandas frame and `INSERT ... SELECT` does the same 50,000 in **0.19s**.

#### Two constraints worth stating rather than discovering again

- **A PostgreSQL PRIMARY KEY proves uniqueness; ClickHouse's does not.**
  `system.columns.is_in_primary_key` is the sparse *sorting* key —
  cell_towers holds 43,276,150 rows for ~42.97M distinct sort-key tuples. So
  ClickHouse always paginates defensively, while Postgres takes the fast
  strict-`>` path but calls `assert_key_unique` on every batch and raises
  `DataQualityError` if the declared key turns out non-unique. A wrong key
  now stops the run instead of quietly shortening it.
- **Keyset speed depends on the key matching the table's physical sort
  order.** With a misaligned key (`radio,mcc,net,area,cell`) batch cost grew
  7.07 → 11.82 → 14.58s over the first 17% — the OFFSET curve keyset exists to
  remove. With the key discovery actually reports
  (`radio,mcc,net,created`, the table's ORDER BY) it stayed ~7s flat.

---

## Phase O — Customisation, run profiles & LLM cost controls (2026-07-22)

### O.4 — Saved run profiles (`src/orchestrator/profiles.py`)
- [x] JSON profiles in `config/profiles/`, pinning `entity_tables` (O.1),
  `matching_rules` (O.2), `conflict_policy` (O.3) and `field_mappings` (O.4).
- [x] Written back **only on a clean completion** — harvesting a failed or
  partly-skipped run would pin a half-formed mapping the next run inherits.
- [x] **Credentials are never written.** Stripped on build *and* again inside
  `save_profile`; `restore_credentials` re-attaches live passwords by
  connection coordinates (type/host/port/database/user) rather than by list
  position, so reordering origins cannot repoint a profile at another server.
- [x] REST: `GET/POST /api/profiles`, `GET/DELETE /api/profiles/{name}`, plus
  `profile_name` / `save_as_profile` on `POST /api/workflow/start`.
- [x] React UI: profile picker + "save this run as" on the merge screen.

### O.1 / O.2 / O.3
- [x] O.1 `analyze_dependencies` reuses a pinned table selection instead of
  re-asking the LLM.
- [x] O.2 per-entity `blocking_keys`, `unique_id_fields` and `strictness`
  (loose/normal/strict = 0.90/0.95/0.98). Only the auto-merge bar moves —
  raising it sends more pairs to review, the recoverable direction. An
  inverted or unknown setting falls back to the defaults with a warning.
- [x] O.3 per-field survivorship policy over
  `priority | completeness | recency | shortest | first_seen`, default
  unchanged. Unknown rule names are dropped with a warning, never silently
  treated as `first_seen`.
- [x] **Defaults are the compatibility contract**: the eval harness is
  byte-identical after O.2/O.3 — precision 1.0000, recall 1.0000, 400→400,
  100→100, 300→300, blocking recall 1.0000.

### O.5 — LLM cost controls (`src/llm/cost.py`)
- [x] Bounded thread-safe LRU semantic cache keyed on the **normalised**
  prompt pair; the key covers model, temperature, both prompts,
  response_format and json_mode, because a cache that can return a different
  answer than the model is worse than no cache.
- [x] Per-run token ledger; `GET /api/workflow/{id}/cost`. It records
  **tokens, not currency** — prices are not hardcoded, since the model changes
  whenever a quota runs out, and an invented cost figure is worse than none.

### Two bugs Phase O's own verification found
- [x] **A pinned mapping produced a *worse* schema than deriving one.**
  Reusing it skipped computing `pending_schema_changes`, so against a fresh
  destination the columns were never created — run 2 built a 1-column table
  where run 1 had 9. The pinned mapping now still reconciles its columns
  against the live destination on every run.
- [x] **The cost ledger recorded nothing.** It keyed on `session_name`, not
  the run's `thread_id`, so every run reported 0 calls while the cache showed
  18 lookups. `run_id` now rides along inside `llm_config`.

### Phase O — Live verification (10/10, 2026-07-22)

Run 1 saves a profile; run 2 replays it into a **fresh** database.

| | run 1 | run 2 |
|---|---|---|
| status | completed | completed |
| destination rows | 2,482 | 2,482 |
| **destination columns** | **14** | **14** (diff: none) |
| LLM calls | 10 | 8 |
| tokens | 71,040 | 0 (all 8 served from cache) |
| tokens saved by cache | — | 20,494 |

The column count is the point: **before Phase O the same two runs produced 15
and 13 columns.** Pinning the mapping made re-running a merge a reproducible
operation, which it was not before.

### The bug the 43M run actually found: cleanup deleted a live spool

The first full-table run extracted **43,276,150 rows — exactly the source
count — and stored 19,076,150**. It reported success. `seq` in the spool was
contiguous 0…19,076,149 with no gaps and no duplicates, which ruled out a
pagination fault and pointed at the file itself.

Cause: `cleanup_old_spools` deleted every spool whose `run_id` was absent from
the caller's keep-set, and that set comes from `_active_workflows` — an
**in-memory** dict. Two unrelated merges started while the scale test was
still writing, and each `start_workflow` swept `scale_full.duckdb` away. The
extraction carried on appending to a silently recreated file.

This was never a test-only problem. The same code path deletes the rows of any
run parked at a HITL gate as soon as the app is restarted (`_active_workflows`
is empty after a restart) and one new merge is started — precisely the rows a
human was about to come back and approve.

- [x] **Age is now the primary guard**, membership only an extra reprieve: a
  spool touched within 6 hours is never swept, whoever it belongs to.
- [x] **`append` verifies its own write** against a running per-entity total
  and raises `DataQualityError` if the file disagrees. Comparing against
  `MAX(seq)+1` alone is not enough — a wiped table restarts at 0 and looks
  perfectly self-consistent, so only an expectation carried from outside the
  file can detect it.

### N.5 — final 43M verification (3/3, verified independently)

Re-run after the spool-cleanup fix, with nothing else touching `logs/spool/`.
The script's own PASS lines were not trusted on their own — the previous run
"passed" two of three checks while silently losing 24M rows — so the spool
file was queried directly and compared against ClickHouse.

| check | value |
|---|---|
| ClickHouse `SELECT count()` (independent oracle) | 43,276,150 |
| rows extracted | 43,276,150 |
| rows in the spool (queried directly) | **43,276,150** |
| `seq` range / distinct | 0…43,276,149 / 43,276,150 — contiguous, no gaps |
| **distinct payloads** | **43,276,150** — no row fetched twice |
| batches | 433 |
| wall clock | 44.6 min (16,184 rows/s) |
| peak RSS | **380.8 MB** for 43.3M rows |
| spool on disk | 3.37 GB |
| flatness (first decile vs last) | 8.84s → 5.68s, ratio **0.64x** |

The distinct-payload count is the check that actually closes the loop. An
exact row count alone cannot distinguish "correct" from "fetched one row twice
and missed another"; identical counts *and* zero duplicate payloads can.

Flatness came out below 1.0 — later batches were *faster* than early ones.
That is not the keyset winning extra ground: the first decile carries the
one-time `COUNT(*)` over 43M rows and a cold page cache. The honest reading is
"cost per batch does not grow with depth", which is the property that was
being tested.

---

## Phase P — Targeted / scoped company merge ("everything about Nova")

New work (2026-07-23) from a live bug report: "Merge all information about Nova"
extracted 0 rows and merged nothing. Two root causes, both fixed. Verified live
against crm_origin (PG) + hr_origin (CH). Suite: **950 unit tests passing** (+42).

### P.0 — the watermark 0-row bug (what the user actually hit)
- [x] Extraction is watermark-incremental; run 1 wrote high-water marks into
  `main_dest.merge_engine_extraction_state`, so run 2 extracted only rows newer
  than that → **0 rows**. Deleting rows from the destination does NOT reset the
  watermark (separate table), and there was no reset path.
- [x] Fix: `force_full_extraction` state flag (set for a fresh/new profile or the
  explicit "Full re-merge" UI toggle) skips the watermark read;
  `clear_extraction_state()` added to both destination connectors.

### P.1–P.4 — the real feature: scoped, join-following extraction
- [x] `request_parsing` now returns `subject` + `scope_type` (it used to discard
  filters, so even run 1 merged ALL companies). Unscoped merges unchanged.
- [x] New LLM stage `plan_scope` (prompt `scope_planning.*`): per-entity table
  filters in three modes — **match** (name substring), **link** (fk `IN` subject
  ids), **link_via** (two-hop through a junction: people via jobs/employment).
- [x] Connector filter contract widened to `{col: {op: eq|ilike|in, value}}` in
  postgres/clickhouse/mongo (bare `{col: value}` still eq).
- [x] Two-phase, per-origin extraction: subject ids collected first, dependents
  restricted to them. An empty link set SKIPS the table (never an empty `IN`).
- [x] New HITL gate `human_review_scope` (deps → scope → matches → mappings →
  final), auto-skipped for unscoped merges.

### P.5 — destination dedup
- [x] Subject-matching destination rows are injected into resolution with their
  existing id pre-seeded, so a cluster adopts that id and the load UPDATES rather
  than duplicates. Bounded to the subject entity; best-effort.

### P.6 / P.8 — preset + eval
- [x] One-click "everything about a company" preset in the UI (`ScopeBody` gate
  renderer, stepper + labels updated).
- [x] Scope-plan golden eval (`tests/eval/scope_plans.json` +
  `test_scope_plan_eval.py`) — caught a real heuristic bug (id column mistaken
  for a name column).

### Phase P — Live verification (2026-07-23)

Ran "Merge all information about Nova" through all gates against the real DBs
(model `qwen3.6-flash-2026-04-16`). The LLM planned match/link/link_via itself.

| entity | extracted (crm + hr) | loaded into main_dest |
|---|---|---|
| company | 5 + 5 = 10 (only Nova) | 10 inserted |
| person | 5 + 5 = 10 | **5 inserted + 5 updated** (dedup) |
| product | 5 + 5 = 10 | 10 |

**The number that matters: main_dest companies 732 → 742 — exactly +10 Nova,
zero non-Nova pollution.** main_dest had 0 Nova before (user had deleted it), so
this is also the live proof of the watermark fix. `link_via` found Nova's people
through the employment junctions on both origins.

**Honest gaps:** cross-origin dedup of the SAME Nova across crm+hr did not merge
(they share too few matchable fields — a pre-existing matching-data limit, not a
Phase P regression); the MongoDB 3rd origin is not seeded/verified (port 27017
blocked on this machine — the connector + its filters are implemented and
unit-tested, only the live seed is deferred).

---

## Phase Q — Library integrations (Splink · Great Expectations · lineage) + rich 3-origin seed

New work (planned 2026-07-25) from a repo-evaluation request: the user collected
20+ open-source projects (entity resolution, MDM, ETL, schema mapping, data
quality, lineage, conflict-free merging) and asked which could upgrade/replace
parts of merge-engine. Full verdict + design in `docs/plans/PHASE_Q_PLAN.md`. Four
libraries adopted (**Splink**, **Great Expectations**, **sqlglot lineage**),
the rest documented as rejected with reasons. This phase also completes the
deferred P.7 (rich seed across 3 live origins including MongoDB).

Constraints (unchanged): work only inside `D:\transfer\merging`; always
`conda activate na180`; exactly one destination at a time; each sub-phase stops
for explicit go-ahead; no sub-phase moves on without green tests + a before/after
number. **Implement in the priority order below (Q.7 → Q.2 → Q.3 → Q.1).**

**⚠️ IMPORTANT COMMAND — verify every task live, immediately.** Check off a task
ONLY after verifying it is actually done AND working live — not just that the code
was written. Live verification = run it against the real app/DBs (start the
server, run the pipeline, query the destination directly, run the unit + eval
tests), observe the real result, and confirm with an independent check (direct
SQL/log/report inspection, never the app's own success message alone). Do this
**immediately after** each task, not batched at phase end. If a task cannot be
verified live, it is NOT done — leave it unchecked and say why.

---

### P.7 — Real ClickHouse sample databases setup (`origin1` & `origin2`) — [x] COMPLETED (2026-07-28)

- [x] **Check port availability & create `sample_databases/docker-compose.yml`**: mapped host ports `8190:8123` (HTTP) and `9005:9000` (Native) for ClickHouse container `Sample_Database` (`clickhouse/clickhouse-server:latest`) with credentials `default:pass`.
- [x] **Configure database initialization**: developed `scripts/import_sample_databases.py` CLI supporting `--fresh`, `--resume`, `--database`, `--table`, `tqdm` progress tracking, JSON state tracking (`.import_progress.json`), and word-boundary quote escaping (`fix_quotes`).
- [x] **Spin up container**: container `Sample_Database` running healthy and responsive (`ping = True`).
- [x] **Live verification**: queried ClickHouse directly to confirm `origin1` (13 tables, **50,841 rows**) and `origin2` (7 tables, **35,212,969 rows**) are live and populated with **35,263,810 total rows** across 20 tables with 0 errors.
- [x] **Re-confirmed live (2026-07-29)**, before starting Q.8: `docker exec Sample_Database clickhouse-client -u default --password pass -q "SELECT count() FROM origin1.Company"` → 2941; same query against `origin2.Company` → 1,270,564. Both databases still up on ports 8190/9005.

---

### Q.8 — PostgreSQL destination from the Global Warehouse schema — [x] COMPLETED (2026-07-29)

Origins are real now (Q.7). The destination side of every demo/verification still
uses whatever ad hoc `main_dest` earlier phases seeded. Stand up the actual
destination schema the user supplied — `sample_databases/destination/ER_DIAGRAM.md`
+ `.html` ("Global Warehouse", 25 tables) — as a real PostgreSQL container, in the
**same** `sample_databases/docker-compose.yml`, on a **non-native port** (host
Postgres already occupies 5432 — confirmed this session).

**Confirmed before writing this section:** the `01_company.sql` … `07_technographic.sql`
files the ER_DIAGRAM's own "Table Summary" names **do not exist anywhere in the
repo** — `find` over `sample_databases/` turned up only `ER_DIAGRAM.md`/`.html`
in `destination/`, and the `.html` has zero `CREATE TABLE` statements (confirmed
via grep) — it's a diagram render, not a schema source with extra detail. The
DDL must be **authored from scratch** from the Mermaid ERD.

**Explicit user instruction (2026-07-29): schema only, NO mock data.** The user
found this schema online and does not want it seeded with any mock/generated
rows — the destination must come up as a completely empty schema.

- [x] Authored `sample_databases/destination/sql/01_company.sql` through
  `07_technographic.sql`: real `CREATE TABLE` DDL for all 25 tables, types
  translated per the ERD (`BIGINT id PK`→`BIGINT GENERATED ALWAYS AS IDENTITY
  PRIMARY KEY`, `string`→`TEXT`, `boolean`→`BOOLEAN`, `timestamptz`→
  `TIMESTAMPTZ`, `date`→`DATE`, `double`→`DOUBLE PRECISION`, `int`→`INTEGER`),
  32 FKs per the Mermaid relation list including the two `company_relation`
  FKs to `company.id`. **No `08_mock_data.sql` authored — no rows inserted
  anywhere.**
- [x] Added `postgres_destination` service to `sample_databases/docker-compose.yml`
  (same file as the ClickHouse origins): `postgres:17`, container
  `Sample_Database_Destination`, DB `global_warehouse`, host port `55432`
  (confirmed free before use — native Windows Postgres holds `5432`),
  volume-mounted `./destination/sql:/docker-entrypoint-initdb.d:ro`.
- [x] Registered the destination in `config/recent_connections.json`
  (`postgresql`, `localhost:55432`, `postgres`/`pass`, `global_warehouse`).
- [x] Added `User_Guide.md` §8.5 documenting the full sample-databases stack
  (origins + this destination), connection details, and the empty-schema note;
  extended the §8.4 summary table with a "Real-world sample stack" row.
- [x] **Verified live (2026-07-29):** `docker compose -f
  sample_databases/docker-compose.yml up -d` → `Sample_Database_Destination`
  reached `healthy`; container logs show all 7 SQL files ran with 25×
  `CREATE TABLE`, 0 errors. Direct `psql \dt` → exactly 25 tables. A
  single aggregate `SELECT count(*)` query across all 25 tables → **every
  table = 0 rows**. `pg_constraint` query → **32 FK constraints**, all correct,
  including both `company_relation` FKs to `company.id`. Independently
  re-verified through the app's own code path — called
  `src.ui.components.db_discovery.test_and_discover_schemas('postgres',
  'localhost', 55432, 'postgres', 'pass', database='global_warehouse')`
  directly (the same function the Setup tab calls): `connected: True`, all 25
  tables listed, `table_row_counts` all 0. `origin1`/`origin2` re-confirmed
  still live in the same session before starting (see P.7 above).
- [x] **2026-07-30 addendum:** `Sample_Database_Destination`'s host port was
  moved from `55432` to `55201`. Windows/Hyper-V's dynamic TCP
  excluded-port-range had drifted to cover 55357-55456 (visible via `netsh
  interface ipv4 show excludedportrange protocol=tcp`) after a host
  sleep/resume, so Docker Desktop refused to bind 55432 anymore. The real fix
  (`net stop/start winnat`) needs admin rights this environment doesn't have;
  55201 was confirmed free and bindable, and every reference —
  `sample_databases/docker-compose.yml`, `config/recent_connections.json`,
  `config/profiles/boeing.json`, `User_Guide.md` — was updated to match.
  Re-verified: 25 tables, 0 rows, reachable at `localhost:55201`.

---

### Q.2 — Great Expectations data-quality gate (**PRIORITY 3**)

Today there is NO row-content validation anywhere in the pipeline. Add a
`validate_data` node between `extract_data` and `resolve_entities` that validates
each entity's extracted rows and surfaces results in the reconciliation report.

- [ ] Add `great-expectations>=1.0.0` to `requirements.txt` + `pyproject.toml`;
  confirm it imports under `na180` without breaking the pandas/duckdb pins. If too
  heavy, fall back to `pandera` (note as escape hatch).
- [ ] New tool `src/mcp_server/tools/data_quality.py`: read each entity's spool
  rows into a pandas frame, run a GX suite — `expect_column_values_to_be_unique`
  on `unique_id_fields`; `..._to_not_be_null` on the name column (warn);
  `..._to_be_between` on `*_year`/`*_date` (warn); `expect_table_row_count_to_be_
  between(min=1)` (**fail** — this also catches the P.0 0-row bug at the right
  place). Return `{entity: {checks_run, checks_passed, checks_failed, failures:
  [{expectation, column, observed, severity}]}}`, severity ∈ {warn, fail}.
- [ ] New node `src/orchestrator/nodes/validate_data.py` (house style, thin
  wrapper); export from `nodes/__init__.py`.
- [ ] Wire into `src/orchestrator/graph.py`: `add_node("validate_data", …)` and
  rewire the `extract_data → resolve_entities` edge to
  `extract_data → validate_data → resolve_entities`. It is NOT a HITL gate (no
  interrupt) — gate-skip/router logic must be unaffected.
- [ ] Add `quality_report: dict` to `MergeState` (init `{}` in both
  `get_initial_state()` branches); node returns `{"quality_report": …}`.
- [ ] Extend `build_reconciliation_report` (`nodes/load_data.py:34`) with a
  `"data_quality"` key; surface a one-line summary in the event log and a small
  panel on the final-approval gate in `Gates.tsx`.
- [ ] **Failure posture:** warn = record only; fail = flag prominently at the
  existing `human_final_approval` gate but do NOT auto-abort (human decides).
  Wrap GX in try/except so a GX failure degrades to "validation unavailable",
  never crashes the run. (`DataQualityError` → `handle_error` escalation exists
  for a future stricter mode; keep v1 non-blocking. Do not duplicate the
  structural checks already in `nodes/preflight.py`.)
- [ ] Tests: `test_data_quality.py` (unique violation, 0-row fail, clean pass,
  GX-import-failure degrade), `test_validate_data_node.py` (reads spool, writes
  report, empty-entities no-op), and a graph-wiring assertion for node placement.
- [ ] **Verify:** run a merge, open `logs/run_reports/<run_id>.json`, assert
  `"data_quality"` present; deliberately seed one duplicate unique-id + one 0-row
  entity and confirm `fail` surfaces at the final gate.

---

### Q.3 — column-level lineage in the reconciliation report (**PRIORITY 4**)

The report has per-entity counts but no field-level provenance. Build it from data
already computed — no new extraction.

- [ ] Add `sqlglot>=25.0.0` to `requirements.txt` + `pyproject.toml`
  (zero-dependency). `sqllineage` NOT required (heavier) — fallback only.
- [ ] New `src/orchestrator/utils/lineage.py::build_lineage(state)` → for each
  entity, map each dest column to its origin sources by joining
  `field_mappings[entity]` (`source_field → dest_column`) with the set of
  `(origin_id, origin_table)` that carried that field (sample the spool rows).
  Output `{entity: {dest_column: {sources: [{origin_id, origin_table,
  source_field}], created_column: bool}}}`.
- [ ] **Preserve `_conflicts` provenance:** `unify_entity_group.py:236` already
  records per-field per-origin attribution (`{field, values:[{origin,value}],
  selected_origin, winning_rule}`) but it is currently discarded at load. Fold it
  into the lineage output — it's the highest-value provenance already computed and
  thrown away.
- [ ] Add `"lineage"` to `build_reconciliation_report` (`nodes/load_data.py:34`)
  and the written run-report JSON. Small collapsible "Lineage" panel in
  `Gates.tsx` (`dest_column ← origin_table.source_field`).
- [ ] (optional enrichment) pass the generated load SQL through
  `sqlglot.lineage.lineage(column, sql)` to validate piece 1; skip if awkward over
  parameterised SQL.
- [ ] Document the known limitation: destination `_source_ids` only carries
  `[{cluster_id}]` not real origin row ids, and `merge_engine_entity_map` exists
  only on a PostgreSQL destination.
- [ ] Tests: `test_lineage.py` (dest column → right sources; created column
  flagged; field in two origins lists both) + extend a report test to assert a
  non-empty `"lineage"` key for a multi-origin entity.
- [ ] **Verify:** in a live report, assert `companies.name` traces back to BOTH
  `crm_origin.companies.company_name` and `hr_origin.organizations.org_title`.

---

### Q.1 — Splink as an optional per-entity matching engine (**PRIORITY 5**)

Largest and only phase with real integration risk. Keep the current hand-rolled
Fellegi–Sunter matcher as the DEFAULT (precision/recall 1.0 on the eval). Add
Splink as a per-entity selectable engine behind the (currently dead)
`matching_rules` config. Default-off, so it cannot regress existing runs.

- [ ] Add `splink>=4.0.0` to `requirements.txt` + `pyproject.toml`; confirm it
  imports under `na180`. If it drags an incompatible duckdb pin, import it lazily
  inside the new module so the rest of the app is unaffected.
- [ ] New `src/mcp_server/tools/splink_matcher.py::resolve_with_splink(...)`
  returning the EXACT result-dict shape of `resolve_entities_impl`
  (`resolve_entities.py:331-351`). At the seam (`resolve_entities.py:158`), read
  `engine = (matching_rules or {}).get("engine", "builtin")`; if `"splink"` and
  importable, delegate and return early; else fall through unchanged.
- [ ] **Splink replaces SCORING only, not clustering.** Run Splink's DuckDB
  backend over a projected per-record comparison view (canonical `name/email/ref/
  name-parts` built with the existing `_blockable_fields`/`_best_name_field` hint
  logic — this solves the cross-origin schema-divergence problem, since Splink
  assumes one unified schema). Train with `estimate_probability_two_random_records
  _match` + `estimate_u_using_random_sampling` + EM. Take pairwise
  `match_probability`, then feed those scores into the EXISTING
  `_build_clusters(...)` (`:1210`).
- [ ] **Preserve the 8 project invariants** (full list in `docs/plans/PHASE_Q_PLAN.md`):
  three-way decision + `pending_merge_candidates` band; deterministic reusable
  cluster ids via the existing `_assign_cluster_id` (`:1182`); the three
  anti-over-merge guards (`_has_conflicting_identity`, `_only_partial_name_
  evidence`, `_has_discriminating_agreement`); repair+cohesion clustering; LLM
  adjudication of the band; equivalent `uncompared_pairs`/`candidate_pairs`
  reporting; `unify_entity_group.py` untouched.
- [ ] Wrap Splink in try/except: any failure logs a warning and **falls back to
  the builtin engine** — never crash a run over an optional engine.
- [ ] **Fix the dead config surface** so `engine: "splink"` is reachable: extend
  `src/api/main.py` profile routes (`POST/GET /api/profiles`) to round-trip a
  `matching_rules` object (already pinned in `profiles.py`), and add a small
  "Advanced matching" per-entity engine dropdown (Built-in | Splink) to
  `PipelineScreen.tsx`. Also fix the false comment at `resolve_entities.py:48`
  (claims `extraction_config["max_comparisons"]` is read — nothing reads it).
- [ ] Tests: `test_splink_matcher.py` (result-shape parity, cluster-id reuse,
  identity-conflict veto holds, graceful fallback on import failure). Keep all
  Phase-L tests green (builtin engine unchanged and still default).
- [ ] **Extend the eval harness** `tests/eval/score.py` with a 7th scenario:
  `evaluate(splink_scorer, pairs)` over the SAME 560 golden pairs. **Acceptance
  bar: Splink precision ≥ 1.0000, recall ≥ 0.99.** Record the before/after
  number — this gates the phase. If Splink doesn't beat the builtin, it ships as a
  documented option and stays off (acceptable, not a failure).

---

### Phase Q — Live verification (fill after the run)

- [x] P.7 (origins): re-confirmed live 2026-07-29 — `origin1.Company`=2,941,
  `origin2.Company`=1,270,564, `Sample_Database` container healthy.
- [ ] Q.8 (destination): `global_warehouse` container up, all 25 tables present
  with non-zero referentially-consistent counts, reachable via the app's Setup
  tab schema discovery.
- [ ] All sub-phase unit tests green; full suite (baseline: confirm current
  count before starting Q.8+) shows +N new tests, 0 failures. Frontend
  `npm run build` + `npx tsc --noEmit` clean.
- [ ] Run a scoped merge (origin1 + origin2 → global_warehouse); query the
  destination directly — row deltas match exactly what was merged, zero
  pollution of unrelated rows.
- [ ] Data-quality `"data_quality"` and `"lineage"` sections present and correct
  in the run report, tracing at least one destination column back to both
  origin1 and origin2 source fields.
- [ ] Splink eval numbers recorded beside the builtin.

**Honest gaps:** _(to be filled after the live run)._

---

## Phase R — Remediation 1-6 + Phase 7 live verification (2026-07-30 → 2026-08-01)

Full detail in project memory `merge-engine-remediation-1-to-6.md`
(`merge-engine-performance-backlog.md` for the speed side). Summary here for
anyone reading the repo without memory access.

### Remediations 1-6 (app-vs-manual-merge gap, ranked fixes)
- [x] Breadth, scoped matcher, type-safety pre-check (`value_coercion.py`),
  schema-ALTER rollback (Fix #4), per-field conflict-policy defaults,
  data-quality scorecard + pre-load guardrail. All verified, zero
  regressions at each step.
- [ ] Remediation #7 (schema-driven planner/"manager" agent) — deferred, needs
  explicit user go-ahead before starting (standing instruction, never skip
  this check).

### Phase 7 — Boeing live verification (real 34,782-record `experience`,
2,941+1,270,564-row origin `Company` tables — see `docs/diagnostics/
BOEING_APP_VS_MANUAL_DIAGNOSIS.md`)

Eight real bugs found and fixed, each only visible at real-world scale — none
were caught by the existing test suite (small fixtures) or earlier
remediation-1-6 diagnosis runs (which failed earlier in the pipeline):

- [x] Bug A — ClickHouse "Field value too long": a 34,782-id `IN (...)`
  filter exceeded the HTTP field-size limit. Fixed with `_chunked_filters`
  (500-id chunks), `src/orchestrator/nodes/extract_data.py`.
- [x] Bug B — HTTP status polls timing out during heavy pairwise scoring.
  Fixed with `await asyncio.sleep(0)` every 1000 pairs,
  `src/mcp_server/tools/resolve_entities.py`.
- [x] Bug C — the real root cause of a 30+ minute "experience" stall:
  candidate-pair blocks were fully materialized (O(n²)) before ever
  truncating to the comparison budget, even though blocks are sorted
  smallest-first specifically to make an early exit safe. Fixed in
  `_generate_candidate_pairs`.
- [x] Bug D — destination `id`/FK columns were `BIGINT GENERATED ALWAYS AS
  IDENTITY`; the app assigns string cluster-id hashes to `id`. User's
  explicit choice: converted the schema to `TEXT` (not a bigint-remapping
  layer) — `sample_databases/destination/sql/*.sql` + ER diagram + live
  `global_warehouse`.
- [x] Bug E — Diffbot's `"d1997-XX-XX"` date encoding had no parser
  anywhere. Added `normalize_date_like()` in `value_coercion.py`.
- [x] Bug F — an empty string (`""`) into a numeric/date/bool column crashed
  the load. `coerce_value` now treats `""` as NULL for non-text types.
- [x] Bug G — FK columns carry raw source ids, but a parent row lands under
  its cluster id — untranslated, every FK to an already-loaded parent
  crashed. Fixed with a new `get_foreign_keys()` (queries
  `information_schema`, no hardcoded table names) +
  `_translate_foreign_keys`, applied post field-mapping in
  `src/mcp_server/tools/load_data.py` (first attempt ran on raw
  source-keyed records by mistake — corrected).
- [x] Bug H — an empty-but-resolved FK lookup (`{}`, an entity type with
  zero extracted records this run) was treated the same as "never
  resolved," skipping translation and crashing anyway. Fixed: check key
  presence, not dict truthiness.
- [x] A false-positive "completed" status (LLM free-quota exhaustion mid-run
  → 0 scope candidates → hollow success) — not a code bug, see below.

**Result:** Boeing reached genuine `status=completed` 2026-08-01 with real
data (33,314 rows across 9 entity tables). `compare_to_golden.py boeing`:
**12.0% overall row parity** vs the hand-merge golden (best: `person` 73.7%,
`company` 45.2%). This is the already-documented app-vs-manual gap
(`merge-engine-app-vs-manual-gap.md`), not a new bug — tonight's fixes made
the pipeline finish, not made it precise. Airbus and Lockheed not yet
re-verified against this fixed pipeline.

### LLM fallback chain automated (2026-08-01)
- [x] `LLMClient.call()` now automatically advances through
  `config.llm.fallback_models` on a quota/billing error instead of raising
  and requiring a manual `config.yaml` edit + app restart. See
  `llm-model-fallback-chain` memory for the current 9-model chain.

### Performance backlog (found, deliberately not fixed — speed is a stated
future priority, see `docs/diagnostics/PERFORMANCE_BOTTLENECKS.md`)
- [ ] Entity-cluster partitioning (`_build_clusters` in `resolve_entities.py`)
  is the dominant runtime cost — unguarded O(n²) per connected component,
  no size cap. Strongest evidence of the five bottlenecks found; start here.
- [ ] Field-mapping LLM calls run serially per entity type (independent,
  trivial `asyncio.gather` win).
- [ ] Pairwise scoring + Fellegi-Sunter fitting are pure-Python over up to
  250k pairs (vectorization candidate).
- [ ] ClickHouse chunked `IN(...)` filters trade correctness for round-trips
  (~70 round-trips for Boeing's 34,782-id case).
- [ ] No mid-run resume — a failed `load_data` step means the next run redoes
  ALL entity clustering from zero.

## Phase 0 — Harness foundation on `main` (2026-08-03 → 2026-08-05)

Full detail in `docs/verification/BASELINE_V1.md` (the authoritative
before/after numbers every later branch is measured against) and project
memories `merge-engine-phase0-bug-fixes.md` /
`merge-engine-harness-phase0-0.1-to-0.3.md`. Summary here for anyone reading
the repo without memory access. Source plan:
`C:\Users\rezas\.claude\plans\hidden-growing-wilkes.md` (§PHASE 0 / §PART 1.5).

### §0.0 — Destination-correctness bugs (fixed first; they invalidated the baseline)
- [x] BUG A — destination schema mutation (43 columns where the contract says
  14): `field_mapping.system.txt` rewritten to `map`/`skip` only (no
  `new_column`), `map_fields_impl(allow_schema_changes=False)` is now a hard
  gate, `_apply_schema_changes` is a load-time backstop, regression test
  pins `company` at exactly 14 columns. Verified live: 14, not 43.
- [x] BUG B — `company.name`/`legal_name` NULL rows: camelCase-aware
  heuristic tier (`entityName`→`name`, `fullName`→`legal_name`) plus the
  rewritten prompt's worked examples. Verified live: `count(name)` 23/23
  (100%), was 16/31.
- [x] BUG C0-C3 — matcher was structurally blind to identity, not just
  under-tuned: C0 canonical comparison projection (compare a uniform
  `{name, domain, ticker, ...}` view, not raw intersected field names) +
  surrogate-key exclusion + type-aware comparison levels; C1 block-purge
  degrades instead of annihilating (and counts every purged pair into
  `uncompared_pairs` — the honesty invariant); C2 adds domain/ticker/tax-id
  deterministic blocking, exempt from purging; C3 canonical projection fixes
  cross-origin field-name divergence in blocking. Verified live:
  `candidate_pairs > 0` for `company` (was 0 at n=31).
- [x] BUG D.1-D.4 — throughput (`docs/verification/CLUSTERING_PROFILE.md`):
  profiled before optimizing; hoisted the two dict comprehensions inside
  `_partition_component`'s inner loop; added a yield point in
  `_build_clusters` (UI no longer freezes 10-30 min with zero feedback);
  parallelized `discover_schemas.py` (independent origins) and
  `map_fields_node.py` (pre-fetched schemas instead of one shared
  connection) via `asyncio.gather`.
- [x] BUG D.5-D.7 — see Part 1.5 below (implemented later in the same
  session, listed here for completeness against the plan's own numbering).

### §0.1-0.9 — Substrate (all live-verified, `python scripts/check.py all` → 9/9)
- [x] 0.1 Dependency truth: `pyproject.toml` reconciled against
  `requirements.txt`; every future branch's dependency pre-declared as an
  extras group (`dspy`, `splink`, `dq`, `lineage`, `swarm`, `perf`).
- [x] 0.2 `scripts/check.py` — one verification command (ruff, ruff-format,
  mypy ratchet, arch lint, doc lint, unit+integration tests, frontend
  typecheck+build, matcher eval gate).
- [x] 0.3 Pre-commit hooks live (`ruff`, `mypy` ratchet, plus 5 project-
  specific local hooks, each traceable to a real past failure —
  `no-blanket-json-ignore`, `no-sample-databases-commit`,
  `no-str-format-on-prompts`, `no-new-bare-except-pass`,
  `no-import-time-graph-compile`). Server CI on `git.n4t.com` still
  unreachable — documented honestly in `docs/decisions/0003-server-ci-unreachable.md`
  rather than pretending it exists.
- [x] 0.4 `CLAUDE.md` (≤100 lines, every line traceable to a real failure),
  `AGENTS.md`, `docs/INDEX.md`, `docs/` restructured into
  `architecture/`/`decisions/`/`plans/`/`verification/`/`diagnostics/`;
  `scripts/lint_docs.py` for broken-link/stale-`file:line` detection.
- [x] 0.5 `scripts/lint_arch.py` — layering (`api`→`orchestrator`→`tools`→
  `connectors`→`utils`), no module-level mutable state in
  `mcp_server/tools/`, no import-time graph compile, ENTER/EXIT logging
  convention. Baseline ratchet: 17 → 15 entries after §0.8's de-globalization.
- [x] 0.6 `docs/verification/BUDGETS.yaml` (machine-checkable performance
  contract) + per-node `timings` written into every run report;
  `drive_app.py --assert-budgets` compares a live run against it.
- [x] 0.7 `pytest-xdist` worker isolation (`MERGE_ENGINE_HISTORY_PATH` keyed
  per-worker); `patch_server_config` converted to a save/restore fixture;
  `unit`/`integration`/`live`/`slow` markers.
- [x] 0.9 Frontend build story documented in `CLAUDE.md` — a fresh clone has
  no UI until `npm run build`; `dist/` deliberately stays gitignored.
- [ ] 0.10 Secret hygiene: `.claude/settings.local.json` was flagged as
  containing what looks like a live `dashscope` bearer token — **flagged
  only, not rotated**, per standing instruction this is the user's call.
- [x] 0.11 `docs/verification/BASELINE_V1.md` recorded (test suite, matcher
  eval, prompt-contract harness, live Boeing run — all four re-measured
  against the model actually configured, not the stale checked-in
  scorecard).

### §0.8 — Conflict-proof the shared seams (this session's primary Phase 0 work)
- [x] `get_initial_state` legacy-config bugs fixed (missing
  `fallback_models`, hardcoded `conflict_policy`).
- [x] `LLMClient` cache-before-parse bug fixed (a malformed response no
  longer gets cached and served forever).
- [x] Dead code removed (`_error_router`, duplicate `MergeState` class).
- [x] Lazy graph compile via module `__getattr__` (was compiling on every
  import).
- [x] Quadratic `errors` accumulation fixed across 8 node files — each node
  now returns only its own new errors; `operator.add`'s reducer preserves
  history, not the node re-returning the whole list every step.
- [x] Passthrough bloat removed from the same 8 files.
- [x] `_LAST_BLOCKING_STATS`/`_LAST_CLUSTERING_STATS` module globals in
  `resolve_entities.py` de-globalized — stats now thread through
  caller-owned dicts. This is what makes §0.0 D.4's parallelism safe.

### Phase 0 exit — honest result
`compare_to_golden.py boeing` after all of §0.0-§0.8: **0.9% overall row
parity** (31/3,427), below the stale `BUDGETS.yaml` floor of 12.0% (v1's
pre-fix number, over a narrower entity scope). Reported exactly as measured,
not smoothed — full root-cause table (per-entity, not a bare number) in
`BASELINE_V1.md` §4c. `BUDGETS.yaml`'s floor deliberately left unchanged
pending an explicit user decision, per the project's "not a mechanical
update" rule.

## Part 1.5 — Speed and Langfuse observability rebuild (2026-08-04 → 2026-08-05)

Lands on `main`, before the branch-creation gate, for the same
conflict-avoidance reason as Phase 0: touches `resolve_entities.py`,
`map_fields_node.py`, `client.py`, `workflow_manager.py` that 3-5 future
branches would otherwise edit at once, and every branch's verification cycle
pays whatever the full run time is. Full detail:
`docs/verification/BASELINE_V1.md` §6/§8,
`docs/diagnostics/BOEING_SPEED_REPORT_2026-08-04.md`.

### §A — Scoring-path waste removal (provably zero precision risk)
- [x] A.0 `scripts/diagnostics/profile_resolve.py` — the falsifiable
  measurement harness: runs `resolve_entities_impl` heuristic-only against
  real ClickHouse data, dumps the full cluster structure to JSON as a
  byte-identical acceptance baseline, profiles with cProfile.
- [x] A.1 Precompute canonical projections once per record instead of up to
  ~6× per pair (the single biggest win — ~350M substring comparisons down
  to ~1,500 for `technology`).
- [x] A.2 `_fit_match_model` now returns its comparison vectors instead of
  discarding them; the scoring loop indexes into them instead of rebuilding.
- [x] A.3 Hoisted 3 function-local imports (`fellegi_sunter` constants) out
  of per-pair/per-field code paths (order 5-10M executions/entity type).
- [x] A.4 Hoisted `_record_id` out of the O(n²) blocking double loop (the
  same defect BUG D.2 had already fixed elsewhere in the file).
- [x] A.5 Cached `_best_name_field` per record inside the A.1 projection
  table.
- [x] A.6 Fixed a latent unbounded-expansion bug in the comparison budget
  check (moved the check inside the inner loop; a `full:dom:` block could
  otherwise expand to hundreds of millions of tuples with no ceiling).
- [x] A.7 `estimate_model`'s M-step fix (byte-identical cluster output
  proven via `profile_resolve.py --check`; isolated CPU cost for
  `technology` fell 121.78s → 32.02s).
- [x] Verification: `profile_resolve.py --check` byte-identical for
  `technology` and `experience`; `tests.eval.score` unchanged (precision
  1.0000, recall 0.9950); `check.py all` 9/9.

### §B — Parallelism (scheduling only; results unchanged)
- [x] B.1 `map_fields`: `asyncio.gather` across entity types, pre-fetching
  destination schemas instead of sharing one connection inside the loop.
  Order-dependent accumulators (`pending_schema_changes`, `errors`) merged
  back in `target_entities` order to keep determinism.
- [ ] B.2 `extract_data` chunk parallelization — **deliberately NOT
  implemented.** Scoped, then found to carry more correctness risk than
  initially estimated: `table_failed` triggers a position-based
  `spool.truncate_after(...)` rollback that assumes strict write ordering;
  concurrent chunk fetches racing straight into spool appends could violate
  that. The only safe fix (gather-then-sequential-merge) trades away the
  node's current constant-memory guarantee for a measured ~1% payoff
  (`extract_data` is 24s of a 2442s run). Deprioritized, not abandoned —
  revisit only if a future baseline shows extraction as a real bottleneck.
- [x] B.3 `resolve_entities`: true multiprocessing for the CPU-bound core.
  `_resolve_core` extracted as a synchronous, module-level, picklable
  function (blocking → model fit → scoring), dispatched via
  `ProcessPoolExecutor` per entity type; LLM adjudication and clustering
  stay in the parent process. Confirmed live (not just in isolation): the
  app log shows `experience`/`technology`/`company_relation` entering
  `resolve_entities_impl` within the same second, all logging
  `dispatched=process_pool`. Largest single contributor to
  `resolve_entities`'s 2.44x speedup (1994.84s → 818.75s).

### §C — Langfuse observability, rebuilt to show the whole graph
- [x] C.0 Root-caused (not theorized) why the trace didn't show the graph:
  Defect 1 — `client.py`'s `trace_config()` **replaced** the ambient
  LangChain callback config instead of merging into it, detaching every LLM
  generation from its parent node span and dropping `langgraph_node`
  metadata. Defect 2 — HITL resumes each ran their own top-level `astream()`
  with no shared handler wiring, fragmenting one run into 3 root traces.
- [x] C.1 Stack/SDK compatibility smoke test: confirmed `langfuse==4.13.2`
  SDK round-trips against the pinned `langfuse:3` server images before
  changing anything.
- [x] C.2 Fixed Defect 1 — callbacks inherited (not replaced) when an
  ambient LangGraph callback manager exists; metadata merged, not
  overwritten. Generations now nest correctly under their node span.
- [x] C.3 Fixed Defect 2 — `rehydrate_workflow` rebuilds the same traced
  config `start_workflow` does (factored into one shared helper);
  `update_state`/`get_state` calls no longer carry callbacks (only
  `astream` does). One session = one trace across every HITL resume.
  Verified live: exactly one trace for a 5-HITL-gate run.
- [x] C.4 Custom `resolve.<entity>.{blocking,fit_model,scoring,clustering}`
  phase spans via Langfuse's own `start_as_current_observation` API (raw
  OpenTelemetry spans were tried first and verified NOT to export — the
  Langfuse span processor filters by instrumentation-scope allowlist).
  `extract.<table>`/`load.<table>` spans deliberately scoped out (same
  judgment call as B.2 — extraction is ~1% of run time; not worth the diff).
- [x] C.5 `scripts/diagnostics/verify_langfuse_trace.py` — 6 automated
  assertions against the real Langfuse API (one trace per run, every node
  has a span, every generation has a non-null parent + model + token usage,
  custom phase spans present, durations within tolerance of the run
  report). Replaces two pre-existing pseudo-checks that could never fail.
- [x] C.6 Langfuse `flush()` wired into FastAPI shutdown and the end of
  every `_run_workflow`/`_continue_workflow`; retry-after-late-stack-start
  fix so tracing isn't permanently dead for a process's lifetime if the
  Langfuse stack comes up after the app does.

### §D.5-D.7 — the remaining throughput items, implemented after A/B/C
- [x] D.5 Scoring-path exact short-circuits in `_string_similarity_cached`:
  boolean "used" array instead of `list.remove()` (O(1) vs O(n) per match),
  plus a provably-exact `difflib` skip (Ratcliff/Obershelp's own
  `ratio = 2M/T` bound proves `seq` can't beat an already-sufficient
  `jaccard`, so the expensive `get_matching_blocks()` call is skipped, never
  approximated). Verified byte-identical via `profile_resolve.py --check`.
- [x] D.6 ClickHouse chunked-extraction round-trips replaced with a
  scratch-table + JOIN approach (was ~70 sequential `IN (...)` round-trips
  for Boeing's 34,782-id case).
- [x] D.7 Mid-run resume: `logs/spool/_resolve_cache.duckdb`
  (`resolve_cache` table, keyed by content hash of the input records) caches
  clustering results across runs. Confirmed live: 40 real cache entries
  written across every entity type during the final Boeing run.

### Final re-verification (2026-08-05, `thread_id=wf_f9b095bf`) — all of D.5-D.7 + B.3 + C.6 together
- [x] Full Boeing run: `resolve_entities` 818.75s (was 1994.84s, **2.44x**),
  `map_fields` 93.48s (was 543.78s, **5.8x**), total wall time **23.4 min**
  (was 50.7 min).
- [x] Correctness unchanged: parity 0.9% (identical to Phase 0's baseline,
  confirmed via a direct `compare_to_golden.py` read, not the driver's
  cached line); `tests.eval.score` precision/recall unchanged; zero
  Tracebacks/`[ERROR]`/`[CRITICAL]`/Langfuse warnings in the app log.
- [x] `verify_langfuse_trace.py wf_f9b095bf`: 6/6 assertions pass. Required
  one test-script fix (not an app bug): B.3's process-pool dispatch
  collapsed the 3 separate `.blocking`/`.fit_model`/`.scoring` spans into
  one `.match` span (a worker process has no event loop to hold open 3
  async span context managers on — timings now ride as span attributes
  instead), so the script's phase-name assertion was updated to match.
- [x] `python scripts/check.py all` → 9/9; 1,325 tests passing.

**Part 1.5 exit — this is the pipeline's fully-verified state**: every item
from Phase 0 §0.0's BUG D list (D.1-D.7) and Part 1.5's A/B.1/B.3/C sections
are implemented and live-verified. B.2 remains deliberately deferred
(documented correctness-risk trade-off above). Per the plan's own
instruction, this is a **hard stop at the BRANCH CREATION GATE** — the five
feature branches (B1 dspy, B2 planner-swarm, B3 quality-lineage, B4
matching-precision, B5 harness-ratchet) have not been started and will not
be until the user gives explicit go-ahead.

## Part 1.6 — Configurability, investigation UI, and cross-platform portability (2026-08-05)

Also lands on `main` before the branch gate, for the same reason as Phase 0
and Part 1.5: touches `src/api/main.py`, `src/orchestrator/graph.py`,
`workflow_manager.py`, `resolve_entities.py`'s node, and `Dockerfile` — files
3-5 branches would otherwise edit at once. Driven by four decisions the user
made explicitly: venv+Docker for portability, all four investigation tools,
config split into an Advanced panel (run-by-run) + Settings page (platform),
and Docker/venv smoke as the CI enforcement target.

### §A — Run-by-run + platform configuration surface
- [x] A.1 Layered config model: `config/platform_overrides.yaml` (gitignored,
  new) deep-merged over the committed `config.yaml`, then `${ENV_VAR}`
  resolution — `load_config()`/`load_raw_config_dict()` in
  `src/utils/config_loader.py`. Takes effect on the *next run*, no restart:
  confirmed live by editing `retry.max_attempts` via the Settings page and
  reading it back through both `GET /api/config` and the real
  `load_config()` path a fresh run uses.
- [x] A.2 `GET /api/config` (effective config + per-key provenance —
  `override`/`env`/`file` — secrets always stripped) and
  `PUT /api/config/platform` (diff-against-baseline save — editing one field
  no longer silently pins every other resolved value, a real bug caught live
  in the browser and fixed before shipping).
- [x] A.3 UI: `AdvancedPanel.tsx` (model/temperature/batch-size overrides,
  matching strictness, LLM-toggle checkboxes, per-gate auto-approve) on the
  Pipeline screen; `SettingsScreen.tsx` (new "Settings" tab) for platform
  defaults + a read-only `.env` presence check (values never shown).
- [x] A.4 The previously-dead `matching_rules` surface made live:
  `get_initial_state` threads `matching_rules`/`run_config` overrides into
  `llm_config`/`extraction_config` (only the keys actually set — a partial
  override never resets the rest); `_matching_rules_for()` in
  `nodes/resolve_entities.py` adds a `"_default"` fallback (same convention
  `conflict_policy` already used) so a rule submitted before
  `target_entities` exists still reaches every entity type.
  New state key `auto_approve_gates`: a gate in that list resumes itself via
  the existing `resume_workflow` path from inside
  `_finalize_stream_status` — reuses 100% of the tested HITL machinery,
  cascades gate-to-gate as each resume's own stream ends, validated against
  `REVIEW_GATES` (`handle_error` can never be one, both at the API layer and
  in `WorkflowManager.start_workflow`).
  **Live-verified** with a real scoped ClickHouse→PostgreSQL run
  (`wf_a93e5822`, Nova subject, `matching_rules={"_default":
  {"strictness":"strict"}}`, `auto_approve_gates=`all 5 review gates): the
  app log shows `human_review_deps` and `human_review_scope` both
  auto-resumed with **zero** manual `/feedback` calls
  ("auto-approving gate ... no human reviewed this checkpoint"), and
  `strictness=strict (auto_match=0.98)` reached every one of 7 real entity
  types (`company`, `address`, `location`, `experience`, `technology`,
  `contact`, `company_relation`). New tests:
  `tests/unit/test_matching_rules_and_policy.py`'s
  `TestMatchingRulesOverridePath`/`TestMatchingRulesDefaultFallback`/
  `TestRunConfigOverrides`/`TestAutoApproveGatesThreading` (15 new cases,
  62/62 passing).

### §B — Investigation UI (all four tools; read-only, `InvestigateScreen.tsx`)
- [x] B.1 Match & cluster inspector — `GET /api/workflow/{id}/matches`
  (`WorkflowManager.get_match_inspection`, reads straight from the
  checkpoint, works for a completed run same as an active one) +
  materialised cluster membership via the spool. Live: real
  `resolution_stats` for all 10 entity types of the completed
  `wf_f9b095bf` Boeing run, `company`'s stats matching
  `BASELINE_V1.md` exactly (`candidate_pairs: 411`); graceful "spool
  cleaned up" message when cluster data no longer exists but the
  checkpoint's own stats still do.
- [x] B.2 Run trace & cost timeline — `GET /api/workflow/{id}/timeline`
  wraps the existing `logs/run_reports/<id>.json` + cost ledger. Live: real
  per-node bars for `wf_f9b095bf` (`resolve_entities` 818.8s, matching
  §8 of `BASELINE_V1.md` exactly).
- [x] B.3 Schema & data explorer — `sample_table_rows()` in
  `src/ui/components/db_discovery.py` (new), bounded to 200 rows,
  identifier-validated (rejected a live SQL-injection-shaped table name
  during testing) via `POST /api/explore/sample`. Live: real rows sampled
  from ClickHouse `origin1.Company`.
- [x] B.4 Run comparison / reconciliation — `GET /api/runs/diff/golden/{subject}`
  wraps `compare_to_golden.py` as a subprocess (same pattern
  `drive_app.py --assert-budgets` already used). Live: Boeing → 0.9% overall
  parity, all 11 tables, matching `BASELINE_V1.md` §4c exactly; unknown
  subject correctly 404s.
  All four verified twice — once via `TestClient` against real ClickHouse/
  PostgreSQL/run-report data, once again through real clicks in the actual
  browser UI after a full app restart to load the new routes.

### §C — Cross-platform portability (venv + Docker)
- [x] C.1 De-hardcoded `na180`/conda-absolute paths from the two genuinely
  *instructional* surfaces — `CLAUDE.md`, `.claude/launch.json`.
  Deliberately left untouched: `TODO.md`, `docs/plans/PHASE_Q_PLAN.md`,
  `docs/verification/{SCORING_PROFILE,BASELINE_PRE_CHANGE}.md` — these are
  append-only historical records (profiling output with real captured
  frames, dated checklists), not instructions; rewriting them would be
  revisionist. `scripts/lint_portability.py` encodes exactly this
  distinction in its own `_SCANNED_GLOBS` list.
- [x] C.2 `scripts/bootstrap.py` (new): plain-venv, no conda anywhere in its
  own logic — creates `.venv/`, `pip install -e ".[dev,perf]"`, builds the
  frontend if `npm` is on PATH, copies `.env.example`→`.env`.
  `.python-version` (3.11), `.env.example` (every env var the app actually
  reads, grepped for real — `LLM_API_KEY`, `LANGFUSE_*`,
  `MERGE_ENGINE_HISTORY_PATH`, `MERGE_ENGINE_DISABLE_RESOLVE_CACHE`),
  `docker/langfuse.env.example` (the previously-undocumented Langfuse
  bootstrap secrets template). **Live-verified end to end**: ran
  `scripts/bootstrap.py` for real, then booted the app from the freshly
  created `D:\...\.venv\Scripts\python.exe` (zero na180 involvement) and
  confirmed it served the real React SPA.
- [x] C.3 Rewrote `Dockerfile` — the previous version could not actually run
  the app (referenced removed Gradio, defaulted to the broken `--mode
  both`, never ran `npm run build`, so it would have booted to the
  `{"status":"api-only"}` stub). New 3-stage build (`node:22-slim` frontend
  stage → `python:3.11-slim` dep builder → slim runtime), `HEALTHCHECK`,
  non-root user. Added `.dockerignore` (didn't exist — the old Dockerfile's
  `COPY src/ src/` would have baked in `.env`/`logs/`/`config/profiles/` if
  present) and `docker/app-compose.yml` (app-only; origins/destination are
  entered live through the UI, not modelled as compose services, so this
  file's only job is running the container with the right env file and a
  `host.docker.internal` alias for reaching the separately-run Langfuse
  stack). **A real bug was found and fixed by the live boot test, not
  invented**: `src/main.py` bound uvicorn to `127.0.0.1` unconditionally —
  works for every native run, but Docker's `-p` port-forwarding cannot
  reach a container's own loopback, so the published port accepted a TCP
  connection and then returned nothing. Fixed with a
  `MERGE_ENGINE_HOST` env var (default `127.0.0.1`, unchanged for every
  non-Docker invocation; the Dockerfile sets it to `0.0.0.0`). **Live-verified
  after the fix**: `docker build` succeeded, the container reported
  `healthy`, and `curl` against the published port returned the real
  `<title>merge-engine</title>` SPA plus a working `/api/workflow/active`
  response.
- [x] C.4 `scripts/lint_portability.py` (new) — hardcoded-path guard (path-
  shaped patterns only, not a bare word match, after a real false-positive
  was found and fixed: the guard was initially flagging the word "na180"
  inside its own explanatory comments) + a completeness check that
  `.env.example`/`.python-version`/`scripts/bootstrap.py`/`Dockerfile`/
  `.dockerignore` all exist. Wired into `scripts/check.py portability`
  (part of `check.py all`) and as a new `.pre-commit-config.yaml` hook.
  Live-verified against a seeded real violation (correctly failed) and the
  clean tree (correctly passed).

### §D — CI/CD + branch-conflict re-audit
- [x] D.1 `git.n4t.com` re-checked per ADR 0003's own revisit trigger — a
  real change since the ADR was written (port 443 now answers, previously
  timed out) but not the change that matters: `curl` gets an unrelated
  `Server: Kestrel` 404 (no Gitea/Forgejo/GitLab signature), while the
  actual git protocol `origin` uses (SSH) still times out and
  `git ls-remote origin` still fails. Server-side CI is still genuinely
  blocked; documented precisely in
  `docs/decisions/0003-server-ci-unreachable.md` rather than either
  declaring the gap closed or ignoring the new data point. Prepared (not
  live-verified — cannot be, no reachable runner) `.gitea/workflows/ci.yml`
  running `check.py all` plus scripted versions of C.2/C.3's exact live boot
  tests, ready to activate the moment a runner exists.
- [x] D.2 Re-audited the six §0.8 conflict seams against the current tree.
  Four hold, verified with assertions not by eye: `errors` hygiene (spot-
  checked `resolve_entities.py`'s node — still returns only its own new
  errors), arch-lint baseline unchanged at 15 (zero new violations from all
  of Part 1.6's edits), no `pyproject.toml`/`requirements.txt` edits this
  part (per R4). **Two do not exist and never did**: `NODE_SPECS` (a
  declarative node-wiring table) and `REPORT_CONTRIBUTORS` (a pluggable
  report-section registry) were part of the original plan's *aspirational*
  §0.8 design table but were never actually built — `graph.py` still wires
  all 17 nodes with inline `add_node`/`add_conditional_edges` calls, and
  `build_reconciliation_report` (`load_data.py`) is still one function
  body. This means R3 ("extend through registries, never through shared
  function bodies... if you find yourself editing build_reconciliation_
  report's body, stop") is currently unenforceable as written — there is no
  registry to redirect a future branch to. Flagged honestly rather than
  silently built (a real refactor of both, out of scope for an audit) or
  silently ignored: **whichever of B2 (new nodes)/B3 (new report sections)
  starts first should build the specific registry it needs**, not assume
  one already exists.
- [x] D.3 Re-ran `pre-commit run --all-files` against the entire current
  tree — all 14 hooks (including the new `lint-portability` hook) passed
  cleanly, which itself is evidence none of Part 1.6's file moves/renames
  broke a hook's glob matching. Spot-verified `no-new-bare-except-pass`
  still blocks a deliberately seeded bad pattern (exit 1, correct message).

**Part 1.6 exit**: `python scripts/check.py all` → 10/10 (9 original +
the new `portability` check). All four investigation tools, the run-by-run/
platform config split, and both from-scratch paths (venv, Docker) are
live-verified against real data, not mocks. Still at the **BRANCH CREATION
GATE** — none of B1-B5 have started.
