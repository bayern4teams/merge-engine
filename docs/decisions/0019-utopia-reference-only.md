# ADR 0019: Utopia — reference only, not adopted

**Status:** reference only (design-review pointer), not adopted.

## Context

Audit §13 #4 named Utopia, a standalone Rust system for knowledge-graph construction with a
decision-ledger design (every assertion recorded as a discrete, evidenced decision rather than
an opaque mutation), as a candidate architecture reference.

## Finding

This project's own destination schema (the `kg` schema — `knowledge_item`, `knowledge_fact`,
`evidence`, `entity`, `entity_name`, `md_override`, ...; see P-1's `metadata_graph.py` and the
audit's own P-1 evidence in `TODO.md`) is already a bitemporal, evidence-carrying knowledge graph
in this same spirit: every fact carries `valid_from`/`valid_to`, a source, a confidence, and — for
human corrections — an additive `md_override` row rather than a mutation of the original decision
(P-1's "additive-not-mutating" guarantee, mutation-tested). Utopia is a standalone Rust runtime
with its own storage engine; adopting it would mean running and operating a second knowledge-graph
system alongside the one this project already has in PostgreSQL, for a design goal this project
already meets.

What is genuinely worth studying is Utopia's decision-ledger *design* — how it structures the
evidence-per-assertion record, and any patterns for reconciling conflicting assertions over time
that this project's own `md_override`/human-review mechanism hasn't yet had to solve at scale.

## Decision

Do not adopt Utopia as a dependency or storage engine. Keep it as a design-review reference: when
this project's own decision-ledger mechanism (`md_override`, human review, evidence tracking)
needs to solve a problem it hasn't yet faced, Utopia's published design is a reasonable place to
look for a solved pattern before inventing one from scratch.

## Consequences

- No new runtime, no second knowledge-graph system to keep in sync with the primary one.
- If a future design review of `md_override`/evidence-tracking surfaces a gap Utopia's design
  addresses, that is the trigger to revisit this ADR with a concrete borrowed pattern — not a
  general adoption of Utopia itself.
