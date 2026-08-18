# ADR 0008: Adopt Great Expectations for format-level data quality — optional

**Status:** accepted, scoped narrowly.

## Context

`data_quality.py`'s `validate_data` node already gates the pipeline correctly (zero-row
families, quality-failure severity feeding the reconciliation grade — see Part A / v2). What it
does not have is format-level checks: is this an email-shaped string, a phone-shaped string, a
date in the expected format, a value from an expected set. Writing those by hand duplicates a
library that already has them well-tested.

## Decision

Adopt Great Expectations 1.x, scoped strictly to format-level expectations `data_quality.py`
does not already cover:

- `EphemeralDataContext` + a pandas backend over rows this project has already sampled — no
  Great Expectations project directory, no separate infrastructure, no persisted GX state.
- Results are read out and persisted as this project's own rows (P-1's metadata tables), not
  left inside a Great Expectations-owned store.
- `data_quality.py` remains the pipeline's actual gate. Great Expectations adds checks; it does
  not become a second gate, does not own the metadata graph, and does not own relationship
  discovery — those stay this project's own code, per the same reasoning that rejected
  OpenMetadata/DataHub as catalog owners (ADR 0010, ADR 0011).

## Consequences

- A Great Expectations expectation failing is reported through the *existing*
  `has_quality_failure`/severity mechanism `validate_data` already feeds into the reconciliation
  grade — not a parallel reporting path a human would have to learn separately.
- If Great Expectations is ever unavailable (not installed, or its extras group not selected),
  the pipeline must still run correctly using only `data_quality.py`'s existing checks —
  format-level checks are additive, never load-bearing for the gate to function at all.
