# ADR 0017: Reject HierGAT

**Status:** rejected.

## Context

Audit §13 #3 named HierGAT (a hierarchical graph-attention model for entity resolution) as a
possible way to bring a relational-matching signal into `resolve_entities.py` — the same family
of idea the audit's own RELATER lead (ADR 0018) points at.

## Finding

The published HierGAT reference implementation targets PyTorch 1.4 and Python 3.7 — both years
past end-of-life relative to this project's stack, and the repository shows no meaningful
maintenance activity since. Adopting it directly would mean vendoring and patching an unmaintained
research codebase against current PyTorch/Python, for a component this project would then own
indefinitely. That cost is not justified when the *idea* HierGAT demonstrates — that relational
context (a company's asserted relationships to other companies) is real, useful matching
evidence — is directly implementable natively, without adopting any of HierGAT's code or its
graph-attention machinery.

HierGAT's own results corroborate the *direction* (relational signals measurably help entity
resolution), which is exactly the reasoning behind B.5's native `_relational_agreement` signal —
see ADR 0018 for the sibling case (RELATER) and B.5 in `.hermes/plans/` /
`TODO.md` for the native implementation this reasoning feeds.

## Decision

Do not adopt HierGAT, vendor its code, or attempt to port its model to a current PyTorch/Python.
Its finding — relational context is real matching evidence — is corroborating input to B.5's
native, bounded `_relational_agreement` signal, not a library dependency.

## Consequences

- No new PyTorch dependency, no unmaintained research code to patch and own.
- If a future need genuinely requires learned graph-attention over entity relationships (not
  just a bounded additive confidence boost), that is the trigger to revisit — evaluate whatever
  actively-maintained graph-attention library exists at that time, not resurrect HierGAT
  specifically.
