# Phase Q — Library integrations (Splink · Great Expectations · Lineage) + rich 3-origin seed

> This plan is written to be **handed to another implementing agent** with no
> prior context. It is deliberately self-contained and detailed. Phases 0–P are
> complete (see `TODO.md`). Everything below is NEW work.
>
> **Hard constraints (carry through every step):**
> - Work only inside `D:\transfer\merging`. Never touch parent folders.
> - Always `conda activate na180` before running Python, tests, or the app
>   (deps: duckdb, structlog, fastapi, asyncpg, clickhouse-connect, pymongo live
>   in `na180`, NOT base). Python at
>   `C:\Users\rezas\miniconda3\envs\na180\python.exe`.
> - Exactly ONE destination configured at a time (any type).
> - Prefer the `codebase-memory` MCP (`search_graph`, `trace_path`,
>   `get_code_snippet`) over raw grep for code lookup — it is re-indexed and
>   current. Fall back to Grep/Read for prose/config.
> - Every phase stops for explicit user go-ahead before the next. No phase moves
>   on without green tests AND a before/after number.
>
> **⚠️ IMPORTANT COMMAND — verify every task live, immediately.** After
> implementing ANY checkbox task, the agent must check it off ONLY after it has
> verified that task is actually done AND working live — not just that the code
> was written. "Live verification" means: run it against the real app/DBs (start
> the server, run the pipeline, query the destination directly, run the unit +
> eval tests), observe the real result, and confirm it with an independent check
> (direct SQL/log/report inspection, never the app's own success message alone).
> Do this **immediately after** each task, not batched at the end of a phase. If
> a task cannot be verified live, it is NOT done — leave it unchecked and say why.
> - Match the existing house style: one file per node, typed `MergeState`,
>   `logger.debug("ENTER …")`/`"EXIT …"` in every node, tests co-located in
>   `tests/unit/test_*.py`, golden evals in `tests/eval/`.

---

## Context — why this is being built

The user collected 20+ open-source GitHub projects across entity resolution,
MDM, ETL, schema mapping, data quality, lineage, and conflict-free merging, and
asked: *"search all of them completely, understand every function/class, tell me
which can help us upgrade/replace/remove parts of our project, then write one
very detailed plan another agent can execute without me re-explaining."*

Separately, the earlier **P.7** ("seed 6 companies across 3 origins + MongoDB")
was only partially done: the live proof ran on the existing Nova data across
**2** origins (crm_origin PG + hr_origin CH). MongoDB was blocked by a local
port-27017 conflict and only 2 companies' worth of Nova rows existed. The user
now wants P.7 done properly: **6 companies, ~200 people each, across all 3
origins including a live MongoDB**, so a manager demo has a convincing corpus.

This plan was researched with: three `Explore` agents over the matcher, the
extract/load path, and the seed/test infrastructure; `WebFetch` on every listed
repo; and direct reads of the graph, load, and map-fields nodes. The four
strategic decisions below were confirmed with the user before writing.

### Decisions locked (user answers, 2026-07-25)
1. **Splink = add as an OPTIONAL engine.** Keep the current hand-rolled
   Fellegi–Sunter matcher as the default (it scores precision/recall 1.0 on our
   eval). Add Splink as a per-entity selectable engine behind the existing
   (currently dead) `matching_rules` config. Reversible, no regression risk.
2. **Also integrate Great Expectations AND column-level lineage** (not just
   document them). Both are real gaps today.
3. **Seed size = 6 companies × ~200 people each** (~1,200 people, ~120 products,
   plus deals/founders/jobs/education/skills/etc.) across the 3 origins.
4. **MongoDB = run in Docker on an alternate host port** (27018) and seed it as a
   fully live 3rd origin.

---

## The library evaluation — verdict on all 20+ repos

Researched each via its GitHub README (WebFetch). Grouped by our decision.

### ADOPT (in this plan)

| Repo | License | Why it wins | Where it plugs in |
|---|---|---|---|
| **Splink** (moj-analytical-services/splink) | MIT | The *exact* model we hand-rolled (Fellegi–Sunter + term-frequency), but mature: EM-trained m/u, DuckDB-native ("1M records/min on a laptop"), maintained (Splink 4, 2024). We already use DuckDB for the spool, so it reads our `rows_*` tables directly. | New optional engine at the `resolve_entities_impl` seam (Q.1). |
| **Great Expectations** (great-expectations/great_expectations) | Apache-2.0 | We have **zero** data-quality validation today. GX validates a pandas/DuckDB dataset with declarative Expectations (nulls, types, ranges, uniqueness) and renders results. | New `validate_data` node after `extract_data` (Q.2). |
| **sqlglot** (`sqlglot.lineage`) + optional **sqllineage** | MIT | Column-level lineage — which origin column fed each destination column — which our reconciliation report lacks. `sqlglot` is zero-dependency and we can drive it from our own `field_mappings`; `sqllineage` (uses sqlfluff/networkx) is the heavier CLI alternative. | Build lineage from `field_mappings` + spool provenance, surface in the report (Q.3). |

### REJECT (documented, not adopted)

| Repo | Why not |
|---|---|
| **Zingg** | AGPL-v3 (licensing risk for a product) **and** requires a Spark cluster. Too heavy to embed. |
| **Dedupe** | MIT but needs interactive human labelling per dataset, weaker than Splink for our SQL/scale path. Splink dominates it. |
| **JedAIToolkit** | **Archived May 2026**, Java/JVM, needs PyJNIus bridge. Dead + wrong runtime. |
| **Liken** | 4 stars, emerging, DataFrame-only, less capable than Splink. |
| **FastER** | Repo 404 (gone). Needs a CUDA GPU we don't have; our 43M-row run already finishes in 44 min. |
| **DoppelGoner** | Hard-coded to the HSDS (human-services) data spec — not our domain. |
| **AtroCore, Pimcore** | PHP enterprise platforms (whole apps, not libraries). Wrong language, not embeddable. |
| **schema-driven MDM platform** | A whole competing FastAPI+Next.js product, not a component to import. |
| **ReplicaDB, GETL, Artie Transfer, goetl, Pansynchro** | Go/Java/C# raw DB-to-DB replication. No entity resolution, not embeddable in Python. Our connectors are already 43M-row proven. |
| **dlt** | Apache-2.0, genuinely good — but it would replace our extract/load connectors, which are scale-proven and carry bespoke keyset/watermark/scope-filter logic. Rip-out cost >> benefit. Documented as "considered, future option for new source types". |
| **Pathway** | Streaming/Rust engine, BSL license. Built for real-time RAG, overkill for batch merges. |
| **FlexMatcher, schema-mapper** | FlexMatcher is stale (51 commits) and needs training data; schema-mapper targets warehouses (BigQuery/Snowflake). Our LLM field-mapping (`map_fields`) is already better for our case. |
| **Data Dictionary Agency** | Schema-doc/visualization tool, not a pipeline component. |
| **Cleanlab, YData Quality** | Cleanlab needs a trained ML model + predicted probabilities (wrong shape for raw DB validation); GX covers our need more directly. YData is profiling, lower priority than GX. |
| **LineageX, Lineage Graph Accelerator** | dbt/warehouse-oriented lineage. `sqlglot`/`sqllineage` fit our SQL-string reality better. |
| **crdt-merge** | 5 stars, BSL, immature; its "order-independent merge" targets distributed model/agent-state merging, not golden-record survivorship. We already have a deterministic per-field conflict policy (`unify_entity_group.py`). Interesting, but risky and solves a problem we don't have. |

---

## Execution priority (do them in THIS order — each stops for user go-ahead)

1. **Q.7 — real ClickHouse origin1/origin2 setup.** ✅ COMPLETE (2026-07-28) —
   see TODO.md. Unblocked realistic live proof for every other phase.
2. **Q.8 — PostgreSQL destination from the Global Warehouse schema.** NEW
   (2026-07-29). With both origins now real and alive, the destination side of
   the demo is still whatever ad hoc `main_dest` was seeded in earlier phases.
   Stand up the actual destination schema the user supplied
   (`sample_databases/destination/ER_DIAGRAM.md`/`.html`, 25 tables) as a real
   PostgreSQL container in the same `sample_databases/docker-compose.yml`, on a
   non-native port. Do this before Q.2/Q.3/Q.1 so those phases' live
   verification runs against the real destination shape, not a synthetic one.
3. **Q.2 — Great Expectations data-quality gate.** Small, self-contained, high
   demo value, and it would have caught the original 0-row bug at the right place.
4. **Q.3 — column-level lineage in the report.** Small, report-only, low risk;
   reuses data already computed.
5. **Q.1 — Splink optional engine.** LAST: largest and only phase with real
   integration risk (the 8 invariants). It is default-off, so it cannot regress
   existing runs — if its eval numbers don't beat the builtin, it ships as a
   documented option and stays off. Acceptable outcome, not a failure.

(The sections below are written in Q.1→Q.8 numeric order for reference, but
**implement in the 1–5 priority order above.**)

---

## Q.1 — Splink as an optional per-entity matching engine

### Goal
Let a run choose, per entity type, between `engine: "builtin"` (today's matcher,
default) and `engine: "splink"`. Zero behaviour change when unset.

### Where it plugs in (the seam)
`src/mcp_server/tools/resolve_entities.py` → `resolve_entities_impl(entity_type,
records, blocking_keys, llm_config, known_cluster_ids, matching_rules)` at
**line 158**. This is the ONLY function the node calls. Everything downstream
(blocking, `_fit_match_model`, `_score_pair`, `_build_clusters`) lives inside it.

**Do NOT rewrite `resolve_entities_impl`.** Instead:
1. Add `engine = (matching_rules or {}).get("engine", "builtin")` near the top
   (after the rule-resolution block, ~line 245).
2. If `engine == "splink"` and Splink is importable, delegate to a NEW module
   `src/mcp_server/tools/splink_matcher.py::resolve_with_splink(...)` that returns
   the **exact same result dict shape** and then `return` early.
3. Otherwise fall through to the existing code unchanged.

### The result-dict contract `resolve_with_splink` MUST reproduce (line 331–351)
```python
{
    "entity_type": str,
    "clusters": [  # each cluster:
        {
            "cluster_id": str,  # deterministic, destination-PK-stable (see below)
            "record_ids": [str],  # "{_origin_id}:{_origin_table}:{_origin_id_row}"
            "match_confidence": float,
            "required_human_review": bool,
        }  # True if len>1 and min_conf < 0.85
    ],
    "total_records": int,
    "auto_matched": int,
    "auto_excluded": int,
    "llm_scored": int,
    "uncompared_pairs": int,
    "candidate_pairs": int,
    "blocks_purged": int,
    "pending_merge_candidates": [{"record_ids": [str, str], "confidence": float}],
}
```

### The 8 project invariants Splink does NOT give you — you must layer them on
(the built-in matcher earns its keep via these; a naïve Splink swap breaks them):

1. **Three-way decision, not binary.** Splink emits a probability. You must still
   split into auto-match (≥ `auto_match`, default 0.95), auto-exclude
   (≤ `auto_exclude`, 0.70), and an *uncertain band* that populates
   `pending_merge_candidates`. That band is what makes the `human_review_matches`
   gate fire (`nodes/resolve_entities.py:359`, `has_low_confidence_clusters`).
2. **Deterministic, reusable cluster IDs.** `_assign_cluster_id` (line 1182)
   reuses an existing destination id when a member already maps to one
   (`known_cluster_ids` from `_load_entity_map`, `nodes/resolve_entities.py:322`),
   else `deterministic_uuid(entity_type, *sorted_member_ids)`
   (`src/utils/id_utils.py:15`, sha256→UUID). Splink's own `cluster_id` must be
   **translated** through `_assign_cluster_id` or re-runs duplicate every dest row.
   REUSE the existing `_assign_cluster_id` function — import it, don't reinvent.
3. **Anti-over-merge guards.** `_has_conflicting_identity` (hard veto on differing
   unique-id fields, line 997), `_only_partial_name_evidence` cap (line 817),
   `_has_discriminating_agreement` (must agree on something with term-frequency
   ≤ 0.05, line 835). Splink has none. Apply them as a post-score clamp before
   clustering, reusing the existing helpers.
4. **Repair + cohesion clustering.** Splink's
   `cluster_pairwise_predictions_at_threshold` is raw transitive closure — exactly
   what our `_partition_component`/`_split_weakest_link` (lines 1345–1449)
   deliberately rejects. Run Splink for pairwise scores only, then feed those
   scores into the EXISTING `_build_clusters(...)` (line 1210). This is the
   cleanest integration: **Splink replaces scoring, not clustering.**
5. **LLM adjudication of the borderline band** (`_judge_pairs_with_llm`, line
   1064) keeps working over any scorer that emits probabilities — keep it.
6. **Cross-origin schema divergence.** THE BIG ONE. Splink assumes one unified
   column schema across all sources. Our records have divergent field names
   (`company_name` vs `org_title`) because `map_fields` runs *after* resolution.
   Two options — pick **(a)** for this phase:
   - **(a) Recommended:** run Splink per-entity over a **projected comparison
     view** — build a small dict per record of canonical comparison fields
     (`name`, `email`, `ref`, name-parts) using the same hint logic as
     `_blockable_fields`/`_best_name_field` (lines 405, 535), then hand Splink a
     uniform 4–6 column frame. This mirrors what `_build_comparison_vector`
     already does internally.
   - (b) Full alignment before matching — out of scope, too invasive.
7. **Reported shortfall numbers.** `uncompared_pairs`/`candidate_pairs`/
   `blocks_purged` flow into `resolution_stats` → the reconciliation report
   (`load_data.py:84-85`). Splink's blocking must yield equivalent counts, or emit
   0/0 with a note. Never silently drop the "duplicates may exist uncompared"
   signal.
8. **`unify_entity_group.py` is untouched.** Survivorship is ours regardless of
   matcher. Do not modify it.

### Splink integration sketch (`splink_matcher.py`)
- Use Splink's **DuckDB backend** (`splink.duckdb`). Feed it a pandas DataFrame of
  the projected comparison view (invariant 6a). We already depend on pandas
  (used by `spool._bulk_insert`) and duckdb.
- `SettingsCreator` with `link_type="dedupe_only"`, `blocking_rules_to_generate_
  predictions=[block_on("name"), block_on("email"), block_on("ref")]`, comparisons
  from `splink.comparison_library` (`NameComparison`, `EmailComparison`,
  `ExactMatch(...).configure(term_frequency_adjustments=True)`).
- Train: `estimate_probability_two_random_records_match`,
  `estimate_u_using_random_sampling`, `estimate_parameters_using_expectation_
  maximisation`. This is Splink's advantage over our 8-iteration no-convergence EM.
- `predict()` → pairwise `match_probability`. Map each pair to our record-id
  strings, apply invariants 1+3, then call the existing `_build_clusters(...)`.
- Wrap the whole thing in try/except: **any Splink failure logs a warning and
  falls back to the builtin engine** (never crash a run over an optional engine).

### Config surface (also fixes a dead feature)
The `matching_rules` config (`{entity: {blocking_keys, unique_id_fields,
strictness, engine}}`) exists in state (`graph.py:468`) and profiles
(`profiles.py:47`, `PINNED_KEYS`) but **no UI/API writes it today** — every run
uses defaults. Add a minimal authoring path so `engine: "splink"` is reachable:
- **Backend:** extend the profile create/read endpoints
  (`src/api/main.py`, routes `POST/GET /api/profiles`) to accept/return a
  `matching_rules` object. It already round-trips through `profiles.py`.
- **Frontend (small):** on `PipelineScreen.tsx`, add an optional "Advanced
  matching" disclosure with a per-entity engine dropdown (`Built-in` |
  `Splink`). Default `Built-in`. Wire into `startWorkflow` body →
  `WorkflowStart` model → `initial_state["matching_rules"]`.
- Also fix the false comment at `resolve_entities.py:48` (claims
  `extraction_config["max_comparisons"]` is read — nothing reads it) while here.

### Dependency
Add `splink>=4.0.0` to `requirements.txt` and `pyproject.toml`. Confirm it
imports under `na180` (`python -c "import splink; print(splink.__version__)"`).
If it drags an incompatible duckdb pin, gate the import lazily inside
`splink_matcher.py` so the rest of the app is unaffected.

### Tests (Q.1)
- `tests/unit/test_splink_matcher.py` (new): result-dict shape parity; cluster-id
  reuse via `known_cluster_ids`; identity-conflict veto still holds; graceful
  fallback when `import splink` raises (monkeypatch).
- **Extend the eval harness** `tests/eval/score.py`: add a 7th scenario that runs
  `evaluate(splink_scorer, pairs)` over the SAME 560 golden pairs
  (`tests/eval/golden.py`) and prints precision/recall/F1 beside the builtin. The
  acceptance bar: **Splink precision ≥ builtin's (1.0000) and recall ≥ 0.99** on
  this set. This is the before/after number that gates the phase.
- Keep all existing Phase-L tests green (they test the builtin engine, which is
  unchanged and still default).

---

## Q.2 — Great Expectations data-quality validation gate

### Goal
After extraction, before resolution, validate each entity's extracted rows and
surface the results in the reconciliation report (and optionally pause on hard
failures). Today there is **no data validation anywhere** in the pipeline.

### Where it plugs in
New node `validate_data` inserted in `src/orchestrator/graph.py` **between
`extract_data` and `resolve_entities`** (current order at `graph.py:320-321`).
Rewire the `extract_data → resolve_entities` edge to
`extract_data → validate_data → resolve_entities`. Add
`builder.add_node("validate_data", validate_data, retry_policy=NETWORK_RETRY)`.

### Files
- `src/orchestrator/nodes/validate_data.py` (new node, house style).
- `src/mcp_server/tools/data_quality.py` (new — the GX logic, so the node stays
  thin, mirroring how nodes delegate to `tools/`).
- `src/orchestrator/nodes/__init__.py` — export `validate_data`.

### Logic (`data_quality.py`)
- Read each entity's rows from the spool (`spool.read_all(entity)` /
  `entity_rows(state, entity)` — same accessor the matcher uses,
  `nodes/resolve_entities.py:74`). Load into a pandas DataFrame (flatten the JSON
  payloads — the rows are already dicts).
- Build a GX in-memory validation (GX 1.x fluent API: `gx.get_context()` →
  `pandas` data source → a `Validator` / Expectation Suite). Expectations to run
  per entity (deterministic, no config needed for v1):
  - every configured `unique_id_fields` value: `expect_column_values_to_be_unique`.
  - the detected name/title column: `expect_column_values_to_not_be_null`
    (warn-level, not fail — real data has gaps).
  - any `*_year`/`*_date` column: `expect_column_values_to_be_between` (sane
    bounds, e.g. year 1800–2100) — warn-level.
  - row count: `expect_table_row_count_to_be_between(min=1, …)` — a hard fail if
    an entity extracted 0 rows (this would ALSO have caught the P.0 watermark bug
    at the right place).
- Return a per-entity `quality_report`:
  `{entity: {checks_run, checks_passed, checks_failed, failures: [{expectation,
  column, observed, severity}]}}`. `severity` ∈ `{"warn","fail"}`.

### State + report wiring
- Add `quality_report: dict` to `MergeState` (`graph.py` TypedDict) and init to
  `{}` in both `get_initial_state()` branches.
- The node returns `{"quality_report": …}`.
- Extend `build_reconciliation_report` (`load_data.py:34`) to include
  `state.get("quality_report")` under a new top-level `"data_quality"` key, so the
  final report and the run-report JSON carry it.
- Surface a one-line summary in the event log and (optional, small) a panel on the
  final-approval gate in `Gates.tsx` ("Data quality: 42/44 checks passed, 2
  warnings").

### Behaviour on failure
- `severity: "warn"` → record only, never block.
- `severity: "fail"` (e.g. an entity with 0 rows, or a declared-unique column with
  duplicates) → set a flag the final-approval gate shows prominently, but **do NOT
  auto-abort** — the human decides at the existing `human_final_approval` gate.
  This respects the project's "fail loudly, human decides" posture (Phase M) and
  avoids a new blocking gate.
- Note: the pipeline already has an escalation channel — `DataQualityError`
  (`utils/retry.py:71`, `disposition="escalate"`) routes to the `handle_error`
  HITL gate (`graph.py:329`). A future stricter mode *could* raise
  `DataQualityError` from `validate_data` to force escalation, but for v1 keep it
  non-blocking (report + final-gate flag) to avoid surprising existing runs.
- Pre-run structural checks already live in `nodes/preflight.py` (reachability,
  writability, LLM liveness) — `validate_data` is specifically for *row-content*
  quality after extraction, a different concern; do not duplicate preflight.
- Wrap GX in try/except: a GX failure degrades to "validation unavailable" in the
  report, never crashes the run (same posture as dest-dedup in Phase P).

### Dependency
Add `great-expectations>=1.0.0` to `requirements.txt` + `pyproject.toml`. GX 1.x
pulls a fair few transitive deps — confirm under `na180` and confirm it does not
conflict with our pandas/duckdb pins. If it's too heavy, the fallback is
`pandera` (lightweight schema/validation) — note this as the escape hatch, but
try GX first per the user's choice.

### Tests (Q.2)
- `tests/unit/test_data_quality.py` (new): unique-violation detected; 0-row entity
  flagged `fail`; clean data → all pass; GX-import-failure path degrades
  gracefully.
- `tests/unit/test_validate_data_node.py` (new): node reads spool, writes
  `quality_report`, is a no-op passthrough when the entity list is empty.
- Extend an existing graph-wiring test to assert the new node sits between extract
  and resolve and that `_derive_skipped_gates`/routers are unaffected (this node
  is NOT a HITL gate — no interrupt, so gate-skip logic must not change).

---

## Q.3 — Column-level lineage in the reconciliation report

### Goal
For each destination table.column, record which origin table.column(s) fed it —
so the report answers "where did `companies.name` come from?" Today the report has
per-entity counts but no field-level provenance.

### Data we already have (no new extraction needed)
- `field_mappings[entity]` = `{source_field: {target_field/column,
  requires_schema_change, …}}` — the origin→dest field map, produced by
  `map_fields` (`nodes/map_fields_node.py:188`, structure confirmed).
- Per-record origin provenance in the spool: every row carries `_origin_id`,
  `_origin_table`, `_origin_id_row` (`spool.py` record_id; `conftest.py` triple).
- Which origins contributed to each unified entity:
  `entity_origin_counts(state, entity)` (`load_data.py:54`).
- **`_conflicts` already carries per-field, per-origin attribution** —
  `unify_entity_group.py:236` records, for every conflicted field,
  `{field, values:[{origin, value}], selected_origin, selected_value,
  winning_rule}`. This is *exactly* column-level lineage for conflicted fields,
  but it is currently **discarded at load** (`_transform_records`,
  `tools/load_data.py:208` only keeps it if a value happens to be a dict, and
  `_conflicts` is `_`-prefixed so never mapped). Q.3 should preserve this into the
  lineage output — it's the highest-value provenance already computed and thrown
  away.
- **Known gap to note (don't fully fix here):** the destination row's
  `_source_ids` currently only carries `[{"cluster_id": …}]`
  (`tools/load_data.py:242`), NOT the actual origin row ids, and the durable
  `merge_engine_entity_map` (origin-row → dest-row) exists **only on a PostgreSQL
  destination** (`postgresql_destination.py:766`; ClickHouse has no
  `save_entity_map`). Record this limitation in the report/lineage docs.

### Where it plugs in
Two complementary pieces:
1. **Field-level lineage from `field_mappings`** (the primary deliverable). New
   helper `build_lineage(state)` in a new module
   `src/orchestrator/utils/lineage.py`, called from `build_reconciliation_report`
   (`load_data.py`). Output shape:
   ```python
   "lineage": {
     "&lt;entity&gt;": {
       "&lt;dest_column&gt;": {
         "sources": [{"origin_id": str, "origin_table": str,
                      "source_field": str}],
         "created_column": bool          # from requires_schema_change
       }, ...
     }, ...
   }
   ```
   Populate `sources` by joining `field_mappings[entity]` (source_field →
   dest_column) with, for each source_field, the set of `(origin_id,
   origin_table)` that actually carried that field — derived by sampling the
   spool rows per entity (`spool.read_all`, look at which origins have that key).
2. **SQL-expression lineage via `sqlglot`** (optional enrichment): where the load
   builds INSERT/UPSERT SQL (`tools/load_data.py`), pass the generated statement
   through `sqlglot.lineage.lineage(column, sql)` to confirm/annotate the
   column-level mapping. Use this to *validate* piece (1), not replace it. If
   `sqlglot` lineage is awkward over our parameterised SQL, skip this enrichment
   and ship piece (1) alone — piece (1) is the user-visible win.

### Report + UI wiring
- Add `"lineage"` to the report dict returned by `build_reconciliation_report`
  and written to `logs/run_reports/<run_id>.json`.
- Small UI: a collapsible "Lineage" section on the final summary in `Gates.tsx`
  rendering, per entity, a `dest_column ← origin_table.source_field` list.

### Dependency
Add `sqlglot>=25.0.0` to `requirements.txt` + `pyproject.toml` (zero-dependency,
safe). `sqllineage` is NOT required (heavier, sqlfluff-based) — only reach for it
if `sqlglot.lineage` proves insufficient; note as fallback.

### Tests (Q.3)
- `tests/unit/test_lineage.py` (new): given a `field_mappings` + spooled rows
  fixture, `build_lineage` maps each dest column to the right origin sources;
  a created column is flagged; a field present in two origins lists both sources.
- Extend `tests/unit/test_*reconciliation*`/`test_load_data*` to assert the report
  now carries a non-empty `"lineage"` key for a multi-origin entity.

---

## Q.7 (completes the deferred P.7) — Real ClickHouse sample databases setup (`origin1` & `origin2`) — [x] COMPLETED (2026-07-28)

### Goal
Stand up and verify the user's real ClickHouse databases located in `sample_databases/`:
1. `origin1`: complete ClickHouse database dump located in `sample_databases/origin1/origin1_2026-07-26_095508`. Created database `origin1` and its 13 tables (`Company`, `Person`, `CompanyContact`, `CompanyExperience`, `Location`, `CompanyIndustry`, etc.).
2. `origin2`: sample ClickHouse SQL dumps located in `sample_databases/origin2_sample/` (`Company_modified.sql`, `Person_modified.sql`, `Location_modified.sql`, `Experience_modified.sql`, `CompanyIndustry_modified.sql`, `PersonContact_modified.sql`, `Industry_modified.sql`). Populates database `origin2`.

### Step-by-Step Instructions & Verified Live Execution:

1. **Docker Compose Setup (`sample_databases/docker-compose.yml`):**
   - Created `sample_databases/docker-compose.yml` with container service `Sample_Database` using `clickhouse/clickhouse-server:latest`.
   - Ports mapped: `8190:8123` (HTTP) and `9005:9000` (Native) with credentials `CLICKHOUSE_USER=default`, `CLICKHOUSE_PASSWORD=pass`.
   - Created CLI importer `scripts/import_sample_databases.py` supporting `--fresh`, `--resume`, `--database`, `--table`, `tqdm` progress tracking, JSON state tracking (`.import_progress.json`), and word-boundary single-quote escaping (`fix_quotes`).

2. **Spin Up & Verification Results:**
   - Container `Sample_Database` is healthy and responsive (`ping = True`).
   - Database `origin1` (13 tables, **50,841 rows** total):
     - `Company`: 2,941 rows, `CompanyContact`: 5,331 rows, `CompanyEmployeeCategory`: 5,135 rows, `CompanyExperience`: 1,231 rows, `CompanyFinancials`: 199 rows, `CompanyIndustry`: 3,941 rows, `CompanyInvestmentRound`: 471 rows, `CompanyInvestor`: 1,261 rows, `CompanyRelation`: 6,175 rows, `CompanyTechnographics`: 20,093 rows, `IndustryClassification`: 340 rows, `Location`: 3,507 rows, `Person`: 1,216 rows.
   - Database `origin2` (7 tables, **35,212,969 rows** total):
     - `Industry`: 30 rows, `Company`: 1,270,564 rows, `Location`: 1,861,119 rows, `CompanyIndustry`: 3,696,483 rows, `PersonContact`: 4,339,990 rows, `Person`: 11,799,200 rows, `Experience`: 12,245,583 rows.
   - **Total:** **35,263,810 rows** across **20 tables** in ClickHouse with 0 errors!

---

## Q.8 — PostgreSQL destination from the Global Warehouse schema — [x] COMPLETED (2026-07-29)

### Goal
Origins are now real (`origin1`/`origin2` in ClickHouse, Q.7). The destination
side of every subsequent demo/verification still points at whatever ad hoc
`main_dest` earlier phases seeded. Stand up the destination schema the user
actually supplied — `sample_databases/destination/ER_DIAGRAM.md` and
`ER_DIAGRAM.html` ("Global Warehouse", 25 tables) — as a real **PostgreSQL**
container, in the **same** `sample_databases/docker-compose.yml` used for the
ClickHouse origins, on a **non-native port** (host Postgres already occupies
5432 — confirmed listening during this session; do not collide with it or with
any earlier ephemeral 5433/5434 seed containers from prior phases).

### Critical fact the implementing agent MUST know before starting
**The `01_company.sql` … `07_technographic.sql` files the ER_DIAGRAM's own
"Table Summary" table names do not exist anywhere in the repo.** Confirmed by a
full `find` over `sample_databases/`: only `ER_DIAGRAM.md` and `ER_DIAGRAM.html`
are present in `destination/` — both are diagram/doc renders (verified the
`.html` has zero `CREATE TABLE` statements and no extra type precision beyond
the `.md`). **The DDL must be authored from scratch**, using the Mermaid ERD in
`ER_DIAGRAM.md` as the sole source of truth for tables, columns, types, and
relationships. Do not go looking for source SQL files that were supposedly
already written — there are none.

**Explicit user instruction (2026-07-29): schema only, NO mock data.** The
user found this schema online and does not want it seeded with any
mock/generated rows. Do **not** author an `08_mock_data.sql` file and do not
insert any rows of any kind — the destination must come up as a completely
empty schema: tables, columns, and constraints only.

### The 25 tables (from the ERD, for the agent's own bookkeeping)
`industry, location, company, company_relation, person, skill, education,
experience, person_skill, contact_email, contact_phone, contact_social,
product_category, product, contract, contract_entity, contract_product,
contract_amendment, contract_milestone, contract_period, finance,
finance_currency_rate, finance_budget, technologic_category, technologic`.
Grouped by the doc's own file plan (author these 8 files even though nothing
before now existed): `01_company.sql` (industry, location, company,
company_relation), `02_person.sql` (person, skill, education, experience,
person_skill), `03_contact.sql` (contact_email/phone/social),
`04_products.sql` (product_category, product), `05_contracts.sql` (contract +
4 contract_* tables), `06_finance.sql` (finance, finance_currency_rate,
finance_budget), `07_technographic.sql` (technologic_category, technologic),
`08_mock_data.sql` (deterministic seed rows for all 25 tables, ~1,150 total per
the doc's own estimate).

### Type translation (ERD type → PostgreSQL type)
`BIGINT id PK` → `BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY` (or
`BIGSERIAL PRIMARY KEY`, matching the house style already used elsewhere for
Postgres DDL, e.g. `_pg_ddl_type()` in `postgresql_destination.py`); `string` →
`TEXT`; `boolean` → `BOOLEAN`; `timestamptz` → `TIMESTAMPTZ`; `date` → `DATE`;
`double` → `DOUBLE PRECISION`; `int` → `INTEGER`. FK columns (`_id FK` in the
diagram) become real `REFERENCES` constraints per the Mermaid relation list at
the top of `ER_DIAGRAM.md` (e.g. `company.industry_id → industry.id`,
`experience.person_id → person.id`, `experience.company_id → company.id`).
`company_relation` is the one self-referencing many-to-many table
(`company_a_id`/`company_b_id` both → `company.id`) — needs two named FK
constraints, not one.

### Docker Compose change
Add a second service to the existing `sample_databases/docker-compose.yml`
(alongside `Sample_Database`, the ClickHouse container) — do not create a
separate compose file, so `docker compose -f sample_databases/docker-compose.yml
up -d` still brings up the whole sample-data stack in one command:
```yaml
  postgres_destination:
    image: postgres:17
    container_name: Sample_Database_Destination
    restart: always
    ports:
      - "55432:5432"          # non-native — confirm free first (see below)
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=pass
      - POSTGRES_DB=global_warehouse
    volumes:
      - ./destination/sql:/docker-entrypoint-initdb.d:ro   # auto-runs 01.._08 on first boot
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10
```
- **Port availability check first**: `netstat -ano | findstr "55432"` — this
  session confirmed `5432` is occupied by a native Windows Postgres service and
  `55432` was free; re-verify at implementation time in case anything changed.
  If `55432` is somehow taken, pick another unused high port and update this
  section + `README.md`/`User_Guide.md` accordingly — the port is a placeholder
  choice, not a hard requirement.
- Postgres's official image auto-runs every `.sql`/`.sh` file in
  `/docker-entrypoint-initdb.d` **in filename sort order** on first boot only
  (an existing data volume skips re-init) — this is why the `01_`…`08_` numeric
  prefixes matter and must be preserved.

### What to build
1. Author `sample_databases/destination/sql/01_company.sql` through
   `07_technographic.sql` — real `CREATE TABLE` DDL for all 25 tables, FKs
   included, matching the ERD exactly (do not add columns not in the diagram;
   do not drop any). **Do NOT author `08_mock_data.sql` or any file that
   inserts rows — schema-only, per explicit user instruction.**
2. Add the `postgres_destination` service to
   `sample_databases/docker-compose.yml` as above.
3. Register the new destination in `config/recent_connections.json` (or note
   it's reachable via the Setup tab's manual entry) so it's one click away in
   the demo: `{"type":"postgresql","host":"localhost","port":55432,
   "user":"postgres","password":"pass","database":"global_warehouse"}`.
4. Update `README.md`/`User_Guide.md`'s connection-details section with the new
   container's host/port/credentials, alongside the existing ClickHouse entry.

### Tests / Verification
- No new Python unit tests are required (this is infrastructure/data, not
  application code) — verification is entirely live, per the plan's
  IMPORTANT COMMAND:
  - `docker compose -f sample_databases/docker-compose.yml up -d`, wait for the
    new container's healthcheck to pass.
  - Directly query via `psql` (or `docker exec Sample_Database_Destination psql
    -U postgres -d global_warehouse -c "\dt"`): confirm all 25 tables exist.
  - `SELECT count(*)` per table; confirm **zero rows in every table** (schema-only,
    no mock data) and that all FK constraints are correctly declared (e.g.
    `\d+ experience` shows the FK to `company.id` and `person.id`).
  - Confirm the app's own `test_and_discover_schemas` (Setup tab / API) reaches
    this destination, lists all 25 tables with correct columns, and that the
    existing single-destination lock still behaves correctly when this is
    chosen as the one configured destination.
  - Do NOT run a merge into it yet as part of Q.8 itself — that's exercised
    naturally once Q.2/Q.3/Q.1 (or any future full pipeline run) uses it as the
    live destination.

---

## Critical files (by phase)

- **Q.1 Splink:** `src/mcp_server/tools/resolve_entities.py` (seam at :158, reuse
  `_assign_cluster_id`:1182, `_build_clusters`:1210, guard helpers); NEW
  `src/mcp_server/tools/splink_matcher.py`; `tests/eval/score.py` (+scenario 7);
  `src/api/main.py` + `PipelineScreen.tsx` (matching_rules authoring);
  `requirements.txt`/`pyproject.toml`.
- **Q.2 GX:** NEW `src/orchestrator/nodes/validate_data.py`,
  `src/mcp_server/tools/data_quality.py`; `src/orchestrator/graph.py` (node +
  edge rewire at :320); `src/orchestrator/nodes/load_data.py:34` (report);
  `Gates.tsx` (summary panel).
- **Q.3 Lineage:** NEW `src/orchestrator/utils/lineage.py`;
  `src/orchestrator/nodes/load_data.py:34` (report); `Gates.tsx` (lineage panel).
- **Q.7 Seed (COMPLETE):** `sample_databases/origin1/`,
  `sample_databases/origin2_sample/`, `sample_databases/docker-compose.yml`,
  `scripts/import_sample_databases.py`.
- **Q.8 Destination (NEW):** `sample_databases/destination/ER_DIAGRAM.md`/`.html`
  (schema source of truth — DDL/mock-data files do NOT exist yet, must be
  authored); NEW `sample_databases/destination/sql/01_company.sql` …
  `08_mock_data.sql`; `sample_databases/docker-compose.yml` (add
  `postgres_destination` service); `config/recent_connections.json`;
  `README.md`/`User_Guide.md`.
- **Docs (all phases):** `docs/PHASE_Q_PLAN.md` (step 0), `TODO.md` (Phase Q +
  the missing P.7 section), `README.md` (Status bullet), and a new memory file
  `merge-engine-phase-q.md` + `MEMORY.md` pointer at the end.

---

## Verification (live + independent — never trust a script's own report)

Run each phase's tests green before moving on; then the end-to-end live proof:

1. **Env:** `conda activate na180`. Origins already live: `Sample_Database`
   (ClickHouse, ports 8190/9005, `origin1`+`origin2` — Q.7, verified 2026-07-28).
   Confirm the new `postgres_destination` port (`55432`, or whatever was
   actually chosen) is free before first bring-up:
   `netstat -ano | findstr "55432"`.
2. **Destination (Q.8):** `docker compose -f sample_databases/docker-compose.yml
   up -d`; **directly query** `global_warehouse` via `psql`/`docker exec` and
   record ground-truth table/row counts for all 25 tables — the baseline the
   app's own schema-discovery gets checked against.
3. **Data quality (Q.2):** run a merge; open
   `logs/run_reports/<run_id>.json`; assert `"data_quality"` is present, all
   checks ran, and (deliberately) seed one duplicate unique-id + one 0-row entity
   to prove `fail`-severity surfaces at the final gate.
4. **Splink (Q.1):** run the same merge once with `engine: builtin` and once
   with `engine: splink`; compare the reconciliation reports — cluster counts
   and inserted/updated should match within tolerance, and **`python -m
   tests.eval.score` scenario 7 must show Splink precision ≥ 1.0000, recall ≥
   0.99** on the golden pairs. Record both numbers.
5. **Lineage (Q.3):** in the same report, assert `"lineage"` correctly traces at
   least one multi-origin destination column back to both `origin1` and
   `origin2` source fields, and a created column is flagged.
6. **Full unit suite green:** `cd /d/transfer/merging && "$PY" -m pytest
   tests/unit/ -q` (baseline recorded after P.7: confirm current count before
   starting Q.8+; expect +N new tests, 0 failures). Rebuild the frontend
   (`cd src/ui/frontend && npm run build`, `npx tsc --noEmit` clean).

## Honest scope note
This is now five substantial, independent work-streams; Q.7 is complete. Implement
the rest in the priority order above (Q.8 → Q.2 → Q.3 → Q.1), each stopping for
user go-ahead. Splink is the only one with real integration risk (the 8
invariants); it is default-off so it cannot regress existing runs, and if its
eval numbers don't beat the builtin it ships as a documented option and stays
off — an acceptable outcome, not a failure. Q.8's DDL/mock-data authoring is new
work with no prior source file to crib from — it must be built strictly from the
`ER_DIAGRAM.md`/`.html` Mermaid diagram, nothing else.
