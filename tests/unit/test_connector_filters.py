"""Unit tests for the extraction filter normaliser (P.4 foundation)."""

from __future__ import annotations

from src.mcp_server.connectors.filters import normalize_filter


def test_none_and_empty_yield_nothing() -> None:
    assert normalize_filter(None) == []
    assert normalize_filter({}) == []


def test_bare_scalar_is_equals() -> None:
    assert normalize_filter({"status": "active"}) == [
        {"column": "status", "op": "eq", "value": "active"}
    ]


def test_explicit_eq() -> None:
    out = normalize_filter({"id": {"op": "eq", "value": 5}})
    assert out == [{"column": "id", "op": "eq", "value": 5}]


def test_ilike() -> None:
    out = normalize_filter({"name": {"op": "ilike", "value": "Nova"}})
    assert out == [{"column": "name", "op": "ilike", "value": "Nova"}]


def test_in_list() -> None:
    out = normalize_filter({"company_id": {"op": "in", "value": [1, 2, 3]}})
    assert out == [{"column": "company_id", "op": "in", "value": [1, 2, 3]}]


def test_in_drops_none_values() -> None:
    out = normalize_filter({"company_id": {"op": "in", "value": [1, None, 2]}})
    assert out == [{"column": "company_id", "op": "in", "value": [1, 2]}]


def test_empty_in_is_dropped() -> None:
    # An empty IN () is a SQL error and never the intent — scoped extraction
    # handles "no subject ids" by not extracting, not by an empty membership.
    assert normalize_filter({"company_id": {"op": "in", "value": []}}) == []


def test_unknown_op_falls_back_to_eq() -> None:
    out = normalize_filter({"x": {"op": "regexp", "value": "y"}})
    assert out == [{"column": "x", "op": "eq", "value": "y"}]


def test_scalar_in_value_is_wrapped() -> None:
    out = normalize_filter({"company_id": {"op": "in", "value": 7}})
    assert out == [{"column": "company_id", "op": "in", "value": [7]}]


def test_in_scratch_passes_through_table_name() -> None:
    # Part 1.5 D.6: the value is a scratch-table name (a string), not a
    # value list — must not go through "in"'s list/empty-list handling.
    out = normalize_filter(
        {"company_id": {"op": "in_scratch", "value": "merge_engine_scratch_abc"}}
    )
    assert out == [
        {"column": "company_id", "op": "in_scratch", "value": "merge_engine_scratch_abc"}
    ]
