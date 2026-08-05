"""
Tests for src/mcp_server/connectors/base.py - Abstract base connector.
"""

import pytest

from src.mcp_server.connectors.base import BaseConnector


class TestBaseConnector:
    """Tests for BaseConnector abstract base class."""

    def test_base_connector_cannot_be_instantiated(self):
        """BaseConnector should not be directly instantiable."""
        with pytest.raises(TypeError):
            BaseConnector({})

    def test_abstract_methods_exist(self):
        """BaseConnector should have all required abstract methods."""
        assert hasattr(BaseConnector, "discover_schema")
        assert hasattr(BaseConnector, "extract")
        assert hasattr(BaseConnector, "close")

    def test_abstract_methods_required(self):
        """Subclass must implement all abstract methods."""

        class IncompleteConnector(BaseConnector):
            pass

        with pytest.raises(TypeError):
            IncompleteConnector({})

    def test_subclass_implements_all_methods(self):
        """Subclass implementing all methods should be instantiable."""

        class CompleteConnector(BaseConnector):
            async def discover_schema(self):
                return {"tables": []}

            async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None):
                return {"rows": [], "total_count": 0}

            async def mark_imported(self, table_name, ids):
                return True

            async def close(self):
                pass

        connector = CompleteConnector({"host": "localhost"})
        assert connector is not None

    def test_config_stored(self):
        """Config should be stored in instance."""

        class TestConnector(BaseConnector):
            async def discover_schema(self):
                return {"tables": []}

            async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None):
                return {"rows": []}

            async def mark_imported(self, table_name, ids):
                return True

            async def close(self):
                pass

        config = {"host": "test", "port": 5432}
        connector = TestConnector(config)
        assert connector.config == config

    def test_pool_initialized_to_none(self):
        """Connection pool should be initialized to None."""

        class TestConnector(BaseConnector):
            async def discover_schema(self):
                return {"tables": []}

            async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None):
                return {"rows": []}

            async def mark_imported(self, table_name, ids):
                return True

            async def close(self):
                pass

        connector = TestConnector({})
        assert connector._pool is None

    @pytest.mark.asyncio
    async def test_close_when_pool_none(self):
        """Close should handle None pool gracefully."""

        class TestConnector(BaseConnector):
            async def discover_schema(self):
                return {"tables": []}

            async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None):
                return {"rows": []}

            async def mark_imported(self, table_name, ids):
                return True

            async def close(self):
                if self._pool is not None:
                    self._pool = None

        connector = TestConnector({})
        await connector.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Should work as async context manager."""

        class TestConnector(BaseConnector):
            async def discover_schema(self):
                return {"tables": []}

            async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None):
                return {"rows": []}

            async def mark_imported(self, table_name, ids):
                return True

            async def close(self):
                self._pool = None

        async with TestConnector({}) as conn:
            assert conn is not None
        # Pool should be None after exit

    def test_extract_signature(self):
        """Extract should have correct signature."""
        import inspect

        sig = inspect.signature(BaseConnector.extract)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "table_name" in params
        assert "columns" in params
        assert "batch_size" in params
        assert "cursor" in params
