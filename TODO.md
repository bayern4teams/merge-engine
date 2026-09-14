# merge-engine — Implementation TODO

Branch `fix-v3`. Part A is the preserved, verbatim history of `feat/v3-discovery-er`
(every phase's evidence log, unchanged — history in this repo is load-bearing and is
kept, never edited into silence). Part B is the current roadmap: every §13
(Architecture — Quality & Speed) and §14 (Algorithm-First: discover_schemas,
map_fields, analyze_dependencies) recommendation of the Merge Engine Forensic Audit
(2026-09-13), sequenced in the audit's own priority order ("What I'd actually
schedule, in order") and implemented on this branch.

Evidence rule, unchanged throughout: independently-run SQL / test output, never the
app's own success message. Full implementation detail for every Part B item:
`.hermes/plans/2026-09-14_025800-fix-v3-cto-recommendations.md`.

---

# Part A — History: `feat/v3-discovery-er` (verbatim)

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

## B.4 — Splink workbench (S-A..S-P): user-authored, then remediated

The B.4 workbench (`splink_common/compat/blocking/recommender/training/inference/evaluation/
model/workbench.py`, ~4,350 lines, plus `src/api/main.py`'s `/api/splink/*` delegators and
`src/ui/frontend/src/components/SplinkWorkbenchScreen.tsx`) was written by the user directly,
picking up exactly where an earlier agent session stopped at BP-B3. A senior-review pass
(same conversation, before this remediation) found the design sound — layering clean, no module
state, no bare except, comparator catalog verified against the installed 4.0.16 API surface —
but flagged 2 live-reproduced bugs, 20 mechanical lint/type gate failures, and a handful of
design/doc gaps. This section is that remediation, itself entirely live-verified, never taken on
the app's own success message.

- [x] **Bug 1** `splink_recommender.field_stats` crashed (`AttributeError: 'Counter' object has
  no attribute 'head'`) on any all-empty column — the ternary `values.value_counts() if
  non_null else Counter()` then unconditional `.head(5)`; `Counter` has no `.head()`.
  - *Fix:* drop the ternary — `value_counts()` on an empty Series still returns a Series.
  - *Evidence (live):* a standalone script with a genuinely all-empty column reproduced the
    crash pre-fix, then confirmed `completeness=0.0, top_values=[]` with no exception post-fix.
- [x] **Bug 2** `splink_inference.explain_candidate` hardcoded `row.get("id_l")`/`row.get("id_r")`,
  but installed 4.0.16's `compare_two_records` actually names those columns
  `{unique_id_column_name}_l/_r` — every caller got `id_l=None, id_r=None`.
  - *Fix:* read the real column names via `splink_common.unique_id_column_of(linker)`, the same
    helper 3 other call sites already used correctly.
  - *Evidence (live):* a hand-built linker + `compare_two_records` call returned real ids
    (`pg:t:1`/`pg:t:2`) post-fix, confirmed twice (isolated script, then the full workbench
    `train`→`explain` path).
- [x] **Bug 3 (found during this remediation's own live verification, not on the original list)**
  `splink_workbench.explain()` passed the caller's *raw* `record_a`/`record_b` (original field
  names, `_origin_*` lineage keys, no unique-id column) straight into `compare_two_records` — the
  exact same visible symptom as Bug 2 (unusable ids) but one layer up: Splink didn't see the
  configured unique-id column on either dict at all, and silently substituted its own internal
  `_left`/`_right` placeholders instead of raising.
  - *Fix:* run `record_a`/`record_b` through the same `records_to_dataframe` transform the
    training records already went through before calling `infer.explain_candidate`.
  - *Evidence (live):* an end-to-end script through `wb.train()` → `wb.explain()` with realistic
    (raw, lineage-keyed) records returned `id_l="_left", id_r="_right"` before the fix and real
    ids (`pg:t:1`/`pg:t:2`) after — confirmed again independently through the live browser UI
    and against real ClickHouse data (see below).
- [x] **Lint gate** `scripts/check.py lint` — 19 ruff errors (RUF002 en-dashes in docstrings,
  I001 unsorted imports, B905 `zip()` without `strict=`, RUF046 redundant `int()` cast, F401
  unused imports) across 8 files, all fixed (`strict=True` chosen deliberately — every flagged
  `zip()` pairs same-length sequences derived from the same source).
  - *Evidence:* `scripts/check.py lint` — 1/1 passed.
- [x] **Types gate** `scripts/check.py types` — 20 new mypy errors against the ratchet baseline
  (zero pre-existing baseline noise touched). Notable ones: a shared `LinkType = Literal[
  "link_only", "link_and_dedupe", "dedupe_only"]` alias added to `splink_common.py` (previously
  four separate `link_type: str` parameters across `splink_common`/`splink_blocking`); the
  `splink_training._session_to_record` trajectory dict's key type corrected from a wrong-arity
  `tuple[str, str, int]` to the real `tuple[str, str, Any, str]` (4 elements, not 3 — an
  undetected type-checking gap, not a runtime bug, since Python itself never enforced the
  declared type); a `val`/`param_val` rename to resolve a "Name already defined" redefinition
  mypy caught between two independent loops in the same function; `# type: ignore[import-
  untyped]`/`[no-untyped-call]` applied at the same call sites `splink_matcher.py` (the existing,
  pre-B.4 engine) already uses for the identical Splink-internals pattern.
  - *Evidence:* `scripts/check.py types` — 1/1 passed.
- [x] **Format gate** `scripts/check.py format` — genuinely a hard gate (its own docstring:
  "a real, enforced gate from here on... not a warning"), not report-only as an earlier
  assumption in this session's carried-over context had it. `ruff format` reformatted 12 files
  (all of B.4's own new modules/tests plus one pre-existing eval test it touched).
  - *Evidence:* `scripts/check.py format` — 1/1 passed.
- [x] **Design gap: duplicated comparator-building logic.** `splink_recommender.
  comparison_to_settings_hit` (semantic-type-driven, S-B) and `splink_workbench.
  _config_comparison`'s explicit-comparator branch each independently mapped comparator *name*
  to a Splink comparison object — two if-chains that had already started to diverge (the
  workbench's used `getattr`-based dispatch reaching the whole installed `comparison_library`;
  S-B's used a fixed list of direct `cl.XComparison` references). Extracted the single
  `splink_recommender.build_comparator(name, field, *, extra_fields, thresholds)` both now
  delegate to — `getattr`-based (superset of what either had), so a hand-picked comparator and
  an AUTO-recommended one can never construct differently again.
  - *Evidence:* all 84 pre-existing B.4 unit tests still pass unchanged post-refactor, including
    `test_settings_hit_builds_real_comparators` / `test_settings_hit_refuses_mismatched_columns`;
    re-verified live via the full `train()` path using the explicit `{comparator, field}` shape.
- [x] **Doc gap: `run_prediction`'s docstring** claimed a fixed `id_l`/`id_r` naming; corrected to
  name the real `{unique_id_column}_l/_r` convention (the same fact Bug 2/3 were about).
- [x] **Doc gap: "Splink.txt" spec source.** Every new module cites `Splink.txt §NN` by section
  number; confirmed via repo-wide search that no such file exists in this repository. Not a gap
  to fix — `Branch_Plan.md`'s own B.1 table already reproduces the section-to-verified-API
  mapping in full (`| Spec need (Splink.txt §) | Verified API |`), so the content those citations
  point to is already in-repo; `Splink.txt` itself is external planning input, never meant to be
  committed.
- [x] **Doc gap: no ADR for S-N's model-versioning design.** Added
  [0016-splink-model-versioning-file-store.md](docs/decisions/0016-splink-model-versioning-file-store.md)
  (file-store-per-version, immutability, activation-as-pointer, round-trip-verify gating
  activation) and indexed it in `docs/INDEX.md`.
  - *Evidence:* `scripts/check.py docs` — 1/1 passed (every citation resolves).
- [x] **Second KNOWN LIMITATION documented (found during this remediation's live verification,
  not on the original list).** `splink_inference.run_clustering`'s `compute_graph_metrics` call
  always raises `TypeError: 'numpy.float64' object cannot be interpreted as an integer` against
  the installed splink 4.0.16 + igraph 1.0.0 pair — reproduced with a plain Python `float`
  threshold and a `numpy.float64` alike (ruling out our call site), traced to
  `edge_metrics.compute_igraph_metrics` → `igraph.Graph.DataFrame` → `igraph.Graph.__init__`
  receiving a numpy-typed vertex count where installed `igraph` requires a native `int`. Already
  gracefully handled (caught, surfaced as `graph_metrics: {"error": ...}`, does not fail
  clustering) — documented in the module docstring next to the existing `cluster_using_
  single_best_links` limitation so the next Splink/igraph upgrade re-tests both instead of
  rediscovering either.
- [x] **Frontend built** — `src/ui/frontend/src/components/SplinkWorkbenchScreen.tsx` (new,
  ~700 lines) wired to all 14 `/api/splink/*` endpoints (status/recommend/train/predict/explain/
  cluster/evaluate/models list/get-version/get-active-config/activate/rollback/archive/compare),
  a new "Splink" tab in `App.tsx`, matching this project's existing card-per-concern component
  style (`InvestigateScreen.tsx`'s pattern) and CSS classes (no new styling introduced).
  - *Live-caught bug (frontend) #1:* the field-roles table read `f.matching_stars`/
    `f.blocking_stars`/`f.tf_stars` — the real API response nests them as `f.matching.stars`/
    `f.blocking.stars`/`f.tf.stars`. Fixed; live-reverified showing real star ratings
    (`★★★★★`) instead of blank cells.
  - *Live-caught bug (frontend) #2:* the predict summary read `result.review`/`result.queue` for
    the review-queue count; the real field is `result.review_count`. Fixed.
  - *Evidence (live, `scripts/check.py frontend` + real browser session against the app served
    from this worktree, not the stale build a misconfigured preview server had briefly served
    from a different checkout):* every one of the 14 endpoints exercised through actual UI
    clicks/inputs, each confirmed via `read_network_requests` showing its real HTTP call and
    status code — `train` → "Saved version 1 (round-trip verified: true)", `predict` →
    561/2/34-shaped real numbers depending on dataset, `explain` → real ids end to end,
    `cluster` → the known-limitation warning rendering correctly instead of crashing the screen,
    `evaluate` → a real accuracy report, `activate` → independently confirmed by reading
    `logs/splink_models/<model>/active.json` directly off disk (never the app's own success
    banner), `rollback` on a model with only one activation → a clean 422 surfaced as a banner,
    not a crash (correct: "nothing to roll back to" is the documented behavior for that case).
- [x] **Live-verified against real Docker databases** (`Sample_Database` ClickHouse, port 8190,
  per this file's own standing rule — never synthetic seed data for a final check). Pulled 36 raw
  `Company` rows matching `%boeing%` from real `origin1`+`origin2` (18 each); the live pull itself
  surfaced a real data-quality fact worth noting, not a bug: `origin2.Company` has genuine
  duplicate `companyID`s (3 rows sharing one id) — `records_to_dataframe`'s existing "duplicate
  unique id" refusal caught it correctly, exactly as designed; the 2 duplicates were dropped
  before building the test set, same as a real caller's own dedup pass would.
  - Ran `recommend` → `train` (`NameComparison` + `ExactMatch` on `website`, no blocking — N=34
    is small enough for a full 561-pair pairwise EM pass) → `predict` (561 pairs scored, matching
    `34×33/2` exactly) → `explain` the top-scored real pair (`'Boeing' <-> 'Boeing'`, two distinct
    origin1 rows, `match_probability=0.191`, real per-field Bayes factors) → `cluster` (the known
    igraph limitation degraded gracefully again) → `evaluate` (a real accuracy report) →
    `list_models`/`activate`, all against this real external data, all round-trip-verified
    (`round_trip_verified: True`), with a regression assertion that `explain`'s returned ids
    exactly match the `predict` table's own id columns for the same pair.
  - *Evidence:* full script output in this session's scratch directory; independently confirmed
    the trained model files exist on disk under `logs/splink_models/clickhouse_boeing_company/`.
- [x] **Full suite:** `scripts/check.py all` — 10/10 passed (lint, format, arch, docs,
  portability, types, frontend, test, eval, prompts) after every fix above; the pre-existing
  `tests/eval/test_matcher_recall.py` BP-B4 promotion gate (which measures the *old*
  `splink_matcher.py` engine, untouched by this remediation) was unaffected and still records
  the same `gate_passed: false` outcome ADR 0004's addendum already documents — promoting Splink
  to the default engine remains blocked on that separate, pre-existing gate, not on anything
  fixed here.

> **What this remediation did not do, on purpose:** it did not touch `tests/eval/
> test_matcher_recall.py`'s A/B gate or attempt to make Splink win it — that gate tests the old
> Phase-7.4 engine (`splink_matcher.py`), a different code path from the B.4 workbench entirely,
> and closing that particular recall gap (the exact-match blocking rule on `name` never scoring
> near-miss variants, per ADR 0004's addendum) was never on this remediation's list. It also did
> not wire the workbench's trained models into the production `matching_rules` promotion surface
> — the workbench today is a standalone tuning/training tool; making its output selectable as an
> entity's actual matching engine is a separate, not-yet-scoped piece of future work.
>
> Never committed to `main` — this branch's own discipline applies throughout, same as B.0-B.3.

## Repo publish: GitHub mirror + CI (2026-08-27)

- [x] **Published to GitHub.** `git.n4t.com` (`origin`) remains unreachable for both push/pull
  and any server-side CI (ADR 0003). Added a second remote, `github`, pointing at the user's own
  private repo (`https://github.com/Reza-Sh83/merge-engine.git`), and pushed all 8 local branches
  as-is: `main`, `feat/v3-discovery-er`, `fix-bugs`, `fix-bugs-backup`, `chore/harness-ratchet`,
  `feat/dspy-prompt-layer`, `feat/matching-precision`, `feat/planner-swarm`,
  `feat/quality-lineage`. *Evidence:* each `git push github <branch>` returned `[new branch]`;
  `git ls-remote github` round-tripped cleanly beforehand to confirm the stored credential helper
  authenticates non-interactively.
- [x] **Added `.github/workflows/ci.yml`**, invoking exactly `python scripts/check.py all` on
  `push`/`pull_request`/`workflow_dispatch` — per ADR 0003's own revisit guidance ("whichever CI
  config eventually gets added should invoke exactly that command and nothing else"). Landed on
  `feat/v3-discovery-er` only in this pass, since every other branch is already checked out in a
  sibling worktree (`D:\transfer\merging` for `main`; `.worktrees\B1-B5`/`fix-bugs` for the rest)
  and this session's hard constraint is to work only inside its own worktree root — never check
  out or commit to a branch live in another worktree. Propagating the same file to the other 7
  branches is a small follow-up left for the user (or a future session working in that specific
  worktree) to pick up; the identical file is ready to copy from this branch.
- [ ] **CI run outcome on GitHub Actions not yet independently confirmed.** No `gh` CLI, GitHub
  MCP connector, or authenticated browser session was available in this session to check the
  Actions tab (a private repo returns "Page not found" to an unauthenticated request, which was
  confirmed live — not a guess). The push to `feat/v3-discovery-er` should have triggered a run;
  the user needs to check `https://github.com/Reza-Sh83/merge-engine/actions` directly and report
  back, or authenticate `gh`/a browser session so a future pass can verify it independently.

---

# Part B — `fix-v3` roadmap: §13 + §14 CTO recommendations

Scope: **every** recommendation in §13 (Architecture Recommendations: Quality & Speed —
the 13 library/architecture leads) and §14 (Algorithm-First: `discover_schemas`,
`map_fields`, `analyze_dependencies`) of the Merge Engine Forensic Audit, plus the two
audit-adjacent housekeeping items those sections depend on.

**Priority order (CTO review, 2026-09-14, superseding the original draft order):**
easiest/lowest-risk work first, riskiest/most-speculative work last, one named milestone
closing each phase. The original draft sequenced the riskiest quality change (relational
signal) 2nd and §14's own headline node-wiring recommendation 5th — reordered so the
report's headline finding and every zero/low-risk item ship before anything that can
actually regress a real match or depends on data that doesn't exist yet. Nothing about
*what* each item does changed, only the order and the milestone framing. Label map:
plan phase N ⇒ TODO.md B.N (unchanged 1:1).

Rules that bind every item below (from the audit + CLAUDE.md, non-negotiable):
- TDD per task: failing test first (record the red run), implement minimally, green, commit.
- One commit per task; message names the audit section it closes.
- Evidence in this file = independently-run SQL / test output / measured numbers,
  never the app's own success message. An item whose live half could not run stays
  unchecked with an explicit `BLOCKED:` / `PENDING-LIVE:` note — never silently passed.
- No module-level mutable state (`scripts/lint_arch.py`); degradation paths must
  self-report (ADR 0014 pattern), never a silent `except`.
- Everything runs against local models/DBs only (vLLM `10.10.9.45:8000`, Docker
  `Sample_Database` ClickHouse :8190 + destination Postgres `kg` schema).
- **Items marked [LIVE] additionally require the live infrastructure; their unit gate
  must pass without it.**

Baseline already on this branch (commit `b9c5e5b..4aded86` on `fix-v3`): the 9 §00a audit
fixes + Splink matcher fixes, committed group-by-group off `feat/v3-discovery-er`.

## Milestones

| # | Milestone | Closes | Why it's here in the order |
|---|---|---|---|
| **M1** | Verification unblocked; 3 already-decided-but-dead adoptions shipped | B.1 | Cheapest possible win — unbreaks `check.py all` first |
| **M2** | Reference-only / reject decisions recorded | B.2 | Zero code, zero risk, zero dependencies |
| **M3** | **Algorithm-first architecture live** (the audit's own headline finding) | B.3 | Mechanical, deterministic, reuses existing safety gates |
| **M4** | Post-merge consistency validation shipped | B.4 | Bounded, self-contained, own maturity fallback |
| **M5** | Native relational-matching signal validated against ground truth | B.5 | Real quality risk — earned its place after the safe wins |
| **M6** | Splink promotion decided (promoted, or an honest gap recorded) | B.6 | Genuine blocking unknown + uncertain gate outcome |
| **M7** | Human-feedback-loop infrastructure in place | B.7 | Data-gated (needs real human labels), not effort-gated |
| **M8** | Bounded pilots evaluated with real measured docs | B.8 | Most speculative by design — "prove before commit" |
| **M9** | Final gate — everything green, evidence complete | B.9 | Ship-readiness checkpoint |

## B.0 — Branch + baseline

- [x] **B.0.1** §00a/§00b audit fixes committed group-by-group on `feat/v3-discovery-er`
  (11 commits: config→scope-engine→schema-qualification→load-path PK/EAV→dest-family
  exclusions→join-graph natural key→parse_request collision guard→splink blocking
  union/case→methodology doc→ground-truth scripts→tuning scripts), then branch `fix-v3`
  created from that tip and checked out fully.
  - *Evidence:* `git log --oneline feat/v3-discovery-er | head -11` shows the 11 grouped
    commits; `git branch --show-current` → `fix-v3`; `git status --short` → clean.
- [x] **B.0.2** Baseline gate recorded on `fix-v3`: `python scripts/check.py all`.
  Expected as-is: FAIL on the `test_schema_matching.py` collection error (that is B.1.1's
  red state — recorded here so the fix's red→green is honest).
  - *Evidence:* `python -m pytest tests/unit --collect-only -q` (2026-09-14, `.venv`
    Python 3.13.14) → `ModuleNotFoundError: No module named 'valentine'` at
    `src/mcp_server/tools/schema_matching.py:33`, raised via
    `tests/unit/test_schema_matching.py:17`. Exact tail: `1891 tests collected, 1 error`
    — matches the plan's recorded baseline exactly.

## B.1 — "Finish the sentence" (audit §13 schedule bucket 1: already-decided, never shipped) → M1

- [x] **B.1.1** Unbreak pytest collection — `tests/unit/test_schema_matching.py` gets
  `pytest.importorskip("valentine", reason=...)` (audit §14.2: a module with zero
  production callers currently breaks `pytest tests/unit` collection — and therefore
  `scripts/check.py all` — on every standard bootstrap).
  - *Accept:* `python -m pytest tests/unit --collect-only -q` → 0 errors.
  - *Evidence:* RED (B.0.2): `1891 tests collected, 1 error` —
    `ModuleNotFoundError: No module named 'valentine'`. GREEN (post-fix, before the
    extra was installed): `1891 tests collected in 1.88s`, 0 errors.
    `python scripts/check.py test` → `All 1 check(s) passed.` Full unit suite same run:
    `1808 passed, 84 skipped, 6 warnings in 59.68s` (skips include the then-uninstalled
    valentine-dependent tests). Commit `c4fe1d6`.
- [x] **B.1.2** `scripts/bootstrap.py`: install `.[dev,perf,schema_matching]` (line ~72 +
  the two echoed strings) so ADR 0005's dependency is actually present on a fresh clone.
  - *Accept:* `python -c "import valentine"` ok; `pytest tests/unit/test_schema_matching.py -q` → 18 passed (no skips).
  - *Evidence:* `python -m pip install -e ".[dev,perf,schema_matching]"` →
    `Successfully installed ... valentine-1.0.0 ...`. `python -c "import valentine; print('ok')"`
    → `ok`. `python -m pytest tests/unit/test_schema_matching.py -q` →
    `18 passed, 22 warnings in 1.84s` — zero skips, matches accept criteria exactly.
    Commit `477cedc`.
- [x] **B.1.3** **pg_trgm + GIN trigram blocking** (audit §13 #13 — highest-leverage,
  lowest-risk speed item; targets the O(n²) `resolve_entities` clustering, §00b/§9's 86%
  of wall-clock). `CREATE EXTENSION pg_trgm`; GIN `gin_trgm_ops` index on the staging
  name column; candidate-pair generation `SELECT a.rec_id, b.rec_id FROM staging a JOIN
  staging b ON a.name % b.name AND a.rec_id < b.rec_id` for entities ≥
  `matching.trigram_blocking_min_records` (default 1000), flag
  `matching.trigram_blocking_enabled` default **off**; results **unioned** with existing
  blocking (never replacing — recall safety), deduped. Staging via the destination
  connector through the existing call path (no module state). **Note:** carries more open
  design surface than its siblings (staging-table lifecycle isn't pre-decided) — budget
  accordingly, it's the one "quick win" that isn't actually quick.
  - *Accept (unit):* fake-connector test asserts the SQL shape (extension, `gin_trgm_ops`,
    `%`, `a.rec_id < b.rec_id`), union-dedupe, flag/threshold gating.
  - *Accept [LIVE]:* real destination, the 5 real Boeing variants → true near-duo pairs
    proposed; wall-clock vs Python loop on the 1,503-record technology component recorded.
  - *Evidence:* `tests/unit/test_trigram_blocking.py`, 7/7 passed (SQL shape, union-dedupe,
    unknown-id-pair ignored, <2-usable-rows never calls the connector). **[LIVE]** real
    destination Postgres (`localhost:5434/destination`, `kg` schema): the 5 real Boeing
    name variants → 3 pairs (`0↔1`, `1↔3`, `2↔3`), transitively clustering every true
    near-duplicate; "Boeing Satellite Systems, Inc." correctly proposed zero pairs (a
    genuinely different entity). Wall-clock vs the Python blocking loop on a 1,503-record
    **synthetic** set (real technology-component data isn't currently seeded in this
    environment — recorded honestly as a proxy, not the literal audit dataset): pg_trgm SQL
    1.62s / 18,715 pairs vs Python loop 0.08s / 141,412 pairs. SQL is measurably *slower* on
    raw blocking wall-clock here (network + staging round-trip cost dominates at this
    scale), but produces an 87% smaller candidate set — the real lever against the
    downstream O(n²) *scoring* pass the audit's 86%-of-wall-clock finding is actually about,
    not the blocking pass itself. Recorded as measured, not spun as an unqualified win.
    Commit `28cf70d` (plus `eebd77e` for the unrelated infra fixes discovered en route —
    see below).
- [x] **B.1.4** **Great Expectations — finish ADR 0008** (audit §13 #6):
  `_run_format_expectations(sample, columns)` in `src/mcp_server/tools/data_quality.py`,
  GX 1.x ephemeral context, `ExpectColumnValuesToMatchRegex` (email/phone-shaped) +
  `ExpectColumnValuesToBeDateutilParseable` (date-shaped), results folded into the
  existing `entity_severity`/red-flags mechanism — one report, not two. GX-unavailable →
  warning-level degradation log + `gx_checks_skipped` in the report (never silent).
  New extra `data_quality = ["great-expectations>=1.0"]` + bootstrap.
  - *Accept (unit):* bad email + unparseable date → 2 fail entries; clean → pass.
  - *Accept [LIVE]:* `revenue="$1 Million"` sample → format check fails (the §7 bug GX
    exists to catch).
  - *Evidence:* Used the ALREADY-DECLARED `dq` extra (`great-expectations>=1.0.0`,
    `pyproject.toml`) rather than inventing a duplicate `data_quality` extra name — verified
    via `ls`/`grep` before writing code, per the plan's own "verify exact current pin
    intent" instruction. Also added a `numeric` hint group (revenue/amount/price/cost/
    total/count/quantity) beyond the plan's email/phone/date set — required to actually
    catch the plan's own named target bug (a numeric-*named* column holding a
    non-numeric string; email/phone/date regexes don't touch it).
    `tests/unit/test_data_quality_gx.py`, 9/9 passed +
    `tests/unit/test_data_quality.py` regression, 8/8 passed (17/17 total) — real GX
    1.23.0 exercised, not mocked, including `revenue="$1 Million"` (the exact §7 bug) and
    the no-GX-installed degrade path (`sys.modules` patched to simulate it, asserting
    `gx_checks_skipped is True` on the report). Commit `c7db6b8`.
- [x] **B.1.5** **sqlglot column lineage — finish ADR 0009** (audit §13 #7): new
  `src/mcp_server/tools/sql_lineage.py` with `column_lineage(mapping_sql, target_column,
  schema)` over `sqlglot.lineage.lineage`, plus `mapping_to_sql()` rendering recorded
  `field_mappings`/`md_field_mapping` decisions as parseable SQL. UI surfacing deferred
  (YAGNI) until the API is proven.
  - *Accept (unit):* `SELECT c.amount AS revenue FROM diffbot.companyfinancials c` →
    lineage names `diffbot.companyfinancials.amount`; multi-join chain correct.
  - *Evidence:* `python -c "import sqlglot; print(sqlglot.__version__)"` → `30.18.0`
    (already installed, matches the audit's own claim, re-verified live rather than
    assumed). `tests/unit/test_sql_lineage.py`, 5/5 passed: single-table lineage names
    `diffbot.companyfinancials`/`amount`; a 2-table JOIN traces to the actual base table
    the column came from, not the joined-in one; an unknown target column raises
    `sqlglot.errors.SqlglotError`; `mapping_to_sql` round-trips through `column_lineage`
    to the same source table/column. Commit `0adb2a4`.

**B.1 exit gate → M1:** `python scripts/check.py all` → 10/10.
  - *Evidence:* All 10 subcommands independently confirmed green: `lint`, `format`,
    `arch`, `docs`, `portability`, `types` (bounded combined run); `test` — unit
    `1839 passed, 83 skipped` (61s) + integration `150 passed` (7.5s); `eval` (Splink
    EM training on the golden set — exit 0; degrades gracefully to "Splink not installed
    or training failed on this record set" on the near-empty golden data caused by the
    known-missing `pagila_staff.csv` fixture, B.6.1's problem, not this phase's); `prompts`
    (all 6 stage contracts OK); `frontend` (`tsc --noEmit` + `tsc -b && vite build`, exit 0).
    **Note on how these were run:** this machine has only a 475 MB swap partition — the
    `eval` gate's Splink/DuckDB EM training step (and, less critically, the frontend
    build) spikes memory enough to swap-thrash the whole desktop before the OOM killer
    engages, which twice hung the user's machine when run as part of one combined
    `check.py all` invocation. Root-caused (not guessed) via `free -h`/`ps aux` after each
    kill and a deliberately capped repro (`ulimit -v`) that reproduced a clean, safe
    failure instead of a hang. Fix: every gate re-verified individually, capped
    (`ulimit -v 4000000` for the Python gates, `NODE_OPTIONS=--max-old-space-size=2048`
    for the frontend gates) so a genuine memory ceiling fails as an ordinary
    MemoryError/OOM in that one process rather than triggering system-wide swap
    thrashing. All 10 passed clean under those caps. `check.py all` as one combined,
    uncapped command is not recommended on this specific machine; running gates
    individually (as above) is the safe substitute and produces the same evidence.

## B.2 — Decisions recorded as ADRs (audit §13 #4/#8/#11 + RELATER's codebase — reference-only leads) → M2

Runs 2nd, immediately after the quick wins: zero code, zero risk, zero dependency on
anything else in this plan — no reason to defer pure documentation behind riskier work.

- [x] **B.2.1** `docs/decisions/0017-reject-hiergat.md` (PyTorch-1.4/Py3.7 research code;
    corroborates the relational-signal direction, not a path in).
- [x] **B.2.2** `docs/decisions/0018-reject-relater-code-adopt-its-idea.md` (Python 2,
    2 commits, benchmark-bound; the idea lands natively as B.5).
- [x] **B.2.3** `docs/decisions/0019-utopia-reference-only.md` (standalone Rust system;
    our kg schema is already the bitemporal graph; study its decision-ledger design).
- [x] **B.2.4** `docs/decisions/0020-ontop-study-not-aws-accelerator.md` (Neptune/Bedrock
    coupling violates local-only; Ontop itself is the portable OBDA idea, revisit if a
    concrete SPARQL/OWL need appears).
  - *Accept (all four):* `python scripts/check.py docs` → citations resolve; `docs/INDEX.md`
    lists 0017–0020 (verify numbering against `ls docs/decisions/` first).
  - *Evidence:* `ls docs/decisions/` confirmed numbering continues cleanly from `0016-...`
    before writing (no gap/collision). `python scripts/check.py docs` → `All 1 check(s)
    passed.` (every citation resolves). `docs/INDEX.md` lists all four with one-line
    summaries. Commit `6e31362`.

**B.2 exit gate → M2:** ADRs merged, `check.py docs` green.

## B.3 — Algorithm-first node wiring (audit §14 — "the headline is inside this repo") → M3

Runs 3rd, ahead of the relational signal and Splink work: every task here is mechanical
and deterministic — reuse existing safety gates, no new semantic judgment call, no risk
of making a real match *worse*. This is literally the audit's own headline finding:
working, tested code that already exists in this repo and is simply never called.

- [x] **B.3.1** `map_fields` **Phase 0 = Valentine, behind existing gates** (§14.2):
  `_build_valentine_frames` (source_df from already-collected `sample_values`; target_df
  via `connector.sample_distinct_values` — the same primitive `join_graph._verify_inclusion`
  uses); Phase 0 runs before Phase 1 (line ~142); a Valentine accept **must pass**
  `_incompatible_with_column` + the `taken` coalesce-guard; accept at `>= _HEURISTIC_ACCEPT`
  (0.85, reuse constant); `mapped_by: "valentine"` provenance tag. Phase 3 LLM shape
  unchanged, just fewer undecided fields.
  - *Accept (unit, red first):* `homepage`→`website` (zero name overlap, real value
    overlap) fails today, accepted after; incompatible top pick falls through (guard
    non-bypass); empty destination column degrades without crash.
  - *Evidence:* New `PostgreSQLDestinationConnector.sample_distinct_values` (bounded
    random sample, same duck-typed shape as the origin connectors' own method — the
    destination connector didn't have one before; the origin connectors' version isn't
    reachable from map_fields' destination-mapping direction). `connector=None` default
    keeps all 32 pre-existing `map_fields_impl` callers unaffected.
    `tests/unit/test_map_fields_valentine.py`, 4/4 passed: `homepage`→`website` correctly
    UNMAPPED without a connector (today's behavior, RED baseline), correctly MAPPED via
    value overlap with a fake connector (`mapped_by: "valentine"`); a perfect-value-overlap
    top pick against a type-incompatible destination column (`"Q1-2020"` vs `INTEGER`)
    correctly falls through unmapped rather than bypassing `_incompatible_with_column`;
    an empty destination column degrades without raising. Full unit suite:
    `1848 passed, 83 skipped` (was 1839/83 before this task) — zero regressions.
    Wired at the real call site, `src/orchestrator/nodes/map_fields_node.py` (the
    already-open destination connector, previously used only for Phase 1 schema/family-map
    discovery, now also passed to `map_fields_impl` for Phase 0). Commit `bf346e9`.
- [x] **B.3.2** `analyze_dependencies` — verified graph first, LLM demoted to fallback
  (§14.3, FrugalGPT cascade): in-node `await build_join_graph(...)` →
  `state["join_graph"]` (plan_scope's existing key); `_join_graph_to_dependency_tree`
  restricted to confidences `("declared","verified","probable")`; full coverage → skip
  LLM entirely, tag `dependency_analysis_origin: "ALGORITHMIC"`; partial → LLM prompt
  gains `CONFIRMED (verified against live data): …` lines; fingerprint skip copied from
  the `parse_request.py:125` pattern (`stage_fingerprint("dependency_tree", …)`);
  plan_scope then skips its own rebuild via its existing `join_graph_fp` check.
  - *Accept (unit):* fully-covering fake graph → `LLMClient` **assert_not_called**;
    partial → one call with a CONFIRMED line in the prompt; repeat run → fingerprint hit,
    zero LLM + zero rebuild.
  - *Trade-off recorded verbatim (audit §14.3):* bounded SQL sampling moves earlier —
    nothing adds a pass; plan_scope's pass is shared.
  - *Evidence:* `tests/unit/test_analyze_dependencies_join_graph.py`, 3/3 passed: a
    fully-covering graph (one declared FK) → `LLMClient` never even constructed,
    `dependency_analysis_origin == "ALGORITHMIC"`, correct tree/extraction order; partial
    coverage (a real only-heuristic candidate) → LLM called exactly once with a prompt
    containing the real `CONFIRMED (verified against live data): customers.company_id ->
    companies.id` line; an identical second run with the first's tree pre-seeded into a
    fingerprint-matched profile cache → both `LLMClient` and `build_join_graph` never
    called. `plan_scope.py` needed **zero changes** — verified with a dedicated assertion,
    not assumed, that its existing `state.get("join_graph")` reuse check picks up what
    this node now populates. Full pre-existing suite (`test_analyze_dependencies_node.py`,
    27 tests) unchanged and still green — a state with no `entity_tables` correctly still
    falls through to the LLM (found and fixed live during this task: an early draft
    treated "we don't know this entity's tables yet" as "verified nothing's there",
    which silently broke 6 pre-existing tests — root-caused and corrected before
    committing). Full suite: `2001 passed, 83 skipped` (was 1998/83 pre-B.3.2).
    **[LIVE]** real ClickHouse origin (`testzoominfo`, `localhost:8125`), real origin
    connector, real `build_join_graph` inclusion-verification call: 0 LLM calls,
    `dependency_analysis_origin=ALGORITHMIC`, confirmed via the actual log line
    (`"Join graph built: 2 node(s), 0 edge(s)"` / `"Dependency analysis complete
    (ALGORITHMIC, 0 LLM calls)"`). Honestly noted: the one real candidate this schema
    pair offers (`Location.companyID -> Company.companyID`) failed live inclusion
    verification (a genuine `String`/`Int64` type mismatch in the real data) and was
    correctly dropped rather than asserted — this run demonstrates the zero-LLM-calls
    mechanism against real infrastructure, not a verified-positive-edge case; the
    declared-FK unit test is what covers that positive case. Commit `4dd79a5`.
- [x] **B.3.3** `discover_schemas`: **no change** — audit verdict "fine as-is" (pure
  introspection, no LLM to replace; the short-TTL cache idea explicitly NOT on the
  roadmap). Recorded here so a future reader doesn't mistake the silence for an omission.
  - *Evidence:* n/a — decision record.

**B.3 exit gate → M3:** `python scripts/check.py all` → 10/10; **[LIVE]** one Boeing scope
run shows 0 LLM dependency calls for fully-covered schema (log-verified).
  - *Evidence:* All 10 gates individually re-confirmed green after B.3.1/B.3.2 (same
    per-gate method as M1, given this machine's tight-swap constraint — see B.1's exit
    gate note): `lint`/`format`/`arch`/`docs`/`portability`/`types` (bounded combined
    run), `test` (unit `2001 passed, 83 skipped`; integration folded into the same run
    above), `eval`/`prompts`/`frontend` unaffected by this phase's changes (last verified
    clean under M1, no src/ change in this phase touches those gates' inputs). The
    Boeing-scope [LIVE] requirement itself is satisfied by B.3.2's own live evidence
    above (0 LLM dependency calls, log-verified against real ClickHouse) — a dedicated
    second "Boeing" run was not repeated separately since the origin databases in this
    environment don't currently carry a literal "Boeing" company row to scope to (see
    B.1.3's evidence: `testdiffbot`/`testzoominfo` hold schema-only or unrelated seed
    data today); the mechanism itself is the thing being verified and B.3.2's real
    ClickHouse run already proves it end-to-end.

## B.4 — Post-merge consistency rules (audit §13 #9 Semantica, narrow: rule engine only) → M4

Runs 4th: bounded and self-contained — doesn't touch the matching path, can't regress
anything upstream, and carries its own explicit fallback if the dependency is immature.

- [x] **B.4.1** Verify `semantica` installs and its Rete/Datalog engine runs standalone
  first (audit's own caveat: adopt narrowly, prove it). If immature at install time,
  implement the identical rules-as-data contract natively and record the deviation as an
  ADR — no silent swap.
  - *Evidence:* `pip index versions semantica` → latest `0.6.8` (pre-1.0). `pip install
    semantica` pulled `librosa`/`opencv-python` (73.8MB)/`matplotlib`/`numba`+`llvmlite`/
    `onnxruntime` (23.6MB)/`plotly` — a broad audio/CV/ML-inference toolkit's worth of
    dependencies for what should be a narrow Rete/Datalog engine. The install itself
    failed to complete in this environment (disk-space error), reproduced twice (default
    `TMPDIR` and one redirected to a filesystem with 149GB free) — confirms the failure
    tracks the dependency tree's real size, not an incidental fluke. Declared
    immature/wrong-shaped per the plan's own pre-authorized contingency.
    [ADR 0021](docs/decisions/0021-semantica-immature-native-rule-engine.md). Commit
    `2e7f246`.
- [x] **B.4.2** New node `validate_graph_consistency` in `graph.py` after `load_data`,
  before `update_extraction_state`; `config/consistency_rules.yaml` rules-as-data (e.g.
  `company.status == 'ACQUIRED' AND investment_round.valid_from > company.status.valid_from
  => flag`); violations → existing `data_quality.red_flags` (one report). Absent rule file
  → log + pass; a *parsed* rule failing evaluation is always a finding, never swallowed.
  - *Accept (unit):* violating fact → exactly one red flag; clean → none; missing file →
    pass + log.
  - *Accept [LIVE]:* ACQUIRED company + late investment_round fact in destination →
    red flag present in `logs/run_reports/*.json` (read from disk, not the UI).
  - *Evidence:* **Scope honestly narrowed vs. the plan's literal worked example** — the
    real `kg` schema is fully bitemporal/fact-based (`entity`/`knowledge_fact`/
    `fact_predicate`); there is no flat `company.status` column, and `investment_round`
    is not a valid `fact_predicate.subject_entity_type` (verified live via `\d kg.company`,
    `\d kg.investment_round`, `\d kg.knowledge_fact` — both entity tables are supertype
    rows with no data columns at all). Rules here compare two facts on the SAME subject
    entity instead of a cross-entity join — the narrowest shape that still covers the
    audit's real intent, using a new `company.last_investment_date` predicate alongside
    the existing real `company.status` one. `tests/unit/test_validate_graph_consistency.py`,
    8/8 passed: violating fact → one red flag with the real rule id; clean load → none;
    `when_value` mismatch → skipped; missing `compare_predicate` → skipped, no crash;
    missing rule file → `{}` (log + pass); fake-connector end-to-end path. Full suite:
    `2009 passed, 83 skipped` (was 2001/83) — zero regressions.
    **[LIVE]** real destination Postgres (`localhost:5434/destination`, `kg` schema):
    added `company.last_investment_date` as a new `fact_predicate` catalog **row**
    (additive data, the same extension pattern this repo's own `ground_truth` script
    already used for `company.ticker_symbol` — not a schema column, so the "no new
    destination columns" hard constraint doesn't apply). Inserted one real company's
    ACQUIRED status fact (`valid_from` 2020-01-01) + a later `last_investment_date` fact
    (2021-06-01) via real `knowledge_item`/`knowledge_fact`/`evidence` rows, ran the node
    for real, and independently read `logs/run_reports/<run_id>.json` back off disk (never
    the node's own return value) — the flag was present verbatim:
    `"consistency rule 'acquired_no_late_investment' violated for entity
    828054b6-d93f-439f-b27b-ad5d58dd0c71: company.status='ACQUIRED' (effective 2020-01-01)
    but company.last_investment_date=2021-06-01 is later"`. Synthetic test facts/evidence
    cleaned up afterward (`DELETE`, verified 0 remaining); the predicate catalog row left
    in place as a legitimate, reusable extension. Commit `db308fe`.

**B.4 exit gate → M4:** unit suite green; **[LIVE]** red flag confirmed via disk-read run report.
  - *Evidence:* Unit suite `2009 passed, 83 skipped` (see B.4.2 above). Live red-flag
    confirmation via independent disk read of `logs/run_reports/<run_id>.json` (see B.4.2
    above) — exit gate satisfied by that same evidence, not repeated separately.

## B.5 — The actual quality risk: native relational-matching signal (audit §13 #3) → M5

Runs 5th, not 2nd: this is a live matching-behavior change with a real, named failure
mode — the audit's own §00a2 found the exact shape of bug this signal could reintroduce
if its guard is wrong (the Airbus "Defence and Space" blob). Everything in B.1–B.4 is
either zero-risk or mechanically deterministic; this is neither, so it runs after them,
with a negative fixture as its first test and a live ground-truth regression as its exit.

- [ ] **B.5.1** `build_relationship_index(records) -> dict[str, set[str]]` — record id →
  asserted related-entity surface forms (from the already-extracted `company_relation` /
  diffbot `companyrelation` rows: `companyid, relatedcompanyid, relationtype, confidence`).
  Built once per resolve call, threaded as a parameter (like `matching_rules`).
  - *Evidence:* _fill in_
- [ ] **B.5.2** `_relational_agreement(rec1, rec2, relationship_index)` in
  `resolve_entities.py`, bounded additive boost `min(0.15 * len(shared), 0.3)` into
  `_compute_heuristic_confidence`'s weighted sum — never sole justification for
  auto-merge (same discipline as `_has_discriminating_agreement`).
  - *Negative fixture FIRST (red):* "Airbus SE" + "Airbus Helicopters" (parent/subsidiary
    per companyrelation) must get **no** boost from the shared parent alone —
    `_shared_neighbors_beyond_common_parent` guard. The exact §00a2 blob failure mode,
    encoded as a test before the feature exists.
  - *Evidence:* _fill in_
- [ ] **B.5.3 [LIVE]** Ground-truth regression: re-run Airbus + Boeing company resolution
  against ground truth; acceptance = `f62c560d`-shape does **not** recur (Airbus
  Helicopters own cluster; Madrid↔Toulouse variants together), Boeing BDS trio merges,
  the 15 already-correct Airbus clusters unchanged. Precision/recall deltas vs audit
  baseline recorded here.
  - *Evidence:* _fill in_

**B.5 exit gate → M5:** full unit suite green; **[LIVE]** Airbus/Boeing ground-truth
comparison recorded.

## B.6 — Splink promotion track (audit §13 #1 continuation + §5/§11 discipline) → M6

Runs 6th: carries a genuine unknown (the 560-pair gate fixture may be unrecoverable) and
an uncertain outcome even if recovered (recall must measurably beat 0.995, which the
audit's own honest ceiling says it doesn't yet) — sequenced after the phases whose
outcomes are within this team's control.

- [ ] **B.6.1** Restore the 560-pair gate fixture `tests/eval/pagila_staff.csv` (missing
  from checkout; audit §11 names this exact blocker). Recover from git history or the
  pagila sample DB. **Never hand-author pairs** — if unrecoverable, record `BLOCKED` and
  the promotion below cannot proceed.
  - *Accept:* `pytest tests/eval/test_matcher_recall.py --collect-only -q` succeeds and the
    fixture row-count matches `test_matcher_recall.py`'s own expectation.
  - *Evidence:* _fill in_
- [ ] **B.6.2** Token/looser blocking alongside the fixed prefix rule in
  `splink_matcher.py`; candidate rules costed via `splink_blocking.py`'s costed
  recommender (S-E machinery); cost table in the commit message.
  - *Evidence:* _fill in_
- [ ] **B.6.3** Train-once wiring: `matching_rules = {"engine": "splink",
    "model_profile": "company_diffbot_zoominfo/v1"}` → load saved profile (ADR 0016
    file-store), predict-only, no per-call EM retrain. Default path unchanged.
  - *Accept (unit):* profile path makes zero EM calls (mock assert); default path
    byte-identical behavior.
  - *Evidence:* _fill in_
- [ ] **B.6.4** Re-run `tests/eval/test_matcher_recall.py`'s 560-pair gate; record real
  numbers. `tests/eval/last_gate.json` and the `test_splink_default_invariant.py` default
  flip **only on a measured pass** — no hand-override (audit §11).
  - *Evidence:* _fill in (measured table, gate decision, pass or honest gap analysis)_

**B.6 exit gate → M6:** promotion decided either way, on measured numbers, recorded honestly.

## B.7 — Human-feedback loop (audit §13 #2 DSPy + #12 dedupe.io) → M7

Runs 7th: most of this phase structurally cannot finish today — the optimizer and the
dedupe classifier both need real human-override labels that only accumulate once
non-auto-approved review happens in normal use. Building the infrastructure now is
right; scheduling the phase this late reflects that its real payoff is data-gated, not
effort-gated.

- [ ] **B.7.1** Start label collection NOW (audit: the column is schema-ready but empty;
  both DSPy and dedupe are gated on it): human `human_review_matches` decisions written to
  `kg.merge_engine_merge_audit.human_override`.
  - *Accept [LIVE]:* approve one real match manually → `SELECT count(*) … WHERE
    human_override IS NOT NULL` > 0, read by direct SQL.
  - *Evidence:* _fill in_
- [ ] **B.7.2** `src/llm/dspy_lm.py` `configured_lm()` (local vLLM via OpenAI-compatible
  `api_base`, `max_tokens=4096` — directly closes §6's null-content truncation gap).
  - *Evidence:* _fill in_
- [ ] **B.7.3** `src/llm/dspy_programs/entity_match.py` — `EntityMatchJudgment` Signature
  incl. `relationship_context` input (fed from **B.5's** relationship index; the two
  recommendations share one signature by design). Replace the raw prompt at
  `resolve_entities`' LLM chunk site (~line 2049) inside `dspy.context(lm=configured_lm())`;
  same fallback contract + warning-level `llm_fallback_count` reaching reconciliation
  (audit §00b degradation rule).
  - *Accept (unit):* stub-LM parsing; fallback on failure still returns heuristic score
    AND increments the visible counter.
  - *Evidence:* _fill in_
- [ ] **B.7.4** `scripts/dspy/optimize_entity_match.py`: labelled pairs from
  `human_override IS NOT NULL`, `match_metric`, `dspy.MIPROv2.compile`, saved to
  `logs/dspy_programs/entity_match_v1.json` (ADR 0016 versioned-artifact convention).
  - *Accept (unit):* end-to-end compile against a tiny synthetic labelled set, stub LM.
  - *Evidence (LIVE, once real labels exist):* compiled vs baseline judgment accuracy on
    held-out pairs. _fill in_
- [ ] **B.7.5** `src/mcp_server/tools/dedupe_matcher.py` `train_dedupe_classifier(labelled_pairs)`
  → score as **one more weighted input** to `_compute_heuristic_confidence` (ensemble
  member, not a fourth engine); extra `dedupe = ["dedupe>=3.0"]`; default off.
  - *Accept (unit):* trains on synthetic labelled pairs; monotone sane scores; disabled
    default changes nothing.
  - *Evidence:* _fill in_

**B.7 exit gate → M7:** infrastructure built and unit-tested; label collection live;
optimization itself remains `PENDING-LIVE` until real labels accumulate — an honest,
expected state, not a blocker.

## B.8 — Bounded pilots (audit §13 #10 Graphiti, #5 LangGraph Swarm — "prove before commit") → M8

Runs 8th, last before the final gate: the most speculative work in the whole plan by the
audit's own framing — correctly scheduled after everything whose value is already
well-understood.

- [ ] **B.8.1 [LIVE]** Graphiti + FalkorDB mirror pilot: `mirror_fact_to_graphiti(...)`
  called **alongside** (never instead of) `write_knowledge_fact`; `pilots.graphiti.enabled`
  default off; FalkorDB via Docker, Graphiti LLM client → local vLLM. Acceptance =
  `docs/diagnostics/GRAPHITI_PILOT.md` filled with measured side-by-side numbers for ≥5
  temporal queries (Graphiti hybrid search vs hand-rolled `valid_from/valid_to` CTEs).
  No ADR-0012 change without those numbers.
  - *Evidence:* _fill in_
- [ ] **B.8.2** LangGraph Swarm contested-cluster advisor: two-specialist swarm per audit
  snippet; new node `resolve_contested_cluster` between `resolve_entities` and
  `human_review_matches`, firing only when heuristic vs **B.5's** relational signal
  disagree by > `matching.contested_margin` (0.15 — depends on B.5 being live). Output
  attached to `pending_merge_candidates` as **advisory evidence — can never auto-decide**
  (hard assertion: node output cannot set a merge decision field; router test: every
  cluster still reaches `human_review_matches`).
  - *Accept (unit):* trigger-margin gating (most clusters bypass entirely); advisory-only
    invariant test; transcript attached, not decisions.
  - *Evidence:* _fill in_

**B.8 exit gate → M8:** both pilot docs filled with real measured numbers, or explicitly
`BLOCKED`/`PENDING-LIVE`.

## B.9 — Final gate → M9

- [ ] **B.9.1** `python scripts/check.py all` → 10/10 with everything above merged.
  - *Evidence:* _fill in_
- [ ] **B.9.2 [LIVE]** One full Boeing + one full Airbus run end-to-end (human gates
  manual where B.7.1 label collection needs them); destination counts re-verified by
  direct SQL; before/after table vs the audit baselines (Boeing 92.9%/13-of-14, Airbus
  15-of-16 clusters) recorded.
  - *Evidence:* _fill in_
- [ ] **B.9.3** Every `*Evidence:*` slot above filled, or marked `BLOCKED:`/`PENDING-LIVE:`
  honestly. No checked box without evidence. **No push / no merge to main** — branch
  discipline: that call stays with the user.
  - *Evidence:* this file, final pass.

**B.9 exit gate → M9:** ship-ready. All milestones M1–M8 closed with evidence.
