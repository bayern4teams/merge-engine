# ADR 0018: Reject RELATER's code, adopt its idea

**Status:** rejected as a dependency; its idea adopted natively (B.5).

## Context

Audit §13 #3 named RELATER (a relational entity-resolution research system) as a candidate
source for the same relational-matching signal HierGAT (ADR 0017) points at: using a record's
asserted relationships to other entities as additional matching evidence, alongside the existing
field-level heuristic/probabilistic scoring in `resolve_entities.py`.

## Finding

RELATER's published reference implementation is Python 2, with a total history of two commits —
effectively an unmaintained snapshot released alongside a paper, not a library meant for reuse.
Its benchmark harness and evaluation code are tightly bound to the paper's own datasets, with no
general-purpose API surface this project could call into without a substantial rewrite. There is
nothing here worth vendoring: no packaging, no stable interface, and a two-decade-old Python
runtime target.

What *is* worth taking from RELATER is its central idea, independently corroborated by HierGAT
(ADR 0017): two records that share asserted relationships to the same third parties are more
likely to be the same real-world entity, and this can be folded into an existing matching pipeline
as one more weighted signal rather than a separate matching stage.

The audit's own §00a2 finding (the Airbus "Defence and Space" blob — a false-merge caused by
exactly this class of relational reasoning done carelessly) is the reason this idea is scheduled
as B.5, not earlier: it lands as a small, bounded, *additive* signal
(`_relational_agreement`, `min(0.15 * len(shared), 0.3)` in `resolve_entities.py`'s weighted sum
— never sole justification for an auto-merge) with an explicit common-parent guard and the exact
Airbus case encoded as a negative regression fixture before the feature is built, not after.

## Decision

Do not adopt RELATER's code or attempt to port its Python 2 implementation. Implement its idea
natively as B.5's `_relational_agreement` signal in `resolve_entities.py`, built from this
project's own `build_relationship_index`, with the Airbus-blob negative fixture as its first test.

## Consequences

- No Python 2 code enters this codebase.
- B.5's native implementation is directly traceable to this decision — see
  `.hermes/plans/2026-09-14_025800-fix-v3-cto-recommendations.md` Phase 5 / `TODO.md` B.5 for
  the implementation and its live ground-truth regression gate.
