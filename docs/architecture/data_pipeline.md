[< back to docs/INDEX.md](../INDEX.md)

# The data pipeline — spool, connectors, resolution, mapping, loading

## 5. The DuckDB spool — why rows left graph state

`src/orchestrator/spool.py`. Before Phase N, every extracted row was
appended to a Python list living directly in `MergeState.extracted_data`,
which LangGraph serialises into a SQLite checkpoint **after every single
node**, and again on every HITL pause/resume cycle. That makes both peak
memory and checkpoint size scale with the size of the *source data* — a
43-million-row table cannot be held in a Python list at all, and even a
2,284-record test merge measured a checkpoint that was 69.6%
`unified_entities` and 28.2% `entity_clusters` by size.

**The fix is a handle, not a smaller list.** `RunSpool` writes rows to
`logs/spool/<run_id>.duckdb`; state holds only
`{"row_count": N, "run_id": ..., "entity": ..., "kind": "rows"}` — a
constant-size dict regardless of how many rows that represents. Every
consumer goes through an accessor function
(`entity_rows`, `entity_row_count`, `entity_origin_counts`,
`entity_records_by_id`, `clusters_for`, `unified_for`) that transparently
handles **both** shapes: a spool handle, or a plain inline list. This
dual-shape design is what let most existing unit tests keep passing
unmodified — they hand rows in as plain lists, and only live/scale runs
actually touch a spool file.

**Rows are stored as JSON in one `payload` column, not as typed SQL
columns.** Different origins disagree about schema *by definition* — that's
the entire point of a merge tool — so a fixed relational schema for spooled
rows would need a migration every time a new origin contributed a new field.
DuckDB reads JSON natively and can still project fields out of it in SQL
(used by the Phase N.3 blocking/scoring queries), so this loses nothing
while staying schema-agnostic.

**`_bulk_insert` — the 1,300x fix.** The obvious way to insert many rows into
DuckDB is `executemany`; measured directly, inserting 50,000 rows with a JSON
payload column that way took **259 seconds** (~193 rows/s) because each row
pays its own JSON parse and bind. At that rate the real 43M-row table would
have taken over 60 hours and the extraction looked simply hung. The fix:
register a pandas DataFrame as a DuckDB virtual table
(`con.register(...)`) and run `INSERT INTO ... SELECT * FROM
_incoming_rows` — the same 50,000 rows in **0.19 seconds**. The slow
row-by-row path is kept only as a fallback if pandas is missing, so a
stripped environment degrades gracefully instead of breaking.

**`append()` verifies its own write, and this is not paranoia.** After every
`_bulk_insert`, `append()` queries `SELECT count(*)` on the table and
compares it against `self._written[entity_type] + len(batch)` — an
expectation the instance carries in memory, not derived from the file. This
is the fix for the single worst bug this project found: `cleanup_old_spools`
(described next) once deleted a spool file **while a different run was still
actively appending to it**, silently recreating an empty file underneath the
running extraction. Every subsequent append then "succeeded" against the
fresh empty table, and the run reported success having stored 19,076,150 of
43,276,150 rows. Critically, comparing against `MAX(seq)+1` *from the file
itself* would not have caught this — a wiped table restarts at seq 0 and
looks perfectly self-consistent from the inside. Only an expectation
carried from **outside** the file (this instance's own running total) can
detect that the file was swapped out underneath it.

**`cleanup_old_spools` — age is the primary guard now, not membership.** The
original design deleted every spool file whose `run_id` wasn't in the
caller's `keep_run_ids` set, and that set came from `_active_workflows` — an
**in-memory** dict that is empty after any app restart. So the bug above
wasn't a test artifact: restart the app while any run is sitting at a HITL
gate, start one new unrelated merge, and the parked run's spool — the exact
rows a human is about to come back and approve — gets swept. The fix
inverts the priority: a file touched within the last 6 hours
(`_SPOOL_MIN_AGE_SECONDS`) is **never** deleted, regardless of whether its
run_id is recognised; membership in the keep-set is only an *extra*
reprieve for files that are already old enough to be eligible.

---

## 6. Connectors and keyset pagination

`src/mcp_server/connectors/`: `postgres.py`, `clickhouse.py`, `mongodb.py`,
`base.py` (the abstract interface), plus two shared helper modules:
`keyset.py` and `watermark.py`.

**Why keyset (seek) pagination instead of `OFFSET`/`LIMIT`.** `OFFSET`
pagination makes the database re-scan and discard every already-returned row
on every page — cost grows with how deep into the table you are, not with
the page size. Keyset pagination instead carries a cursor
(`WHERE <key> > :last_seen ORDER BY <key> LIMIT :n`) so every page costs the
same regardless of depth. Measured directly on the real cell_towers table:
an aligned key stayed flat around 7 seconds per batch through the whole run;
a misaligned key (one that didn't match the table's physical `ORDER BY`)
grew 7.07s → 11.82s → 14.58s over just the first 17% of the table — the
exact OFFSET-shaped curve keyset pagination exists to remove. **The
practical rule this taught: always paginate on the key the schema-discovery
step actually reports as the table's sort order, never a hand-picked
"natural" key that merely looks unique.**

**Non-unique keys silently drop rows unless you compensate, and this was
measured, not assumed.** A naive keyset implementation uses a strict `>`
predicate at the batch boundary. If the ordering key isn't unique, every row
sharing the boundary value with the last row of the previous batch is
permanently excluded — nobody's `>` ever includes it again. Measured on a
synthetic 300,000-row table with a 3-column, 144-distinct-value key: **33%
of rows were silently lost, and the run reported success.** The fix
(`keyset.py`): the predicate becomes `>=`, and a `boundary_seen` count rides
in the cursor recording how many rows at that exact boundary value the
previous batch already returned, so the next batch knows to
`drop_consumed()` that many matching rows from its own front before
returning the rest. A **second**, subtler bug surfaced fixing the first one:
with `>=` and no further tiebreak, PostgreSQL doesn't guarantee equal-key
rows come back in the same order across two separate queries — it duplicated
3 of 16,044 real Pagila rows (counts matched, contents didn't). The fix
adds a deterministic tiebreak column to the `ORDER BY`: `md5(row::text)` for
Postgres, `cityHash64(*)` for ClickHouse.

**PostgreSQL's `PRIMARY KEY` can be trusted for uniqueness. ClickHouse's
declared "primary key" cannot, and the code treats them differently on
purpose.** `information_schema.table_constraints` in Postgres is a real
uniqueness guarantee enforced by the database. ClickHouse's
`system.columns.is_in_primary_key` is merely the sparse **sorting** key used
for its MergeTree index — it guarantees nothing about uniqueness. This was
verified directly against the real dataset used in this project: the
cell_towers table has 43,276,150 rows for only ~42.97 million distinct
5-column key tuples. Consequently:
- `postgres.py` computes `keyset_is_unique(keyset, pk_columns)` and, when
  true, takes a faster strict-`>` path — but still calls
  `assert_key_unique()` on every batch and raises `DataQualityError` if a
  key declared unique turns out not to be, rather than trusting the
  declaration and losing rows.
- `clickhouse.py` **never** takes that fast path at all — the
  `keyset_is_unique`/`assert_key_unique` machinery was deliberately removed
  from this file after the measurement above, not merely left unused. It
  always uses `>=` + `drop_consumed` + the `cityHash64(*)` tiebreak.

**`watermark.py`** is a separate concern from keyset pagination: it tracks,
per `{origin_id}:{table}`, the last value seen in a designated watermark
column (usually a timestamp or auto-increment ID) so a *subsequent run* of
the same merge only extracts rows that arrived since the last run — this is
what makes incremental re-runs cheap. It is written to
`merge_engine_extraction_state` by `update_extraction_state_node`, and only
after a successful load, so a failed run never advances a watermark past
data that didn't actually land.

---

## 6b. Source analysis: catalog tables, ETL columns, inclusion dependencies, roles

Three things happen to the discovered schemas before any row is matched.

**Catalog tables and ETL metadata columns (P12)**
(`src/mcp_server/tools/metadata_classification.py`). Two categories, not
conflated. A *catalog/system table* (engine-owned schemas and reserved name
prefixes such as `pg_`, `sqlite_`, `system.`) is removed from the discovered
schema, so no later node sees it. An *ETL metadata column* (audit/load/DBMS
vocabulary, by name) is excluded from matching and field mapping — so never
projected — but **kept in the corpus as provenance** and listed in the table's
`metadata_columns`. Override layers, lowest to highest: detected default →
per-origin → per-run (`RunConfig.metadata_overrides`, state
`metadata_overrides`) with `treat_as_business` / `treat_as_metadata` /
`treat_as_catalog` / `treat_as_data`. Every exclusion is recorded in
`schema["classification"]` with its misclassification risk. Detection is by
name, so a business field that looks like metadata is misclassified until
overridden.

**Join graph by inclusion dependency (P3)**
(`src/mcp_server/tools/join_graph.py`). Declared FKs are authoritative but
empty for ClickHouse and MongoDB origins. The name-guessing tier is gone:
edges are found from *values* alone. A (child column, parent column) pair is a
candidate when the parent column is unique and the two are not both their own
table's primary key; it is scored by containment `|child ∩ parent| / |child|`
(pushed into SQL through each connector's `sample_distinct_values`), pre-pruned
by a binomial bound, tested against coincidence with the parent's density, and
the ranked list is cut at its largest score gap. No name pattern, no fixed
threshold. With nothing to sample it proposes nothing. A foreign key into a
dense surrogate sequence is not inferred (declared FKs cover it).

**Table roles and row relationships (P4, P7).** From the graph's FK columns and
the rows' own uniqueness, `classify_junction_and_weak_tables` proposes
*junction* tables (a minimal unique column combination equal to the union of
two or more FKs) and *weak entities* (minimal key mixes FKs with a local
discriminator). `derive_relationship_edges` adds typed row edges
(`DIRECT_PARENT`/`ULTIMATE_PARENT` from self-INDs, `RELATED` from junctions;
cycles are listed, not broken). The results land in state as `table_roles` and
`relationship_edges` and are **shown at the dependency gate** (`table_roles`,
`relationship_cycles` in its payload) for confirmation; nothing downstream acts
on a role, and a run replaying a pinned profile does not recompute them.
The mapper uses the destination's FK info to tell a dimension table from a
child (P4).

---

## 7. Entity resolution: Fellegi–Sunter scoring, blocking, clustering

`src/mcp_server/tools/resolve_entities.py` orchestrates; `fellegi_sunter.py`
holds the scoring model. This is the subsystem the user explicitly called
out as the highest-consequence one to get right ("over-merging is the worse
error").

### Why Fellegi–Sunter, not similarity averaging

The original scorer averaged string similarities (Jaccard + `SequenceMatcher`)
and compared the result to two hand-picked constants (0.95 auto-match, 0.70
auto-exclude). That has no way to express the single most useful fact in
record linkage: **agreeing on a rare value is much stronger evidence than
agreeing on a common one.** Two people both named "Rory" is nearly worthless
evidence if 248 people in the table share that first name; two records with
the same rare surname is strong evidence, but a naive average can't tell the
difference between the two agreements.

Fellegi–Sunter formalises this. For each field, the model estimates:

```
m = P(field agrees | records ARE the same entity)
u = P(field agrees | records are NOT the same entity)
```

Each comparison contributes `log2(m/u)` bits of evidence when it agrees, or
`log2((1-m)/(1-u))` when it disagrees; per-field contributions sum into a
total match weight, which converts to a probability via a sigmoid
(`_sigmoid_bits`). This is exactly the arithmetic in `MatchModel.score()` and
`FieldModel.weight()`.

**`u` is where term frequency enters, and this is the actual point of the
whole subsystem.** For an *exact* agreement, `FieldModel.weight()` swaps in
the value's own observed frequency as `u` instead of the field's average
disagreement rate: `u = term_frequency.get(value)`. A value shared by 20% of
records has a correspondingly small `log2(m/u)` contribution; a value unique
in the dataset has a very large one. `build_term_frequencies()` computes this
once per entity type across every record, not once per pair — recomputing it
per pair would make the whole scoring pass quadratic in the wrong dimension.

**m is learned unsupervised, via a small EM loop (`estimate_model`), not
hand-tuned; the field-level `u` comes from randomly sampled pairs (P13).**
No labelled training pairs are required. Starting from conservative priors (`_PRIOR_M`, `_PRIOR_U`), each
iteration (1) scores every candidate pair with the current model to get a
"responsibility" (how much this pair looks like a match), then (2)
re-estimates each comparison level's m/u weighted by those responsibilities,
mixed with the priors at a fixed weight (`_PRIOR_WEIGHT = 20.0`) so a small
or unlucky batch of candidate pairs can't swing the model to something
absurd. `_PRIOR_MATCH_RATE = 0.10` is the assumed base rate of true matches
among candidate pairs before any estimation — a prior, not a measured
constant, chosen conservatively.

**Why `u` is not taken from the EM (P13).** The EM only sees the *blocked*
candidate pairs, which are look-alikes by construction, so the `u` it
estimates ("how often do non-matches agree on this field?") is far too high
and every agreement is under-weighted. `_fit_match_model` therefore draws
`SAMPLE_PAIRS` (3,000) pairs uniformly from the *unblocked* cross product
(`sample_random_pairs`, seeded, records sorted by id so the sample is
reproducible) and `estimate_model(sampled_vectors=..., population_pairs=...)`
takes each field's level probabilities from them. `m` is fitted first, on the
candidates, and `u` swapped in afterwards (pinning `u` inside the EM would
dilute `m`); `match_prior` is re-expressed for the same random-pair population
(EM match mass / C(n, 2)), and `sampled_vectors` without `population_pairs`
is refused. Do not unify this with the exact-value path above: that one uses
`df/N` from `build_term_frequencies` directly and needs no sampling.

**Names are compared as token sets, IDF-weighted (P14).** Whole-value
frequency cannot down-weight a *word* shared by most names (a legal-form
suffix). For multi-word text values `build_token_document_frequencies` gives
per-field token document frequencies and `idf_soft_similarity` scores two names
as a symmetric Monge-Elkan over the character-level ratio, weighted by
`log2(N/df)` — so "Acme X Ltd" vs "Beta X Ltd" no longer looks alike because of
"X" and "Ltd", while a typo'd token still earns most of its match (plain token
cosine was measured to turn every typo into DISAGREE). Only values with both a
letter and a space take this path; atomic fields keep the value-level table.

**Comparison *levels*, not just agree/disagree.** `LEVEL_EXACT`,
`LEVEL_STRONG` (minor typo/formatting difference), `LEVEL_WEAK` (loosely
similar), `LEVEL_DISAGREE`, and `LEVEL_NULL` (one side missing — the field
contributes *zero* evidence, deliberately, rather than being coerced into
"disagree"; a missing value tells you nothing about whether two records
match). This preserves the pre-existing string-comparison logic
(`_has_conflicting_identity`, `_PARTIAL_NAME_FIELDS`, the fuzzy comparators)
as *inputs* to the probabilistic model instead of throwing that work away —
they now decide which level a comparison falls into, and Fellegi–Sunter
decides how much that level is worth.

**Declared unique identifiers get a real veto, and this needed a second,
separate wiring fix to actually apply everywhere.** `_has_conflicting_identity`
checks whether two records disagree on a field the caller has declared a
unique identifier (either via a hardcoded substring heuristic,
`_UNIQUE_IDENTITY_HINTS`, or explicitly via `matching_rules.unique_id_fields`,
Phase O.2). The first wiring only threaded `extra_unique_ids` through
`_score_pair`, which is the path used when the EM model has been fitted
(needs at least `_MIN_PAIRS_FOR_MODEL` = 30 candidate pairs). A unit test
using a 2-record case — nowhere near 30 pairs — caught that the *heuristic*
path (`_compute_heuristic_confidence`, which runs alone below that
threshold) never received `extra_unique_ids` at all, so two records sharing
a name but declaring different member IDs merged anyway on a small dataset.
Both paths now receive it. This is exactly the kind of gap that a design
review reading the code wouldn't necessarily catch — it took a
minimal-reproduction unit test.

### Blocking: why the old hard cap was a correctness bug, not a performance knob

The original design compared candidate pairs only up to `_MAX_COMPARISONS`
(10,000, later 250,000) and simply broke out of every loop past it. The
remaining pairs were **never compared**, and the run still reported success.
On a 2,484-record merge generating 28,170 candidate pairs, this silently
dropped 18,170 of them, and — because block iteration order isn't guaranteed
stable — **two identical runs of the same data produced 2,483 and 2,482
output rows**, with `llm_scored=0` in both, ruling out LLM non-determinism.
Phase L made the loss *counted*; it was still loss (later measured: 711,670
pairs of one entity discarded unexamined while the run ended `completed`).
That is data loss with no evidence it happened, which this project treats as
unacceptable.

**There is no comparison budget any more (P2/P5).** A pair is excluded only
because a property computed from the block collection says so, never because
a counter ran out (`_generate_candidate_pairs` in `resolve_entities.py`):

1. **Token blocking (P5).** Every Unicode word token of every data value is a
   block key; attribute names are never read, so no field is privileged and
   the same code serves any schema. A record with no usable token shares a
   fallback block, so missing data never silently excludes it. Blocks with an
   identical member set are kept once.
2. **Block filtering + purging.** Each record keeps only the blocks whose
   token IDF is at least the mean IDF of its own tokens (geometric mean of
   its block sizes; chosen over the arithmetic mean by measurement), and a
   block links two records when *either* keeps it. Purging then drops the
   largest blocks while one holds more comparisons than all the others
   together, or its token is held by most records (IDF < 1 bit), provided
   every member still sits in another surviving block. Nothing is tuned.
3. **Meta-blocking (WNP).** Each candidate edge is weighted
   `sum(1 / comparisons(b))` over the blocks it shares and survives when it
   reaches the mean weight at one of its endpoints.

Pruning is lossy by design (a true pair whose only shared block is dropped is
lost), so it is measured, not assumed: the run report carries
`block_memberships_filtered` (memberships, not pairs),
`pairs_lost_by_block_filtering_ub` and `pairs_pruned_by_purging_ub` (both UPPER
BOUNDS — a pair is counted once per shared block, never enumerated), and
`pairs_pruned_by_meta_blocking`; pair completeness is computed against
ground truth by `tests/eval/score.py` (`blocking_report`, P19). Without labels
blocking recall is unmeasured. A name in a script without spaces (CJK) is one
token, so a one-character variant does not co-block.

`_MAX_CANDIDATE_PAIRS` (5,000,000, ~1.5 GB) is an **out-of-memory guard, not
a quality knob**: if more pairs than that survive meta-blocking, the call
raises `CandidatePairBudgetExceeded` and the run fails loudly rather than
truncating. `uncompared_pairs` stays in the report and is always 0.
Pair-level fallout is also visible at the matches gate, below.

### Clustering: confident edges, WNP, conflict repair, triangle support

Raw union-find over every edge scoring ≥ the auto-match threshold has one
structural weakness: one bad edge chain-merges an entire connected
component (`A` matches `B`, `B` matches `C`, therefore `A`/`B`/`C` are all
one cluster, even if `A` and `C` share nothing). `_build_clusters` therefore:

- **Joins only confident edges** (score ≥ auto-match). A pair below it is
  never unioned; merging on "not sure" evidence measurably destroyed distinct
  people.
- **Prunes with WNP** (`_wnp_prune`): an edge survives when it is at least
  the mean weight at one of its two ends — the same node-centric rule as
  blocking, no hand-set confidence cut. A weak bridge goes here. Lossy by
  design (recall for precision).
- **Repairs conflicts** inside each component: records whose exclusive
  identifiers disagree (email, tax ID — measured from the run's own corpus,
  P6) cannot share a cluster; the conflicting members are separated rather
  than a partition guessed.
- **Requires triangle support** (`_triangle_parts`): where a part has
  corroborated edges (their ends share a neighbour), an uncorroborated edge is
  a bridge between two groups and is dropped however strong. A part with no
  triangle (a chain) stays whole. Repair runs *inside* each triangle part, so
  one conflict between two bridged entities does not dissolve both.
- **Optionally enforces 1:1** (`one_to_one`, off unless the caller knows the
  destination needs it): a part spanning two sources is reduced to its
  maximum-weight assignment.

The average-linkage **cohesion cut** that used to run after repair is no
longer part of the main path (`_split_component` passes `min_cohesion=0`;
`_average_linkage`/`_split_weakest_link` remain only as that parameter's
non-zero branch). Name
coherence is *not* a cut either: names are a quality **flag** (P16, below),
because a name-based split destroyed real merges.

**Borderline pairs (between the auto-exclude and auto-match thresholds) are
never auto-unioned, and they now go to the matches gate to be decided (P15).**
Given the user's explicit priority — "over-merging is the worse error, bias
every threshold toward splitting" — a borderline pair is left separate and
listed as `pending_merge_candidates`. The matches gate
(`human_review_matches`) also receives, per entity, a `band_summary`: the
thresholds in force, predicted match / possible / exclude counts, and the
score distribution (log2 odds for a model posterior, Sturges bins from the
data's own range). The operator can answer per pair (`pair_decisions`:
`merge` | `keep_separate`) and/or commit a different bar for an entity
(`band_thresholds`, valid only if `0 <= exclude < match <= 1`).
`apply_band_decisions` (shared by `WorkflowManager.resume_workflow` and the
gate body, because the body never runs on resume) re-derives the clusters
from the retained scored pairs via `rebuild_band_clusters`: edges at or over
the bar are joined strongest first, a rejected pair is a cannot-link, and a
cluster a human already decided is joinable only by an accepted pair, so a
"keep apart" removes one link, never the Match links around it. An undecided
pair or unrecognised action never merges. Pairs below the run's own exclude
bar are not retained, so a committed exclude bar *lower* than the run's needs
the stage retried.

**Decision memory (P9).** If `MERGE_ENGINE_DECISION_MEMORY` names a file (the code also reads a
`decision_memory_path` state key, which `MergeState` does not declare, so use
the env var), each answered band pair is
persisted keyed by a stable sha256 of the question (gate, entity, the pair
and both records' content — a changed attribute is a new question), and a later
run applies matching answers at `resolve_entities` without asking
(`resolution_stats[e]["decisions_from_memory"]`); only the remainder reaches
the gate. Off by default; an unreadable file means "ask again". An abandoned
gate (abort/reject) records nothing. It is a single JSON file with no
concurrency control — Part 3's decision store replaces it.

**Cluster IDs are deterministic, computed as
`sha256(f"{entity_type}:{'|'.join(sorted(record_ids))}")[:32]`, not random
UUIDs.** The previous behaviour (`generate_uuid()` per cluster per run) meant
re-running identical data on unchanged source rows produced a *different*
cluster ID every time, and since `load_data.py` uses the cluster ID as the
destination primary key when nothing better is available, re-running the
same merge **duplicated every row** in the destination. A deterministic hash
of the sorted member record IDs makes the same input cluster always resolve
to the same output ID — a prerequisite for the watermark-based incremental
extraction in §6 to actually be safe to re-run.

### Per-entity matching rules (Phase O.2)

`matching_rules` in state, keyed by entity type:
`{blocking_keys, unique_id_fields, strictness}`. `strictness` is one of
`loose`/`normal`/`strict`, mapping to auto-match thresholds `0.90`/`0.95`/
`0.98` (`_thresholds_for` in `resolve_entities.py`) — **it only ever moves
the auto-merge bar, never the auto-exclude bar**, so a stricter setting sends
*more* pairs to human review rather than merging more aggressively; that's
the recoverable direction given the "over-merging is worse" priority. An
inverted configuration (`auto_match <= auto_exclude`) or an unrecognised
strictness string falls back to the historical defaults (0.95/0.70) with a
logged warning, rather than either crashing or silently merging everything.
An entity with no configured rules gets those exact same historical defaults
— **this is the compatibility contract**: the whole eval harness (precision
1.0000, recall 1.0000, cluster counts unchanged) was re-run after adding this
feature specifically to prove that an unconfigured entity behaves
byte-identically to before Phase O existed.

---

## 8. Unification and conflict resolution

`src/mcp_server/tools/unify_entity_group.py` (per-cluster merge logic),
called from `src/orchestrator/nodes/unify_entities.py`.

**Conflict resolution is a configurable rule chain, not a hardcoded
if/elif.** Before Phase O.3, surviving-value selection for a field with
disagreeing candidates was a fixed chain: origin priority, then
the longest non-empty value, then recency. `conflict_policy` in
state now lets a per-field (or `"_default"`) list of rules override that:
`priority | longest | recency | shortest | first_seen` (plus the numeric
rules). `completeness` is kept as a config **alias** of `longest` so saved
profiles still load (P18): it always meant raw string length, not a
record-level "fewest NULLs".
`resolve_policy()` looks up the field's chain (or falls back to
`"_default"`, or the historical three-rule chain if neither is configured);
`_apply_rule()` applies one rule to the current candidate set, and
`_resolve_conflict()` walks the chain, stopping as soon as exactly one
candidate survives. **Unknown rule names are dropped with a logged warning,
never silently treated as `first_seen`** — a typo in a saved profile's
conflict policy should be visible, not quietly change behaviour to something
the user didn't ask for.

`shortest` (minimum non-zero length) is a genuinely new rule not present in
the original hardcoded chain — added because a per-field configurable system
with only the original three rules wouldn't actually let a user express
"prefer the terser value" for fields like normalised phone numbers or codes,
which is a real, common survivorship preference. If the whole configured
chain leaves more than one candidate standing, `first_seen` is the final,
always-available tiebreak, guaranteeing the function always returns exactly
one value.

**Survivorship repairs (P18).** `longest` is text-only: on any non-text
candidate it is a no-op and the chain falls through (string length is a
digit-count accident for numbers). The **higher** `priority` number wins, and
an origin's optional `field_priorities` `{field: int}` overrides it for one
field. `first_seen` is a total order — origin, then source table, then source
row id — independent of extraction order. With no precomputed reliability
table `measured_reliability` is measured from the cluster's own records instead
of being a silent no-op. When `numeric_max`/`numeric_min` resolve a field whose
sources disagreed, the conflict record carries `numeric_disagreement`
(min/max/spread/relative spread) and the reconciliation report aggregates it
per field as `numeric_disagreements`. None of this makes survivorship
non-destructive: the losing values are still gone at the end of the run.

---

## 9. Field mapping and the destination schema

`src/mcp_server/tools/map_fields.py`, orchestrated by
`src/orchestrator/nodes/map_fields_node.py`. Maps each unified entity's
fields onto destination table columns, using heuristics first (name
similarity, type compatibility) and falling back to an LLM call for fields
the heuristic can't map with confidence (`field_mapping_enabled` in
`llm_config`).

**A pinned mapping (Phase O.4) must still reconcile against the live
destination — this was found by live verification, not by reading the code,
and would have shipped otherwise.** The first implementation of "reuse a
saved mapping" simply set `field_mappings[entity_type] = pinned_mapping` and
skipped calling `map_fields_impl` entirely — which also skipped computing
`pending_schema_changes`, the list of destination columns that need
creating. Against a genuinely fresh destination database, this meant **no
columns were ever created**: a live two-run test showed run 1 (which derived
the mapping itself) building a 9-column table, and run 2 (replaying the
pinned mapping) building a 1-column table. The fix reconciles the pinned
mapping's target columns against the destination's *actual current* schema
on every run — `existing = {c["name"].lower() for c in dest_columns}`, and
for every pinned `(source_field → target)` pair whose `dest_column` isn't
already present, a schema-change entry is generated via `_map_type_generic`
just as if the mapping had been freshly derived. A saved profile pins *which*
mapping to use, not an assumption that the destination already has the
columns for it.

**A second, smaller bug was caught fixing the first one:**
`field_mappings[entity][source_field]` is a **dict**
(`{dest_column, confidence}`), not a bare column-name string — the first
attempt at the reconciliation fix assumed a bare string and needed
correcting once the pinned-mapping shape was actually inspected.

---

## 10. Loading — transactional, and honestly not always

`src/mcp_server/tools/load_data.py`, `src/mcp_server/connectors/
postgresql_destination.py`, `src/mcp_server/connectors/
clickhouse_destination.py`.

**`load_data_node` will not write unless `load_approval is True`, checked
independently of gate routing.** This is a deliberate belt-and-suspenders
design: the graph's edge routing (§3) is *supposed* to prevent reaching
`load_data` after a rejection, but the node itself also refuses to write if
the approval flag isn't set, so a routing bug can never silently turn a
rejection into a write. Before this existed, rejecting the final approval
gate set `load_approval=False` and the graph proceeded to `load_data`
anyway, which never read that flag at all — a complete no-op reject that
looked, from the UI, like it had worked.

**The pre-load gate: invariants measured on this run, refusal writes nothing
(P10).** `pre_load_gate` (`src/orchestrator/nodes/load_data.py`) measures four
invariants per entity — mapping coverage (a floor), NULL share of the identity
field, largest-cluster share of possible merges, and count of P16-flagged
clusters (ceilings) — and **proposes the thresholds from the run's own
entities** (medians, with a documented 1/2 cap on the shares and a per-entity
slack so a small healthy lookup is not refused for being small). The same call
builds the gate payload (`pre_load_gate` in the `human_final_approval` data) and
decides the load. The operator confirms or edits the numbers on the approve
feedback as `thresholds` (`{invariant: number}`; kept in state as
`pre_load_thresholds` so a retry through the error gate is judged against them);
a value the UI sends back unchanged is not an edit, and a non-number fails
closed. A violated invariant makes `load_data` return a `fail` disposition
(`DataQualityError`, no error-gate "skip" that would walk on as if loaded)
naming each invariant and entity, **before anything is written**. It can only
refuse; it cannot roll back a partial write, and a run whose entities are all
bad alike is caught only by the floors or by the operator.

**Destination defect report (P17).** `destination_schema_defects` is a pure
check of each mapped entity against the destination's introspected schema: a
table that lacks a mapped column, or whose declared columns beyond its primary
key are fed by nothing while the run has values for columns it lacks, or that
does not exist while the destination has others, is *defective*. The whole
entity is **withheld** (left empty, no DDL — "no new destination columns"
stands) and reported with `missing_columns` and `unprojectable_rows`
(`held_links` stays in the load's own report). The final-approval payload shows
it as `destination_defects` before the reviewer approves.

**Label-free quality signals in the run report (P16).** The reconciliation
report carries `entities[e].quality` (`quality_signals` in
`src/mcp_server/tools/cluster_quality.py`): homonymy rate, name-variation rate,
Hill numbers of the cluster sizes, B-cubed against the previous run's
clustering, and `flagged_clusters`/`flagged_count`. B-cubed needs a history file
(`MERGE_ENGINE_QUALITY_HISTORY`; a `quality_history_path` state key is read
first but is likewise undeclared; keyed by `run_id` so the preview and the load
of one run agree); on run one or with no
file it is `None` plus a "not available" note, never a number. A cluster is
flagged when its names split into unrelated groups further apart than a
threshold derived from this run's own names — **a flag, never a cut**; no
labels are involved.

**PostgreSQL destination loads are staged and swapped atomically; ClickHouse
loads are not, and the code says so rather than pretending.**
`PostgreSQLDestinationConnector` writes into a `<table>__staging_<run_id>`
table first, then merges/swaps it into the real table inside one
transaction — either the whole entity lands, or none of it does, and
watermarks only advance after that commit succeeds. Killing the destination
mid-load and checking the real table is the actual live verification this
project ran for Phase M (`SELECT count(*)` unchanged, watermark
untouched). ClickHouse has no equivalent multi-statement transaction
primitive in the way this project uses it, so its destination connector is
documented as **not** atomic — an honest, stated limitation rather than a
false claim of parity.

**Foreign-key columns are translated from raw source ids to destination
cluster ids at load time (2026-08-01), not left as-is.** A cluster's
destination `id` is `deterministic_uuid(entity_type, *member_record_ids)` (see
§8) — never equal to any individual member's raw source id — but a field
mapped onto an FK column (e.g. `company_relation.company_a_id`) still carries
that raw id, copied straight from the origin record. Left untranslated, every
FK to an already-loaded parent violates the constraint immediately
(`Key (company_a_id)=(<raw id>) is not present in table "company"`).
`PostgreSQLDestinationConnector.get_foreign_keys(table)` queries
`information_schema` for the table's real FK constraints (column →
referenced table, no hardcoded names), and `load_data_impl` (`src/
mcp_server/tools/load_data.py`) — **after** `_transform_records` applies the
field mapping, since the FK column names only exist post-mapping — calls
`_translate_foreign_keys` to rewrite each FK value to the referenced row's
cluster id via a `{raw_id: cluster_id}` lookup built from
`entity_clusters` state. An id with no known cluster is set to `NULL`
rather than left dangling (a genuinely unresolvable link, e.g. a category
name that was never its own extracted entity, is worth losing — a load that
crashes the whole transaction over it is not). One subtlety that cost a
second bug fix: a referenced entity type that was resolved but had **zero**
clusters this run produces a real, empty lookup dict (`{}`) — Python's
`if not lookup:` treats that identically to "never looked up at all," which
skipped translation and let the bad value through. The fix checks
`referenced_table not in cluster_lookup_for_table` (key presence) instead of
dict truthiness.

**Value coercion (`src/mcp_server/tools/value_coercion.py`) also grew two
more classes of "not malformed, just not what this column's type expects"**
handling, in the same spirit as the money-string/hex-id cases already
documented in §9: an empty string (`""`) is a missing-value placeholder for
any non-text-like target type, coerced to `NULL` rather than rejected
(Postgres has no implicit `'' → NULL` cast for numeric/date/bool columns);
and Diffbot's date encoding (a leading `d` tag, `XX` standing in for an
unknown month/day — `"d1997-XX-XX"`) is normalized to a plain ISO date via
`normalize_date_like()`, filling unknown components with `01` rather than
failing the whole batch on the first partial date.

---
