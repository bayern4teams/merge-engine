"""Phase 4.1/4.7 (merge-engine-remediation-1-to-6): value <-> SQL-type
reconciliation. Governed by three real crashes a live run hit: money strings
into ``double precision`` (Boeing/Airbus), an approximation into an integer
column, and a hex id into ``bigint`` (Lockheed) — the last one must stay
genuinely incompatible, not be silently mis-parsed.
"""

from __future__ import annotations

import pytest

from src.mcp_server.tools.value_coercion import (
    coerce_value,
    is_compatible,
    normalize_date_like,
    parse_magnitude,
)


class TestParseMagnitude:
    def test_dollar_billion(self) -> None:
        assert parse_magnitude("$6.7 Billion") == pytest.approx(6.7e9)

    def test_dollar_million(self) -> None:
        assert parse_magnitude("$1 Million") == pytest.approx(1e6)

    def test_over_with_comma(self) -> None:
        assert parse_magnitude("Over 10,000") == pytest.approx(10000.0)

    def test_plain_number_with_commas(self) -> None:
        assert parse_magnitude("1,234") == pytest.approx(1234.0)

    def test_bare_number(self) -> None:
        assert parse_magnitude("42") == pytest.approx(42.0)

    def test_hex_id_is_not_a_magnitude(self) -> None:
        # Contains digits, but is not — in its entirety — a number. A bare
        # `search` would wrongly find "1541495295" inside this and succeed.
        assert parse_magnitude("ba34c3bb1541495295e82f2db9ebe703") is None

    def test_empty_and_none(self) -> None:
        assert parse_magnitude("") is None
        assert parse_magnitude(None) is None

    def test_non_numeric_text(self) -> None:
        assert parse_magnitude("Boeing Company") is None


class TestCoerceValue:
    def test_money_string_into_double_precision(self) -> None:
        value, ok, _reason = coerce_value("$6.1 Million", "double precision")
        assert ok is True
        assert value == pytest.approx(6.1e6)

    def test_approximation_into_integer(self) -> None:
        value, ok, _reason = coerce_value("Over 10,000", "integer")
        assert ok is True
        assert value == 10000

    def test_hex_id_into_bigint_is_genuinely_incompatible(self) -> None:
        value, ok, reason = coerce_value(
            "ba34c3bb1541495295e82f2db9ebe703", "bigint"
        )
        assert ok is False
        assert reason

    def test_none_passes_through(self) -> None:
        assert coerce_value(None, "bigint") == (None, True, "")

    def test_no_target_type_passes_through(self) -> None:
        assert coerce_value("anything", "") == ("anything", True, "")

    def test_bool_from_text(self) -> None:
        assert coerce_value("true", "boolean") == (True, True, "")
        assert coerce_value("0", "boolean") == (False, True, "")

    def test_unrecognisable_bool_is_incompatible(self) -> None:
        _value, ok, reason = coerce_value("maybe", "boolean")
        assert ok is False
        assert reason

    def test_already_correct_type_is_untouched(self) -> None:
        assert coerce_value(42, "bigint") == (42, True, "")
        assert coerce_value(3.14, "double precision") == (3.14, True, "")

    def test_text_target_stringifies_anything(self) -> None:
        assert coerce_value(42, "text") == ("42", True, "")

    def test_diffbot_tagged_date_into_date_column(self) -> None:
        value, ok, _reason = coerce_value("d1916-01-01", "date")
        assert ok is True
        assert value == "1916-01-01"

    def test_diffbot_partial_date_unknown_month_and_day(self) -> None:
        value, ok, _reason = coerce_value("d1997-XX-XX", "date")
        assert ok is True
        assert value == "1997-01-01"

    def test_clean_iso_date_is_a_noop(self) -> None:
        value, ok, _reason = coerce_value("2000-03-24", "date")
        assert ok is True
        assert value == "2000-03-24"

    def test_unparseable_date_text_passes_through(self) -> None:
        value, ok, _reason = coerce_value("sometime last year", "date")
        assert ok is True
        assert value == "sometime last year"

    def test_empty_string_into_double_precision_is_null(self) -> None:
        assert coerce_value("", "double precision") == (None, True, "")

    def test_empty_string_into_bigint_is_null(self) -> None:
        assert coerce_value("", "bigint") == (None, True, "")

    def test_empty_string_into_boolean_is_null(self) -> None:
        assert coerce_value("", "boolean") == (None, True, "")

    def test_empty_string_into_date_is_null(self) -> None:
        assert coerce_value("", "date") == (None, True, "")

    def test_whitespace_only_string_is_also_null(self) -> None:
        assert coerce_value("   ", "double precision") == (None, True, "")

    def test_empty_string_into_text_is_preserved(self) -> None:
        # A TEXT column can legitimately hold "" as a real value — don't
        # turn it into NULL there.
        assert coerce_value("", "text") == ("", True, "")

    def test_timestamp_column_is_not_treated_as_date(self) -> None:
        # A "timestamp with time zone" column must not go through the
        # date-only normalizer — created_at values like this already load
        # fine and must pass through untouched.
        value, ok, _reason = coerce_value(
            "2026-06-15T04:59:04", "timestamp with time zone"
        )
        assert ok is True
        assert value == "2026-06-15T04:59:04"


class TestNormalizeDateLike:
    def test_strips_diffbot_prefix(self) -> None:
        assert normalize_date_like("d2000-03-24") == "2000-03-24"

    def test_fills_unknown_month_and_day(self) -> None:
        assert normalize_date_like("d1997-XX-XX") == "1997-01-01"

    def test_no_prefix_still_normalizes(self) -> None:
        assert normalize_date_like("1997-XX-XX") == "1997-01-01"

    def test_non_matching_text_returns_none(self) -> None:
        assert normalize_date_like("not a date") is None

    def test_non_string_returns_none(self) -> None:
        assert normalize_date_like(None) is None
        assert normalize_date_like(20000324) is None


class TestIsCompatible:
    def test_all_samples_coercible(self) -> None:
        ok, _reason = is_compatible(["$1 Million", "$6.1 Million"], "double precision")
        assert ok is True

    def test_one_incompatible_sample_fails_the_check(self) -> None:
        ok, reason = is_compatible(
            ["ba34c3bb1541495295e82f2db9ebe703"], "bigint"
        )
        assert ok is False
        assert "bigint" in reason

    def test_no_samples_is_vacuously_compatible(self) -> None:
        ok, _reason = is_compatible([], "bigint")
        assert ok is True

    def test_all_none_samples_is_vacuously_compatible(self) -> None:
        ok, _reason = is_compatible([None, None], "bigint")
        assert ok is True
