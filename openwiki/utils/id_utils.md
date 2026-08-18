---
type: utility
title: UUID Utilities
description: >
  UUID generation and validation utilities: random UUIDs for new records,
  deterministic UUIDs for reproducible entity IDs, hex/dash format conversion.
tags: [utils, uuid, id-generation, deterministic]
---

# UUID Utilities

## Purpose

Provides UUID generation and validation for merge-engine. Two modes:

1. **Random UUIDs** — for new records with no deterministic ID needed
2. **Deterministic UUIDs** — for reproducible entity IDs (same inputs → same ID)

## File

`src/utils/id_utils.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `generate_uuid()` | Generate a random UUID-4 |
| `generate_deterministic_uuid(key_data)` | Generate a UUID from hash of key_data |
| `validate_uuid(uuid_str)` | Validate UUID format |
| `hex_to_dash(hex_str)` | Convert hex UUID to dashed format |
| `dash_to_hex(dash_str)` | Convert dashed UUID to hex format |

## Deterministic UUID

Used by `resolve_entities` to ensure the same entity pair always gets the
same cluster ID, enabling idempotent re-runs:

```python
def generate_deterministic_uuid(key_data: bytes) -> str:
    """Generate a UUID-5 (SHA-1) from key data for deterministic reproducibility."""
    hash = hashlib.sha512(key_data).hexdigest()
    return str(uuid.UUID(hash[:32]))  # Convert to UUID format
```

## Test Coverage

- `tests/unit/test_id_utils.py` — UUID generation, validation, format conversion
