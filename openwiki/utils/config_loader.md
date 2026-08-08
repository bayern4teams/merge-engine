---
type: utility
title: Configuration Loader
description: >
  Configuration loading and validation for merge-engine: load_config() with
  Pydantic validation, ${ENV_VAR} resolution, platform overrides, and
  environment variable resolution.
tags: [utils, configuration, pydantic, validation]
---

# Configuration Loader

## Purpose

Loads and validates configuration from `config/config.yaml`, resolving
`${ENV_VAR}` placeholders and deep-merging platform overrides.

## File

`src/utils/config_loader.py`

## Key Functions

| Function | Return | Description |
|----------|--------|-------------|
| `load_config()` | `Config` | Full load with Pydantic validation |
| `load_raw_config_dict(resolve_env: bool)` | `dict` | Plain dict, optional env resolution |
| `load_platform_overrides()` | `dict` | Load `config/platform_overrides.yaml` |
| `save_platform_overrides(overrides)` | `None` | Save overrides (secrets stripped) |

## Loading Flow

1. Load `config/config.yaml` (base)
2. Deep-merge `config/platform_overrides.yaml` (if exists)
3. Resolve `${ENV_VAR}` placeholders from environment
4. Validate against `Config` Pydantic model

## Pydantic Models

- `Config` — top-level container
- `DestinationConfig` — destination database settings
- `LLMConfig` — LLM provider settings
- `RetryConfig` — retry policy defaults
- `ExtractionConfig` — extraction behavior defaults

## Test Coverage

- `tests/unit/test_config_loader.py` — loading, validation, override merging (13KB)
