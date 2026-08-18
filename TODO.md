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
- [ ] **P-2** Connector capability matrix (`capabilities()`, `discover_indexes()`,
  `discover_statistics()`, `profile_column()`, `estimate_query_cost()` on the existing connector
  base).
- [ ] **P-3** Profiling engine (FULL/SAMPLED/APPROXIMATE, database-native per connector).
- [ ] **P-4** Semantic type inference (evidence-carrying, no LLM in the first pass).
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
