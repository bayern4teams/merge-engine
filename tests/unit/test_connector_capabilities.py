"""Unit tests for the connector capability matrix (Part B, P-2).

Covers: BaseConnector's conservative default, each concrete connector's
capabilities() override, and the shape of discover_indexes/
discover_statistics/profile_column/estimate_query_cost for PostgreSQL and
ClickHouse (the two connectors with real Docker infrastructure available to
live-verify against separately -- see the P-2 TODO.md entry for the honest
MongoDB gap).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_server.connectors.base import BaseConnector
from src.mcp_server.connectors.clickhouse import ClickHouseConnector
from src.mcp_server.connectors.mongodb import MongoDBConnector
from src.mcp_server.connectors.postgres import PostgresConnector


class _MinimalConnector(BaseConnector):
    """The smallest concrete subclass -- exercises the base defaults
    exactly as an as-yet-unoverridden connector would."""

    async def discover_schema(self):
        return {}

    async def extract(self, table_name, columns, **kwargs):
        return {}


class TestBaseDefault:
    def test_default_capabilities_are_conservative(self) -> None:
        caps = _MinimalConnector({}).capabilities()
        # Sampling and pushdown are safe to assume for any query-shaped
        # connector; everything requiring a specific guarantee (declared
        # constraints, EXPLAIN, parallelism) defaults to False rather than
        # a guessed True a caller might act on incorrectly.
        assert caps["supports_sampling"] is True
        assert caps["supports_pushdown"] is True
        for key in (
            "supports_declared_pk",
            "supports_declared_fk",
            "supports_declared_unique_constraints",
            "supports_statistics",
            "supports_explain",
            "supports_cte",
            "supports_temp_tables",
            "supports_window_functions",
            "supports_parallel_queries",
            "supports_column_comments",
            "supports_table_comments",
        ):
            assert caps[key] is False, f"{key} should default False"

    @pytest.mark.asyncio
    async def test_default_discover_indexes_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="does not support discover_indexes"):
            await _MinimalConnector({}).discover_indexes("t")

    @pytest.mark.asyncio
    async def test_default_estimate_query_cost_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="does not support estimate_query_cost"):
            await _MinimalConnector({}).estimate_query_cost("t")


class TestPerConnectorCapabilities:
    def test_postgres_declares_full_relational_support(self) -> None:
        caps = PostgresConnector({}).capabilities()
        assert caps["supports_declared_pk"] is True
        assert caps["supports_declared_fk"] is True
        assert caps["supports_declared_unique_constraints"] is True
        assert caps["supports_explain"] is True

    def test_clickhouse_reports_no_declared_fk_or_unique(self) -> None:
        """The load-bearing example from the module docstring: ClickHouse
        has no first-class FK constraint, so relationship discovery must
        route to inference here, never to a (nonexistent) declared list."""
        caps = ClickHouseConnector({}).capabilities()
        assert caps["supports_declared_fk"] is False
        assert caps["supports_declared_unique_constraints"] is False
        # A MergeTree ORDER BY / PRIMARY KEY clause is declarable, just not
        # an enforced uniqueness constraint -- these two must not collapse
        # to the same answer, or a caller would wrongly infer uniqueness.
        assert caps["supports_declared_pk"] is True

    def test_mongodb_reports_no_declared_pk_or_fk(self) -> None:
        caps = MongoDBConnector({}).capabilities()
        assert caps["supports_declared_pk"] is False
        assert caps["supports_declared_fk"] is False
        assert caps["supports_declared_unique_constraints"] is True


class TestPostgresProfileColumn:
    @pytest.mark.asyncio
    async def test_sampled_mode_wraps_the_source_and_echoes_mode_back(self) -> None:
        connector = PostgresConnector({"connection": {"database": "d"}})
        engine = MagicMock()
        conn = AsyncMock()
        row = {
            "total_count": 100,
            "null_count": 5,
            "distinct_count": 40,
            "min_value": "1",
            "max_value": "999",
        }
        result = MagicMock()
        result.mappings.return_value.fetchone.return_value = row
        conn.execute = AsyncMock(return_value=result)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        engine.connect = MagicMock(return_value=cm)
        connector._engine = engine

        profile = await connector.profile_column("company", "revenue", mode="SAMPLED")

        assert profile["mode"] == "SAMPLED"
        assert profile["total_count"] == 100
        assert profile["null_count"] == 5
        assert profile["null_ratio"] == pytest.approx(0.05)
        assert profile["distinct_count"] == 40
        sql = conn.execute.call_args.args[0].text
        assert "LIMIT :sample_size" in sql

    @pytest.mark.asyncio
    async def test_full_mode_does_not_limit_the_source(self) -> None:
        connector = PostgresConnector({"connection": {"database": "d"}})
        engine = MagicMock()
        conn = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.fetchone.return_value = {
            "total_count": 0,
            "null_count": 0,
            "distinct_count": 0,
            "min_value": None,
            "max_value": None,
        }
        conn.execute = AsyncMock(return_value=result)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        engine.connect = MagicMock(return_value=cm)
        connector._engine = engine

        await connector.profile_column("company", "revenue", mode="FULL")
        sql = conn.execute.call_args.args[0].text
        assert "LIMIT" not in sql


class TestClickHouseProfileColumn:
    @pytest.mark.asyncio
    async def test_approximate_mode_uses_uniq_not_uniqexact(self) -> None:
        connector = ClickHouseConnector({})
        connector._database = "origin2"
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[(100, 0, 42, "1", "999")])
        connector._client = client

        profile = await connector.profile_column("Company", "companyID", mode="APPROXIMATE")

        assert profile["mode"] == "APPROXIMATE"
        sql = client.query.call_args.args[0]
        assert "uniq(" in sql
        assert "uniqExact(" not in sql

    @pytest.mark.asyncio
    async def test_sampled_mode_uses_uniqexact_and_limits(self) -> None:
        connector = ClickHouseConnector({})
        connector._database = "origin2"
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[(100, 0, 42, "1", "999")])
        connector._client = client

        await connector.profile_column("Company", "companyID", mode="SAMPLED", sample_size=5000)

        sql = client.query.call_args.args[0]
        params = client.query.call_args.kwargs["parameters"]
        assert "uniqExact(" in sql
        assert "LIMIT {sample_size:UInt64}" in sql
        assert params["sample_size"] == 5000


class TestPostgresDiscoverStatistics:
    @pytest.mark.asyncio
    async def test_falls_back_to_n_live_tup_when_never_analyzed(self) -> None:
        """Live-caught: a real freshly-loaded 2-row table reported
        reltuples=-1 (PostgreSQL's own "never analyzed" sentinel), not 0 or
        2. n_live_tup (tracked by autovacuum independently of ANALYZE) is
        the live fallback."""
        connector = PostgresConnector({"connection": {"database": "d"}})
        engine = MagicMock()
        conn = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.fetchone.return_value = {
            "row_count_estimate": -1,
            "n_live_tup": 2,
            "total_bytes": 32768,
            "last_analyze": None,
            "last_autoanalyze": None,
        }
        conn.execute = AsyncMock(return_value=result)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        engine.connect = MagicMock(return_value=cm)
        connector._engine = engine

        stats = await connector.discover_statistics("company")
        assert stats["row_count_estimate"] == 2

    @pytest.mark.asyncio
    async def test_uses_reltuples_directly_once_analyzed(self) -> None:
        connector = PostgresConnector({"connection": {"database": "d"}})
        engine = MagicMock()
        conn = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.fetchone.return_value = {
            "row_count_estimate": 1270564,
            "n_live_tup": 1270000,
            "total_bytes": 552543551,
            "last_analyze": None,
            "last_autoanalyze": None,
        }
        conn.execute = AsyncMock(return_value=result)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        engine.connect = MagicMock(return_value=cm)
        connector._engine = engine

        stats = await connector.discover_statistics("company")
        assert stats["row_count_estimate"] == 1270564


class TestClickHouseDiscoverIndexes:
    @pytest.mark.asyncio
    async def test_reports_order_by_as_the_primary_index(self) -> None:
        connector = ClickHouseConnector({})
        connector._database = "origin2"
        client = MagicMock()
        client.query.side_effect = [
            MagicMock(result_rows=[("companyID", "companyID")]),  # sorting_key, primary_key
            MagicMock(result_rows=[]),  # no data-skipping indices
        ]
        connector._client = client

        indexes = await connector.discover_indexes("Company")

        assert len(indexes) == 1
        assert indexes[0]["kind"] == "order_by"
        assert indexes[0]["columns"] == ["companyID"]
        assert indexes[0]["is_primary"] is True


class TestClickHouseSampleDistinctValuesIsRandom:
    """Live-caught (BP-B2, 2026-08-19): an unfiltered `LIMIT` with no
    ordering returns ClickHouse's first N distinct values in scan order,
    not a random sample -- real origin2.CompanyIndustry.companyID (2.66M
    distinct values) scored 0.646 inclusion coverage against Company under
    the unordered sample, vs. a true full-table ratio of 0.254 confirmed
    by direct SQL. `ORDER BY rand()` is required before `LIMIT`."""

    @pytest.mark.asyncio
    async def test_unfiltered_sample_orders_by_rand_before_limiting(self) -> None:
        connector = ClickHouseConnector({})
        connector._database = "origin2"
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[("a",), ("b",)])
        connector._client = client

        await connector.sample_distinct_values("CompanyIndustry", "companyID", limit=500)

        sql_text = client.query.call_args.args[0]
        assert "ORDER BY rand()" in sql_text
        assert sql_text.index("ORDER BY rand()") < sql_text.index("LIMIT")

    @pytest.mark.asyncio
    async def test_filtered_sample_does_not_need_randomizing(self) -> None:
        """A filter_values query targets specific values directly -- it is
        not a sample subject to ordering bias, so no ORDER BY is added."""
        connector = ClickHouseConnector({})
        connector._database = "origin2"
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[("a",)])
        connector._client = client

        await connector.sample_distinct_values(
            "Company", "companyID", limit=500, filter_values=["a", "b"]
        )

        sql_text = client.query.call_args.args[0]
        assert "ORDER BY rand()" not in sql_text
