# ADR 0006: Adopt Desbordante for deep profiling — optional, AGPL-isolated

**Status:** accepted, with a hard isolation requirement.

## Context

P-5 (candidate & composite keys) and P-6 (relationship discovery) both need exact/approximate
functional dependencies, inclusion dependencies, and unique column combinations on sampled
data. Nothing in this project computes any of those today; a pruned in-house search would
duplicate a well-tested C++ algorithm library for a marginal, non-default feature.

## Decision

Adopt Desbordante (`pip install desbordante`, Python >= 3.10) as an **optional** deep-profiling
pass, feeding P-5's composite-key search and P-6's relationship discovery with candidates the
SQL-native primary path did not already find.

**Desbordante is licensed AGPL-3.0.** That license is incompatible with being a base
dependency of a project that is not itself AGPL, so this adoption comes with hard constraints,
not just a note:

- Desbordante lives behind an **extras install** (a new `pyproject.toml` optional-dependencies
  group, e.g. `deep-profile`), never in the base dependency list.
- It is **never imported at module scope** anywhere in `src/` — only inside the function that
  actually calls it, guarded so a default install of this project never touches AGPL code and
  never links against it at import time.
- The integration lives in one file (`src/mcp_server/tools/deep_profile.py`), not scattered
  across the P-5/P-6 modules, so the AGPL boundary is one file to audit, not many.
- Output (candidate keys, functional dependencies, inclusion dependencies) is copied into this
  project's own PostgreSQL metadata tables (P-1) as plain data — the metadata graph itself never
  depends on Desbordante being installed to be read.

## Consequences

- P-5/P-6 must degrade gracefully — a full, correct pipeline run without the `deep-profile`
  extra installed, using only the SQL-native primary path, is a requirement, not a nice-to-have.
- Anyone distributing this project (not just running it) with the extra installed needs to
  understand the AGPL's network-copyleft implications for their own deployment; this ADR
  records that constraint so it is never discovered after the fact.
