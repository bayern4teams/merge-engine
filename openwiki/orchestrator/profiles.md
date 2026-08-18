---
type: configuration
title: Saved Run Profiles
description: Saved run profiles pin the LLM's non-deterministic metadata decisions (entity_tables, matching_rules, conflict_policy, field_mappings) so re-runs on identical data produce identical results.
tags: [profiles, configuration, reproducibility, pins]
---

# Saved Run Profiles

## Purpose

Two full runs of identical data produce identical matching (28,170 candidate pairs, 2,482 clusters) but **different destination schemas** — 15 columns versus 13 — because the LLM's field mapping varied on whether `customer_id` folds into `id`. Matching is deterministic; the metadata decisions around it are not. Re-running a merge is therefore not a reproducible operation until those decisions are pinned. A profile pins them.

## Pinned Keys

Four categories of metadata are pinned in a profile (never recomputed):

```python
PINNED_KEYS = (
    "entity_tables",  # O.1 — which source tables hold each entity
    "matching_rules",  # O.2 — blocking keys / unique ids / strictness
    "conflict_policy",  # O.3 — per-field survivorship
    "field_mappings",  # O.4 — unified field → destination column
)
```

## Security

Secrets are deliberately NOT stored. `connection.password` is stripped on save via `strip_secrets()` and re-supplied at run time by the caller. Key names containing `password`, `passwd`, `secret`, `api_key`, `token` are blanked out.

Profiles are matched back to live credentials on a **coordinate key** (`host|port|database|user|type`) rather than by index, so reordering origins cannot silently point a profile at the wrong server.

## File Layout

Profiles are saved to `config/profiles/<safe_name>.json` where `safe_name` strips all non-filename characters.

## Key Functions

| Function | Purpose |
|----------|---------|
| `build_profile(state, origins, destination)` | Capture pinned keys from current workflow state |
| `save_profile(name, profile_dict)` | Write to `config/profiles/<name>.json` |
| `load_profile(name)` | Read from disk, re-attach live passwords |
| `list_profiles()` | List all profile names |
| `delete_profile(name)` | Remove from disk |
| `strip_secrets(value)` | Recursively blank password-like values |
| `apply_profile(profile_dict, config)` | Re-apply profile pins to a config |
| `capture_from_state(state)` | Snapshot current MergeState into profile format |
| `connection_key(origin)` | Coordinate key for matching to live credentials |
| `safe_name(name)` | Filesystem-safe name (max 80 chars) |
| `_now()` | ISO 8601 timestamp |

## Test Coverage

- `tests/unit/test_run_profiles.py` — save/load round-trip, secret stripping, name sanitization
- `tests/unit/test_dest_dedup.py` — destination dedup against loaded profiles
