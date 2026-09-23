# ADR 0005: Adopt Valentine as the first-pass schema matcher

**Status:** accepted.

## Context

Today, field mapping (`map_fields.py`) goes straight from exact/heuristic name matching to an
LLM call for anything ambiguous. That works, but it spends LLM tokens on ambiguity that a
purpose-built schema-matching algorithm can often resolve for free, and it has no evaluation
harness of its own — the only ground truth is whatever the LLM produces.

## Decision

Adopt Valentine (`pip install valentine`, delftdata/valentine) as a first pass in P-7's schema
matching, ahead of the existing deterministic exact/heuristic pass and the LLM:

- **Algorithms used:** `Coma`, `Cupid`, `DistributionBased`, `JaccardDistanceMatcher`,
  `SimilarityFlooding` — a mix of name-similarity, instance-based, and structural matchers, so
  no single failure mode (e.g. purely name-based) dominates.
- **Input is sampled, not full tables.** Valentine takes pandas/polars DataFrames; it runs on
  the same sampled rows the rest of the pipeline already pulls, never a full-table read.
- **Ships its own evaluation metrics** against ground truth, which is pointed at this project's
  own golden mapping set — giving schema matching an evaluation harness for the first time,
  independent of whatever the LLM happens to produce.

Ordering in P-7: Valentine's ranked candidates first, the deterministic exact/heuristic pass
from `map_fields.py` merges in, and the LLM sees only what remains ambiguous after both. This
directly implements "algorithm-first, LLM optional" and reduces field-mapping token spend.

## Consequences

- A field mapping's `origin` (`ALGORITHMIC` / `OSS_TOOL` / `LLM_ASSISTED` / `USER_CONFIRMED`,
  per the P-1 metadata schema) distinguishes a Valentine-sourced mapping from an LLM one, so a
  human reviewing mappings can see which ones are algorithm-backed.
- Canonical entity types (P-7) are defined once, source-independent, so Valentine's per-pair
  matching feeds a stable target rather than a moving one.
