"""
Configuration loader with Pydantic validation and environment variable resolution.

Loads config.yaml, resolves ${ENV_VAR} placeholders, optionally merges
config.secrets.yaml, and validates against Pydantic models.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Pydantic Models (canonical definitions)
# ---------------------------------------------------------------------------


class OriginConnection(BaseModel):
    """Connection parameters for an origin database."""

    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    uri: str | None = None


class OriginConfig(BaseModel):
    """Configuration for a single origin database."""

    id: str
    type: str  # "postgres" | "mongodb" | "databricks"
    connection: OriginConnection
    priority: int = Field(default=0, ge=0)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        valid = {"postgres", "mongodb", "databricks"}
        if v not in valid:
            raise ValueError(f"Origin type must be one of {valid}, got '{v}'")
        return v


class DestinationConfig(BaseModel):
    """Configuration for the Databricks destination."""

    type: str = "databricks"
    host: str
    http_path: str
    token: str
    catalog: str
    schema_name: str = Field(
        alias="schema",
        description="Databricks schema (namespace) name",
    )
    tables: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of entity_type -> destination_table_name",
    )

    @field_validator("http_path")
    @classmethod
    def validate_http_path(cls, v: str) -> str:
        pattern = r"^/sql/\d\.\d/warehouses/[a-zA-Z0-9-]+$"
        if not re.match(pattern, v):
            raise ValueError(
                f"http_path must match pattern '^/sql/\\d\\.\\d/warehouses/[a-zA-Z0-9-]+$', got '{v}'"
            )
        return v


class LLMConfig(BaseModel):
    """Configuration for the LLM service."""

    provider: str = "openai"  # "openai" | "azure" | "anthropic"
    model: str
    api_key: str
    base_url: str | None = None
    temperature: float = 0.0

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        valid = {"openai", "azure", "anthropic"}
        if v not in valid:
            raise ValueError(f"LLM provider must be one of {valid}, got '{v}'")
        return v


class RetryConfig(BaseModel):
    """Retry configuration."""

    max_attempts: int = 3
    backoff: str = "exponential"  # "exponential" | "linear"
    min_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0


class ExtractionConfig(BaseModel):
    """Extraction defaults."""

    default_batch_size: int = 1000
    max_batch_size: int = 10000


class Config(BaseModel):
    """Top-level application configuration."""

    origins: list[OriginConfig] = Field(min_length=1)
    destination: DestinationConfig
    llm: LLMConfig
    blocking_keys: dict[str, list[str]] = Field(default_factory=dict)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)

    @field_validator("origins")
    @classmethod
    def check_unique_ids(cls, v: list[OriginConfig]) -> list[OriginConfig]:
        ids = [o.id for o in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Origin IDs must be unique")
        return v


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*; override values win."""
    result = base.copy()
    for key, val in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _resolve_env_vars(obj: Any) -> Any:
    """Replace ${VAR_NAME} placeholders with environment variable values."""
    pattern = re.compile(r"\$\{(\w+)\}")

    def _replacer(text: str) -> str:
        def _fn(match: re.Match) -> str:
            var_name = match.group(1)
            # Environment variables take final precedence
            env_val = os.environ.get(var_name)
            if env_val is not None:
                return env_val
            # Fall back to placeholder unchanged if not found
            return match.group(0)

        return pattern.sub(_fn, text)

    if isinstance(obj, str):
        return _replacer(obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = str(Path("config/config.yaml"))
_DEFAULT_SECRETS_PATH = str(Path("config/config.secrets.yaml"))


def load_config(
    config_path: str | None = None,
    secrets_path: str | None = None,
) -> Config:
    """Load, merge, resolve, and validate the application configuration.

    Parameters
    ----------
    config_path : str, optional
        Path to the main YAML config.  Defaults to ``config/config.yaml``.
    secrets_path : str, optional
        Path to the secrets YAML file.  Defaults to ``config/config.secrets.yaml``.
        If the file does not exist it is silently skipped.

    Returns
    -------
    Config
        Validated Pydantic configuration model.

    Raises
    ------
    pydantic.ValidationError
        If the configuration fails validation.
    FileNotFoundError
        If the main config file does not exist.
    """
    config_path = config_path or _DEFAULT_CONFIG_PATH
    secrets_path = secrets_path or _DEFAULT_SECRETS_PATH

    # Load base config
    with open(config_path, "r", encoding="utf-8") as fh:
        config_data = yaml.safe_load(fh)

    # Load secrets (optional)
    secrets_data: dict[str, Any] | None = None
    if secrets_path and Path(secrets_path).exists():
        with open(secrets_path, "r", encoding="utf-8") as fh:
            secrets_data = yaml.safe_load(fh)

    # Resolve environment variables
    config_data = _resolve_env_vars(config_data)

    # Merge secrets (secrets take precedence over config.yaml values)
    if secrets_data:
        config_data = _deep_merge(config_data, secrets_data)

    # Re-resolve after merge (secrets might contain env-var references too)
    config_data = _resolve_env_vars(config_data)

    return Config(**config_data)