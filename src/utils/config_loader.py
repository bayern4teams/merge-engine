"""
Configuration loader with Pydantic validation and environment variable resolution.

Loads config.yaml, resolves ${ENV_VAR} placeholders, and validates against Pydantic models.
Origin databases are loaded dynamically via the Gradio UI at runtime and are NOT
part of the config file.

All origin and destination credentials are entered interactively in the UI.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# Load .env so ${ENV_VAR} placeholders in config.yaml resolve regardless of
# which module is imported first (client.py also loads it; this just makes
# config_loader independently correct).
try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]

    # Resolve .env relative to the project root (config_loader.py is at
    # src/utils/config_loader.py), so placeholders resolve regardless of CWD.
    _ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(_ENV_FILE, override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Pydantic Models (canonical definitions)
# ---------------------------------------------------------------------------


class DestinationConfig(BaseModel):
    """Configuration for the destination database (PostgreSQL or ClickHouse)."""

    type: str = "postgresql"
    host: str = ""
    port: int = 5432
    user: str = ""
    password: str = ""
    database: str = ""
    schema_name: str = Field(
        default="public",
        alias="schema",
        description="Database schema name",
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = ("postgresql", "clickhouse")
        if v not in allowed:
            raise ValueError(f"Destination type must be one of {allowed}, got '{v}'")
        return v


class LLMConfig(BaseModel):
    """Configuration for the LLM service."""

    provider: str = "openai"  # "openai" | "azure" | "anthropic"
    model: str
    # Tried in order after ``model`` whenever a call hits a quota/billing
    # error (LLMClient._advance_model) — a same-account retry can't recover
    # from that, so the client moves to the next model instead of failing.
    fallback_models: list[str] = Field(default_factory=list)
    api_key: str
    base_url: str | None = None
    temperature: float = 0.0
    structured_output: bool = True
    # Hybrid-LLM feature gates: heuristics always run; these enable the
    # LLM fallback for borderline entity pairs / unmappable fields.
    entity_resolution_enabled: bool = True
    field_mapping_enabled: bool = True

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

    destination: DestinationConfig
    llm: LLMConfig
    retry: RetryConfig = Field(default_factory=RetryConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*; override values win."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
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

# Part 1.6.A: platform-by-platform overrides live in a gitignored overlay next
# to config.yaml, deep-merged over the committed base. config.yaml stays the
# committed default; a machine's ports/host/base_url/tracing settings go here so
# moving the project to another platform is an edit to one gitignored file, not
# a diff against a tracked one. Written by the Settings page (PUT /api/config/
# platform), never containing secrets (those stay in .env / ${ENV_VAR}).
PLATFORM_OVERRIDES_PATH = Path("config") / "platform_overrides.yaml"

_SECRET_CONFIG_KEYS = ("password", "passwd", "secret", "api_key", "token")


def load_platform_overrides() -> dict[str, Any]:
    """Return the platform overlay dict, or {} if none exists."""
    if not PLATFORM_OVERRIDES_PATH.exists():
        return {}
    with open(PLATFORM_OVERRIDES_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_platform_overrides(overrides: dict[str, Any]) -> None:
    """Persist the platform overlay. Secrets are refused, never written."""
    stripped = _strip_config_secrets(overrides)
    PLATFORM_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLATFORM_OVERRIDES_PATH, "w", encoding="utf-8") as fh:
        yaml.safe_dump(stripped, fh, default_flow_style=False, sort_keys=False)


def _strip_config_secrets(value: Any) -> Any:
    """Recursively blank secret-ish values so a config dict is safe to return."""
    if isinstance(value, dict):
        return {
            k: ("" if k.lower() in _SECRET_CONFIG_KEYS and v else _strip_config_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_strip_config_secrets(v) for v in value]
    return value


def load_raw_config_dict(
    config_path: str | None = None, resolve_env: bool = True
) -> dict[str, Any]:
    """The merged (base + platform overlay) config as a plain dict.

    Unlike :func:`load_config` this does not validate into the Pydantic model —
    it is what the Settings API introspects and returns (secrets stripped by the
    caller). ``resolve_env=False`` keeps ``${VAR}`` placeholders visible so the
    UI can show which values come from the environment.
    """
    config_path = config_path or _DEFAULT_CONFIG_PATH
    with open(config_path, encoding="utf-8") as fh:
        base = yaml.safe_load(fh) or {}
    merged = _deep_merge(base, load_platform_overrides())
    return _resolve_env_vars(merged) if resolve_env else merged


def load_config(
    config_path: str | None = None,
) -> Config:
    """Load, resolve, and validate the application configuration.

    The committed ``config.yaml`` is the base; a gitignored
    ``config/platform_overrides.yaml`` (if present) is deep-merged over it so a
    per-machine setting never requires editing the tracked file. ``${ENV_VAR}``
    placeholders are then resolved.

    Parameters
    ----------
    config_path : str, optional
        Path to the main YAML config.  Defaults to ``config/config.yaml``.

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

    # Load base config, then deep-merge the gitignored platform overlay over it.
    with open(config_path, encoding="utf-8") as fh:
        config_data = yaml.safe_load(fh) or {}
    config_data = _deep_merge(config_data, load_platform_overrides())

    # Resolve environment variables
    config_data = _resolve_env_vars(config_data)

    return Config(**config_data)


def build_destination_config(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    schema: str = "public",
) -> DestinationConfig:
    """Build a DestinationConfig from runtime values (e.g., from the UI).

    This is the primary way destination config is created — via user input
    in the Gradio UI, not from the config file.

    Parameters
    ----------
    host : str
        PostgreSQL hostname or IP address.
    port : int
        PostgreSQL port number.
    user : str
        PostgreSQL username.
    password : str
        PostgreSQL password.
    database : str
        Database name.
    schema : str
        Schema name (default: "public").

    Returns
    -------
    DestinationConfig
        Validated destination configuration.
    """
    return DestinationConfig(
        type="postgresql",
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        schema=schema,
    )


def build_origin_config(
    origin_id: str,
    db_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    priority: int = 0,
) -> dict[str, Any]:
    """Build an origin config dict from runtime values (e.g., from the UI).

    Origin configs are created dynamically by the user in the Gradio UI.
    They are not stored in config files.

    Parameters
    ----------
    origin_id : str
        Unique identifier for this origin (e.g., "crm", "hr_db").
    db_type : str
        Database type: "postgres" or "mongodb".
    host : str
        Hostname or IP address.
    port : int
        Port number.
    user : str
        Database username.
    password : str
        Database password.
    database : str
        Database name.
    priority : int
        Priority for conflict resolution (higher = preferred source).

    Returns
    -------
    dict
        Origin configuration dictionary.
    """
    return {
        "id": origin_id,
        "type": db_type,
        "connection": {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
        },
        "priority": priority,
    }
