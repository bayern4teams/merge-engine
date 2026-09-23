"""Unit tests for the V3 profiling engine (Part B, P-3).

_classify_pattern/_shape_metrics are pure functions over an already-fetched
sample, tested directly. profile_column_full/profile_and_store are tested
against a mocked connector/store -- the real database-side aggregation is
P-2's job (already tested and live-verified there); what this module owns
is combining that with the Python-side shape metrics and persisting the
result with `mode` always present.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_server.tools.profiling import (
    _classify_pattern,
    _shape_metrics,
    profile_and_store,
    profile_column_full,
)


class TestClassifyPattern:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("a1b2c3d4-e5f6-7890-abcd-ef1234567890", "uuid"),
            ("user@example.com", "email"),
            ("2026-08-18", "date_iso"),
            ("2026-08-18T14:30:00", "date_iso"),
            ("42", "numeric"),
            ("-3.14", "numeric"),
            ("Hello World", "alpha"),
            ("abc123", "alphanumeric"),
            ("!!!not-any-shape###", "other"),
        ],
    )
    def test_classifies_each_shape(self, value: str, expected: str) -> None:
        assert _classify_pattern(value) == expected

    def test_a_uuid_is_never_misclassified_as_numeric_or_alphanumeric(self) -> None:
        """The ordering guarantee the module docstring promises: the most
        specific bucket wins, checked before looser ones."""
        assert _classify_pattern("550e8400-e29b-41d4-a716-446655440000") == "uuid"


class TestShapeMetrics:
    def test_length_buckets_are_correct(self) -> None:
        values = ["", "abc", "a" * 30, "a" * 100, "a" * 300]
        result = _shape_metrics(values, top_n=5)
        assert result["length_distribution"] == {
            "0": 1,
            "1-10": 1,
            "11-50": 1,
            "51-255": 1,
            "256+": 1,
        }

    def test_top_values_counts_frequency_not_just_presence(self) -> None:
        values = ["Boeing", "Boeing", "Boeing", "Airbus", "Airbus", "Lockheed"]
        result = _shape_metrics(values, top_n=2)
        assert result["top_values"] == [
            {"value": "Boeing", "count": 3},
            {"value": "Airbus", "count": 2},
        ]

    def test_none_values_are_excluded_from_every_metric(self) -> None:
        values = ["a", None, "b", None, None]
        result = _shape_metrics(values, top_n=10)
        assert sum(result["length_distribution"].values()) == 2
        assert sum(v["count"] for v in result["top_values"]) == 2

    def test_type_distribution_reflects_the_original_python_types(self) -> None:
        values = [1, "two", 3.0, True]
        result = _shape_metrics(values, top_n=10)
        # bool is a subclass of int in Python, so True's type name is "bool"
        # not "int" -- a caller distinguishing them (relevant for MongoDB's
        # dynamic schema, where a column can genuinely mix types) needs
        # this to NOT collapse bool into int.
        assert result["type_distribution"] == {"int": 1, "str": 1, "float": 1, "bool": 1}

    def test_empty_sample_produces_empty_not_error(self) -> None:
        result = _shape_metrics([], top_n=10)
        assert result["length_distribution"] == {}
        assert result["top_values"] == []
        assert result["pattern_distribution"] == {}
        assert result["type_distribution"] == {}


class TestProfileColumnFull:
    @pytest.mark.asyncio
    async def test_combines_database_aggregates_with_python_shape_metrics(self) -> None:
        connector = MagicMock()
        connector.profile_column = AsyncMock(
            return_value={
                "mode": "SAMPLED",
                "total_count": 4,
                "null_count": 0,
                "null_ratio": 0.0,
                "distinct_count": 3,
                "min_value": "Airbus",
                "max_value": "Lockheed",
            }
        )
        connector.extract = AsyncMock(
            return_value={
                "rows": [
                    {"name": "Boeing"},
                    {"name": "Boeing"},
                    {"name": "Airbus"},
                    {"name": "Lockheed"},
                ]
            }
        )

        profile = await profile_column_full(connector, "company", "name", sample_size=100)

        assert profile["mode"] == "SAMPLED"
        assert profile["distinct_ratio"] == pytest.approx(0.75)
        assert profile["unique_ratio"] == pytest.approx(0.75)
        assert profile["top_values"][0] == {"value": "Boeing", "count": 2}
        connector.extract.assert_called_once_with("company", ["name"], batch_size=100)

    @pytest.mark.asyncio
    async def test_zero_total_count_does_not_divide_by_zero(self) -> None:
        connector = MagicMock()
        connector.profile_column = AsyncMock(
            return_value={"mode": "FULL", "total_count": 0, "null_count": 0, "distinct_count": 0}
        )
        connector.extract = AsyncMock(return_value={"rows": []})

        profile = await profile_column_full(connector, "empty_table", "col")
        assert profile["distinct_ratio"] == 0.0
        assert profile["unique_ratio"] == 0.0


class TestProfileAndStore:
    @pytest.mark.asyncio
    async def test_persists_the_full_profile_via_insert_profile(self) -> None:
        connector = MagicMock()
        connector.profile_column = AsyncMock(
            return_value={
                "mode": "SAMPLED",
                "total_count": 2,
                "null_count": 0,
                "null_ratio": 0.0,
                "distinct_count": 2,
                "min_value": "a",
                "max_value": "b",
            }
        )
        connector.extract = AsyncMock(return_value={"rows": [{"col": "a"}, {"col": "b"}]})
        store = MagicMock()
        store.insert_profile.return_value = "profile-id-1"

        result = await profile_and_store(
            store, connector, "column-id-1", "table", "col", run_id="run-1"
        )

        assert result == "profile-id-1"
        store.insert_profile.assert_called_once()
        args, kwargs = store.insert_profile.call_args
        assert args[0] == "column-id-1"
        assert args[1] == "SAMPLED"
        assert kwargs["run_id"] == "run-1"
        assert kwargs["distinct_count"] == 2
