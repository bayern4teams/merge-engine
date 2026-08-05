"""
Tests for src/utils/config_loader.py - Configuration loader with Pydantic validation.
"""

import tempfile

import pytest
import yaml

from src.utils.config_loader import (
    DestinationConfig,
    ExtractionConfig,
    LLMConfig,
    RetryConfig,
    _resolve_env_vars,
    build_destination_config,
    build_origin_config,
    load_config,
)


class TestDestinationConfig:
    """Tests for DestinationConfig Pydantic model."""

    def test_default_values(self):
        """DestinationConfig should have correct default values."""
        config = DestinationConfig()
        assert config.type == "postgresql"
        assert config.port == 5432
        assert config.schema_name == "public"

    def test_custom_values(self):
        """DestinationConfig should accept custom values."""
        config = DestinationConfig(
            host="localhost",
            port=5433,
            user="admin",
            password="secret",
            database="mergedb",
            schema="custom",
        )
        assert config.host == "localhost"
        assert config.port == 5433
        assert config.user == "admin"
        assert config.database == "mergedb"
        assert config.schema_name == "custom"

    def test_alias_support(self):
        """DestinationConfig should support 'schema' alias."""
        config = DestinationConfig(**{"schema": "my_schema"})
        assert config.schema_name == "my_schema"

    def test_invalid_type_raises(self):
        """Invalid type should raise ValidationError."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            DestinationConfig(type="mysql")


class TestLLMConfig:
    """Tests for LLMConfig Pydantic model."""

    def test_default_provider(self):
        """Default provider should be openai."""
        config = LLMConfig(model="gpt-4", api_key="test-key")
        assert config.provider == "openai"
        assert config.model == "gpt-4"
        assert config.api_key == "test-key"
        assert config.temperature == 0.0

    @pytest.mark.parametrize("provider", ["openai", "azure", "anthropic"])
    def test_valid_providers(self, provider):
        """All valid providers should be accepted."""
        config = LLMConfig(model="gpt-4", api_key="test-key", provider=provider)
        assert config.provider == provider

    def test_invalid_provider_raises(self):
        """Invalid provider should raise ValidationError."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            LLMConfig(model="gpt-4", api_key="test-key", provider="gemini")

    def test_base_url_optional(self):
        """base_url should be optional (None by default)."""
        config = LLMConfig(model="gpt-4", api_key="test-key")
        assert config.base_url is None

    def test_custom_temperature(self):
        """Custom temperature should be accepted."""
        config = LLMConfig(model="gpt-4", api_key="test-key", temperature=0.7)
        assert config.temperature == 0.7

    def test_fallback_models_defaults_to_empty_list(self):
        config = LLMConfig(model="gpt-4", api_key="test-key")
        assert config.fallback_models == []

    def test_fallback_models_accepted_in_order(self):
        config = LLMConfig(
            model="gpt-4",
            api_key="test-key",
            fallback_models=["gpt-4-mini", "gpt-3.5"],
        )
        assert config.fallback_models == ["gpt-4-mini", "gpt-3.5"]


class TestRetryConfig:
    """Tests for RetryConfig Pydantic model."""

    def test_default_values(self):
        """RetryConfig should have correct defaults."""
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.backoff == "exponential"
        assert config.min_backoff_seconds == 1.0
        assert config.max_backoff_seconds == 30.0

    def test_custom_values(self):
        """RetryConfig should accept custom values."""
        config = RetryConfig(
            max_attempts=5,
            backoff="linear",
            min_backoff_seconds=0.5,
            max_backoff_seconds=10.0,
        )
        assert config.max_attempts == 5
        assert config.backoff == "linear"
        assert config.min_backoff_seconds == 0.5
        assert config.max_backoff_seconds == 10.0


class TestExtractionConfig:
    """Tests for ExtractionConfig Pydantic model."""

    def test_default_values(self):
        """ExtractionConfig should have correct defaults."""
        config = ExtractionConfig()
        assert config.default_batch_size == 1000
        assert config.max_batch_size == 10000


class TestResolveEnvVars:
    """Tests for _resolve_env_vars() function."""

    def test_resolve_simple_env_var(self):
        """Simple ${VAR} should be resolved."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("TEST_VAR", "test_value")
            result = _resolve_env_vars("${TEST_VAR}")
            assert result == "test_value"

    def test_resolve_missing_env_var(self):
        """Missing env var should leave placeholder unchanged."""
        result = _resolve_env_vars("${NONEXISTENT_VAR_XYZ}")
        assert result == "${NONEXISTENT_VAR_XYZ}"

    def test_resolve_in_dict(self):
        """Env vars in dict values should be resolved."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DB_HOST", "localhost")
            result = _resolve_env_vars({"host": "${DB_HOST}"})
            assert result == {"host": "localhost"}

    def test_resolve_in_list(self):
        """Env vars in list items should be resolved."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ITEM1", "value1")
            mp.setenv("ITEM2", "value2")
            result = _resolve_env_vars(["${ITEM1}", "${ITEM2}", "static"])
            assert result == ["value1", "value2", "static"]

    def test_non_string_unchanged(self):
        """Non-string values should be returned unchanged."""
        assert _resolve_env_vars(123) == 123
        assert _resolve_env_vars(45.6) == 45.6
        assert _resolve_env_vars(None) is None


class TestBuildDestinationConfig:
    """Tests for build_destination_config() function."""

    def test_build_with_defaults(self):
        """Should create DestinationConfig with defaults."""
        config = build_destination_config(
            host="localhost",
            port=5432,
            user="admin",
            password="secret",
            database="mergedb",
        )
        assert config.type == "postgresql"
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.schema_name == "public"

    def test_build_with_custom_schema(self):
        """Should create DestinationConfig with custom schema."""
        config = build_destination_config(
            host="localhost",
            port=5433,
            user="admin",
            password="secret",
            database="mergedb",
            schema="unified",
        )
        assert config.port == 5433
        assert config.schema_name == "unified"


class TestBuildOriginConfig:
    """Tests for build_origin_config() function."""

    def test_build_postgres_origin(self):
        """Should create postgres origin config dict."""
        config = build_origin_config(
            origin_id="crm",
            db_type="postgres",
            host="crm-db.example.com",
            port=5432,
            user="crm_user",
            password="crm_pass",
            database="crm_db",
        )
        assert config["id"] == "crm"
        assert config["type"] == "postgres"
        assert config["connection"]["host"] == "crm-db.example.com"
        assert config["connection"]["port"] == 5432
        assert config["connection"]["database"] == "crm_db"
        assert config["priority"] == 0

    def test_build_mongodb_origin(self):
        """Should create mongodb origin config dict."""
        config = build_origin_config(
            origin_id="hr_db",
            db_type="mongodb",
            host="mongo.example.com",
            port=27017,
            user="hr_user",
            password="hr_pass",
            database="hr_db",
            priority=10,
        )
        assert config["type"] == "mongodb"
        assert config["priority"] == 10

    def test_build_with_custom_priority(self):
        """Should accept custom priority."""
        config = build_origin_config(
            origin_id="priority_test",
            db_type="postgres",
            host="localhost",
            port=5432,
            user="user",
            password="pass",
            database="db",
            priority=50,
        )
        assert config["priority"] == 50


class TestLoadConfig:
    """Tests for load_config() function."""

    def test_load_from_custom_path(self):
        """Should load config from specified path."""
        config_data = {
            "destination": {
                "host": "localhost",
                "user": "admin",
                "password": "secret",
                "database": "mergedb",
            },
            "llm": {
                "model": "gpt-4",
                "api_key": "test-key",
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            f.flush()
            config = load_config(f.name)
            assert config.destination.host == "localhost"
            assert config.llm.model == "gpt-4"

    def test_load_with_env_var_resolution(self):
        """Should resolve ${ENV_VAR} placeholders."""
        config_data = {
            "destination": {
                "host": "${DB_HOST}",
                "user": "${DB_USER}",
                "password": "${DB_PASSWORD}",
                "database": "mergedb",
            },
            "llm": {
                "model": "gpt-4",
                "api_key": "test-key",
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            f.flush()
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("DB_HOST", "prod-db.example.com")
                mp.setenv("DB_USER", "prod_user")
                mp.setenv("DB_PASSWORD", "prod_pass")
                config = load_config(f.name)
                assert config.destination.host == "prod-db.example.com"
                assert config.destination.user == "prod_user"
                assert config.destination.password == "prod_pass"

    def test_load_missing_file_raises(self):
        """Loading non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_load_with_defaults(self):
        """Should use default retry and extraction configs."""
        config_data = {
            "destination": {
                "host": "localhost",
                "user": "admin",
                "password": "secret",
                "database": "mergedb",
            },
            "llm": {
                "model": "gpt-4",
                "api_key": "test-key",
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            f.flush()
            config = load_config(f.name)
            assert config.retry.max_attempts == 3
            assert config.extraction.default_batch_size == 1000

    def test_load_invalid_config_raises(self):
        """Invalid config should raise ValidationError."""
        config_data = {
            "destination": {
                "type": "mysql",  # Invalid type
                "host": "localhost",
                "user": "admin",
                "password": "secret",
                "database": "mergedb",
            },
            "llm": {
                "model": "gpt-4",
                "api_key": "test-key",
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            f.flush()
            with pytest.raises(Exception):  # Pydantic ValidationError
                load_config(f.name)
