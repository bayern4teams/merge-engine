# Clustering profile — BUG D.1

Live measurement, taken after the C0-C2 matching-precision fixes and the D.2/D.3
`_build_clusters`/`_partition_component` changes landed (both on `main`, uncommitted
at the time of this run — same session).

## Method

Not a full end-to-end Boeing run (that needs `drive_app.py` through every HITL gate
and takes 60-90 minutes; deferred to §0.11's baseline recording). Instead,
`resolve_entities_impl("person", ...)` was called directly against **real** person
records pulled straight from the live `origin1`/`origin2` ClickHouse containers
(`docker exec Sample_Database clickhouse-client`), the same live-verification
methodology used earlier in this session for the field-mapping coalesce fix:

- All 1,215 `origin1.Person` rows.
- 1,925 `origin2.Person` rows whose `fullName` matches an `origin1.Person.name`
  case-insensitively — deliberately chosen to create real common-name collisions
  (e.g. multiple distinct "James Hudson"s), which is exactly the shape that
  produces large blocks and dense connected components.
- 4,000 more `origin2.Person` rows sampled uniformly at random, to reach a total
  scale (7,140 records) comparable to the historically slow `person`/`technology`
  entity types in `PERFORMANCE_BOTTLENECKS.md`.
- No LLM config passed — heuristic-only scoring, so the number below is pure
  matcher/clustering cost, not LLM latency.

Script: ephemeral, not checked in (`scratchpad/live_cluster_profile.py`), reading
the `_LAST_CLUSTERING_STATS` module dict that BUG D.1's instrumentation now
populates on every `_build_clusters` call.

## Result

```
Total records: 7,140 (origin1=1,215, origin2 matched=1,925, origin2 random=4,000)

resolve_entities_impl wall time: 211.00s
clusters: 7,021
auto_matched: 2,181
auto_excluded: 247,818
llm_scored: 0
candidate_pairs: 250,000   (of >=17,574,731 generated — budget-capped, C1's honest
                             degrade: 17,324,731 pairs reported as uncompared, not
                             silently dropped)
blocks_purged: 5

clustering_seconds: 0.281
largest_component_size: 176
large_components (>200 members): 0
component_count: 6,240

Cluster size histogram: 1 -> 6,908 | 2 -> 108 | 3 -> 5
```

## Reading this honestly

**Clustering (`_build_clusters`/`_partition_component`) was not the bottleneck in
this run — it took 0.28s out of 211s total (0.13%).** The 211s is almost entirely
`_generate_candidate_pairs` (materializing pairs up to the budget out of >17.5M
raw candidates from common-name blocks) plus `_fit_match_model`/`_score_pair` over
the 250,000-pair budget. This does **not** match `PERFORMANCE_BOTTLENECKS.md`'s
original claim that `_partition_component`/`_average_linkage`/`_split_weakest_link`
are "the dominant cost by a wide margin" — but that claim predates the C0-C2
matching-precision fixes landing on `main`. Before those fixes the matcher
generated *zero* candidate pairs at real corpus sizes (BUG C1), so `_build_clusters`
received nothing and was trivially fast for a different reason (no work at all,
not efficient work); the original 10-30 minute figures must have come from an
older code path or a different record shape than this measurement. This profile
supersedes that guess with a real number, taken after the matcher was fixed to
actually generate candidates.

**The largest connected component was 176 members** — below this run's
`_LARGE_COMPONENT_THRESHOLD = 200` visibility gate, so `_partition_component`'s
O(n^2) conflict check ran at most ~15,400 pair-comparisons for that one component,
which is why clustering stayed under 0.3s even with 6,240 components to process.
The D.2 hoist (removing the two dict-comprehension rebuilds from the inner loop)
is still a correct, risk-free win — it just wasn't the constraint in *this*
dataset shape.

**Where the real cost is, per this measurement: blocking/scoring, not
clustering.** A follow-up profile of `_generate_candidate_pairs` and
`_fit_match_model`/`_score_pair` (D.5's vectorization target) would be more
directly useful than further clustering optimization, given this evidence. This
is exactly the kind of profile-before-optimizing finding
`PERFORMANCE_BOTTLENECKS.md` and this plan's own D.1 instruction call for —
choosing an algorithmic change to `_build_clusters` beyond the D.2 hoist is
**not** supported by this data and was correctly not attempted.

## Caveats

- This is a `person`-shaped sample chosen to be adversarial for blocking (real
  name collisions), not a full Boeing run across all entity types. `company`
  and `technology` entity types were not re-profiled here.
- 5 blocks were purged-but-admitted (C1's degrade path) into the budget-capped
  pipeline rather than being dropped, consistent with the fix already verified
  earlier this session.
- A full post-fix Boeing run via `drive_app.py --assert-budgets` (§0.6, §0.11)
  is still the authoritative end-to-end measurement and has not been done in
  this session; this profile is a targeted, real-data substitute sized to fit
  inside a single session rather than a 60-90 minute full run.
