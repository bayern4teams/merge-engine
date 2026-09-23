# ADR 0013: Informatica — capability benchmark only, never a dependency

**Status:** accepted (as a non-decision: explicitly not adopted, not rejected as unsuitable —
out of category).

## Context

Informatica is a mature commercial data-integration and master-data-management product line
with capabilities (schema discovery, data profiling, entity resolution, lineage) that overlap
significantly with what Part B builds. It was reviewed as part of the tool survey.

## Decision

Informatica is not, and was never considered as, an installable dependency of this project — it
is a commercial product requiring licensing this project does not have and would not seek for
an in-process, locally-runnable pipeline. It was reviewed strictly as a **capability benchmark**:
what does a mature commercial discovery/matching/lineage product consider table-stakes, so that
Part B's own scope (profiling modes, confidence/evidence on every metadata row, human-review
workflows, cluster diagnostics) can be checked against an external bar rather than only against
this project's own prior assumptions.

## Consequences

- No code in this repository imports, links against, or depends on any Informatica product.
- Where a specific Part B phase description references matching an Informatica-class
  capability, that is a design-quality bar, not an integration requirement.
