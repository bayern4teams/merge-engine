"""
Unit tests for the configuration loader module.

Tests config loading, validation, env var resolution, and error handling.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.utils.config_loader import (
    Config,
    DestinationConfig,
    LLMConfig,
    OriginConfig,
    OriginConnection,
    _deep_merge,
    _resolve_env_vars,
    load_config,
)


class TestDeepMerge:
    """Tests for the _deep_merge helper."""

    def test_simple_merge(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_list_override(self) -> None:
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = _deep_merge(base, override)
        assert result == {"items": [4, 5]}

    def test_empty_override(self) -> None:
        base = {"a": 1}
        override = {}
        result = _deep_merge(base, override)
        assert result == {"a": 1}


class TestResolveEnvVars:
    """Tests for environment variable resolution."""

    def test_no_placeholders(self) -> None:
        assert _resolve_env_vars("hello world") == "hello world"

    def test_single_placeholder_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "resolved_value")
        result = _resolve_env_vars("${MY_VAR}")
        assert result == "resolved_value"

    def test_single_placeholder_missing(self) -> None:
        result = _resolve_env_vars("${MISSING_VAR_12345}")
        # Unresolved placeholders remain unchanged
        assert result == "${MISSING_VAR_12345}"

    def test_dict_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "localhost")
        data = {"connection": {"host": "${HOST}", "port": 5432}}
        result = _resolve_env_vars(data)
        assert result == {"connection": {"host": "localhost", "port": 5432}}

    def test_list_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITEM", "value")
        data = ["${ITEM}", "static"]
        result = _resolve_env_vars(data)
        assert result == ["value", "static"]


class TestOriginConnection:
    """Tests for OriginConnection Pydantic model."""

    def test_uri_only(self) -> None:
        conn = OriginConnection(uri="mongodb://localhost:27017/db")
        assert conn.uri == "mongodb://localhost:27017/db"
        assert conn.host is None

    def test_full_connection(self) -> None:
        conn = OriginConnection(
            host="localhost",
            port=5432,
            user="admin",
            password="secret",
            database="mydb",
        )
        assert conn.host == "localhost"
        assert conn.port == 5432
        assert conn.database == "mydb"


class TestOriginConfig:
    """Tests for OriginConfig Pydantic model."""

    def test_valid_postgres(self) -> None:
        config = OriginConfig(
            id="crm",
            type="postgres",
            connection=OriginConnection(host="localhost", port=5432, user="u", password="p", database="db"),
            priority=1,
        )
        assert config.id == "crm"
        assert config.type == "postgres"

    def test_invalid_type(self) -> None:
        with pytest.raises(ValidationError, match="Origin type must be one of"):
            OriginConfig(
                id="bad",
                type="oracle",  # type: ignore
                connection=OriginConnection(),
            )

    def test_negative_priority(self) -> None:
        with pytest.raises(ValidationError, match="greater_than_equal"):
            OriginConfig(
                id="neg",
                type="postgres",
                connection=OriginConnection(),
                priority=-1,
            )


class TestDestinationConfig:
    """Tests for DestinationConfig Pydantic model."""

    def test_valid_destination(self) -> None:
        dest = DestinationConfig(
            host="test.databricks.com",
            http_path="/sql/1.0/warehouses/abc123",
            token="test_token",
            catalog="main",
            schema="unified",
        )
        assert dest.type == "databricks"
        assert dest.http_path == "/sql/1.0/warehouses/abc123"

    def test_invalid_http_path(self) -> None:
        with pytest.raises(ValidationError, match="http_path must match pattern"):
            DestinationConfig(
                host="test",
                http_path="invalid/path",
                token="test",
                catalog="main",
                schema="test",
            )


class TestLLMConfig:
    """Tests for LLMConfig Pydantic model."""

    def test_valid_openai(self) -> None:
        llm = LLMConfig(provider="openai", model="gpt-4o", api_key="key123")
        assert llm.provider == "openai"

    def test_invalid_provider(self) -> None:
        with pytest.raises(ValidationError, match="LLM provider must be one of"):
            LLMConfig(provider="unknown_provider", model="gpt-4", api_key="key")  # type: ignore

    def test_default_temperature(self) -> None:
        llm = LLMConfig(model="gpt-4o", api_key="key")
        assert llm.temperature == 0.0


class TestConfig:
    """Tests for top-level Config model."""

    def test_unique_origin_ids(self, test_config: Config) -> None:
        """Config with unique origin IDs should validate."""
        ids = [o.id for o in test_config.origins]
        assert len(ids) == len(set(ids))

    def test_at_least_one_origin(self) -> None:
        with pytest.raises(ValidationError, match="1 validation error for Config"):
            Config(
                origins=[],  # type: ignore
                destination=DestinationConfig(
                    host="test", http_path="/sql/1.0/warehouses/abc", token="t", catalog="c", schema="s"
                ),
                llm=LLMConfig(model="gpt-4", api_key="key"),
            )

    def test_duplicate_origin_ids(self) -> None:
        origins = [
            OriginConfig(id="same", type="postgres", connection=OriginConnection()),
            OriginConfig(id="same", type="mongodb", connection=OriginConnection(uri="mongodb://x")),
        ]
        with pytest.raises(ValidationError, match="Origin IDs must be unique"):
            Config(
                origins=origins,  # type: ignore
                destination=DestinationConfig(
                    host="test", http_path="/sql/1.0/warehouses/abc", token="t", catalog="c", schema="s"
                ),
                llm=LLMConfig(model="gpt-4", api_key="key"),
            )


class TestLoadConfig:
    """Tests for the load_config function."""

    def test_load_from_file(self, test_config: Config, test_config_path: str) -> None:
        """Loading config from the test fixture file should succeed."""
        loaded = load_config(test_config_path)
        assert len(loaded.origins) == 2
        assert loaded.origins[0].id == "test_crm"
        assert loaded.destination.catalog == "main"

    def test_load_with_env_override(
        self, test_config_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variables should override config.yaml values.

        Note: The test fixture uses ORIGIN_DB_PASSWORD for the MongoDB origin,
        not CRM_DB_PASSWORD. This tests that env vars matching placeholder
        patterns in the config file get resolved.
        """
        # The test fixture's first origin (crm) has password '${CRM_DB_PASSWORD}'
        monkeypatch.setenv("CRM_DB_PASSWORD", "env_override")
        # Need to also unset any existing value that might shadow
        monkeypatch.delenv("CRM_DB_PASSWORD", raising=False)
        monkeypatch.setenv("CRM_DB_PASSWORD", "env_override")
        loaded = load_config(test_config_path)
        # The config_loader resolves ${PLACEHOLDER} patterns from env vars
        assert loaded.origins[0].connection.password == "env_override"

    def test_load_missing_file(self) -> None:
        """Loading a non-existent config should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_load_with_secrets_file(self, temp_config_file: str) -> None:
        """Loading with a secrets file should merge correctly."""
        secrets_path = temp_config_file + ".secrets"
        with open(secrets_path, "w") as f:
            yaml.dump({"destination": {"token": "secret_override"}}, f)
        try:
            loaded = load_config(temp_config_file, secrets_path)
            assert loaded.destination.token == "secret_override"
        finally:
            os.unlink(secrets_path)

    def test_load_with_invalid_config(self) -> None:
        """Loading an invalid config should raise ValidationError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"origins": [], "destination": {}, "llm": {}}, f)
            try:
                with pytest.raises(ValidationError):
                    load_config(f.name)
            finally:
                os.unlink(f.name)