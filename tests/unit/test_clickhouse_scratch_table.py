"""Unit tests for ClickHouseConnector's scratch-table extraction path
(Part 1.5 D.6): a large linked-id filter gets one uploaded scratch table
plus a subquery instead of many chunked IN(...) round-trips, which is what
blew past ClickHouse's HTTP field-size limit on a live Boeing run.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.mcp_server.connectors.clickhouse import _SCRATCH_TABLE_RE, ClickHouseConnector


def _connector_with_mock_client() -> tuple[ClickHouseConnector, MagicMock]:
    connector = ClickHouseConnector({"connection": {"database": "test_db"}})
    mock_client = MagicMock()
    connector._client = mock_client
    connector._database = "test_db"
    return connector, mock_client


class TestCreateIdScratchTable:
    async def test_generates_a_uniquely_named_table_matching_the_validator(self) -> None:
        connector, mock_client = _connector_with_mock_client()
        table_name = await connector.create_id_scratch_table([1, 2, 3])
        assert _SCRATCH_TABLE_RE.match(table_name)

    async def test_creates_table_then_inserts_all_values(self) -> None:
        connector, mock_client = _connector_with_mock_client()
        table_name = await connector.create_id_scratch_table([10, 20, 30])

        mock_client.command.assert_called_once()
        create_sql = mock_client.command.call_args.args[0]
        assert f"`test_db`.`{table_name}`" in create_sql
        assert "CREATE TABLE" in create_sql

        mock_client.insert.assert_called_once_with(
            table_name, [[10], [20], [30]], column_names=["id"]
        )

    async def test_infers_column_type_from_first_non_null_value(self) -> None:
        connector, mock_client = _connector_with_mock_client()
        await connector.create_id_scratch_table([None, "abc-123", None])
        create_sql = mock_client.command.call_args.args[0]
        assert "String" in create_sql

    async def test_infers_int_type(self) -> None:
        connector, mock_client = _connector_with_mock_client()
        await connector.create_id_scratch_table([42, 43])
        create_sql = mock_client.command.call_args.args[0]
        assert "Int64" in create_sql


class TestDropScratchTable:
    async def test_drops_the_named_table(self) -> None:
        connector, mock_client = _connector_with_mock_client()
        await connector.drop_scratch_table("merge_engine_scratch_abc123")
        mock_client.command.assert_called_once()
        drop_sql = mock_client.command.call_args.args[0]
        assert "DROP TABLE IF EXISTS" in drop_sql
        assert "`test_db`.`merge_engine_scratch_abc123`" in drop_sql

    async def test_failure_is_swallowed_not_raised(self) -> None:
        # Best-effort cleanup: a drop failure must never propagate and break
        # the extraction that already succeeded.
        connector, mock_client = _connector_with_mock_client()
        mock_client.command.side_effect = RuntimeError("connection reset")
        await connector.drop_scratch_table("merge_engine_scratch_abc123")  # must not raise


class TestExtractInScratchFilter:
    async def test_builds_a_subquery_condition_with_no_bound_params(self) -> None:
        connector, mock_client = _connector_with_mock_client()
        mock_result = MagicMock()
        mock_result.result_rows = []
        mock_result.column_names = ["id"]
        mock_client.query.return_value = mock_result

        await connector.extract(
            table_name="people",
            columns=["id"],
            filter={
                "company_id": {
                    "op": "in_scratch",
                    "value": "merge_engine_scratch_0123456789abcdef0123456789abcdef",
                }
            },
        )

        query_sql = mock_client.query.call_args.args[0]
        assert (
            "`company_id` IN (SELECT `id` FROM `test_db`.`merge_engine_scratch_0123456789abcdef0123456789abcdef`)"
            in query_sql
        )
        # No f0_0-style per-value bind params for this op, unlike "in".
        params = mock_client.query.call_args.kwargs.get("parameters", {})
        assert not any(k.startswith("f0") for k in params)

    async def test_rejects_a_table_name_that_does_not_match_the_generated_pattern(self) -> None:
        connector, _mock_client = _connector_with_mock_client()
        with pytest.raises(ValueError, match="Invalid scratch table name"):
            await connector.extract(
                table_name="people",
                columns=["id"],
                filter={"company_id": {"op": "in_scratch", "value": "'; DROP TABLE users; --"}},
            )


class TestSampleDistinctValues:
    """Phase 4.1: bounded DISTINCT sample used by join_graph.py's
    inclusion-dependency check -- ClickHouse origins never carry declared
    foreign keys, so this is the tier that actually finds join edges here."""

    async def test_pushes_a_bounded_distinct_query(self) -> None:
        connector, mock_client = _connector_with_mock_client()
        mock_result = MagicMock()
        mock_result.result_rows = [("a",), ("b",)]
        mock_client.query.return_value = mock_result

        values = await connector.sample_distinct_values("Company", "website", limit=500)

        assert values == ["a", "b"]
        query_sql = mock_client.query.call_args.args[0]
        assert "DISTINCT `website`" in query_sql
        assert "`test_db`.`Company`" in query_sql
        params = mock_client.query.call_args.kwargs.get("parameters", {})
        assert params == {"limit": 500}

    async def test_filter_values_pushes_an_in_clause_instead_of_arbitrary_sampling(self) -> None:
        """Phase 4.1 sampling fix (2026-08-10): given filter_values, this
        must query for THOSE SPECIFIC values, not an arbitrary top-N --
        verified live against a real ClickHouse Company/CompanyFinancials
        pair where independent sampling scored 0.069 overlap despite the
        relationship being 100% real."""
        connector, mock_client = _connector_with_mock_client()
        mock_result = MagicMock()
        mock_result.result_rows = [("c1",), ("c2",)]
        mock_client.query.return_value = mock_result

        values = await connector.sample_distinct_values(
            "Company", "companyID", limit=500, filter_values=["c1", "c2", "c3"]
        )

        assert values == ["c1", "c2"]
        query_sql = mock_client.query.call_args.args[0]
        assert " IN " in query_sql
        params = mock_client.query.call_args.kwargs.get("parameters", {})
        assert params["values"] == ["c1", "c2", "c3"]
