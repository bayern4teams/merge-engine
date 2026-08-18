# V3 tool evaluation

Capability matrix for the tools Part B (the V3 discovery/entity-resolution engine, see
[Branch_Plan.md](../Branch_Plan.md)) adopts, rejects, or benchmarks against. Every row was
verified before this document was written, not derived from it: Splink by introspecting the
**installed 4.0.16 package** directly, everything else against official documentation. The
ADRs in [decisions/](decisions/) each cover one row below in decision-record form; this file is
the reference table they point back to.

## Adopted

### Splink 4.0.16 — matcher (replaces the builtin engine, kept as fallback)

Already an installed dependency (`splink>=4.0.0` in `pyproject.toml`'s `splink` extra). Pure
Python, compiles its model to SQL, executes on **DuckDB in-process** (`duckdb`, already a
dependency) — no server, no cloud, no network call. Verified API surface, organized by the
capability it backs:

| Capability | Verified API |
|---|---|
| Field profiling, missingness | `splink.exploratory.profile_columns(table_or_tables, db_api, column_expressions, top_n, bottom_n)`, `completeness_chart(...)` |
| Comparisons, levels, fuzzy thresholds | `splink.comparison_library`: `ExactMatch`, `NameComparison`, `ForenameSurnameComparison`, `EmailComparison`, `DateOfBirthComparison`, `PostcodeComparison`, `LevenshteinAtThresholds`, `JaroWinklerAtThresholds`, `JaroAtThresholds`, `DamerauLevenshteinAtThresholds`, `JaccardAtThresholds`, `ArrayIntersectAtSizes`, `AbsoluteDateDifferenceAtThresholds`, `AbsoluteTimeDifferenceAtThresholds`, `DistanceInKMAtThresholds`, `CosineSimilarityAtThresholds`, `DistanceFunctionAtThresholds`, `PairwiseStringDistanceFunctionAtThresholds`, `CustomComparison`. `splink.comparison_level_library`: `NullLevel`, `ExactMatchLevel`, `JaroWinklerLevel`, `LevenshteinLevel`, `DamerauLevenshteinLevel`, `JaccardLevel`, `JaroLevel`, `ArrayIntersectLevel`, `ArraySubsetLevel`, `ColumnsReversedLevel`, `AbsoluteDifferenceLevel`, `AbsoluteDateDifferenceLevel`, `PercentageDifferenceLevel`, `DistanceFunctionLevel`, `DistanceInKMLevel`, `CosineSimilarityLevel`, `LiteralMatchLevel`, `CustomLevel`, `ElseLevel`, `And`, `Or`, `Not` |
| Similarity distributions | `splink.exploratory.comparator_score_df` / `comparator_score_chart` / `comparator_score_threshold_chart`, `phonetic_transform_df`, `phonetic_match_chart` |
| Term frequency | `Linker.table_management.compute_tf_table(column_name)`, `register_term_frequency_lookup(...)`, `Linker.visualisations.tf_adjustment_chart(output_column_name, n_most_freq, n_least_freq)` |
| Prediction blocking + cost analysis | `splink.blocking_analysis.count_comparisons_from_blocking_rule(...)`, `cumulative_comparisons_to_be_scored_from_blocking_rules_data(...)` and `..._chart(...)`, `n_largest_blocks(...)`; `splink.blocking_rule_library.block_on(*cols, salting_partitions, arrays_to_explode)`, `And`/`Or`/`Not`/`CustomRule` |
| Training blocking | same counting functions applied to training rules — the API is shared, the *separation from prediction blocking* is ours to enforce |
| u estimation | `Linker.training.estimate_u_using_random_sampling(max_pairs=1e6, seed=None)` |
| m estimation, multiple EM passes | `Linker.training.estimate_parameters_using_expectation_maximisation(blocking_rule, estimate_without_term_frequencies, fix_probability_two_random_records_match, ...)`; also `estimate_m_from_pairwise_labels(...)`, `estimate_m_from_label_column(...)` |
| Match prior | `Linker.training.estimate_probability_two_random_records_match(deterministic_matching_rules, recall, max_rows_limit)` |
| Training UI + diagnostics | `splink.em_training_session` API; `Linker.visualisations.parameter_estimate_comparisons_chart(include_m, include_u)`, `m_u_parameters_chart()`, `match_weights_chart()` |
| Parameter viewer, reproducibility | `Linker.misc.save_model_to_json(out_path, overwrite)` — returns the complete settings dict incl. m/u/prior; `Linker.misc.query_sql(...)` |
| Comparison vector + waterfall explainability | `Linker.inference.predict(threshold_match_probability, threshold_match_weight, ...)`, `compare_two_records(record_1, record_2, include_found_by_blocking_rules)`, `Linker.visualisations.waterfall_chart(records, filter_nulls, remove_sensitive_data)`, `comparison_viewer_dashboard(df_predict, out_path, num_example_rows, minimum_comparison_vector_count)`, `match_weights_histogram(df_predict, target_bins)` |
| Labelling | `Linker.table_management.register_labels_table(input_data)`, `Linker.evaluation.labelling_tool_for_specific_record(...)` |
| Threshold optimisation | `Linker.evaluation.accuracy_analysis_from_labels_table(labels, threshold_match_probability, output_type=...)` and `..._from_labels_column(...)` — output types cover threshold-selection, ROC and precision-recall |
| FP/FN explorers | `Linker.evaluation.prediction_errors_from_labels_table(labels, include_false_positives, include_false_negatives, threshold_match_probability)` and `..._from_labels_column(...)`; `unlinkables_chart(x_col)` |
| Clustering + inspection | `Linker.clustering.cluster_pairwise_predictions_at_threshold(df_predict, threshold_match_probability)`, `cluster_using_single_best_links(df_predict, duplicate_free_datasets, ...)`, `compute_graph_metrics(df_predict, df_clustered)`; standalone `splink.clustering.cluster_pairwise_predictions_at_threshold(nodes, edges, db_api, ...)`; `Linker.visualisations.cluster_studio_dashboard(...)` |

Two clustering entry points matter: the standalone `splink.clustering` function takes
`nodes`/`edges` directly, which lets the A/B gate (BP-B4) cluster **our own** pairwise scores
with Splink's connected-components implementation, without adopting Splink's scorer to do it.

Every phase in `Branch_Plan.md`'s B.4 verifies its call against the installed version at
runtime (`splink.__version__`, `installed_minor_version`) and refuses to run against an
unexpected major — this table describes 4.0.16's surface, not a guarantee about any other
version.

### Valentine — schema matching, first pass

`pip install valentine` (delftdata/valentine). Algorithms: `Coma`, `Cupid`,
`DistributionBased`, `JaccardDistanceMatcher`, `SimilarityFlooding`. Input is pandas/polars
DataFrames, so it runs on **sampled** rows, never full tables. Ships evaluation metrics against
ground truth, which points at this project's own golden mapping set. Used first in P-7's
schema-matching pass; the deterministic exact/heuristic pass already in `map_fields.py` merges
in, and the LLM sees only what remains ambiguous after both.

### Desbordante — deep profiling, optional and isolated

`pip install desbordante` (Python >= 3.10). **License: AGPL-3.0.** Deep profiling on sampled
extracts only: exact/approximate inclusion dependencies, unique column combinations (candidate
keys, including composite), functional dependencies, denial constraints. pandas input,
in-memory, C++ core.

The AGPL is the reason this is optional and isolated, not a base dependency: it must live
behind an extras install, never be imported at module scope (so a default install of this
project never touches AGPL code), and its own ADR records the licensing constraint explicitly.

### datasketch `MinHashLSHEnsemble` — containment queries, optional accelerator

Answers "is column A's value set contained in column B's" (containment, not Jaccard) across
many candidate pairs at once. Used in P-6 to prune relationship candidates before the exact SQL
inclusion check runs — the SQL check (already built in `join_graph.py`) stays the primary,
exact path; this only reduces how many candidates reach it.

### Great Expectations 1.x — format-level data quality, optional

`EphemeralDataContext` + pandas backend over already-sampled rows: no project directory, no
infrastructure. Used for the format-level expectations this project does not have today
(email/phone/regex/date/value-set), with results persisted as this project's own rows.
`data_quality.py` stays the pipeline's actual gate — Great Expectations never becomes the owner
of the metadata graph or relationship discovery; it only adds checks that gate already didn't
have.

### networkx + sqlglot + the Jailer model — graph, lineage, extraction

`networkx` (BSD, in-process) for graph traversal over the metadata tables P-1 creates;
PostgreSQL for graph storage (not a graph database — see the Neo4j rejection below). `sqlglot`
(already an installed dependency) for SQL parsing and column-level lineage. Jailer's
subject + association + restriction model for scoped extraction — already adopted in the V2
(Part A) `plan_scope`/`extract_data`/`join_graph.py` work; P-9 extends it, does not replace it.

## Rejected

### OpenMetadata

A Docker deployment needs, at minimum: the OpenMetadata server, a MySQL/PostgreSQL metadata
database, **Elasticsearch or OpenSearch**, and an Airflow container for UI-managed ingestion.
The `openmetadata-ingestion` Python package is a *client* — it discovers metadata and pushes it
to a running server; it is not a library this project could consume standalone for its
profiling algorithms. The cost is four new services and a sync contract; the benefit is a
catalogue UI this project would still have to wrap, because its domain model (entity anchors,
join paths, context scores, matching datasets) has no OpenMetadata equivalent. It solves
cataloguing, which is not this project's bottleneck.

### DataHub

Same shape as OpenMetadata, worse footprint. Quickstart brings GMS, the React frontend,
Elasticsearch, MySQL, Kafka + ZooKeeper + Schema Registry; documented minimum 8 GB RAM / 2 cores
for dev, 16 GB+ for production. `acryl-datahub`'s REST and Kafka emitters both require that
server (or a Kafka broker) to be reachable.

It does have the strongest metadata **model** of the two rejected platforms, and this project
adopts that model without the infrastructure: source -> dataset -> column entities, edges
carrying `confidence`/`origin`/`review_status`, column-level lineage — stored in PostgreSQL
behind a project-owned metadata-graph module (P-1), so nothing couples this project to one
graph implementation, and either platform could be adopted later by writing an emitter against
this project's own tables.

### Neo4j

Another server for a graph that will hold thousands of nodes, not millions. PostgreSQL +
networkx covers every traversal this project needs (metadata graph in P-8, entity subgraph
traversal in P-9).

### Informatica

Capability benchmark only — read for what a mature commercial discovery/matching product
considers table-stakes, never evaluated as a dependency, never installed.

## How V3 relates to the existing (V2) pipeline

New subsystem, shared connectors, replace where the advantage over the hand-built V2 approach
is decisive and measured — not new-for-its-own-sake.

| Subsystem | Decision | Why |
|---|---|---|
| Connectors (`src/mcp_server/connectors/`) | **Reuse unchanged** | Already expose sampling, filtered distinct values, keyset pagination, FK introspection. V3 adds only the missing capability methods in P-2 |
| Profiling | **New, database-native** | Almost nothing exists today; SQL-native (`uniqExact`, quantiles, `topK`) beats pulling rows client-side |
| Candidate/composite keys | **New** (Desbordante optional deep pass) | Nothing exists today |
| Relationship discovery | **Hybrid — `join_graph.py` stays primary** | It pushes work into SQL and survives 43M-row tables; Desbordante/datasketch are sampled accelerators that feed it more candidates, never a replacement for the exact SQL check |
| Schema matching | **Hybrid — Valentine first, LLM last** | Implements "algorithm-first, LLM optional" directly and cuts field-mapping LLM token spend |
| Data quality | **Hybrid — this project's gate stays authoritative, GX adds expectations** | `validate_data` already blocks a run correctly; GX only adds format checks it didn't have |
| Metadata catalog | **New, PostgreSQL** | Both off-the-shelf platforms rejected above |
| Matcher | **Replace with Splink**, builtin kept as a fallback engine | Trained m/u instead of hand-tuned weights; TF adjustments; real blocking pair-counts; waterfall explainability; threshold optimisation from labels; cluster graph metrics — all otherwise hand-built. Gated on the golden pairs at BP-B4: Splink becomes the default only when it beats the builtin on precision **and** recall, measured, never assumed |
| Candidate generation | **Replace budget truncation with blocking analysis** | `count_comparisons_from_blocking_rule` + `n_largest_blocks` cost a blocking rule *before* generating pairs, which removes the uncompared-pairs failure mode by construction instead of capping and reporting it after the fact |
| Clustering | **Replace with Splink's**, keep this project's own behind the engine flag | `compute_graph_metrics` gives cluster-quality diagnostics with no current equivalent |
| Orchestration (LangGraph) | **Reuse** | V3 engines are called from nodes; the graph shape is unchanged except where a phase explicitly says so |

Every replaced component keeps its predecessor reachable behind a per-entity `engine` selector,
so a regression in the new path is a config change, not a revert.
