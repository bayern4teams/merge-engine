"""Deterministic fingerprints for run-profile stage caching (Phase A4).

A saved profile (``profiles.py``) already pins *what a run decided*
(``entity_tables``, ``field_mappings``, etc.) — but nothing before this
module could tell whether those decisions still apply to the *current*
source/destination shape. ``map_fields_node.py`` was the one node that got
this right: it reconciles a pinned mapping against the live destination
schema before trusting it. Every other stage either ignored the pin
entirely or trusted it blindly. This module gives every stage the same
"does my input still match what I was pinned against" check, cheaply,
without re-discovering anything.

Credentials never enter a fingerprint — only connection *coordinates*
(host/port/database/schema) plus the shape discover_schemas already found
(table and column names/types). Two runs against the same coordinates with
an unchanged schema get the same fingerprint; a single added/removed/
retyped column changes it, which is exactly the granularity a schema-drift
guard needs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def _hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(_stable(part).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


def _origin_coords(origin: dict[str, Any]) -> tuple[Any, ...]:
    conn = origin.get("connection") or {}
    return (
        str(origin.get("type", "")).lower(),
        str(conn.get("host", "")).lower(),
        conn.get("port"),
        str(conn.get("database", "")).lower(),
        str(conn.get("schema") or conn.get("schema_name") or "").lower(),
    )


def _table_shape(table: dict[str, Any]) -> tuple[Any, ...]:
    cols = sorted(
        (str(c.get("name")), str(c.get("type")))
        for c in table.get("columns", []) or []
        if c.get("name")
    )
    return (table.get("full_name") or table.get("name"), tuple(cols))


def origin_fingerprint(origin: dict[str, Any], schema: dict[str, Any] | None) -> str:
    """One origin's coordinates + discovered table/column shape.

    ``schema`` is this origin's own entry from state's ``schemas`` dict
    (``{"tables": [...]}. ``None`` (discovery hasn't run yet) hashes to its
    own stable value rather than raising — a caller comparing "before" and
    "after" discovery gets a real fingerprint change either way.
    """
    tables = sorted(_table_shape(t) for t in (schema or {}).get("tables", []) or [])
    return _hash(_origin_coords(origin), tables)


def origins_fingerprint(origins: list[dict[str, Any]], schemas: dict[str, Any]) -> str:
    """Combined fingerprint across every origin, order-independent.

    This is the fingerprint every derived-from-schema stage (entity
    grouping, dependency analysis, join graph, field mapping) keys its own
    cache entry on: unchanged source shape -> the stage's prior output is
    still valid, full stop.
    """
    per_origin = sorted(
        origin_fingerprint(o, schemas.get(str(o.get("id"))))
        for o in (origins or [])
        if o.get("id") is not None
    )
    return _hash(per_origin)


def destination_fingerprint(destination: dict[str, Any] | None) -> str:
    """The destination's connection coordinates only — not its schema.

    Unlike an origin, the destination schema is a fixed contract
    (CLAUDE.md: "No new destination columns, ever"), so for a given
    deployment it does not vary independently of *which* destination you
    are pointed at. Coordinates alone are the correct granularity: point at
    the same database, get the same fingerprint, with no schema
    introspection round trip needed just to confirm what is already
    guaranteed true by the contract.
    """
    dest = destination or {}
    coords = (
        str(dest.get("type", "")).lower(),
        str(dest.get("host", "")).lower(),
        dest.get("port"),
        str(dest.get("database", "")).lower(),
        str(dest.get("schema") or dest.get("schema_name") or "").lower(),
    )
    return _hash(coords)


def stage_fingerprint(stage: str, **inputs: Any) -> str:
    """A named stage's fingerprint over whatever inputs it declares.

    Keeping this as one function (rather than one per stage) means adding a
    new cacheable stage never means inventing a new hashing convention —
    just naming its real inputs.
    """
    return _hash(stage, inputs)
