# ADR 0004: Adopt Splink 4.0.16 as the matcher, replacing the builtin engine

**Status:** accepted, gated. Splink becomes the *default* engine only when BP-B4's measured
A/B gate says so — this ADR adopts it as an available engine, not as the default from day one.

## Context

The builtin Fellegi-Sunter matcher (V2 / Part A) is hand-built: hand-tuned weights, no term-
frequency adjustment, a comparison-budget truncation instead of costed blocking, no waterfall
explainability, no threshold optimisation from labelled data, no cluster-quality diagnostics.
Every one of those gaps is something a mature open-source probabilistic matching library
already solves.

## Decision

Adopt Splink 4.0.16 (`pip install splink`, already an installed dependency behind the `splink`
extra in `pyproject.toml`) as a second matching engine, selectable per entity type alongside the
builtin. Full verified API surface in [../tool-evaluation.md](../tool-evaluation.md).

Key properties that made this the only realistic candidate:

- **Runs 100% locally.** Pure Python, compiles its model to SQL, executes on DuckDB in-process
  (already a dependency). No server, no cloud, no network call — verified by introspecting the
  installed package, not by reading marketing copy.
- **Trained parameters, not asserted ones.** `estimate_u_using_random_sampling` +
  `estimate_parameters_using_expectation_maximisation` replace hand-tuned weights with
  parameters fit to this project's own data.
- **Costed blocking before execution.** `count_comparisons_from_blocking_rule` +
  `n_largest_blocks` answer "how many pairs will this rule generate" *before* generating them —
  the structural fix for the comparison-budget-truncation failure mode (see ADR 0014).
- **Explainability built in.** `waterfall_chart` shows exactly which fields drove a match
  decision, per pair — something the builtin engine has no equivalent for.
- **Threshold optimisation from labels**, not a hardcoded 0.85/0.95. Never claim recall without
  labels (`accuracy_analysis_from_labels_table`).

## Consequences

- Every phase that calls a Splink API verifies `splink.__version__`/`installed_minor_version`
  at runtime first and refuses to run against an unexpected major — this ADR pins behaviour
  against 4.0.16 specifically, not "whatever Splink happens to be installed."
- The builtin engine is kept, not deleted. A regression in the Splink path is a config change
  (switch the entity's `engine` back to builtin), never a revert of this ADR.
- Splink does not become the default engine by this ADR alone. BP-B4 measures both engines over
  the 560 golden pairs plus the Airbus and Boeing hand-merge baselines; Splink becomes default
  only when it beats the builtin on **both** precision and recall, and the Airbus run's golden
  parity improves on the measured 12.0% Boeing figure. `tests/eval/test_matcher_recall.py` is
  the mechanism, not a written assertion.
