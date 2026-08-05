"""Shared watermark-candidate detection for incremental extraction.

Given a table's discovered columns and primary key, picks a column to use
as an incremental watermark: a timestamp-like column (catches inserts and
updates) takes priority; a monotonic single-column integer primary key is
the fallback (insert-only capture — row updates after initial extraction
are not re-picked-up, since there's no way to detect them without a
timestamp column).
"""

from __future__ import annotations

from typing import Any

_TIMESTAMP_NAME_HINTS = (
    "updated_at",
    "modified_at",
    "updated_on",
    "modified_on",
    "last_modified",
    "last_updated",
    "update_time",
    "modify_time",
)
_TIMESTAMP_TYPE_HINTS = ("timestamp", "datetime", "date")
_NUMERIC_TYPE_HINTS = ("int", "serial", "bigint", "smallint", "integer")


def detect_watermark_column(
    columns: list[dict[str, Any]],
    primary_key: list[str],
) -> tuple[str | None, str | None]:
    """Pick a watermark column and its kind ("timestamp" | "numeric" | None).

    Parameters
    ----------
    columns : list[dict]
        Discovered columns, each with at least ``name`` and ``type``.
    primary_key : list[str]
        Discovered primary key column names (possibly empty/composite).

    Returns
    -------
    tuple[str | None, str | None]
        (watermark_column, watermark_kind), both None if no usable column exists.
    """
    by_name = {c["name"].lower(): c for c in columns}

    for hint in _TIMESTAMP_NAME_HINTS:
        col = by_name.get(hint)
        if col and any(h in col.get("type", "").lower() for h in _TIMESTAMP_TYPE_HINTS):
            return col["name"], "timestamp"

    # Fallback: any timestamp-typed column matching a name hint substring
    for col in columns:
        name_lower = col["name"].lower()
        type_lower = col.get("type", "").lower()
        if any(h in type_lower for h in _TIMESTAMP_TYPE_HINTS) and any(
            h in name_lower for h in ("updated", "modified")
        ):
            return col["name"], "timestamp"

    # Fallback: single-column monotonic integer primary key (insert-only capture)
    if len(primary_key) == 1:
        col = by_name.get(primary_key[0].lower())
        if col and any(h in col.get("type", "").lower() for h in _NUMERIC_TYPE_HINTS):
            return col["name"], "numeric"

    return None, None
