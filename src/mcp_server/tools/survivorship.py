"""Measured per-origin field reliability — Phase A6.

``unify_entity_group.py`` already has a real, per-field-class rule chain
(``priority`` -> ``longest`` -> ``recency``, with smart defaults for
revenue/counts/identity fields) — not the single uniform policy an earlier
audit assumed. The genuine gap: its ``"priority"`` rule is a STATIC number a
user types in when adding an origin (``origin.priority``), not anything
measured from the data. When no one has bothered to set it (the common
case — every origin defaults to priority 0), it is a no-op tiebreak that
falls straight through to the next rule.

This module measures what ``priority`` was standing in for: for a given
entity type's full record set, how complete is each origin's data for THIS
SPECIFIC FIELD, actually observed — not asserted. ``unify_entity_group.py``
consumes it as a new ``"measured_reliability"`` rule, inserted into the
identity-field default chains between the (still-authoritative-if-set)
manual ``priority`` and the historical fallback rules.
"""

from __future__ import annotations

from typing import Any


def measure_field_reliability(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """``{origin_id: {field: completeness_ratio}}`` across every record an
    origin contributed to one entity type.

    Completeness is the plain non-null, non-blank fraction — the same
    measure of non-blank values, aggregated across an origin's whole
    contribution instead of one record at a time. An
    origin with only 1-2 records still gets a score (rather than being
    excluded), since even a small sample beats no signal at all when it is
    the only evidence available for that origin.
    """
    non_null_counts: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for rec in records:
        origin_id = str(rec.get("_origin_id") or "?")
        totals[origin_id] = totals.get(origin_id, 0) + 1
        for field, value in rec.items():
            if field.startswith("_"):
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            non_null_counts.setdefault(origin_id, {})
            non_null_counts[origin_id][field] = non_null_counts[origin_id].get(field, 0) + 1

    reliability: dict[str, dict[str, float]] = {}
    for origin_id, total in totals.items():
        if total <= 0:
            continue
        reliability[origin_id] = {
            field: round(count / total, 4)
            for field, count in non_null_counts.get(origin_id, {}).items()
        }
    return reliability
