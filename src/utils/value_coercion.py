"""
Source-value <-> destination-column-type reconciliation.

Different origins legitimately model the same concept as different physical
types, and some model it as free text that a human would read as a number
without a second thought: revenue as ``"$6.7 Billion"``, an employee count as
``"Over 10,000"``, a headcount as a plain int. None of these are malformed
data — they are just not what the destination column's declared SQL type can
accept as-is. Left unhandled, the FIRST such value anywhere in a batch aborts
the whole atomic load (Fix #3 in merge-engine-remediation-1-to-6, project
memory: Boeing/Airbus crashed on money strings into ``double precision``,
Lockheed on a hex id into ``bigint``).

This module is the one place that knows how a value relates to a target SQL
type, so ``map_fields`` (a pre-check at mapping time — refuse an incompatible
mapping before it is even proposed) and the destination connector (the actual
coercion at load time) agree on what is convertible. Some values genuinely
cannot be converted (a hex identifier is not a number, however you squint at
it) — ``coerce_value``/``is_compatible`` say so honestly rather than
inventing a number, so the caller can route the field to a new TEXT column
instead of crashing the load.
"""

from __future__ import annotations

import re
from typing import Any

# Values that mean "true"/"false" when an origin stores a boolean as text.
_TRUTHY = {"1", "t", "true", "y", "yes", "on"}
_FALSY = {"0", "f", "false", "n", "no", "off"}

_MAGNITUDE_UNITS = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "t": 1e12,
    "trillion": 1e12,
}

# Leading qualifiers that describe an approximation rather than the number
# itself ("Over 10,000" means the number IS 10,000, just not exact).
_FILLER_PREFIX_RE = re.compile(
    r"^(over|approximately|approx\.?|about|more than|up to|nearly|roughly|around)\s+",
    re.IGNORECASE,
)

# Requires the ENTIRE (filler-stripped) string to be the numeric expression —
# deliberately NOT a bare `search`, or a hex id like "ba34c3bb1541495295e82f2"
# would have a numeric-looking substring found inside it and be misread as a
# real magnitude.
_MAGNITUDE_FULL_RE = re.compile(
    r"^[-+]?\$?\s*([0-9][0-9,]*\.?[0-9]*)\s*"
    r"(thousand|million|billion|trillion|k|mm|bn|m|b|t)?\.?$",
    re.IGNORECASE,
)

# Diffbot's knowledge-graph date encoding: a leading "d" tag on an otherwise
# ISO date, with "XX" standing in for an unknown month or day (e.g. Boeing's
# foundingDate "d1997-XX-XX", "d1916-01-01"). The leading "d" is optional so
# an already-clean "YYYY-MM-DD" passes through as a no-op.
_DIFFBOT_DATE_RE = re.compile(r"^d?(\d{4})-(\d{2}|XX)-(\d{2}|XX)$", re.IGNORECASE)


def normalize_date_like(value: Any) -> str | None:
    """Strip Diffbot's "d" date tag and fill unknown "XX" components with
    "01" so the result is a plain ISO date Postgres can parse. Returns None
    if ``value`` isn't in this shape at all (some other, genuinely
    unparseable date text)."""
    if not isinstance(value, str):
        return None
    m = _DIFFBOT_DATE_RE.match(value.strip())
    if not m:
        return None
    year, month, day = m.groups()
    month = "01" if month.upper() == "XX" else month
    day = "01" if day.upper() == "XX" else day
    return f"{year}-{month}-{day}"


def parse_magnitude(text: Any) -> float | None:
    """Parse a human-written magnitude to a float, or None if it isn't one.

    ``"$6.7 Billion"`` -> 6.7e9, ``"Over 10,000"`` -> 10000.0,
    ``"1,234"`` -> 1234.0. Returns None for text that is not, in its
    entirety, a number with an optional currency sign/magnitude word — so a
    hex identifier that merely CONTAINS digits is correctly rejected rather
    than partially parsed.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    s = _FILLER_PREFIX_RE.sub("", s).strip()
    match = _MAGNITUDE_FULL_RE.match(s)
    if not match or not match.group(1):
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (match.group(2) or "").lower()
    return number * _MAGNITUDE_UNITS.get(unit, 1.0)


def coerce_value(value: Any, target_sql_type: str) -> tuple[Any, bool, str]:
    """Adapt one value to a target SQL type.

    Returns ``(coerced_value, ok, reason)``. ``ok=False`` means the value
    genuinely cannot be represented as that type — the caller must not
    invent a number, it should route the field elsewhere (a new TEXT column,
    or refuse the mapping) rather than let the database reject it mid-batch.
    """
    if value is None:
        return None, True, ""

    t = (target_sql_type or "").strip().lower()
    if not t:
        return value, True, ""

    # An empty string is a missing-value placeholder for any type that isn't
    # itself free text (a flat/CSV-style origin's way of saying "no value"),
    # not a genuine value of that type — Postgres rejects "" for boolean,
    # numeric, date, and uuid columns outright, so treat it as NULL rather
    # than a bad value for the DB to reject mid-batch.
    if (
        isinstance(value, str)
        and value.strip() == ""
        and not any(k in t for k in ("text", "char", "varchar", "json"))
    ):
        return None, True, ""

    if "bool" in t:
        if isinstance(value, bool):
            return value, True, ""
        if isinstance(value, (int, float)):
            return bool(value), True, ""
        text = str(value).strip().lower()
        if text in _TRUTHY:
            return True, True, ""
        if text in _FALSY:
            return False, True, ""
        return value, False, f"{value!r} is not a recognisable boolean"

    if any(k in t for k in ("double", "real", "numeric", "decimal", "float")):
        if isinstance(value, bool):
            return float(value), True, ""
        if isinstance(value, (int, float)):
            return float(value), True, ""
        magnitude = parse_magnitude(value)
        if magnitude is not None:
            return magnitude, True, ""
        return value, False, f"{value!r} is not numeric and no magnitude could be parsed from it"

    if any(k in t for k in ("bigint", "integer", "smallint", "serial")):
        if isinstance(value, bool):
            return int(value), True, ""
        if isinstance(value, int):
            return value, True, ""
        if isinstance(value, float):
            return int(value), True, ""
        magnitude = parse_magnitude(value)
        if magnitude is not None:
            return int(magnitude), True, ""
        return value, False, f"{value!r} is not an integer and no magnitude could be parsed from it"

    if any(k in t for k in ("text", "char", "varchar", "uuid", "json")):
        return (value if isinstance(value, str) else str(value)), True, ""

    if "date" in t and "timestamp" not in t:
        normalized = normalize_date_like(value)
        if normalized is not None:
            return normalized, True, ""
        return value, True, ""

    # An unrecognised/unrestricted target type (e.g. timestamp — parsing
    # those is the connector's existing job, not this module's) passes through
    # unchanged; the database is the final arbiter.
    return value, True, ""


def is_compatible(sample_values: list[Any], target_sql_type: str) -> tuple[bool, str]:
    """Would mapping onto a column of ``target_sql_type`` survive these samples?

    Used at mapping time, before a field is bound to an existing destination
    column, so an incompatible mapping is refused up front instead of
    crashing the load on whichever row happens to carry the bad value.
    """
    values = [v for v in (sample_values or []) if v is not None]
    if not values:
        return True, "no non-null samples to judge"

    incompatible: list[tuple[Any, str]] = []
    for v in values:
        _coerced, ok, reason = coerce_value(v, target_sql_type)
        if not ok:
            incompatible.append((v, reason))

    if incompatible:
        sample_value, sample_reason = incompatible[0]
        return False, (
            f"{len(incompatible)}/{len(values)} sample value(s) incompatible "
            f"with {target_sql_type!r} (e.g. {sample_value!r}: {sample_reason})"
        )
    return True, ""
