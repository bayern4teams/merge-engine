"""Shared normalisation for extraction filters.

The ``filter`` argument to a connector's ``extract`` used to mean one thing:
``{column: value}`` → ``column = value``. Scoped merges ("everything about
Nova") need two more shapes — case-insensitive substring match on a name, and
membership in a set of subject ids — so the contract is widened to:

    {column: value}                         → equals (unchanged, back-compat)
    {column: {"op": "eq",    "value": v}}   → equals
    {column: {"op": "ilike", "value": v}}   → case-insensitive substring
    {column: {"op": "in",    "value": [..]}}→ membership
    {column: {"op": "in_scratch", "value": name}} → membership via a scratch
        table (Part 1.5 D.6; ClickHouse-only, built by
        ClickHouseConnector.create_id_scratch_table — ``name`` is a table
        name, not a value list, so it is exempt from the "in" op's
        list/empty-list handling below)

This module turns either shape into a flat list of canonical conditions; each
connector renders them in its own dialect (named binds for PostgreSQL, typed
placeholders for ClickHouse, a query dict for MongoDB), because the binding
syntax is what actually differs between them.
"""

from __future__ import annotations

from typing import Any

_VALID_OPS = ("eq", "ilike", "in", "in_scratch")


def normalize_filter(filter: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return ``[{"column", "op", "value"}, ...]`` for any accepted filter shape.

    A bare scalar is ``eq``. An ``in`` with an empty list is dropped — an empty
    ``IN ()`` is a SQL error in some dialects and means "match nothing", which a
    caller that produced no ids never intends here (the scoped-extraction code
    handles "no subject ids" explicitly rather than via an empty IN).
    """
    conditions: list[dict[str, Any]] = []
    for column, spec in (filter or {}).items():
        if isinstance(spec, dict) and "op" in spec:
            op = str(spec.get("op", "eq")).strip().lower()
            value = spec.get("value")
        else:
            op, value = "eq", spec
        if op not in _VALID_OPS:
            op = "eq"
        if op == "in":
            values = list(value) if isinstance(value, (list, tuple, set)) else [value]
            values = [v for v in values if v is not None]
            if not values:
                continue
            conditions.append({"column": str(column), "op": "in", "value": values})
        else:
            conditions.append({"column": str(column), "op": op, "value": value})
    return conditions
