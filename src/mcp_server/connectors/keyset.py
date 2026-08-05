"""Keyset (seek) pagination cursors shared by every origin connector.

``OFFSET n LIMIT m`` makes a full table scan quadratic: to return rows
40,000,000-40,001,000 the database must first walk past forty million rows,
and it does that again for every subsequent batch. On the 43M-row cell_towers
table that is the difference between a merge that finishes and one that does
not.

Keyset pagination replaces the offset with a *value*: "give me the next
thousand rows after this one", which an index can jump straight to, so every
batch costs the same as the first.

The catch is that the ordering key must be unique, or a batch boundary
landing in the middle of a run of equal values will silently skip or repeat
rows. A watermark column alone rarely is — Pagila stamps thousands of rows
with an identical ``last_update`` — so the key is the watermark *followed by*
the primary key, which breaks those ties.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.orchestrator.utils.retry import DataQualityError

logger = logging.getLogger("merge-engine.connectors.keyset")


def choose_keyset(
    watermark: dict[str, Any] | None,
    pk_columns: list[str] | None,
) -> list[str]:
    """Pick the columns that uniquely order this table, best first.

    Returns an empty list when nothing here can guarantee a stable order, in
    which case the caller must fall back to offset pagination.
    """
    keyset: list[str] = []
    watermark_col = (watermark or {}).get("column")
    if watermark_col:
        keyset.append(str(watermark_col))
    for column in pk_columns or []:
        if column and column not in keyset:
            keyset.append(str(column))
    return keyset


def encode_cursor(
    keyset_values: list[Any] | None = None,
    offset: int | None = None,
    total_count: int | None = None,
    boundary_seen: int | None = None,
) -> str:
    """Pack the position (and the once-computed row count) into a cursor.

    ``total_count`` rides along so the expensive ``COUNT(*)`` runs once per
    table instead of once per batch — on a large table that count is itself a
    full scan, and doing it every batch doubled the cost of the whole
    extraction.

    ``boundary_seen`` is how many rows sharing the *final* key value have
    already been returned. It is what makes a non-unique key safe: see
    :func:`advance_boundary`.
    """
    payload: dict[str, Any] = {}
    if keyset_values is not None:
        payload["k"] = [_jsonable(v) for v in keyset_values]
    if offset is not None:
        payload["o"] = offset
    if total_count is not None:
        payload["n"] = total_count
    if boundary_seen:
        payload["s"] = int(boundary_seen)
    return json.dumps(payload, default=str)


def decode_cursor(cursor: str | None) -> dict[str, Any]:
    """Unpack a cursor into ``{keyset, offset, total_count}``.

    Accepts a bare integer string as an offset so cursors issued by the
    previous offset-only implementation still resume correctly.
    """
    empty: dict[str, Any] = {
        "keyset": None,
        "offset": 0,
        "total_count": None,
        "boundary_seen": 0,
    }
    if cursor is None:
        return empty

    text = str(cursor).strip()
    if not text:
        return empty

    if text.lstrip("-").isdigit():
        return {
            "keyset": None,
            "offset": int(text),
            "total_count": None,
            "boundary_seen": 0,
        }

    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("Unreadable pagination cursor %r — restarting the table.", cursor)
        return empty
    if not isinstance(payload, dict):
        return empty

    return {
        "keyset": payload.get("k"),
        "offset": int(payload.get("o") or 0),
        "total_count": payload.get("n"),
        "boundary_seen": int(payload.get("s") or 0),
    }


def keyset_is_unique(keyset: list[str] | None, pk_columns: list[str] | None) -> bool:
    """Can this ordering key be trusted to identify a row uniquely?

    Only a declared primary key proves it. When the keyset contains every PK
    column, no two rows can share it, so pagination can use a strict ``>``
    and skip the deduplication machinery entirely — which is the normal case
    for a discovered table and the one that must stay fast.

    Anything else (a watermark on its own, a hand-picked "natural key") is
    assumed non-unique, because assuming otherwise silently drops rows.
    """
    if not keyset or not pk_columns:
        return False
    return set(pk_columns).issubset(set(keyset))


def key_of(row: dict[str, Any], keyset: list[str]) -> list[Any]:
    """The row's ordering-key values, normalised the way a cursor stores them."""
    return [_jsonable(row.get(column)) for column in keyset]


def assert_key_unique(rows: list[dict[str, Any]], keyset: list[str], table_name: str) -> None:
    """Fail loudly if a key we were told is unique turns out not to be.

    The fast path uses a strict ``>``, which is only correct when no two rows
    share the key. That claim comes from schema discovery finding a primary
    key — but a wrong claim silently drops every row sharing a batch
    boundary, and the run still reports success. Since the batch is already in
    memory, checking costs nothing, and a duplicate here is proof the claim
    was wrong.
    """
    if not keyset or len(rows) < 2:
        return
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = tuple(key_of(row, keyset))
        if key in seen:
            raise DataQualityError(
                f"Table {table_name} was paginated on {keyset}, which schema "
                f"discovery reported as a primary key, but two rows share the "
                f"value {list(key)!r}. Keyset pagination on a non-unique key "
                f"silently drops rows, so this run stopped instead. Give the "
                f"table a real primary key, or configure a key that is unique.",
                table=table_name,
            )
        seen.add(key)


def drop_consumed(
    rows: list[dict[str, Any]],
    keyset: list[str],
    boundary_key: list[Any] | None,
    boundary_seen: int,
) -> list[dict[str, Any]]:
    """Remove rows at the boundary key that a previous batch already returned.

    Only rows whose key actually equals ``boundary_key`` are dropped, and only
    up to ``boundary_seen`` of them. Blindly slicing off the first N rows would
    silently discard fresh rows if anything changed at the boundary between
    batches; matching on the key means a shrunken boundary just yields fewer
    drops rather than eating real data.
    """
    if not boundary_key or boundary_seen <= 0:
        return rows
    target = list(boundary_key)
    dropped = 0
    out: list[dict[str, Any]] = []
    for row in rows:
        if dropped < boundary_seen and key_of(row, keyset) == target:
            dropped += 1
            continue
        out.append(row)
    return out


def advance_boundary(
    emitted: list[dict[str, Any]],
    keyset: list[str],
    previous_key: list[Any] | None,
    previous_seen: int,
) -> tuple[list[Any], int]:
    """Next cursor position: the last row's key, and how many share it.

    A keyset compared with a strict ``>`` assumes the ordering key is unique.
    When it is not — and in real data it usually is not — every row sharing
    the last row's key is excluded from the next batch and silently lost. On a
    300,000-row table keyed by three non-unique columns that cost 100,000 rows
    (33%) with no error raised.

    So the predicate is ``>=`` and this returns the count of already-returned
    rows at that key for the next batch to skip. The carry matters: when a
    batch ends on the *same* key it started on, the skip count accumulates
    rather than resetting, or the next batch would re-emit rows.

    The one cost is that a key value shared by more rows than a single batch
    makes the skip grow until that value is exhausted. That is bounded by the
    key's multiplicity, and it is the price of not losing rows.
    """
    last_key = key_of(emitted[-1], keyset)
    at_last = sum(1 for row in emitted if key_of(row, keyset) == last_key)
    carry = previous_seen if previous_key and list(previous_key) == last_key else 0
    return last_key, carry + at_last


def _jsonable(value: Any) -> Any:
    """Cursor values travel through JSON, so dates and Decimals become text."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
