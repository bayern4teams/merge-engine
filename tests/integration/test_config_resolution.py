"""Integration tests for configuration resolution and loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml


class TestConfigResolutionIntegration:
    """Test config loading with env var resolution and secrets merge."""

    @pytest.mark.asyncio
    async def test_config_loads_from_file(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that config loads from the test config file."""
        from src.utils.config_loader import load_config

        config = load_config(str(test_config_path))

        assert len(config.origins) == 2
        assert config.destination.host == "databricks.cloud"
        assert config.llm.model == "gpt-4"

    @pytest.mark.asyncio
    async def test_env_var_resolution(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that ${ENV_VAR} placeholders are resolved."""
        # Create a config with env var placeholders
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "${DB_HOST}",
                        "port": 5432,
                        "user": "${DB_USER}",
                        "password": "${DB_PASSWORD}",
                        "database": "test_db",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "${DATABRICKS_TOKEN}",
                "schema": "test",
                "catalog": "main",
                "tables": {"company": "companies"},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "${OPENAI_API_KEY}",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_env_config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        try:
            # Set environment variables
            env_vars = {
                "DB_HOST": "production-db.example.com",
                "DB_USER": "merge_engine",
                "DB_PASSWORD": "super_secret_password",
                "DATABRICKS_TOKEN": "dapi_token_123",
                "OPENAI_API_KEY": "sk-test-key-456",
            }

            with patch.dict(os.environ, env_vars, clear=False):
                from src.utils.config_loader import load_config
                config = load_config(str(config_file))

                assert config.origins[0].connection.host == "production-db.example.com"
                assert config.origins[0].connection.user == "merge_engine"
                assert config.origins[0].connection.password == "super_secret_password"
                assert config.destination.token == "dapi_token_123"
                assert config.llm.api_key == "sk-test-key-456"
        finally:
            config_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_env_var_missing_unchanged(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that unresolved env vars keep their ${VAR} placeholder."""
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "${NONEXISTENT_VAR_XYZ}",
                        "port": 5432,
                        "user": "test",
                        "password": "test",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "token",
                "schema": "test",
                "catalog": "main",
                "tables": {},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "${MISSING_KEY}",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_missing_env.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        try:
            with patch.dict(os.environ, {}, clear=False):
                from src.utils.config_loader import load_config
                config = load_config(str(config_file))

                # Missing vars should keep their placeholder
                assert config.origins[0].connection.host == "${NONEXISTENT_VAR_XYZ}"
        finally:
            config_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_secrets_file_merge(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that secrets file values override config.yaml values."""
        # Base config
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "localhost",
                        "port": 5432,
                        "user": "test",
                        "password": "original_password",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "original_token",
                "schema": "test",
                "catalog": "main",
                "tables": {},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "original_key",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_merge_config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        # Secrets file
        secrets_content = {
            "destination": {
                "token": "secret_token_override",
            },
            "llm": {
                "api_key": "secret_api_key_override",
            },
        }

        secrets_file = test_config_path.parent / "test_merge_secrets.yaml"
        with open(secrets_file, "w") as f:
            yaml.dump(secrets_content, f)

        try:
            from src.utils.config_loader import load_config
            config = load_config(
                str(config_file),
                secrets_path=str(secrets_file),
            )

            # Secrets should override base config
            assert config.destination.token == "secret_token_override"
            assert config.llm.api_key == "secret_api_key_override"
            # Non-overridden values stay the same
            assert config.origins[0].connection.password == "original_password"
        finally:
            config_file.unlink(missing_ok=True)
            secrets_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_secrets_file_missing_is_ignored(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that missing secrets file is silently skipped."""
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "localhost",
                        "port": 5432,
                        "user": "test",
                        "password": "test",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "token",
                "schema": "test",
                "catalog": "main",
                "tables": {},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "key",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_no_secrets.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        non_existent_secrets = test_config_path.parent / "non_existent_secrets.yaml"

        try:
            from src.utils.config_loader import load_config
            # Should not raise even though secrets file doesn't exist
            config = load_config(
                str(config_file),
                secrets_path=str(non_existent_secrets),
            )
            assert config is not None
        finally:
            config_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_deep_merge_nested_dicts(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that secrets deep-merge nested dictionaries."""
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "localhost",
                        "port": 5432,
                        "user": "test",
                        "password": "original",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "token",
                "schema": "test",
                "catalog": "main",
                "tables": {"company": "companies"},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "key",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_deep_merge.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        # Only override connection.host and connection.password within connection
        secrets_content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "production-db.example.com",
                        "port": 5432,
                        "user": "test",
                        "password": "new_password",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
        }

        secrets_file = test_config_path.parent / "test_deep_merge_secrets.yaml"
        with open(secrets_file, "w") as f:
            yaml.dump(secrets_content, f)

        try:
            from src.utils.config_loader import load_config
            config = load_config(
                str(config_file),
                secrets_path=str(secrets_file),
            )

            assert config.origins[0].connection.host == "production-db.example.com"
            assert config.origins[0].connection.password == "new_password"
            # Non-overridden nested field stays
            assert config.origins[0].connection.port == 5432
        finally:
            config_file.unlink(missing_ok=True)
            secrets_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_config_validation_unique_origin_ids(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that duplicate origin IDs are rejected."""
        content = {
            "origins": [
                {
                    "id": "duplicate_id",
                    "type": "postgres",
                    "connection": {
                        "host": "localhost",
                        "port": 5432,
                        "user": "test",
                        "password": "test",
                        "database": "db1",
                    },
                    "priority": 1,
                },
                {
                    "id": "duplicate_id",
                    "type": "mongodb",
                    "connection": {
                        "uri": "mongodb://localhost:27017",
                        "database": "db2",
                    },
                    "priority": 2,
                },
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "token",
                "schema": "test",
                "catalog": "main",
                "tables": {},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "key",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_duplicate_ids.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        try:
            from pydantic import ValidationError
            from src.utils.config_loader import load_config

            with pytest.raises(ValidationError) as exc_info:
                load_config(str(config_file))

            assert "unique" in str(exc_info.value).lower()
        finally:
            config_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_config_validation_origin_type(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that invalid origin types are rejected."""
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "invalid_type",
                    "connection": {
                        "host": "localhost",
                        "port": 5432,
                        "user": "test",
                        "password": "test",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "token",
                "schema": "test",
                "catalog": "main",
                "tables": {},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "key",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_invalid_origin_type.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        try:
            from pydantic import ValidationError
            from src.utils.config_loader import load_config

            with pytest.raises(ValidationError) as exc_info:
                load_config(str(config_file))

            assert "type" in str(exc_info.value).lower()
        finally:
            config_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_config_validation_http_path(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that invalid http_path is rejected."""
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "localhost",
                        "port": 5432,
                        "user": "test",
                        "password": "test",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "invalid_http_path",
                "token": "token",
                "schema": "test",
                "catalog": "main",
                "tables": {},
            },
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "api_key": "key",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_invalid_http_path.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        try:
            from pydantic import ValidationError
            from src.utils.config_loader import load_config

            with pytest.raises(ValidationError) as exc_info:
                load_config(str(config_file))

            assert "http_path" in str(exc_info.value).lower()
        finally:
            config_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_config_validation_llm_provider(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that invalid LLM provider is rejected."""
        content = {
            "origins": [
                {
                    "id": "test_origin",
                    "type": "postgres",
                    "connection": {
                        "host": "localhost",
                        "port": 5432,
                        "user": "test",
                        "password": "test",
                        "database": "test",
                    },
                    "priority": 1,
                }
            ],
            "destination": {
                "type": "databricks",
                "host": "databricks.cloud",
                "http_path": "/sql/1.0/warehouses/abc123",
                "token": "token",
                "schema": "test",
                "catalog": "main",
                "tables": {},
            },
            "llm": {
                "provider": "invalid_provider",
                "model": "gpt-4",
                "api_key": "key",
                "temperature": 0.0,
            },
        }

        config_file = test_config_path.parent / "test_invalid_llm_provider.yaml"
        with open(config_file, "w") as f:
            yaml.dump(content, f)

        try:
            from pydantic import ValidationError
            from src.utils.config_loader import load_config

            with pytest.raises(ValidationError) as exc_info:
                load_config(str(config_file))

            assert "provider" in str(exc_info.value).lower()
        finally:
            config_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_blocking_keys_preserved(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that blocking_keys config is properly loaded."""
        from src.utils.config_loader import load_config

        config = load_config(str(test_config_path))

        assert "company" in config.blocking_keys
        assert "name" in config.blocking_keys["company"]
        assert "person" in config.blocking_keys
        assert "email" in config.blocking_keys["person"]

    @pytest.mark.asyncio
    async def test_retry_config_defaults(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that retry config loads with defaults."""
        from src.utils.config_loader import load_config

        config = load_config(str(test_config_path))

        assert config.retry.max_attempts == 3
        assert config.retry.backoff == "exponential"

    @pytest.mark.asyncio
    async def test_extraction_config_defaults(
        self,
        test_config_path: Path,
    ) -> None:
        """Test that extraction config loads with defaults."""
        from src.utils.config_loader import load_config

        config = load_config(str(test_config_path))

        assert config.extraction.default_batch_size == 100
        assert config.extraction.max_batch_size == 1000