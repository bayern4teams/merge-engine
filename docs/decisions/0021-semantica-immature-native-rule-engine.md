# ADR 0021: `semantica` immature at install time — native rule engine instead

**Status:** accepted. `semantica` rejected for now; the identical rules-as-data contract
implemented natively (B.4).

## Context

Audit §13 #9 named `semantica` as a candidate Rete/Datalog rule engine for post-merge
consistency validation (B.4): rules like "an ACQUIRED company's status can't predate a later
investment round" evaluated against just-written destination facts, folding violations into the
existing `data_quality.red_flags` mechanism. The plan itself pre-authorized this exact
contingency: "first verify the PyPI package installs and its Rete/Datalog engine works
standalone; if immature, implement the identical rules-as-data contract with plain-Python clause
evaluation and record the deviation as an ADR."

## Finding (verified live, 2026-09-14)

1. **Version.** `pip index versions semantica` → latest `0.6.8`, entirely pre-1.0. Not
   disqualifying by itself, but a signal worth weighing alongside what follows.
2. **Dependency footprint.** `pip install semantica` pulled in a wildly disproportionate
   dependency tree for what the audit describes as a narrow Rete/Datalog rule engine:
   `librosa` (audio processing), `opencv-python` (73.8 MB, computer vision), `matplotlib`,
   `numba`/`llvmlite` (a JIT compiler toolchain), `onnxruntime` (23.6 MB, ML inference),
   `plotly`. None of these have any plausible relationship to evaluating boolean rule clauses
   against a handful of destination-table facts. This is the actual disqualifying signal: the
   package is evidently a broad, general-purpose toolkit under a name that suggested a narrow
   rule engine, not the focused library the audit's lead assumed.
3. **Install outcome.** The install did not complete in this environment (`OSError: [Errno 28]
   No space left on device` while downloading `plotly`), reproduced twice — once against the
   default `TMPDIR`, once redirected to a filesystem with 149 GB free — confirming the failure
   tracks the sheer size of the dependency tree, not an incidental temp-space fluke. Whether or
   not this specific failure reproduces in every environment, a rule-engine dependency that
   drags in an audio/CV/ML-inference stack is the wrong shape for this job regardless of whether
   it happens to install cleanly somewhere else.

Together this is exactly the "immature/inappropriate for narrow adoption" case the plan's own
contingency anticipated — not a close call.

## Decision

Do not adopt `semantica`. Implement B.4's rules-as-data contract natively:
`config/consistency_rules.yaml` (rule id, a boolean `when` clause, severity) evaluated by plain
Python clause matching in a new `validate_graph_consistency` orchestrator node — no rule-engine
dependency at all. This is the ladder's own "does this need to exist at all / does stdlib
cover it" reasoning: a handful of AND/comparison clauses over a small, just-written fact set
does not need a Rete network — a linear scan over the rules, each evaluated against the fetched
rows, is the entire problem.

## Consequences

- Zero new dependency. No audio/CV/ML-inference stack enters this project for a rule check.
- The native evaluator is intentionally narrow: boolean clauses over dotted field paths
  (`company.status`, `investment_round.valid_from`), AND-only composition, no forward-chaining
  or derived facts. If a genuine need for chained/derived-fact rules appears later, that is the
  trigger to re-evaluate rule engines generally — not to revisit `semantica` specifically, whose
  disqualifying issue (a rule engine bundled with an unrelated ML/media toolkit) is about that
  package, not about rule engines as a category.
- Revisit if `semantica` ships a slimmed, rule-engine-only distribution, or if a different,
  narrowly-scoped Rete/Datalog library appears worth evaluating on its own merits.
