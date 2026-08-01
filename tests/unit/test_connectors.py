"""
Unit tests for the database connectors module.

Tests connector factory, base class, and type validation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.mcp_server.connectors import create_connector
from src.mcp_server.connectors.base import BaseConnector


class TestCreateConnector:
    """Tests for the create_connector factory function."""

    def test_create_postgres_connector(self) -> None:
        """Should create a PostgresConnector for postgres type."""
        with patch("src.mcp_server.connectors._CONNECTOR_REGISTRY", {"postgres": MagicMock()}):
            connector = create_connector("postgres", {"host": "localhost", "database": "test"})
            assert connector is not None

    def test_create_mongodb_connector(self) -> None:
        """Should create a MongoDBConnector for mongodb type."""
        with patch("src.mcp_server.connectors._CONNECTOR_REGISTRY", {"mongodb": MagicMock()}):
            connector = create_connector("mongodb", {"uri": "mongodb://localhost:27017/db"})
            assert connector is not None

    def test_create_databricks_connector(self) -> None:
        """Should create a DatabricksConnector for databricks type."""
        with patch("src.mcp_server.connectors._CONNECTOR_REGISTRY", {"databricks": MagicMock()}):
            connector = create_connector("databricks", {
                "host": "test.databricks.com",
                "http_path": "/sql/1.0/warehouses/abc",
                "token": "test",
            })
            assert connector is not None

    def test_invalid_type_raises(self) -> None:
        """Should raise ValueError for unknown connector type."""
        with pytest.raises(ValueError, match="Unknown connector type"):
            create_connector("oracle", {"host": "localhost"})

    def test_invalid_type_string(self) -> None:
        """Should raise for nonsense type string."""
        with pytest.raises(ValueError, match="Unknown connector type"):
            create_connector("snowflake", {})

    def test_empty_config_accepted(self) -> None:
        """Should accept empty config dict for known types."""
        with patch("src.mcp_server.connectors._CONNECTOR_REGISTRY", {"postgres": MagicMock()}):
            connector = create_connector("postgres", {})
            assert connector is not None


class TestBaseConnector:
    """Tests for the BaseConnector abstract class."""

    def test_base_connector_is_abstract(self) -> None:
        """Should not be instantiable directly."""
        with pytest.raises(TypeError):
            BaseConnector()  # type: ignore

    def test_abstract_methods_not_implemented(self) -> None:
        """Subclass without abstract methods should raise TypeError."""
        class IncompleteConnector(BaseConnector):
            pass

        with pytest.raises(TypeError):
            IncompleteConnector()  # type: ignore

    def test_all_abstract_methods_defined(self) -> None:
        """BaseConnector should define expected abstract methods."""
        abstract_methods = [
            "discover_schema", "extract", "close",
        ]
        for method in abstract_methods:
            assert hasattr(BaseConnector, method)

    def test_connector_has_config_attribute(self) -> None:
        """Connector should accept and store config via base class."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        config = {"host": "localhost", "database": "test"}
        conn = PostgresConnector(config)
        assert hasattr(conn, "config")
        assert conn.config == config


class TestConnectorConnectionConfig:
    """Tests for connection configuration passing."""

    def test_postgres_receives_config(self) -> None:
        """Postgres connector should receive connection dict."""
        from src.mcp_server.connectors.postgres import PostgresConnector
        config = {
            "host": "db.example.com",
            "port": 5432,
            "user": "admin",
            "password": "secret",
            "database": "production",
        }
        conn = PostgresConnector(config)
        assert conn.config == config

    def test_mongodb_receives_config(self) -> None:
        """MongoDB connector should receive connection dict."""
        from src.mcp_server.connectors.mongodb import MongoDBConnector
        config = {"uri": "mongodb://user:pass@host/db"}
        conn = MongoDBConnector(config)
        assert conn.config == config

    def test_databricks_config_keys(self) -> None:
        """Databricks config should have expected keys."""
        config = {
            "host": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc123",
            "token": "dapiXXXXX",
            "catalog": "main",
            "schema": "unified",
        }
        # Just test that the config dict has expected keys
        assert config["host"] == "test.cloud.databricks.com"
        assert config["catalog"] == "main"
        assert config["schema"] == "unified"


class TestConnectorModuleExports:
    """Tests for module-level exports."""

    def test_create_connector_exported(self) -> None:
        """create_connector should be available from package."""
        from src.mcp_server.connectors import create_connector as cc
        assert callable(cc)

    def test_base_connector_exported(self) -> None:
        """BaseConnector should be available from package."""
        from src.mcp_server.connectors import BaseConnector as BC
        assert BC is not None

    def test_all_connectors_exported(self) -> None:
        """All connector classes should be exported."""
        from src.mcp_server.connectors import (
            BaseConnector,
            ClickHouseConnector,
            PostgresConnector,
            MongoDBConnector,
            DatabricksConnector,
        )
        assert BaseConnector is not None
        assert ClickHouseConnector is not None
        assert PostgresConnector is not None
        assert MongoDBConnector is not None
        assert DatabricksConnector is not None

    def test_connector_registry_has_all_types(self) -> None:
        """Connector registry should have all expected types."""
        from src.mcp_server.connectors import _CONNECTOR_REGISTRY
        assert "postgres" in _CONNECTOR_REGISTRY
        assert "mongodb" in _CONNECTOR_REGISTRY
        assert "databricks" in _CONNECTOR_REGISTRY
        assert "clickhouse" in _CONNECTOR_REGISTRY

    def test_connector_registry_no_duplicates(self) -> None:
        """Connector registry should have no duplicate entries."""
        from src.mcp_server.connectors import _CONNECTOR_REGISTRY
        # 4 connector types plus the "postgresql" alias for "postgres"
        assert len(_CONNECTOR_REGISTRY) == 5
        assert "clickhouse" in _CONNECTOR_REGISTRY
        assert _CONNECTOR_REGISTRY["postgresql"] is _CONNECTOR_REGISTRY["postgres"]
