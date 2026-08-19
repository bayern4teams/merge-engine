# merge-engine — Branch `feat/v3-discovery-er` Implementation TODO

Tracks Part B (V3 discovery/entity-resolution engine) up to BP-B3, per `Branch_Plan.md` at this
worktree's root. Scope override in effect: continue straight through BP-B1 and BP-B2 without
stopping or committing to `main`; stop at BP-B3 for live verification and human review.

---

## B.0 — Branch and worktree

- [x] **B.0** Branch `feat/v3-discovery-er` created off `main` (post-v2-tag), worktree at
  `.worktrees/v3-discovery-er`. `Branch_Plan.md` copied in verbatim (B.0 through end of B.3,
  with a scope-override header). This `TODO.md` created fresh (the inherited one was
  `fix-bugs`'s, not V3's).
  - *Evidence:* `git worktree list` shows `feat/v3-discovery-er` at `.worktrees/v3-discovery-er`;
    `git log --oneline -1` on the new branch matches `main`'s tip (`95c3295`).

## B.1 — Tool decisions written up (`docs/tool-evaluation.md` + ADRs)

- [x] **B.1.1** `docs/tool-evaluation.md` — full capability matrix (adopted tools' verified API
  surface + rejected tools' reasons), drawn from `Branch_Plan.md`'s B.1 section, not re-derived.
  - *Evidence:* `scripts/lint_docs.py` passes (every link/citation resolves);
    `scripts/check.py lint format types arch docs` — 5/5 passed.
- [x] **B.1.2** 11 ADRs in `docs/decisions/0004`..`0014` (repo's real convention, not the plan's
  guessed `docs/adr/` path — `docs/INDEX.md` already indexes `decisions/`). One per tool
  decision: adopt Splink (0004, gated on BP-B4)/Valentine (0005)/Desbordante (0006, AGPL
  isolated)/datasketch (0007)/Great Expectations (0008)/networkx+sqlglot+Jailer (0009); reject
  OpenMetadata (0010)/DataHub (0011, model adopted)/Neo4j (0012); Informatica benchmark-only
  (0013); replace budget-truncation with blocking analysis (0014).
  - *Evidence:* `docs/INDEX.md` lists all 11; `git log --oneline -1` shows commit `d9a29de`.

> BP-B1 (plan's own text: "stop for review") — **not a pause point on this branch** per the
> scope override above. Noted as reached, then continuing straight into B.2/B.3.

## B.2 — How V3 relates to the existing pipeline

- [ ] **B.2** No code — this is the reuse/new/hybrid/replace decision table already written in
  `Branch_Plan.md`. Referenced by each P-phase below as it lands; no separate TODO item beyond
  confirming each phase actually follows its assigned decision (reuse/replace/hybrid).

## B.3 — Pre-Splink phases (P-1 .. P-10)

Each phase: implement -> unit tests -> live check against the real ClickHouse/PostgreSQL boxes
(`Sample_Database` / `Sample_Database_Destination` Docker containers, per `merge-engine-real-world-test-dbs`
memory) -> update this file with independent evidence, never the app's own success message.

- [x] **P-1** Domain model & metadata store — `src/mcp_server/tools/metadata_graph.py`
  (`MetadataStore` class) + PostgreSQL DDL for all 13 tables (md_run, md_source, md_database,
  md_schema, md_table, md_column, md_profile, md_candidate_key, md_relationship,
  md_canonical_field, md_field_mapping, md_edge, md_override). Stable generated ids everywhere
  (never keyed on name alone); origin/review_status validated in Python before any write;
  overrides are additive-only (a new `md_override` row, never a mutation of the target row).
  Known gap, not yet done: no dedicated config/UI wiring for the metadata store's own
  connection — it currently takes an explicit connection dict, same shape every other connector
  takes, rather than a `config.yaml` section. Tracked as a P-1 follow-up, not silently dropped.
  - *Evidence (unit):* `tests/unit/test_metadata_graph.py`, 24/24 passed. Mutation-tested the
    additive-override guarantee: temporarily changed `record_override`'s SQL from `INSERT` to
    `UPDATE`, confirmed `test_record_override_only_inserts_into_md_override` fails red, restored,
    confirmed green again.
  - *Evidence (live, independent SQL — never the module's own return values):* ran
    `ensure_schema()` + a full write path (`create_run` → `upsert_source/database/schema/table/
    column` → `insert_candidate_key` → `record_override` → `get_overrides`) against the real
    `Sample_Database_Destination` container (`global_warehouse`). `psql \dt md_*` shows all 13
    tables. `SELECT ... FROM md_source JOIN md_database JOIN md_schema JOIN md_table JOIN
    md_column` returns the full chain correctly (`origin2` → `Company` → `companyID`).
    `SELECT * FROM md_candidate_key` shows `confidence=1.0` UNCHANGED after the override;
    `SELECT * FROM md_override` shows the override recorded as its own separate row with both
    `original_decision` and `human_decision` — confirms additive-not-mutating end-to-end, not
    just in mocks.
  - *Evidence (static):* `scripts/check.py all` — 10/10 passed (mypy baseline regenerated via
    `scripts/update_mypy_baseline.py` after reviewing the diff: the prior baseline was stale,
    last regenerated 2026-08-05 on `main` before all the work this branch's `main` merge
    brought in; the only *new* entries are this file's 3 already-accepted-pattern
    `psycopg2` stub-missing lines, identical to `postgresql_destination.py`'s own baselined
    entries).
- [x] **P-2** Connector capability matrix. `BaseConnector.capabilities()` (conservative default)
  + `discover_indexes()`/`discover_statistics()`/`profile_column()`/`estimate_query_cost()`
  (base defaults raise `NotImplementedError`, matching the existing `sample_distinct_values`
  pattern). Real overrides for `PostgresConnector` and `ClickHouseConnector`; `MongoDBConnector`
  gets `capabilities()` only (no live Mongo container available to verify the other four
  against — tracked as a follow-up, not silently skipped).
  - *Evidence (unit):* `tests/unit/test_connector_capabilities.py`, 13/13 passed — includes the
    load-bearing case the module docstring calls out (`supports_declared_fk: False` for
    ClickHouse/MongoDB) and a regression test for a bug caught live (below).
  - *Evidence (live, independent — real ClickHouse `origin2` + real PostgreSQL
    `global_warehouse`):* `capabilities()`, `discover_indexes()`, `discover_statistics()`,
    `profile_column()` (all 3 modes), `estimate_query_cost()` all run against real data and
    return real, checkable numbers (e.g. ClickHouse `Company`: 1,270,564 rows, 9 parts, sorting
    key `companyID`; PostgreSQL `company`: real `company_pkey` btree index found).
  - **Real bug found and fixed during live verification:** PostgreSQL's `pg_class.reltuples`
    is `-1` for a never-analyzed table — a genuine 2-row table reported `row_count_estimate: -1`,
    not 0 or 2. Fixed with a fallback to `pg_stat_user_tables.n_live_tup` (tracked by autovacuum
    independently of `ANALYZE`). Verified live across all three real states: never-analyzed
    (-1 -> falls back to n_live_tup, itself still 0 immediately after a fresh write since
    autovacuum's stats collector lags), and post-`ANALYZE` (correctly reports the real count,
    2, with `last_analyzed` populated) — confirmed by running `ANALYZE company;` on the real
    container and re-querying.
- [x] **P-3** Profiling engine — `src/mcp_server/tools/profiling.py`. Scale-sensitive metrics
  (null/distinct counts) via P-2's `connector.profile_column()` (database-native); per-value
  shape metrics (length distribution, top values by frequency, pattern distribution, type
  distribution) computed in Python over a bounded raw sample pulled via `connector.extract()`
  (deliberate: `sample_distinct_values` already de-duplicates, which breaks frequency counting
  for top_values). `profile_and_store()` glues this to P-1's `MetadataStore.insert_profile`
  (added — P-1 had the DDL but no insert helper yet) / `get_latest_profile`. `mode` always
  recorded (PreSplink §46).
  - *Evidence (unit):* `tests/unit/test_profiling.py`, 18/18 passed.
  - *Evidence (live, independent — real ClickHouse `origin2.Company` 2000-row sample, real
    PostgreSQL `global_warehouse.company` full scan):* ClickHouse `name` column profiled
    correctly (1998/2000 distinct, realistic length/pattern buckets, e.g. `{'alpha': 1733,
    'other': 261, 'alphanumeric': 6}`). Full store round-trip against the real destination:
    registered real source/database/schema/table/column rows, called `profile_and_store`,
    then independently read the row back via `get_latest_profile` — matches what was computed
    (`top_values: [{'value': 'Lockheed Martin', 'count': 1}, ...]`).
- [x] **P-4** Semantic type inference — `src/mcp_server/tools/semantic_type_inference.py`.
  Heuristic pass (no LLM) uses column NAME (normalized across camelCase/PascalCase/
  snake_case) plus P-3's measured `pattern_distribution` to corroborate or contradict the
  name's implied type; 22-type taxonomy (`SEMANTIC_TYPES`). LLM fallback only when the
  heuristic confidence is below 0.6, tagged `LLM_ASSISTED`, via a new
  `semantic_type_inference` prompt pair — new `md_semantic_type` table added to P-1's store
  (one row per inference, not a mutable label, so a later re-classification is a visible new
  row).
  - *Evidence (unit):* `tests/unit/test_semantic_type_inference.py`, 22/22 passed.
  - *Real bug found and fixed during live verification:* a bare `name` column measured as
    ~98% alpha-shaped text was boosted to 0.93 confidence for `PERSON_NAME` — but
    `PERSON_NAME` and `ORGANIZATION_NAME` both corroborate on the identical `alpha` pattern
    bucket, so that measurement cannot actually discriminate between them; the confidence
    boost was overclaiming. Fixed by identifying pattern buckets more than one semantic type
    shares (`_AMBIGUOUS_PATTERNS`) and suppressing the corroboration boost for those — the
    ambiguous pattern can still rule a name match OUT (near-zero corroboration) but never rule
    one IN over an equally-plausible sibling. Two new regression tests cover both directions.
  - *Evidence (live, independent — real ClickHouse `origin2.Company`):* `name` → `PERSON_NAME`
    at 0.75 (post-fix, no longer overclaiming); `companyID`/`domain` → `UNKNOWN` (correctly no
    strong signal); `website` → `URL` at 0.75. Full store round-trip against the real
    PostgreSQL `global_warehouse`: `store.ensure_schema()` (picked up the new
    `md_semantic_type` table), `insert_semantic_type`, then independently confirmed via
    `psql`: `SELECT semantic_type, confidence, origin FROM md_semantic_type` returned
    `PERSON_NAME | 0.75 | ALGORITHMIC`, matching what was computed.
- [ ] **P-5** Candidate & composite keys (declared PK/unique first, then inferred, then pruned
  composite search, optional Desbordante UCC pass).
- [ ] **P-6** Relationship discovery & scoring (extends `join_graph.py`, ten signals, no opaque
  score).

> **BP-B2** (plan's own text: "🚩 Commit to main if clean") — **do not commit to main** per the
> scope override above. Live-check both ClickHouse origins per the plan's own definition, note
> the result here, and continue straight into P-7.

- [ ] **P-7** Schema matching & canonical schema (Valentine first, deterministic pass merges in,
  LLM sees only what remains ambiguous).
- [ ] **P-8** Metadata graph + graph UI (networkx over P-1 tables, metadata graph vs entity
  graph kept strictly separate).
- [ ] **P-9** Entity anchors, subgraph, coverage, extraction (never auto-merges on name
  equality; traversal policy with depth/node/edge/record caps; preview before any expensive
  run).
- [ ] **P-10** Canonical matching dataset (the Splink handoff artefact, materialised in DuckDB,
  registered directly as a Splink input).

> **BP-B3** — the handoff contract is provable: for a given Airbus run, every canonical record
> answers "where did this come from?" by SQL alone. Live-verify, report back, **stop here** —
> do not proceed into B.4 (Splink phases) or touch `main` without explicit go-ahead.
