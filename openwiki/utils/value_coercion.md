---
type: utility
title: Value Coercion
description: >
  Value coercion for merge-engine: source-value to destination-type reconciliation
  with magnitude parsing, date normalization, and type compatibility checks.
  Used by map_fields and PostgreSQLDestinationConnector.
tags: [utils, coercion, type-conversion, data-quality]
---

# Value Coercion

## Purpose

Handles reconciliation of source values to destination types. Used in two
contexts:

1. **Field mapping compatibility** — `map_fields` node checks if a source field
   can be mapped to a destination column before calling the LLM
2. **Load-time coercion** — `PostgreSQLDestinationConnector._coerce_to_column_type`
   adapts values at load time when the destination type differs from source

## File

`src/utils/value_coercion.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `coerce_value(value, target_type)` | Coerce a value to a target type |
| `check_type_compatibility(source_type, target_type)` | Check if types are compatible |

## Coercion Features

| Feature | Description |
|---------|-------------|
| **Magnitude parsing** | Parses numeric magnitudes (1000, 1000.5, 1e6) |
| **Date normalization** | Normalizes various date formats to target format |
| **Type compatibility** | Checks if a source type can be safely coerced to a target type |

## Coercion Stats

When used by `PostgreSQLDestinationConnector`, coercion attempts and failures
are tracked:

```python
coercion_stats = {"attempted": 0, "failed": 0}
for value in row:
    try:
        coerced = coerce_value(value, dest_type)
    except ValueError:
        coercion_stats["failed"] += 1
```

These stats surface in the reconciliation report's `type_coercion_failures` field.

## Test Coverage

- `tests/unit/test_value_coercion.py` — coercion, magnitude parsing, date normalization
