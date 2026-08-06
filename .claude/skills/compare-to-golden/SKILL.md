---
name: compare-to-golden
description: Perform row-level diff comparison of an app run's output against a hand-merged golden baseline.
---

# Compare To Golden Skill

Compares the PostgreSQL destination tables (`global_warehouse.public`) against the hand-merged golden schema (`global_warehouse_golden.<subject>`) using natural keys and resolved foreign keys.

## Invocation Command
```bash
python scripts/diagnostics/compare_to_golden.py <subject>
```

Where `<subject>` is one of `boeing`, `airbus`, or `lockheed`.

## Description
Generates a JSON diff report containing matched rows, missing rows, extra rows, and overall parity percentage, saving output to `docs/verification/<subject>_diff.json`.
