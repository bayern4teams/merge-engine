"""Cross-run cache for resolve_entities' matching decision (Part 1.5 D.7).

A ``load_data`` failure used to force every entity's blocking, scoring and
clustering to redo from zero on the next attempt — even when the only thing
that changed was a bug fix downstream in the loader, and the extracted
records were byte-identical to the run that already correctly resolved them.
This is what made the fix-and-retry loop during development cost ~1-1.5h per
attempt: the expensive part (matching) had nothing to do with the part that
actually failed (loading).

Keyed by a content hash of everything that can change the matching decision
— see ``content_hash`` — so a hit is only ever safe, never approximate: any
change to the input records, the matching rules, the scope subject, or
whether LLM adjudication was requested invalidates the cache automatically.

``known_cluster_ids`` is deliberately EXCLUDED from the hash.
``_assign_cluster_id`` is a cheap, pure function of
``(entity_type, member_record_ids, known_cluster_ids)`` — it is not part of
the expensive work this cache exists to skip. Including it in the hash would
make the cache miss on almost every run (a destination accumulating rows
changes ``known_cluster_ids`` on every load), for zero safety benefit.
Instead, ``apply_known_cluster_ids`` re-derives every cluster's id for the
CURRENT run on every cache hit, cheaply and correctly.

Storage is a dedicated DuckDB file alongside the per-run spools
(``logs/spool/_resolve_cache.duckdb``) rather than a per-run ``RunSpool`` —
the whole point is that this cache outlives any single run's spool. Every
read/write degrades to a no-op on any failure (corrupt file, missing
``duckdb``, concurrent-access lock): a cache miss just means the entity gets
recomputed, which is always correct, only slower. This must never be the
thing that breaks a merge run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("merge-engine.tools.resolve_cache")

# Same directory as src.orchestrator.spool's per-run spool files (not
# imported from there — src.orchestrator sits ABOVE src.mcp_server.tools in
# the layering, and this module has no other reason to reach upward for a
# single path literal). Kept in sync by convention: both name the directory
# "logs/spool" directly.
_SPOOL_DIR = Path("logs") / "spool"
CACHE_PATH = _SPOOL_DIR / "_resolve_cache.duckdb"

# Bump when the cached result's shape, or what the matching algorithm
# considers relevant input, changes -- so an entry written by old code can
# never be misread as if new code had produced it. Not a precision-relevant
# constant; a plain cache-format tag.
_CACHE_FORMAT_VERSION = 1


def _record_id(rec: dict[str, Any]) -> str:
    return f"{rec.get('_origin_id', '')}:{rec.get('_origin_table', '')}:{rec.get('_origin_id_row', '')}"


def content_hash(
    entity_type: str,
    records: list[dict[str, Any]],
    matching_rules: dict[str, Any] | None,
    subject: str | None,
    blocking_keys: list[str] | None,
    llm_requested: bool,
) -> str:
    """Deterministic hash of everything that can change the matching outcome.

    Order-independent over records (sorted by stable record id) so the same
    record set hashes identically regardless of extraction order.
    """
    parts = sorted(
        (_record_id(rec), json.dumps(rec, sort_keys=True, default=str)) for rec in records
    )
    payload = {
        "cache_format_version": _CACHE_FORMAT_VERSION,
        "entity_type": entity_type,
        "records": parts,
        "matching_rules": matching_rules or {},
        "subject": subject,
        "blocking_keys": list(blocking_keys) if blocking_keys else None,
        "llm_requested": bool(llm_requested),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _connect() -> Any:
    import duckdb

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(CACHE_PATH))
    con.execute(
        "CREATE TABLE IF NOT EXISTS resolve_cache ("
        "  content_hash VARCHAR PRIMARY KEY,"
        "  entity_type VARCHAR,"
        "  created_at TIMESTAMP DEFAULT current_timestamp,"
        "  result_json JSON"
        ")"
    )
    return con


def get(content_hash_value: str) -> dict[str, Any] | None:
    """The cached result for this hash, or ``None`` on a miss or any failure.

    ``MERGE_ENGINE_DISABLE_RESOLVE_CACHE`` forces every read to miss, without
    touching writes — set by scripts/diagnostics/profile_resolve.py, whose
    entire job is proving the matching code itself is byte-identical after an
    edit. A cache hit there would return a PRE-edit result and call it
    verified without ever exercising the new code.
    """
    if os.environ.get("MERGE_ENGINE_DISABLE_RESOLVE_CACHE"):
        return None
    if not CACHE_PATH.exists():
        return None
    try:
        with _connect() as con:
            row = con.execute(
                "SELECT result_json FROM resolve_cache WHERE content_hash = ?",
                [content_hash_value],
            ).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = json.loads(row[0])
        return result
    except Exception as exc:  # never let a cache read break a merge run
        logger.warning("resolve_cache read failed (%s) — treating as a cache miss.", exc)
        return None


def put(content_hash_value: str, entity_type: str, result: dict[str, Any]) -> None:
    """Store a matching result. Best-effort: a write failure is logged, not raised."""
    try:
        with _connect() as con:
            con.execute("DELETE FROM resolve_cache WHERE content_hash = ?", [content_hash_value])
            con.execute(
                "INSERT INTO resolve_cache (content_hash, entity_type, result_json) "
                "VALUES (?, ?, ?)",
                [content_hash_value, entity_type, json.dumps(result, default=str)],
            )
    except Exception as exc:  # never let a cache write break a merge run
        logger.warning("resolve_cache write failed (%s) — continuing uncached.", exc)


def apply_known_cluster_ids(
    result: dict[str, Any],
    entity_type: str,
    known_cluster_ids: dict[str, str] | None,
) -> dict[str, Any]:
    """Re-derive every cluster's id for the CURRENT run, on a cache hit.

    Reusing a cached ``cluster_id`` verbatim would carry a stale destination
    row id from whichever run originally populated the cache — corrupting
    the current run's load (updating the wrong row, or duplicating one, per
    ``_assign_cluster_id``'s own docstring). This is what makes a cache hit
    safe regardless of ``known_cluster_ids`` never being part of the hash.
    """
    from src.mcp_server.tools.resolve_entities import _assign_cluster_id

    clusters = []
    for cluster in result.get("clusters", []):
        member_ids = cluster.get("record_ids", [])
        clusters.append(
            {
                **cluster,
                "cluster_id": _assign_cluster_id(entity_type, member_ids, known_cluster_ids),
            }
        )
    return {**result, "clusters": clusters}
