# ADR 0014: Replace comparison-budget truncation with costed blocking analysis

**Status:** accepted, tied to the Splink adoption in ADR 0004 but recorded separately because
it is its own architectural decision, not just a consequence of picking a matching library.

## Context

The builtin matcher (V2 / Part A) generates candidate pairs via blocking, and when a block
produces more pairs than a fixed comparison budget, it truncates: scores the N "most
discriminating" pairs and honestly reports the rest as uncompared (never silently drops them —
see the reconciliation report's `uncompared_pairs` field, live-verified at scale during BP-A2:
596,246,937 candidate pairs generated, 250,000 scored, the remaining ~596M reported, not
hidden). That honesty is correct, but the underlying failure mode — a blocking rule producing
far more pairs than can be scored — is still discovered *after* the pairs already exist.

## Decision

Replace the after-the-fact truncation with Splink's costed-blocking-before-execution approach:
`count_comparisons_from_blocking_rule` and `n_largest_blocks` estimate how many pairs a
blocking rule will generate **before** it runs, so an overly broad rule (the "⚠ Too broad"
warning at a configured pair budget) is caught and fixed at rule-design time, not discovered by
generating hundreds of millions of pairs and then deciding which fraction to score.

This does not remove the comparison-budget concept — it moves the check earlier, where it can
actually prevent the failure mode instead of only reporting its size honestly.

## Consequences

- The builtin engine's own truncation-and-report behavior stays as-is when the builtin engine is
  selected (ADR 0004: it remains available, not deleted); this ADR only changes behavior for
  entities running the Splink engine.
- A blocking rule flagged "too broad" must be a UI-surfaced, human-actionable warning (S-E, per
  `Branch_Plan.md`'s B.4), not a silent auto-adjustment — the person configuring matching for an
  entity type chooses how to narrow the rule, the system does not guess on their behalf.
