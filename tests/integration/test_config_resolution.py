"""
Integration tests for configuration resolution.

Tests that the config loader properly loads, resolves env vars,
and validates the application configuration.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError


class TestConfigResolutionIntegration:
    """Integration tests for config loading and validation."""

    @pytest.fixture
    def minimal_config_data(self) -> dict:
        """Generate minimal valid config for testing."""
        return {
            "destination": {
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "user": "test_user",
                "password": "test_pass",
                "database": "test_db",
                "schema": "public",
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test_key_12345",
            },
            "retry": {
                "max_attempts": 3,
                "backoff": "exponential",
                "min_backoff_seconds": 1,
                "max_backoff_seconds": 30,
            },
            "extraction": {
                "default_batch_size": 1000,
                "max_batch_size": 10000,
            },
        }

    def test_config_loads_from_file(
        self,
        minimal_config_data: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that config loads correctly from a YAML file."""
        from src.utils.config_loader import load_config

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(minimal_config_data, f)
            try:
                monkeypatch.delenv("OPENAI_API_KEY", raising=False)
                monkeypatch.setenv("OPENAI_API_KEY", "env_api_key")
                cfg = load_config(f.name)
                assert cfg.destination.type == "postgresql"
                assert cfg.destination.host == "localhost"
                assert cfg.destination.port == 5432
                assert cfg.llm.provider == "openai"
                assert cfg.llm.model == "gpt-4o"
            finally:
                os.unlink(f.name)

    def test_env_var_resolution(
        self,
        minimal_config_data: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that environment variables are resolved from config."""
        from src.utils.config_loader import load_config

        config_with_env = {
            "destination": {
                "type": "postgresql",
                "host": "${DB_HOST}",
                "port": 5432,
                "user": "${DB_USER}",
                "password": "static_pass",
                "database": "test_db",
                "schema": "public",
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test_key",
            },
            "retry": {
                "max_attempts": 3,
                "backoff": "exponential",
                "min_backoff_seconds": 1,
                "max_backoff_seconds": 30,
            },
            "extraction": {
                "default_batch_size": 1000,
                "max_batch_size": 10000,
            },
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(config_with_env, f)
            try:
                monkeypatch.setenv("DB_HOST", "remote-host.example.com")
                monkeypatch.setenv("DB_USER", "remote_user")
                cfg = load_config(f.name)
                assert cfg.destination.host == "remote-host.example.com"
                assert cfg.destination.user == "remote_user"
            finally:
                os.unlink(f.name)

    def test_env_var_missing_unchanged(
        self,
        minimal_config_data: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that missing env vars leave placeholders unchanged."""
        from src.utils.config_loader import load_config

        config_with_env = {
            "destination": {
                "type": "postgresql",
                "host": "${NONEXISTENT_VAR_XYZ}",
                "port": 5432,
                "user": "admin",
                "password": "pass",
                "database": "db",
                "schema": "public",
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "key",
            },
            "retry": {
                "max_attempts": 3,
                "backoff": "exponential",
                "min_backoff_seconds": 1,
                "max_backoff_seconds": 30,
            },
            "extraction": {
                "default_batch_size": 1000,
                "max_batch_size": 10000,
            },
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(config_with_env, f)
            try:
                cfg = load_config(f.name)
                assert cfg.destination.host == "${NONEXISTENT_VAR_XYZ}"
            finally:
                os.unlink(f.name)

    def test_build_destination_config(
        self,
    ) -> None:
        """Test that build_destination_config creates valid config."""
        from src.utils.config_loader import build_destination_config

        dest = build_destination_config(
            host="localhost",
            port=5432,
            user="test_user",
            password="test_pass",
            database="test_db",
            schema="custom_schema",
        )
        assert dest.type == "postgresql"
        assert dest.host == "localhost"
        assert dest.port == 5432
        assert dest.user == "test_user"
        assert dest.database == "test_db"
        assert dest.schema_name == "custom_schema"

    def test_build_origin_config(
        self,
    ) -> None:
        """Test that build_origin_config creates valid config dict."""
        from src.utils.config_loader import build_origin_config

        origin = build_origin_config(
            origin_id="crm_db",
            db_type="postgres",
            host="crm.example.com",
            port=5432,
            user="crm_user",
            password="crm_pass",
            database="crm_data",
            priority=5,
        )
        assert origin["id"] == "crm_db"
        assert origin["type"] == "postgres"
        assert origin["connection"]["host"] == "crm.example.com"
        assert origin["connection"]["port"] == 5432
        assert origin["priority"] == 5

    def test_deep_merge_nested_dicts(self) -> None:
        """Test that _deep_merge correctly handles nested dicts."""
        from src.utils.config_loader import _deep_merge

        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

        # Test list override
        base2 = {"items": [1, 2, 3]}
        override2 = {"items": [4, 5]}
        result2 = _deep_merge(base2, override2)
        assert result2 == {"items": [4, 5]}

    def test_config_validation_invalid_type(self) -> None:
        """Test that invalid destination type raises ValidationError."""
        from src.utils.config_loader import DestinationConfig

        with pytest.raises(ValidationError, match="must be 'postgresql'"):
            DestinationConfig(type="databricks")  # type: ignore

    def test_config_validation_missing_fields(self) -> None:
        """Test that missing required fields raises ValidationError."""
        from src.utils.config_loader import Config

        with pytest.raises(ValidationError):
            Config(
                destination={},  # type: ignore
                llm={},  # type: ignore
            )

    def test_retry_config_defaults(self) -> None:
        """Test that RetryConfig has correct defaults."""
        from src.utils.config_loader import RetryConfig

        retry = RetryConfig()
        assert retry.max_attempts == 3
        assert retry.backoff == "exponential"
        assert retry.min_backoff_seconds == 1.0
        assert retry.max_backoff_seconds == 30.0

    def test_extraction_config_defaults(self) -> None:
        """Test that ExtractionConfig has correct defaults."""
        from src.utils.config_loader import ExtractionConfig

        extraction = ExtractionConfig()
        assert extraction.default_batch_size == 1000
        assert extraction.max_batch_size == 10000