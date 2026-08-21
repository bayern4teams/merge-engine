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
- [x] **P-5** Candidate & composite keys — `src/mcp_server/tools/candidate_keys.py`. Three
  tiers: declared (relabels P-2's `discover_indexes()`, `AUTO_ACCEPTED`), single-column
  inference (from P-3's `unique_ratio`/`null_ratio`, `NEEDS_REVIEW`), pruned composite search
  (only identifier-like low-null columns, 2-column pairs only, each pair verified against a
  real bounded query before being reported — never stored on a guess). Never labels an
  inferred candidate a primary key. Desbordante UCC pass **not done** — tracked as an explicit
  follow-up, not silently dropped (the pruned SQL-verified path is the primary mechanism per
  `Branch_Plan.md`'s own P-5 text; Desbordante was always the *optional* accelerator).
  - *Evidence (unit):* `tests/unit/test_candidate_keys.py`, 22/22 passed.
  - *Evidence (live, independent SQL):* real ClickHouse `origin2.Company` → `companyID`
    correctly found as `DECLARED_PRIMARY_KEY` (via its ORDER BY key); real PostgreSQL
    `global_warehouse.company` → `id` correctly found as `DECLARED_PRIMARY_KEY` (via
    `pg_index`). Full store round-trip: `psql SELECT columns, key_type, confidence,
    review_status FROM md_candidate_key` independently confirms `["id"] |
    DECLARED_PRIMARY_KEY | 1 | AUTO_ACCEPTED`.
- [x] **P-6** Relationship discovery & scoring — `src/mcp_server/tools/relationship_scoring.py`.
  All ten PreSplink §21 signals implemented independently and kept visible (§22 "no opaque
  score"): `datatype`, `semantic_type`, `name_similarity`, `value_overlap`,
  `inclusion_coverage` (exact, database-verified via targeted `filter_values` query — mirrors
  `join_graph.py`'s `_verify_inclusion`), `target_uniqueness`, `cardinality`, `null_behaviour`,
  `table_semantics`, `identifier_likelihood`. `score_relationship()` aggregates by averaging
  only the non-`None` scores (a sampling failure on one signal never drags the average toward
  zero) and classifies into `FOREIGN_KEY`/`SHARED_LOOKUP`/`POSSIBLE_JOIN`/`UNRELATED`.
  - *Evidence (unit):* `tests/unit/test_relationship_scoring.py`, 32/32 passed (includes the
    camelCase regression test below).
  - *Live-caught bug, cross-phase:* `score_identifier_likelihood()`'s name-shape fallback regex
    (`_id$` etc.) only matches snake_case — real ClickHouse `origin2.Location.companyID`
    scored 0.1 ("no signal") instead of the expected 0.7, because `"companyID".lower()` =
    `"companyid"` has no underscore before `id`. Investigating found the **identical bug
    pattern already in P-5's already-committed `candidate_keys.py`** (`_IDENTIFIER_LIKE` regex,
    same blind spot — confirmed via direct test that `_IDENTIFIER_LIKE.search("companyid")` is
    `None`), meaning composite-key pruning was silently excluding every camelCase-named ID
    column. Fixed by extracting P-4's existing camelCase-aware normalizer into a new shared
    `src/utils/column_naming.py` (placed below `src/mcp_server/tools/` per CLAUDE.md's layering,
    since P-4/P-5/P-6 can't import sideways from each other) and updating all three modules to
    call it. Red-then-green confirmed: mutating the normalizer back to plain `.lower()` made
    both new regression tests (`test_camelcase_identifier_columns_are_recognized_too` in P-5,
    `test_camelcase_id_columns_get_the_name_shape_signal_too` in P-6) fail as expected;
    restoring the fix made all 77 tests across P-4/P-5/P-6 pass again.
  - *Evidence (live, independent SQL — real ClickHouse `origin2`):* `discover_indexes("Company")`
    confirms real `companyID` primary key; `Location` table genuinely has camelCase
    `companyID`/`locationID` columns. Post-fix, `score_identifier_likelihood(None, "companyID")`
    returns `{"score": 0.7, "source": "name_shape"}` (was 0.1 pre-fix). Full end-to-end
    `Location.companyID → Company.companyID` scoring: `name_similarity=1.0` (exact match),
    `inclusion_coverage=0.258` (sampled). Independently verified against ground truth via direct
    SQL aggregate (not the app's own numbers): `SELECT count() FROM origin2.Location WHERE
    companyID IN (SELECT companyID FROM origin2.Company)` = 472,898 / 1,861,119 total Location
    rows = 0.254 — matches the sampled 0.258 within sampling noise, confirming the
    `filter_values`-based exact-coverage technique is correct (a naive independent "sample both
    sides and intersect" check was tried first and wrongly reported 0.0, because ClickHouse's
    string-sorted `Company.companyID` sample of the first ~100K distinct values out of 1.27M
    total is not representative — this is exactly why P-6's implementation deliberately filters
    the parent query by the child's own sampled values instead of independent sampling).
  - *Full suite:* `scripts/check.py all` — 10/10 passed (one `ruff format` violation caught and
    fixed in the new test file before this pass).
  - **Follow-up fix (found while starting P-8, 2026-08-19):** re-reading `Branch_Plan.md`'s own
    P-6 text ("each stored as evidence with the coverage formula") while building P-8's graph
    reader surfaced that P-6, as originally committed, computed all ten signals but **never
    persisted them** — no call to `store.insert_relationship()` anywhere in
    `relationship_scoring.py`. This is a real completeness gap, not optional (unlike this same
    module's already-documented, correctly-deferred `datasketch MinHashLSHEnsemble` pruning
    step, which ADR 0007 itself calls "optional accelerator" and notes "a schema small enough
    that exhaustive exact checking is already fast does not need this path exercised" — true at
    this project's current real scale, so still not implemented, and still fine not to be).
    Added `store_relationship()`: persists via `insert_relationship`, skips storage entirely for
    `UNRELATED` (a non-relationship is not evidence worth keeping), `AUTO_ACCEPTED` at
    `confidence >= 0.7` (the same bar `_classify_relationship_type` already uses for
    `FOREIGN_KEY`) else `NEEDS_REVIEW`.
    - *Evidence (unit):* 3 new tests in `test_relationship_scoring.py`, 35/35 passed for the file.
    - *Evidence (live, independent SQL — real ClickHouse `origin1` + real PostgreSQL
      `global_warehouse`):* computed all ten real signals for `origin1.CompanyExperience.companyID
      -> origin1.Company.companyID` (1230 rows/482 distinct child, 3940/3940 distinct parent, 0
      nulls — all real numbers from direct SQL, not fabricated) — `FOREIGN_KEY` at confidence
      0.91. Stored via `store_relationship`; independently confirmed via `psql` join across
      `md_relationship`/`md_column`: `companyID | companyID | FOREIGN_KEY | coverage=1 |
      confidence=0.91 | ALGORITHMIC | AUTO_ACCEPTED` — matches the script's own output exactly.
    - *mypy fix alongside:* `store_relationship` returning `Any` from a `str | None`-annotated
      function (`store` is typed `Any`, matching every other `store_*` function in this
      codebase) — fixed with an explicit `relationship_id: str = store.insert_relationship(...)`
      before the return, same pattern `metadata_graph.py`'s own `upsert_canonical_field` already
      uses. Also fixed two pre-existing mypy errors in P-7's `schema_matching.py` surfaced by the
      same `scripts/check.py types` run (pandas has no stubs — added the same
      `# type: ignore[import-untyped]` `splink_matcher.py` already uses for the identical import;
      an unannotated list-comprehension made mypy infer `object` instead of `float` for a sort
      key). `scripts/check.py all`: 10/10 passed after the fix.

> **BP-B2** (plan's own text: "🚩 Commit to main if clean") — **do not commit to main** per the
> scope override above. Live-check both ClickHouse origins per the plan's own definition, note
> the result here, and continue straight into P-7.
>
> **Done, live-verified, not committed to main.** Ran `join_graph.py`'s real `build_join_graph()`
> against both real ClickHouse origins (`origin1`: 13 tables, `origin2`: 7 tables). Before the
> fix below: 11 edges found, 10 real (origin1, all `overlap=1.0`) + 1 false positive
> (`origin2.CompanyIndustry.companyID -> Company.companyID`, claimed `probable` at
> `overlap=0.646`).
>
> **Live-caught bug (out-of-plan, fixed now — not deferred, since it directly fails BP-B2's own
> bar):** `sample_distinct_values()`'s unfiltered branch — shared by `join_graph.py`'s V2
> inclusion-dependency check AND P-6's brand-new `score_inclusion_coverage` — issued
> `SELECT DISTINCT col ... LIMIT N` with no ordering. ClickHouse returns the first N distinct
> values in MergeTree scan order (not a random sample), which silently biased confidence
> numbers. Independently confirmed via direct SQL aggregate:
> `SELECT count() FROM origin2.CompanyIndustry WHERE companyID IN (SELECT companyID FROM
> origin2.Company)` → 673,855 / 2,656,247 = **0.254** true ratio, vs. the biased sample's 0.646 —
> a false `probable` edge (>`_PROBABLE_THRESHOLD`=0.5) that a genuine random sample does not
> produce. Fixed in all three connectors for consistency (same shared interface method):
> - `clickhouse.py`: added `ORDER BY rand()` before `LIMIT`.
> - `postgres.py`: PostgreSQL rejects `SELECT DISTINCT col ... ORDER BY random()` directly
>   (`ORDER BY expressions must appear in select list` — live-caught against the real
>   `global_warehouse` destination DB), so the fix wraps the `DISTINCT` in a subquery before
>   randomizing order.
> - `mongodb.py`: added a `$sample` stage (20x oversampled) before `$group`, for a genuine
>   random sample of documents instead of natural scan order.
>
> Post-fix, re-ran the exact same live check: `CompanyIndustry.companyID -> Company.companyID`
> sampled overlap dropped to 0.216 (close to the 0.254 ground truth, within `n=500` sampling
> noise) — correctly below the `probable` threshold and no longer asserted.
> `build_join_graph()` re-run: **10 edges, all origin1, all real, all still found** — independently
> spot-confirmed two more via direct SQL (`CompanyFinancials.companyID`: 29/29 matched;
> `origin2.PersonContact.personID -> Person.personID`, correctly NOT asserted: true ratio
> 1,082,811/4,321,730 = 0.250, consistent with `origin2`'s other ~25%-overlap columns, none of
> which cross the `probable` bar). **Bar met exactly: every relationship V2 found is still found
> (10/10), and no relationship is now asserted that hand-written SQL cannot confirm.**
>
> ClickHouse fix live-verified against real `origin2` data (above). PostgreSQL fix live-verified
> as a functional smoke test against the real `global_warehouse` destination DB (`company.id`
> sample returns valid rows post-fix; table too small — 2 rows — to reproduce the bias
> dramatically, but confirms the subquery-wrapped SQL is syntactically correct against real
> PostgreSQL). MongoDB fix could not be live-verified — no mflix-style Mongo container is
> currently running in this environment (best-effort, consistent with BP-A2's own precedent) —
> covered by a unit-test regression instead (`test_mongodb_connector.py`, pipeline shape
> assertion).
>
> New regression tests (red-then-green confirmed for all three): unit tests, all passing.
> `scripts/check.py all`: 10/10 passed.
>
> **Honest finding on the plan's own worked example (checked 2026-08-19, while completing P-6's
> persistence follow-up above):** the plan text names `Company↔Experience↔Person↔PersonContact`
> as a chain that should be "present with measured coverage." Checked directly against the real
> schema: `origin2.Experience` has NO `personID` column at all (`experienceID`, `companyID`,
> `tags`, `title`, `role`, `created_at` only — confirmed via `DESCRIBE TABLE`), so this exact
> 4-node chain cannot be formed from `origin2` alone. `origin1` has `CompanyExperience` WITH a
> real `personID` column (linking to `Person`), but `origin1` has no `PersonContact` table at
> all. Since `join_graph.py`'s edges are strictly intra-origin by design (cross-origin "same
> entity" links are Phase 3's `entity_grouping` job, not a join-graph edge — see the module's own
> docstring), no single origin in the real seed data can produce this literal 4-node chain today.
> What DOES exist and IS verified: `origin1.CompanyExperience.personID -> Person.personID`
> (98%+ overlap, part of the 10 real edges above) and, separately in `origin2`,
> `PersonContact.personID -> Person.personID` (a real but weak ~25% relationship, correctly not
> asserted as a strong edge per the sampling-bias fix above) — two genuine but disconnected
> fragments, not the single connected chain the plan's text describes. Recorded here rather than
> fabricated or silently ignored, per this project's own standing rule that a plan's worked
> examples are verification targets, not automatic truths.

- [x] **P-7** Schema matching & canonical schema — `src/mcp_server/tools/schema_matching.py`
  (ADR 0005). Three tiers: Valentine (`Coma`+`DistributionBased`+`JaccardDistanceMatcher`, merged
  by max score across matchers, each matcher's own vote kept as evidence — `pip install
  valentine`, added as pyproject.toml's new `schema_matching` optional group), the deterministic
  heuristic pass (reuses `map_fields.py`'s own `_heuristic_match` rather than a second copy), the
  LLM (new `schema_matching` stage/prompt pair) for what remains ambiguous after both. The
  canonical schema is *discovered*, not preset like V2's fixed destination contract: the first
  table for an entity type seeds canonical fields 1:1; later tables match against that seed;
  no-match becomes a NEW canonical field.
  - *Evidence (unit):* `tests/unit/test_schema_matching.py`, 18/18 passed.
  - *Live-caught bug #1 (in V2's already-shipped `map_fields.py`, not new code):*
    `_heuristic_match("ceoName", "name")` returned 0.8 (auto-accept under `_apply_heuristic_tier`'s
    own `>= 0.5` bar) via the blanket `s in d or d in s` substring shortcut, silently contradicting
    that very function's own adjacent comment ("`ceoName` and `founderName` ... must NOT be mapped
    onto it") — the token-overlap check the comment describes was never reached because the
    substring shortcut returns first. Fixed with a narrow, precedented gate
    (`_has_unsafe_role_prefix`, mirroring the existing `_safe_coalesce_target`/
    `_SAFE_COALESCE_QUALIFIERS` pattern already in the file) blocking only role-qualifier prefixes
    (ceo/founder/cofounder/owner/manager/contact) that name a DIFFERENT entity — confirmed it does
    NOT affect the existing, intentionally-passing `email_address<->email`, `full_name<->name`
    cases (158 tests across every `map_fields`-adjacent suite still pass). Red-then-green
    confirmed via a new regression test in `test_map_fields_detail.py`.
  - *Live-caught bug #2 (new, in this module):* real `origin2.Person`'s `firstName`/`lastName`/
    `middleName` each independently scored 0.8 against a bare `name` seed column (all contain
    "name" as a substring) — without a guard, all three would silently "accept" onto the SAME
    canonical field, with whichever is stored last winning in any consumer that treats a
    canonical field as single-valued. Fixed with a "one winner per target column" rule: only the
    first (highest-scoring, here `fullName`) source column may accept a given target; later
    claims on an already-taken target (below 1.0) are marked `ambiguous` instead. Red-then-green
    confirmed.
  - *Evidence (live, independent — real ClickHouse `origin1`/`origin2`):* seeded canonical
    `company` schema from `origin1.Company` (24 real columns), matched real `origin2.Company` (14
    columns) against it: `companyID`/`description`/`website`/`created_at` exact Valentine matches
    (1.0); `name` -> `entityName` (0.8, heuristic); `type` -> `types` (1.0, Valentine); 8 genuinely
    new concepts (`domain`, `employeeCount`, `phone`, `revenue`, `rank`, `alexaRank`,
    `revenueRange`, `ticker`) correctly became new canonical fields, zero ambiguous. Separately,
    `origin1.Person`/`origin2.Person` exercised the one-winner-per-target fix live (`fullName`
    accepted onto `name`, `firstName`/`lastName`/`middleName` correctly `ambiguous`).
  - *Evidence (live, independent SQL — real PostgreSQL `global_warehouse`):* full storage
    round-trip via `MetadataStore` — `store_mappings` wrote 25 seed canonical fields + 8 new ones
    from origin2 (33 total, `psql SELECT entity_type, field_name FROM md_canonical_field`
    independently confirms the count and exact field names) and 14 `md_field_mapping` rows for
    `origin2.Company` (`psql` join across `md_field_mapping`/`md_column`/`md_canonical_field`
    independently confirms every source_column/canonical_field/confidence/origin/review_status
    pair matches what the script printed).
  - *Gap, honestly noted:* the LLM tier (3) could not be live-verified end-to-end — no
    `LLM_API_KEY` is configured in this environment (no `.env`, only `.env.example`) — covered
    instead by mocked unit tests exercising both the LLM-accept and LLM-failure paths. In the two
    live pairs tested, tiers 1+2 alone resolved everything with zero ambiguous columns reaching
    tier 3 for Company, and the Person pair's ambiguous columns correctly fell back to
    `NEEDS_REVIEW` per the tested no-LLM-configured path.
  - *Full suite:* `scripts/check.py all` — 10/10 passed.
- [x] **P-8** Metadata graph + graph UI (ADR 0009) — `src/mcp_server/tools/metadata_graph_view.py`
  (backend) + `src/ui/frontend/src/components/MetadataGraphScreen.tsx` (frontend), wired through
  a new `POST /api/metadata-graph` endpoint (`query_metadata_graph` in `db_discovery.py`, same
  connect-and-catch pattern as the existing lineage lookup). `build_metadata_graph()` reads every
  P-1 table via 8 new `MetadataStore.list_*` bulk readers and builds one `networkx.DiGraph`:
  containment edges (source→database→schema→table→column, confidence 1.0) plus P-6
  `relationship` and P-7 `field_mapping` semantic edges. Strictly the METADATA graph — no code
  path here can produce an entity node (P-9's job, a different graph object per ADR 0009's own
  consequence). Four filters (`filter_by_min_confidence`/`_node_type`/`_source`/`_depth`), all
  server-side and reused by the frontend's confidence slider / type checkboxes / focus feature.
  UI: SVG lane layout (one lane per node type), pan (drag) + zoom (wheel/buttons), search
  (highlights matching labels), type/confidence filters, per-node expand/collapse (hides
  `contains` descendants), click node → metadata panel, click edge → evidence panel, double-click
  node → server-side depth-bounded focus.
  - *Evidence (unit):* `tests/unit/test_metadata_graph_view.py` (12 tests) +
    `tests/unit/test_metadata_graph.py`'s new `TestBulkReaders` (5 tests) — 41/41 passed.
    Frontend: `npm run typecheck` clean, `npm run build` clean.
  - *Evidence (live, independent SQL — real PostgreSQL `global_warehouse`):* `build_metadata_graph`
    against the metadata accumulated by every earlier P-1–P-7 live-verification run in this
    session produced 104 nodes / 101 edges (10 sources, 5 databases, 6 schemas, 7 tables, 43
    columns, 33 canonical fields, 61 `contains` + 39 `field_mapping` + 1 `relationship` edges).
    Independently confirmed via a direct `psql` count across all 8 tables: `10 | 5 | 6 | 7 | 43 |
    33 | 1 | 39` — exact match, no silent drops or duplicates.
  - *Evidence (live, real browser — full interactive verification, not just the API):* found and
    fixed a real environment issue first — `preview_start` was launching the dev server from the
    wrong git worktree (`fix-bugs`, this session's original directory) rather than
    `v3-discovery-er`, so the new endpoint 404/405'd against completely unrelated code; fixed by
    starting the server directly in the correct worktree and attaching the Browser pane via
    `url`. With that fixed: `POST /api/metadata-graph` against the real destination returned the
    same 104/101 verified above. In the actual rendered UI: node click shows the real `Company`
    table's metadata (id/schema_id/name/row_count_estimate); edge click shows the real
    `FOREIGN_KEY` relationship stored during P-6's follow-up (confidence 0.91, coverage 1,
    `AUTO_ACCEPTED`) — exact match to what was persisted. Search for "companyID" highlighted
    exactly 3 matching nodes. Unchecking the "column" type filter dropped 104→61 nodes, 101→18
    edges (only non-column containment survives, as expected). Raising the confidence slider to
    0.85 dropped 101→49 edges without touching node count (confidence filters edges only, never
    nodes, by design). Collapsing the `Company` table node hid its columns (104→79 nodes,
    101→50 edges) and showed "Company (collapsed)". Double-clicking `Company` and using "Focus
    here" narrowed the graph via the real depth-2 server-side traversal (104→55 nodes/edges), and
    "Clear focus" correctly restored the full 104/101.
  - *Full suite:* `scripts/check.py all` — 10/10 passed.
- [x] **P-9** Entity anchors, subgraph, coverage, extraction — `src/mcp_server/tools/entity_extraction_v3.py`
  (ADR 0015). `discover_anchors()`: ranked candidates with evidence, never auto-picks a winner
  on name equality (§31). `TraversalPolicy` + `traverse_subgraph()`: policy-capped BFS
  (`max_depth`/`max_nodes`/`max_edges`/`min_edge_confidence`/`allowed_edge_types`/
  `blocked_edge_types`/`allowed_sources`) over the real `JoinGraph`, cycle-preventing by
  construction, per-path provenance via direction-aware `PathHop`s (the same type
  `join_graph.find_paths` already returns). `compute_context_score()` (§33): confidence-tier
  product, decayed by hop count. `build_coverage_matrix()` (§34): table- and field-level.
  `plan_extraction()`/`execute_extraction()` (§35, §55, §56): cost-estimate preview via each
  connector's real `estimate_query_cost` (P-2), then a fixpoint hop-walk extraction mirroring
  `extract_data.py`'s own `_resolve_link_path` shape. Wired into `plan_scope_impl` as an
  in-function `scope_config.engine == "v3_discovery"` branch (ADR 0015), following the
  `resolve_entities.py` Splink-flag precedent exactly: falls through silently to the V2
  heuristic floor on any failure or empty result. `extract_data.py` needs zero changes, since
  the new engine's output is adapted into the identical wire shape.
  - *Evidence (unit):* `tests/unit/test_entity_extraction_v3.py` (25 tests) +
    `tests/unit/test_plan_scope.py`'s new `TestV3DiscoveryPlan`/`TestPlanScopeImplV3EngineFlag`
    (7 tests) — 64/64 passed for the two files together, zero regressions in the other 32
    pre-existing `plan_scope` tests.
  - *Live-caught bug #1:* `execute_extraction` called `connector.extract(table, [], ...)` —
    `[]` isn't this codebase's "all columns" convention (confirmed: `clickhouse.py` only
    special-cases `columns == ["*"]`), producing a malformed query with no SELECT list — a real
    ClickHouse syntax error on the very first live call against `origin1.Company`. Fixed by
    passing `["*"]`.
  - *Live-caught bug #2 (found after fixing #1 — every downstream hop still returned 0 rows):*
    propagated join-column values were being run through `join_graph.normalize_val()`
    (lowercases + strips URL prefixes — correct for its actual job, cross-column equality
    *comparison* during inclusion-dependency verification) before being used as real query
    filter values. A genuine case-sensitive id like `E-xSjHM5bMJmwwRfrYLwwkw` filtered on its
    lowercased form matched zero rows in the real, case-sensitive database. Fixed by using seed
    values verbatim. Red-then-green confirmed via a new regression test
    (`test_propagated_filter_values_keep_their_original_case`).
  - *Evidence (live, independent — real ClickHouse `origin1`/`origin2`):* `discover_anchors`
    for "Boeing" against real `origin1.Company` found 36 ranked candidates, including **three
    separate rows literally named "Boeing" exactly** — concrete confirmation of why §31 matters
    (not theoretical). `traverse_subgraph` from `(origin1,Company)`+`(origin2,Company)` with
    `min_edge_confidence="verified"` reached exactly the 10 real edges / 12 nodes BP-B2 already
    established, `Person` correctly reached at depth 2 with `context_score=0.7671`.
    `plan_extraction`'s real cost estimate for `origin2.Company` (1,270,564 rows) exactly
    matches the direct SQL count BP-B2 already confirmed independently.
  - *Evidence (live, cross-checked against the golden baseline):* `execute_extraction` seeded
    with `entityName ILIKE '%Boeing%'`/`'%Airbus%'` against real `origin1` pulled real rows
    across all 10-11 reached tables. **Financials matched the golden baseline exactly both
    times** — 20/20 for Boeing, 28/28 for Airbus (the cleanest apples-to-apples number
    available: one source table, no dedup/entity-resolution step in between). `Person` count
    (39, origin1-only) is consistent with golden's combined total (539 = ~500 from origin2 + 39
    from origin1). The flagged `plan_scope_impl` (`scope_config={"engine": "v3_discovery"}`)
    produced a valid, wire-compatible `scope_plan` for a real Boeing run against real schemas:
    `company` matched in both origins, `person` linked via a real 2-hop `origin1` path,
    `origin2`'s `Person` correctly excluded (no edge there clears the `verified` bar,
    consistent with BP-B2's finding that `origin2`'s relationships are mostly weak ~25%
    overlap).
  - *Scope decision, honestly narrower than the plan's literal text (see ADR 0015):* the flag
    replaces V2's **planning** step only; `extract_data.py`'s actual row-pulling execution
    (watermarks, incremental extraction, chunked/scratch-table `IN` filters — 1000+ lines,
    heavily battle-tested) is untouched. `entity_extraction_v3.execute_extraction` is proven,
    directly-callable, and live-verified on its own (above) but not wired into the live
    pipeline's extraction step; its single-batch-per-hop fetch doesn't paginate the way
    `extract_data.py` does, a real gap if it were wired in directly — exactly why that wiring
    is deferred to a future ADR rather than done partially now.
  - *Full suite:* `scripts/check.py all` — 10/10 passed.
- [x] **P-10** Canonical matching dataset — `src/mcp_server/tools/canonical_matching_dataset.py`.
  The Splink handoff artefact: `build_dataset_rows()` assembles P-9's raw extraction output plus
  P-7's real field mappings into one row per source record — `record_id`, `source_id`,
  `entity_type`, `entity_anchor_id`, `canonical_entity_id` (deliberately `NULL` — Splink's own
  job in B.4, not yet reached), `source_table`, `source_record_id`, `canonical_fields`,
  `original_fields`, `context_score`, `graph_path`, `provenance`, `discovery_evidence`,
  `quality_diagnostics`. `materialize()` writes it into the run's own DuckDB spool file as a real
  typed table (never one opaque JSON blob) via an `ON CONFLICT (record_id) DO UPDATE` upsert.
  Never imports `src/orchestrator/spool.py` directly (layering) — the caller passes the spool's
  own file path in, same pattern P-9's modules already use.
  - *Evidence (unit):* `tests/unit/test_canonical_matching_dataset.py`, 10/10 passed.
  - *Live-caught bug #1:* the very first real `materialize()` call against real Boeing data hit
    silent data loss — 2447 rows built, but only 144 landed. Root cause: my live-verification
    script supplied `"companyID"` (a foreign key) as `CompanyExperience`'s own `record_id_column`;
    every experience row for the same company collapsed onto the same `record_id`, and the
    upsert silently overwrote all but the last one. Fixed in the **module itself**, not just the
    script: `build_dataset_rows()` now verifies the supplied `pk_column`'s values are actually
    unique across the batch per table, and falls back to positional record ids (already the
    established fallback for "no pk_column given") the moment they aren't — logging a warning
    rather than silently colliding. Red-then-green confirmed via
    `test_a_non_unique_pk_column_falls_back_to_positional_ids_not_a_collision`.
  - *Live-caught bug #2 (found immediately after fixing #1):* the DuckDB schema declared
    `source_record_id TEXT NOT NULL`, but the module's own docstring already documented that a
    table with no known PK column legitimately produces `source_record_id=NULL` — the fallback
    from bug #1 triggered exactly that path and hit the constraint on the very next real
    `materialize()` call. Fixed by making the column nullable. Red-then-green confirmed via
    `test_a_row_with_no_source_record_id_is_still_materialized`.
  - *Evidence (live, independent SQL — real Boeing extraction, real P-7 field mappings from the
    metadata store):* 2447 rows built and materialized (matching P-9's own extraction row counts
    exactly, table by table: `Company` 18, `CompanyContact` 39, `CompanyEmployeeCategory` 188,
    `CompanyExperience` 39, `CompanyFinancials` 20, `CompanyIndustry` 18, `CompanyInvestmentRound`
    6, `CompanyRelation` 559, `CompanyTechnographics` 1503, `Location` 18, `Person` 39).
    Independently confirmed via `duckdb` CLI-style queries against the materialized file:
    `SELECT count(*), count(DISTINCT record_id)` = `(2447, 2447)` — zero collisions, zero loss.
    A `Company` record's `canonical_fields` shows the real P-7 mapping applied correctly
    (`entity_name`, `full_name`, `website`, ... populated from the real ClickHouse row). A
    `CompanyExperience` record (the table that exposed bug #1) has `source_record_id=NULL`
    (honest — it has no genuine single-column key) but its full `original_fields` and
    `provenance` are intact, so "where did this come from" is still answerable by SQL alone —
    just not addressable by a single id column, which is a true fact about that table, not a gap
    in the dataset.
  - *Full suite:* `scripts/check.py all` — 10/10 passed.

> **BP-B3** — the handoff contract is provable: for a given Airbus run, every canonical record
> answers "where did this come from?" by SQL alone. Live-verify, report back, **stop here** —
> do not proceed into B.4 (Splink phases) or touch `main` without explicit go-ahead.
>
> **Done, live-verified 2026-08-22, not committed to main.** Ran the full P-9 -> P-10 pipeline
> end to end for a real Airbus run against real ClickHouse `origin1`: `discover_anchors` +
> `traverse_subgraph` (policy: `max_depth=3`, `min_edge_confidence="verified"`) reached 10 real
> tables from the `Company` anchor; `execute_extraction` pulled 2840 real rows scoped to
> `entityName ILIKE '%Airbus%'`; `build_dataset_rows` + `materialize` assembled them (with P-7's
> real, already-stored field mappings) into a real DuckDB `canonical_matching_dataset` table.
>
> Independently confirmed via direct SQL against the materialized file (not the script's own
> printed output): `SELECT count(*), count(DISTINCT record_id)` = `(2840, 2840)` — zero
> collisions, zero silent loss (the exact failure mode P-10's own live-caught bug #1 could have
> produced, now guarded against). Per-table row counts match P-9's own extraction exactly
> (`Company` 7, `CompanyContact` 16, `CompanyEmployeeCategory` 138, `CompanyExperience` 33,
> `CompanyFinancials` 28, `CompanyIndustry` 7, `CompanyRelation` 572, `CompanyTechnographics`
> 2000, `Location` 7, `Person` 32).
>
> The bar itself, checked directly: the real `Company` record for "Airbus" (`full_name` "Airbus
> SAS") has every real column correctly mapped to its canonical field (24 columns, each with its
> own `discovery_evidence` entry naming the source column, canonical field, confidence, and
> origin — `ALGORITHMIC`, from P-7's real live-verified mapping run). A real `Person` record two
> hops from the anchor (Bernard Lathière, "French politician & former Airbus CEO") answers
> "where did this come from" with nothing but its own row: `graph_path` shows the exact join
> chain (`Company.companyID -> CompanyExperience.companyID`, then
> `CompanyExperience.personID -> Person.personID`, each hop's real confidence tier and
> verification rationale intact), `provenance` names the origin/table/key-column, and
> `original_fields` preserves the untouched raw row. All of this is one `SELECT` against real,
> materialized columns — never a JSON blob requiring app code to interpret, never something
> requiring the pipeline to be re-run.
>
> **Handoff contract: provable.** Continuing into B.4 (Splink) or committing this branch to
> `main` both require the user's explicit go-ahead, per the standing scope for this work.
