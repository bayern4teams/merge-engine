> **Scope override for this branch (explicit user instruction, given after Part A's v2 tag):**
> continue implementing straight through **BP-B1** and **BP-B2** without stopping — do not treat
> BP-B1's "stop for review" as a pause point, and do not commit to `main` at BP-B2 even though
> the text below says to. Everything through **BP-B3** stays on this branch only. Stop at
> BP-B3, live-verify it, and report back — same pattern as BP-A2 — before touching `main` again.
> B.4 (the Splink phases, BP-B4 and beyond) is out of scope for this instruction.

## B.0 Branch and worktree

```bash
git branch feat/v3-discovery-er main
git worktree add .worktrees/v3-discovery-er feat/v3-discovery-er
```

All Part B work happens **only** in `.worktrees/v3-discovery-er`. Copy this plan in as
`Branch_Plan.md` at that worktree's root and keep its own `TODO.md` live, per CLAUDE.md branch
discipline.

## B.1 Tool decisions — research complete, do not repeat it

Every row below was verified: Splink by **introspecting the installed 4.0.16 package**, the
rest against official documentation. `docs/tool-evaluation.md` and the ADRs are written *from*
this table, not re-derived.

### Adopted

**Splink 4.0.16 — already installed, runs 100% locally.** It is a pure Python library that
compiles its model to SQL and executes it on a local backend; we use **DuckDB in-process**
(`duckdb 1.5.5`, already a dependency). No server, no cloud, no network. Verified API surface:

| Spec need (Splink.txt §) | Verified API |
|---|---|
| §11 field profiling, §16 missingness | `splink.exploratory.profile_columns(table_or_tables, db_api, column_expressions, top_n, bottom_n)`, `completeness_chart(...)` |
| §13–15 comparisons, levels, fuzzy thresholds | `splink.comparison_library`: `ExactMatch`, `NameComparison`, `ForenameSurnameComparison`, `EmailComparison`, `DateOfBirthComparison`, `PostcodeComparison`, `LevenshteinAtThresholds`, `JaroWinklerAtThresholds`, `JaroAtThresholds`, `DamerauLevenshteinAtThresholds`, `JaccardAtThresholds`, `ArrayIntersectAtSizes`, `AbsoluteDateDifferenceAtThresholds`, `AbsoluteTimeDifferenceAtThresholds`, `DistanceInKMAtThresholds`, `CosineSimilarityAtThresholds`, `DistanceFunctionAtThresholds`, `PairwiseStringDistanceFunctionAtThresholds`, `CustomComparison`. `splink.comparison_level_library`: `NullLevel`, `ExactMatchLevel`, `JaroWinklerLevel`, `LevenshteinLevel`, `DamerauLevenshteinLevel`, `JaccardLevel`, `JaroLevel`, `ArrayIntersectLevel`, `ArraySubsetLevel`, `ColumnsReversedLevel`, `AbsoluteDifferenceLevel`, `AbsoluteDateDifferenceLevel`, `PercentageDifferenceLevel`, `DistanceFunctionLevel`, `DistanceInKMLevel`, `CosineSimilarityLevel`, `LiteralMatchLevel`, `CustomLevel`, `ElseLevel`, `And`, `Or`, `Not` |
| §15 similarity distributions | `splink.exploratory.comparator_score_df/comparator_score_chart/comparator_score_threshold_chart`, `phonetic_transform_df`, `phonetic_match_chart` |
| §17 term frequency | `Linker.table_management.compute_tf_table(column_name)`, `register_term_frequency_lookup(...)`, `Linker.visualisations.tf_adjustment_chart(output_column_name, n_most_freq, n_least_freq)` |
| §18–19 prediction blocking + analysis | `splink.blocking_analysis.count_comparisons_from_blocking_rule(...)`, `cumulative_comparisons_to_be_scored_from_blocking_rules_data(...)` and `..._chart(...)`, `n_largest_blocks(...)`; `splink.blocking_rule_library.block_on(*cols, salting_partitions, arrays_to_explode)`, `And`/`Or`/`Not`/`CustomRule` |
| §20 training blocking | same counting functions applied to training rules — the API is shared, the *separation* is ours to enforce |
| §22 u estimation | `Linker.training.estimate_u_using_random_sampling(max_pairs=1e6, seed=None)` |
| §23/26 m estimation, multiple EM passes | `Linker.training.estimate_parameters_using_expectation_maximisation(blocking_rule, estimate_without_term_frequencies, fix_probability_two_random_records_match, ...)`; also `estimate_m_from_pairwise_labels(...)`, `estimate_m_from_label_column(...)` |
| §28 prior | `Linker.training.estimate_probability_two_random_records_match(deterministic_matching_rules, recall, max_rows_limit)` |
| §24/27 training UI + diagnostics | `splink.em_training_session` API; `Linker.visualisations.parameter_estimate_comparisons_chart(include_m, include_u)`, `m_u_parameters_chart()`, `match_weights_chart()` |
| §29 parameter viewer, §46 reproducibility | `Linker.misc.save_model_to_json(out_path, overwrite)` — returns the complete settings dict incl. m/u/prior; `Linker.misc.query_sql(...)` |
| §30–31 comparison vector + waterfall | `Linker.inference.predict(threshold_match_probability, threshold_match_weight, ...)`, `compare_two_records(record_1, record_2, include_found_by_blocking_rules)`, `Linker.visualisations.waterfall_chart(records, filter_nulls, remove_sensitive_data)`, `comparison_viewer_dashboard(df_predict, out_path, num_example_rows, minimum_comparison_vector_count)`, `match_weights_histogram(df_predict, target_bins)` |
| §32–36 labelling | `Linker.table_management.register_labels_table(input_data)`, `Linker.evaluation.labelling_tool_for_specific_record(...)` |
| §37–38 threshold optimisation | `Linker.evaluation.accuracy_analysis_from_labels_table(labels, threshold_match_probability, output_type=…)` and `..._from_labels_column(...)` — output types cover threshold-selection, ROC and precision-recall |
| §41–42 FP/FN explorers | `Linker.evaluation.prediction_errors_from_labels_table(labels, include_false_positives, include_false_negatives, threshold_match_probability)` and `..._from_labels_column(...)`; `unlinkables_chart(x_col)` |
| §47–48 clustering + inspection | `Linker.clustering.cluster_pairwise_predictions_at_threshold(df_predict, threshold_match_probability)`, `cluster_using_single_best_links(df_predict, duplicate_free_datasets, ...)`, `compute_graph_metrics(df_predict, df_clustered)`; standalone `splink.clustering.cluster_pairwise_predictions_at_threshold(nodes, edges, db_api, ...)`; `Linker.visualisations.cluster_studio_dashboard(...)` |

Note the two clustering entry points: the standalone `splink.clustering` function takes
`nodes`/`edges` directly, which is how we cluster **our** pairwise scores with Splink's
connected-components implementation without adopting its scorer — useful for the A/B gate.

**Valentine** (`pip install valentine`, delftdata/valentine) — schema matching first pass.
Algorithms: `Coma`, `Cupid`, `DistributionBased`, `JaccardDistanceMatcher`,
`SimilarityFlooding`. Input is pandas/polars DataFrames, so it runs on our **sampled** rows,
never full tables. Ships evaluation metrics against ground truth, which we point at our own
golden mapping set.

**Desbordante** (`pip install desbordante`, **AGPL-3.0**, Python ≥ 3.10) — deep profiling on
sampled extracts only: exact/approximate **inclusion dependencies**, **unique column
combinations** (candidate keys, incl. composite), **functional dependencies**, denial
constraints. pandas input, in-memory, C++ core. **The AGPL is the reason it is optional and
isolated**: it lives behind `src/mcp_server/tools/deep_profile.py` guarded by an extras
install, is never imported at module scope, and the ADR records the licensing constraint.

**datasketch `MinHashLSHEnsemble`** — containment (not Jaccard) queries; the scalable path for
"is column A's value set contained in column B's" across many candidate pairs. Optional
accelerator; the SQL inclusion check from V2 stays the primary, exact path.

**Great Expectations 1.x** — optional, `EphemeralDataContext` + pandas backend over our
already-sampled rows, so **no project directory, no infrastructure**. Used for the format-level
expectations we do not have (email/phone/regex/date/value-set), with results persisted as our
own rows. Our `data_quality.py` stays the pipeline gate — GX never becomes the owner of the
metadata graph or relationship discovery (PreSplink §60).

**networkx** (BSD, in-process) for graph traversal; PostgreSQL tables for graph storage.
**sqlglot 30.16.0** (already installed) for SQL parsing / column-level lineage.
**Jailer**'s subject + association + restriction model, already adopted in V2.

### Rejected — with reasons (PreSplink §5, §6, §29, §74)

**OpenMetadata — rejected.** A Docker deployment needs, at minimum: the OpenMetadata server,
a MySQL/PostgreSQL metadata database, **Elasticsearch or OpenSearch**, and the Airflow
container if you want UI-managed ingestion. The `openmetadata-ingestion` Python package is a
*client* — it discovers metadata and **pushes it to a running server**; it is not a library you
can consume standalone for its profiling algorithms. So the cost is four new services and a
sync contract, and the benefit is a catalogue UI we would still have to wrap because our domain
model (entity anchors, join paths, context scores, matching datasets) has no OpenMetadata
equivalent. It solves cataloguing, which is not our bottleneck.

**DataHub — rejected, same shape, worse footprint.** Quickstart brings GMS, the React frontend,
**Elasticsearch**, **MySQL**, **Kafka** + ZooKeeper + Schema Registry; documented minimum
**8 GB RAM / 2 cores** for dev, 16 GB+ for production. `acryl-datahub`'s REST and Kafka emitters
both require that server (or a Kafka broker) to be reachable. Strongest metadata model of the
two — and we **adopt the model**: source → dataset → column entities, edges carrying
`confidence`/`origin`/`review_status`, column-level lineage. We store it in PostgreSQL behind
`src/mcp_server/tools/metadata_graph.py`, so nothing couples us to one graph implementation
(PreSplink §29) and we can adopt either platform later by writing an emitter.

**Neo4j — rejected.** Another server for a graph that is thousands of nodes, not millions.
PostgreSQL + networkx covers every traversal in PreSplink §32/§65.

**Informatica** — capability benchmark only, per PreSplink §8. Never a dependency.

> **BP-B1** — write `docs/tool-evaluation.md` (the full capability matrix PreSplink §4/§76
> demands) and `docs/adr/ADR-001..011` from this section. **Stop for review.** No integration
> code before the ADRs exist.

## B.2 How V3 relates to the existing pipeline

Per your call: new subsystem, shared connectors, **replace where the advantage is decisive**.

| Subsystem | Decision | Why |
|---|---|---|
| Connectors (`src/mcp_server/connectors/`) | **Reuse unchanged** | Already expose sampling, filtered distinct values, keyset pagination, FK introspection. V3 adds only the missing capability methods in P-2 |
| Profiling | **New, database-native** | We have almost none; SQL-native (`uniqExact`, quantiles, `topK`) beats pulling rows |
| Candidate/composite keys | **New** (Desbordante optional deep pass) | Nothing exists today |
| Relationship discovery | **Hybrid — `join_graph.py` stays primary** | It pushes work into SQL and survives 43M rows; Desbordante/datasketch are sampled accelerators feeding it more candidates |
| Schema matching | **Hybrid — Valentine first, LLM last** | Directly implements "algorithm-first, LLM optional" and cuts mapping tokens |
| Data quality | **Hybrid — ours is the gate, GX adds expectations** | `validate_data` already blocks the run correctly; GX adds format checks |
| Metadata catalog | **New, PostgreSQL** | Both platforms rejected above |
| **Matcher** | **REPLACE with Splink**, builtin kept as fallback engine | Trained m/u instead of hand-tuned weights; TF adjustments; real blocking pair-counts; waterfall explainability; threshold optimisation from labels; cluster graph metrics. All of it otherwise hand-built. Gated on the 560 golden pairs — Splink becomes default **only** when it beats the builtin on precision **and** recall, measured, not assumed |
| **Candidate generation** | **REPLACE budget truncation with blocking analysis** | `count_comparisons_from_blocking_rule` + `n_largest_blocks` cost a rule *before* generating pairs, which removes the 2.4M-uncompared-pairs failure by construction |
| Clustering | **Replace with Splink's**, keep ours behind the engine flag | `compute_graph_metrics` gives cluster-quality diagnostics we have no equivalent for |
| Orchestration (LangGraph) | **Reuse** | V3 engines are called from nodes; the graph shape is unchanged except where phases say so |

Everything replaced keeps its predecessor reachable behind the per-entity `engine` selector, so
a regression is a config change, not a revert.

## B.3 Pre-Splink phases

Each phase: implement → unit tests → live check against the real ClickHouse/PostgreSQL boxes →
update `TODO.md` with independent evidence.

### P-1 — Domain model & metadata store (PreSplink §15, §28, §29, §40, §73)

- `src/mcp_server/tools/metadata_graph.py` + PostgreSQL DDL: `md_source`, `md_database`,
  `md_schema`, `md_table`, `md_column`, `md_profile`, `md_candidate_key`, `md_relationship`,
  `md_canonical_field`, `md_field_mapping`, `md_edge` — every row carrying `confidence`,
  `evidence JSONB`, `algorithm_version`, `origin` (`ALGORITHMIC|OSS_TOOL|LLM_ASSISTED|USER_CONFIRMED`),
  `review_status` (`AUTO_ACCEPTED|AUTO_REJECTED|NEEDS_REVIEW|USER_ACCEPTED|USER_REJECTED`).
- Stable internal ids for every object; **never** key on table name alone (§15).
- Discovery-run model (§40): `md_run(kind, status, started_at, finished_at, counts, warnings,
  errors, configuration, algorithm_versions)` with statuses
  `QUEUED|RUNNING|COMPLETED|FAILED|CANCELLED|PARTIAL`.
- Human overrides are **additive** (§39): `md_override` rows recording original decision, human
  decision, user, timestamp, reason. The algorithmic row is never mutated.

### P-2 — Connector capability matrix (PreSplink §12, §13, §14)

Extend the existing connector base with `capabilities() -> dict` covering
`supports_declared_pk/fk/unique_constraints/statistics/sampling/explain/cte/temp_tables/
window_functions/pushdown/parallel_queries/column_comments/table_comments`, plus
`discover_indexes()`, `discover_statistics()`, `profile_column()`, `estimate_query_cost()`.
ClickHouse and MongoDB report `supports_declared_fk: False` — that fact stops being a surprise
and starts being a routing input. Read-only enforcement, SSL, connection/query timeouts and
credential redaction per §14/§71.

### P-3 — Profiling engine (PreSplink §16, §46)

Database-native, three modes **FULL / SAMPLED / APPROXIMATE**, never a full scan by default.
ClickHouse: `uniqExact`/`uniq`, `topK`, `quantiles`, `count(col)`. PostgreSQL: `pg_stats`
first, sampled SQL second. Metrics per §16 (null count/ratio, distinct count/ratio, unique
ratio, min/max, length distribution, top values, pattern distribution, type distribution).
Every stored profile records **which mode produced it** and the UI shows Exact/Approximate/
Sampled explicitly (§46).

### P-4 — Semantic type inference (PreSplink §17)

Evidence-carrying inference over the type set in §17, from column name + datatype + regex +
format + length + uniqueness + frequency + samples. Every inference stores its signals and a
confidence. **No LLM in the first pass**; the LLM only proposes labels for `UNKNOWN` columns
and its output is tagged `LLM_ASSISTED` and overridable (§26).

### P-5 — Candidate & composite keys (PreSplink §18, §19)

Declared PK/unique first. Then single-column inference from measured uniqueness + null ratio.
Then **pruned** composite search (identifier-like, low-null, complementary columns only — never
brute force). Optional Desbordante UCC pass on sampled extracts for exact/approximate UCCs.
Types recorded as `DECLARED_PRIMARY_KEY|DECLARED_UNIQUE|INFERRED_SINGLE_COLUMN|INFERRED_COMPOSITE`
— an inferred candidate is never called a PK (§18).

### P-6 — Relationship discovery & scoring (PreSplink §20–24, §62)

Extends V2's `join_graph.py` rather than replacing it. Ten signals per §21 (datatype, semantic
type, name similarity, value overlap, inclusion coverage, target uniqueness, cardinality, null
behaviour, table semantics, identifier likelihood), each stored as evidence with the
`coverage(A→B) = |distinct(A) ∩ distinct(B)| / |distinct(A)|` formula the doc specifies.
datasketch `MinHashLSHEnsemble` prunes candidates before the exact SQL check. Cardinality
inference (§24) and the relationship types/review states of §23. **No opaque score** (§22): the
UI shows each contributing signal.

> **BP-B2** — live check on both ClickHouse origins: every relationship the V2 join graph found
> is still found, `Company↔Experience↔Person↔PersonContact` is present with measured coverage,
> and no relationship is asserted that hand-written SQL cannot confirm. 🚩 **Commit to main**
> if clean — P-1..P-6 are additive and cannot regress the v2 pipeline.

### P-7 — Schema matching & canonical schema (PreSplink §25, §27)

Valentine (`Coma` + `DistributionBased` + `JaccardDistanceMatcher`) over sampled frames produces
ranked candidates; the deterministic exact/heuristic pass from `map_fields.py` merges in; the
LLM sees only what remains ambiguous. Every mapping stores `source_column`, `canonical_field`,
`confidence`, `evidence`, `origin`. Canonical entity types per §27, source-independent.

### P-8 — Metadata graph + graph UI (PreSplink §28, §50, §63)

networkx over the P-1 tables; strict separation of **metadata graph** (tables/columns) from
**entity graph** (Airbus, John Smith) per §63. Interactive graph UI with zoom/pan/search/focus/
expand/collapse/depth/confidence/type/source filters; click node → metadata, click edge →
evidence.

### P-9 — Entity anchors, subgraph, coverage, extraction (PreSplink §30–37, §64, §65)

Anchor discovery returns ranked candidates with evidence and **never auto-merges on name
equality** (§31). Traversal policy: `max_depth`, `max_nodes`, `max_edges`, `max_records`,
`min_edge_confidence`, `allowed/blocked_edge_types`, source restrictions, cycle prevention,
per-path provenance (§65). Context score per extracted record (§33). Entity coverage matrix,
table-level **and** field-level (§34). Extraction pushes filters to the source (§35), with a
query planner using `EXPLAIN` where available and a **preview before any expensive run** (§55,
§56). This is where V2's `plan_scope`/`extract_data` get replaced by the general engine — behind
a flag, with the Airbus/Boeing baselines as the regression gate.

### P-10 — Canonical matching dataset (PreSplink §38, §66, §67)

The Splink handoff artefact: `record_id`, `source_id`, `entity_type`, `entity_anchor_id`,
`canonical_entity_id`, canonical fields, original fields, source table, source record id,
context score, graph path, provenance, discovery evidence, quality diagnostics. Materialised in
DuckDB (the existing spool) and registered directly as a Splink input — no copy.

> **BP-B3** — the handoff contract is provable: for a given Airbus run, every canonical record
> answers "where did this come from?" by SQL alone. 🚩 **Commit to main.**

