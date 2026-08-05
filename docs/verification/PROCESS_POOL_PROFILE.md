# Process-pool dispatch profile — Part 1.5 B.3

Measured live against real ClickHouse-extracted records
(`origin1.CompanyTechnographics`), not synthetic data. Two questions: does
dispatching a single entity to a `ProcessPoolExecutor` worker pay for
itself, and does dispatching multiple entities concurrently deliver real
parallelism.

## 1. Single-entity dispatch: pickling/IPC overhead, no parallelism to offset it

`technology`, 1,503 records, `llm_config=None` (heuristic-only, no network
variance):

```
inline:           22.52s
pool-dispatched:   23.67s   (+1.15s, ~5% slower)
```

Cluster membership, `auto_matched`, `auto_excluded`, `candidate_pairs`,
`uncompared_pairs` all identical between the two — the dispatch is
behaviour-preserving, but on its own it is a small net loss. This is what
`_resolve_core`'s docstring means by "dispatch overhead would not be
recovered" below `_PROCESS_POOL_MIN_RECORDS`.

## 2. Two-entity concurrent dispatch: the actual value proposition

Two independent 1,503-record `technology`-shaped datasets (same table,
disjoint id ranges so they don't collide in the resolve_cache), run via
`asyncio.gather` against a shared 2-worker pool:

```
sequential (today's behaviour):        35.25s
concurrent + process pool (B.3):       23.00s
speedup:                               1.53x
```

Both entities' cluster structures verified byte-identical to their
sequential-run counterparts.

## 3. Windows `spawn` correctness

`ProcessPoolExecutor` on Windows uses `spawn`, which re-imports the calling
module in the worker — verified `_resolve_core` and its full call chain
(`_generate_candidate_pairs`, `_fit_match_model`, `_score_pair`, and
everything `fellegi_sunter.py`/`RecordCache` touches) round-trip through
pickle correctly with no import-time side effects triggering the
`_check_not_importing_main` guard (the only trip-up encountered was in the
throwaway verification script itself, missing `if __name__ ==
"__main__":` — not a defect in the shipped code, which is a library module
that scripts import into, not a `__main__`-executed entry point).

## 4. What this means for the two thresholds in code

- `_PROCESS_POOL_MIN_RECORDS = 500` (`resolve_entities.py`): per-entity
  floor below which dispatch is never worth considering, set well under
  `technology`'s 1,503 (which measured as a net win once paired with
  another entity) with headroom for entities in the few-hundred-to-1,503
  range that carry a comparably large candidate-pair count despite fewer
  records (e.g. a table with many duplicate-heavy blocks).
- **The node (`nodes/resolve_entities.py`) additionally requires at least
  TWO qualifying entities before it creates a pool at all** — directly
  encoding finding #1 above. A run with only one large entity (the common
  case for many merges) runs that entity inline, exactly as before B.3,
  and pays zero pool-creation or pickling overhead.

## 5. Open item

This profile used two same-sized synthetic-but-real datasets for a clean
A/B. The real Boeing corpus's entity-size distribution (`experience`
34,782, `technology` 1,503, `company_relation` 559, everything else under
100) was not re-profiled entity-by-entity through the pool — the full live
Boeing run (`docs/verification/BASELINE_V1.md`) is the end-to-end proof for
that specific, real workload.
