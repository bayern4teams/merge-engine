---
type: configuration models
title: Configuration Models
description: >
  Pydantic configuration models for merge-engine: Config, DestinationConfig,
  LLMConfig, RetryConfig, and ExtractionConfig with field definitions,
  defaults, and validation rules.
tags: [configuration, pydantic, models, schema]
---

# Configuration Models

## Purpose

Pydantic models providing type-safe configuration validation at load time.

## File

`src/utils/config_loader.py`

## Model Hierarchy

```python
class Config(BaseModel):
    destination: DestinationConfig
    llm: LLMConfig
    retry: RetryConfig
    extraction: ExtractionConfig
```

### Config

Top-level configuration container.

| Field | Type | Description |
|-------|------|-------------|
| `destination` | `DestinationConfig` | Destination database settings |
| `llm` | `LLMConfig` | LLM service settings |
| `retry` | `RetryConfig` | Retry policy defaults |
| `extraction` | `ExtractionConfig` | Extraction behavior defaults |

### DestinationConfig

Destination database configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `str` | `"postgresql"` | `"postgresql"` or `"clickhouse"` |
| `host` | `str` | `""` | Database host |
| `port` | `int` | `5432` | Database port |
| `user` | `str` | `""` | Database user |
| `password` | `str` | `""` | Database password |
| `database` | `str` | `""` | Database name |
| `schema_name` | `str` | `"public"` | Database schema (alias: `schema`) |

Validation: `type` must be one of `("postgresql", "clickhouse")`.

### LLMConfig

LLM service configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | `str` | `"openai"` | `"openai"`, `"azure"`, `"anthropic"` |
| `model` | `str` | *(required)* | Model name |
| `fallback_models` | `list[str]` | `[]` | Quota fallback chain |
| `api_key` | `str` | *(required)* | API key |
| `base_url` | `str \| None` | `None` | Override API base URL |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `structured_output` | `bool` | `True` | Enable JSON schema response |
| `entity_resolution_enabled` | `bool` | `True` | Enable LLM in entity resolution |
| `field_mapping_enabled` | `bool` | `True` | Enable LLM in field mapping |

Validation: `provider` must be one of `{"openai", "azure", "anthropic"}`.

### RetryConfig

Retry policy defaults.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_attempts` | `int` | `3` | Maximum retry attempts |
| `backoff` | `str` | `"exponential"` | `"exponential"` or `"linear"` |
| `min_backoff_seconds` | `float` | `1.0` | Minimum backoff interval |
| `max_backoff_seconds` | `float` | `30.0` | Maximum backoff interval |

### ExtractionConfig

Extraction behavior defaults.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `batch_size` | `int` | *(configurable)* | Rows per batch |
| `limit` | `int \| None` | `None` | Maximum rows (None = unlimited) |

## Loading Functions

| Function | Return | Description |
|----------|--------|-------------|
| `load_config()` | `Config` | Full load with validation and env resolution |
| `load_raw_config_dict(resolve_env: bool = True)` | `dict` | Plain dict without Pydantic validation |
| `load_platform_overrides()` | `dict` | Load platform_overrides.yaml |
| `save_platform_overrides(overrides)` | `None` | Save overrides (secrets stripped) |

## Test Coverage

- `tests/unit/test_config_loader.py` — all model validation, override merging, secret stripping
