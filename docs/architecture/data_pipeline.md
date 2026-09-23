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

**m and u are learned unsupervised, via a small EM loop
(`estimate_model`), not hand-tuned.** No labelled training pairs are
required. Starting from conservative priors (`_PRIOR_M`, `_PRIOR_U`), each
iteration (1) scores every candidate pair with the current model to get a
"responsibility" (how much this pair looks like a match), then (2)
re-estimates each comparison level's m/u weighted by those responsibilities,
mixed with the priors at a fixed weight (`_PRIOR_WEIGHT = 20.0`) so a small
or unlucky batch of candidate pairs can't swing the model to something
absurd. `_PRIOR_MATCH_RATE = 0.10` is the assumed base rate of true matches
among candidate pairs before any estimation — a prior, not a measured
constant, chosen conservatively.

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

Before Phase L, comparing every candidate pair above `_MAX_COMPARISONS =
10,000` simply broke out of every loop and logged a warning — the remaining
records were **never compared**, and the run still reported success. On a
2,484-record merge generating 28,170 candidate pairs, this silently dropped
18,170 of them, and — because block iteration order isn't guaranteed
stable — **two identical runs of the same data produced 2,483 and 2,482
output rows respectively**, with `llm_scored=0` in both, ruling out LLM
non-determinism as the cause. That's not a performance limitation described
in different words — it's data loss with no evidence it happened, which is
precisely the failure mode this whole project treats as unacceptable.

The fix is layered blocking, each layer cheap and SQL-expressible against the
DuckDB spool: multi-rule deterministic blocking (several explicit blocking
keys unioned, rather than one first-token key), phonetic/normalised keys so
spelling variants co-block, a sorted-neighborhood sliding-window pass that
catches near-misses no exact key catches, and block-size purging (a block
holding 20% of the dataset carries no discriminating information and is
dropped). Any pair that genuinely cannot be compared under any layer is
**counted and surfaced in the reconciliation report** — never silently
dropped. Measured blocking recall on the eval harness: **1.0000** at 2.4% of
the cost of comparing all pairs.

### Clustering: repair + cohesion cut, not raw transitive closure

Raw union-find over every edge scoring ≥ the auto-match threshold has one
structural weakness: one bad edge chain-merges an entire connected
component (`A` matches `B`, `B` matches `C`, therefore `A`/`B`/`C` are all
one cluster, even if `A` and `C` share nothing). Two passes were added on top
of the connected components:

- **Repair**: a component containing a hard conflict on a declared unique
  identifier (differing email, tax ID, member ID) is split rather than
  merged — a unique-identifier disagreement is treated as stronger evidence
  than any amount of accumulated similarity.
- **Cohesion cut**: average-linkage clustering runs *inside* each surviving
  component and cuts weak internal links, so a chain like `A–B–C` where
  `A↔B` is strong but `B↔C` is only just above threshold can break into two
  clusters instead of one.

**Borderline pairs (between the auto-exclude and auto-match thresholds) are
no longer auto-unioned.** Given the user's explicit priority — "over-merging
is the worse error, bias every threshold toward splitting" — a borderline
edge with real evidence behind it but not enough to clear the bar goes to
human review instead of merging unreviewed.

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
completeness (longest non-empty value), then recency. `conflict_policy` in
state now lets a per-field (or `"_default"`) list of rules override that:
`priority | completeness | recency | shortest | first_seen`.
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
