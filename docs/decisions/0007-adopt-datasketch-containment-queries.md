# ADR 0007: Adopt datasketch `MinHashLSHEnsemble` for containment queries

**Status:** accepted, optional accelerator.

## Context

`join_graph.py` (V2 / Part A) already confirms a candidate relationship by an exact SQL
inclusion-dependency check: is every distinct value of column A actually present in column B.
That check is correct and pushes work into SQL, but it is O(candidate pairs) — for a schema
with hundreds of columns across origins, checking every column pair exactly does not scale.

## Decision

Adopt `datasketch`'s `MinHashLSHEnsemble` as a candidate-pruning step ahead of the exact SQL
check, not a replacement for it. `MinHashLSHEnsemble` answers containment ("is column A's value
set contained in column B's"), which is the right query shape for inclusion dependencies —
plain MinHash LSH answers Jaccard similarity, a different question that would misrank a
small-lookup-table-contained-in-a-large-fact-table relationship.

Used in P-6: sample column value sets, build the ensemble index, query it for likely-contained
pairs, and only run the exact SQL inclusion check (already built) against that shortlist.

## Consequences

- The exact SQL check remains the sole source of truth for whether a relationship is asserted —
  `datasketch` only affects *which candidates get checked*, never *whether a checked candidate
  passes*. No relationship in the metadata graph is ever asserted on approximate evidence alone.
- This is purely a performance optimization for the many-columns case; a schema small enough
  that exhaustive exact checking is already fast does not need this path exercised, and P-6's
  tests must cover both the pruned and the unpruned code path.
