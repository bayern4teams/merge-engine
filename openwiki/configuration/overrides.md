---
type: configuration overrides
title: Platform & Session Overrides
description: >
  Configuration override system: platform_overrides.yaml for site-specific
  customizations, session env overrides for runtime per-session env vars,
  and provenance tracking.
tags: [configuration, overrides, platform, session]
---

# Platform & Session Overrides

## Purpose

Two override systems provide runtime configuration flexibility without
editing the base `config/config.yaml`.

## File

`src/utils/config_loader.py` — platform overrides
`src/api/main.py` — session overrides and provenance

## Platform Overrides

### File

`config/platform_overrides.yaml` (gitignored)

### Loading

```python
def load_platform_overrides() -> dict[str, Any]:
    if not PLATFORM_OVERRIDES_PATH.exists():
        return {}
    with open(PLATFORM_OVERRIDES_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
```

### Deep Merge

Overrides are deep-merged into the base config:

```python
def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
```

### Saving (with Secret Stripping)

```python
def save_platform_overrides(overrides: dict[str, Any]) -> None:
    stripped = _strip_config_secrets(overrides)
    PLATFORM_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLATFORM_OVERRIDES_PATH, "w", encoding="utf-8") as fh:
        yaml.safe_dump(stripped, fh, default_flow_style=False, sort_keys=False)
```

Secret keys are blanked: `password`, `passwd`, `secret`, `api_key`, `token`.

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/config/platform` | PUT | Deep-merge a platform override into `platform_overrides.yaml` |
| `/api/config` | GET | Returns effective config, overrides, and provenance |

## Session Environment Overrides

### Storage

```python
_SESSION_ENV_OVERRIDES: dict[str, str] = {}  # in src/api/main.py
```

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/config/env` | POST | Apply session-only env overrides |
| `/api/config/env/reset` | POST | Clear all session overrides |

### Scope

Session overrides are process-scoped: they persist across API calls but are
reset on process restart. They are not saved to disk.

### Usage in Config Loading

When `load_config()` is called, session env overrides are applied to the
process environment temporarily, so `${ENV_VAR}` placeholders resolve against
the session overrides.

## Provenance Tracking

The `_provenance()` closure traces each config value:

```python
def _provenance(section: str, key: str) -> str:
    if key in (overlay.get(section) or {}):
        return "override"
    raw_val = (base_raw.get(section) or {}).get(key)
    if isinstance(raw_val, str) and raw_val.startswith("${") and raw_val.endswith("}"):
        return "env"
    return "file"
```

Returned in the `/api/config` response alongside the effective config.

## Test Coverage

- `tests/unit/test_config_loader.py` — override loading, deep merge, secret stripping
- `tests/integration/test_config_resolution.py` — full config resolution
