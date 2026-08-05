"""Per-run DuckDB spool: the extracted rows live on disk, not in graph state.

Every extracted row used to be appended to a Python list, stored in
``MergeState.extracted_data``, and then serialised into a LangGraph
checkpoint on every single step — and again on every HITL pause and resume.
That makes peak memory and checkpoint size both proportional to the size of
the source data, so a merge of the 43M-row cell_towers table cannot start,
let alone survive a human walking away from the review gate.

The spool replaces the row lists with a handle. Rows go into a DuckDB file
under ``logs/spool/<run_id>.duckdb``; state carries only
``{entity: {"table": ..., "row_count": ...}}``. DuckDB is columnar and
out-of-core, so the later phases can also do blocking and scoring as SQL
against this same file instead of nested Python loops.

Rows are stored as JSON text in a single ``payload`` column rather than as
typed columns. Origins disagree about schema by definition — that is the
whole point of a merge — and a fixed table would have to be migrated every
time a new origin contributed a field. DuckDB reads JSON natively, so the
SQL phases can still project individual fields out of it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from src.orchestrator.utils.retry import DataQualityError

logger = logging.getLogger("merge-engine.orchestrator.spool")

SPOOL_DIR = Path("logs") / "spool"

# Fallback path only — see _bulk_insert for why this is not the normal route.
_INSERT_CHUNK = 2000

# A spool untouched for this long is finished with. Anything more recent is
# assumed to belong to a run still in flight — possibly in another process —
# and is never swept. See cleanup_old_spools.
_SPOOL_MIN_AGE_SECONDS = 6 * 60 * 60


def _bulk_insert(con: Any, table: str, columns: list[str], rows: list[tuple]) -> None:
    """Insert many rows at once, the only way DuckDB does this quickly.

    ``executemany`` looks like the obvious call and is unusably slow here: it
    binds and parses row by row, and every payload is JSON, so each row pays
    a parse. Measured on this machine, 50,000 spool rows took **259 seconds**
    that way — 1M rows would be 86 minutes and the 43M-row cell_towers table
    over 60 hours, which is what made extraction look hung at scale.

    Handing DuckDB a columnar frame instead lets it ingest the whole batch in
    one pass: the same 50,000 rows take **0.19s**, a ~1,300x difference, and
    the payload column is still queryable as JSON afterwards.

    pandas ships with DuckDB's own tooling and is already a dependency, but
    the slow path is kept as a fallback so a stripped environment degrades in
    speed rather than breaking outright.
    """
    if not rows:
        return
    try:
        import pandas as pd

        frame = pd.DataFrame(rows, columns=columns)
        con.register("_incoming_rows", frame)
        try:
            con.execute(f"INSERT INTO {table} SELECT * FROM _incoming_rows")
        finally:
            con.unregister("_incoming_rows")
        return
    except ImportError:
        logger.warning("pandas unavailable; falling back to slow row-by-row spool inserts")

    placeholders = ", ".join("?" for _ in columns)
    for i in range(0, len(rows), _INSERT_CHUNK):
        con.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})",
            rows[i : i + _INSERT_CHUNK],
        )


def spool_path(run_id: str) -> Path:
    """Where this run's spool file lives."""
    safe = "".join(c for c in str(run_id or "unknown") if c.isalnum() or c in "-_")
    return SPOOL_DIR / f"{safe or 'unknown'}.duckdb"


def _table_for(entity_type: str, kind: str = "rows") -> str:
    """A safe DuckDB table name for one entity's records of a given kind.

    ``rows`` are the extracted source records; ``unified`` are the merged
    output records; ``clusters`` are the grouping decisions. All three used
    to sit in checkpointed state — measured on a 2,284-record merge, the
    checkpoint was 69.6% unified_entities and 28.2% entity_clusters.
    """
    safe = "".join(c if c.isalnum() else "_" for c in str(entity_type))
    return f"{kind}_{safe or 'entity'}"


class RunSpool:
    """The extracted rows for one run, on disk.

    Opened per use rather than held open: the workflow manager runs the graph
    on a background thread and tears the event loop down between HITL gates,
    and a DuckDB handle outliving that is how a run ends up reading from a
    closed database.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "unknown")
        self.path = spool_path(self.run_id)
        # Rows this instance believes it has written, per entity. Compared
        # against what the file actually holds after every append — see the
        # check in append() for the failure that motivated it.
        self._written: dict[str, int] = {}

    # ── Lifecycle ────────────────────────────────────────────────────

    def _connect(self):
        import duckdb

        self.path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(self.path))

    def exists(self) -> bool:
        return self.path.exists()

    def discard(self) -> None:
        """Delete this run's spool. Safe to call when it was never created."""
        try:
            if self.path.exists():
                self.path.unlink()
                logger.info("Discarded spool %s", self.path)
        except OSError as exc:
            logger.warning("Could not delete spool %s: %s", self.path, exc)

    # ── Writing ──────────────────────────────────────────────────────

    def reset_entity(self, entity_type: str) -> None:
        """Drop and recreate an entity's table.

        Called before an entity is re-extracted so a retry does not append to
        the rows the failed attempt already wrote.
        """
        table = _table_for(entity_type)
        self._written[entity_type] = 0
        with self._connect() as con:
            con.execute(f"DROP TABLE IF EXISTS {table}")
            con.execute(
                f"CREATE TABLE {table} ("
                "  seq BIGINT,"
                "  record_id VARCHAR,"
                "  origin_id VARCHAR,"
                "  origin_table VARCHAR,"
                "  payload JSON"
                ")"
            )

    def append(self, entity_type: str, rows: Iterable[dict[str, Any]]) -> int:
        """Add rows to an entity's table. Returns how many were written."""
        batch = list(rows)
        if not batch:
            return 0

        table = _table_for(entity_type)
        with self._connect() as con:
            self._ensure_table(con, table)
            start = con.execute(f"SELECT COALESCE(MAX(seq), -1) + 1 FROM {table}").fetchone()[0]

            payload_rows = []
            for offset, row in enumerate(batch):
                origin_id = str(row.get("_origin_id", ""))
                origin_table = str(row.get("_origin_table", ""))
                record_id = f"{origin_id}:{origin_table}:{row.get('_origin_id_row', '')}"
                payload_rows.append(
                    (
                        start + offset,
                        record_id,
                        origin_id,
                        origin_table,
                        json.dumps(row, default=str),
                    )
                )

            _bulk_insert(
                con,
                table,
                ["seq", "record_id", "origin_id", "origin_table", "payload"],
                payload_rows,
            )

            # Confirm the rows are actually there. A 43M-row extraction once
            # reported success having stored 19,076,150 of them, because the
            # spool file was deleted underneath it and silently recreated —
            # every append then "succeeded" against a fresh, empty table.
            #
            # Comparing against this instance's own running total is what
            # catches that: a wiped table restarts `start` at 0 and looks
            # perfectly self-consistent, so only an expectation carried from
            # outside the file can tell the difference.
            expected = self._written.get(entity_type, start) + len(batch)
            stored = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if stored != expected:
                raise DataQualityError(
                    f"Spool for run {self.run_id!r} holds {stored} rows for "
                    f"'{entity_type}' but should hold {expected} after this "
                    f"batch. Something else modified the spool mid-run, so "
                    f"the extracted rows cannot be trusted.",
                    entity=entity_type,
                    rows_affected=len(batch),
                )
            self._written[entity_type] = stored
        return len(batch)

    def truncate_after(self, entity_type: str, keep: int) -> int:
        """Drop everything written past `keep` rows. Returns how many went.

        This is the spool's version of the Phase M rule that a table which
        could not be read in full must not be merged as though it were: the
        partial rows are removed rather than left to look complete.
        """
        table = _table_for(entity_type)
        with self._connect() as con:
            self._ensure_table(con, table)
            before = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            con.execute(f"DELETE FROM {table} WHERE seq >= ?", [keep])
            after = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        # This is a deliberate removal, so it becomes the new expectation —
        # otherwise the next append would report it as tampering.
        self._written[entity_type] = after
        return before - after

    # ── Reading ──────────────────────────────────────────────────────

    def row_count(self, entity_type: str) -> int:
        table = _table_for(entity_type)
        if not self.exists():
            return 0
        with self._connect() as con:
            if not self._table_exists(con, table):
                return 0
            return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def entities(self) -> list[str]:
        """Entity types with a table in this spool."""
        if not self.exists():
            return []
        with self._connect() as con:
            names = [
                r[0]
                for r in con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name LIKE 'rows_%'"
                ).fetchall()
            ]
        return [n[len("rows_") :] for n in names]

    def read_all(self, entity_type: str) -> list[dict[str, Any]]:
        """Every row for an entity, in extraction order.

        The whole point of the spool is not to need this — prefer
        :meth:`iter_rows` — but the pre-Phase-N nodes take a list, and
        loading one entity at a time is still a large improvement on holding
        every entity in checkpointed state at once.
        """
        return list(self.iter_rows(entity_type))

    def iter_rows(self, entity_type: str, chunk: int = 10000) -> Iterator[dict[str, Any]]:
        """Stream an entity's rows without materialising them all at once."""
        table = _table_for(entity_type)
        if not self.exists():
            return
        with self._connect() as con:
            if not self._table_exists(con, table):
                return
            offset = 0
            while True:
                block = con.execute(
                    f"SELECT payload FROM {table} ORDER BY seq "
                    f"LIMIT {int(chunk)} OFFSET {int(offset)}"
                ).fetchall()
                if not block:
                    return
                for (payload,) in block:
                    yield json.loads(payload)
                offset += len(block)

    def per_origin_counts(self, entity_type: str) -> dict[str, int]:
        """How many rows each origin contributed, counted in SQL.

        The reconciliation report needs this; walking every row in Python to
        build it would put the whole entity back in memory, which is the one
        thing the spool exists to avoid.
        """
        table = _table_for(entity_type)
        if not self.exists():
            return {}
        with self._connect() as con:
            if not self._table_exists(con, table):
                return {}
            return {
                (origin or "unknown"): int(count)
                for origin, count in con.execute(
                    f"SELECT origin_id, COUNT(*) FROM {table} GROUP BY origin_id"
                ).fetchall()
            }

    def read_by_ids(self, entity_type: str, record_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch specific records by their cross-node key.

        This is what makes cluster-at-a-time unification possible: only the
        records in the cluster being merged are ever in memory.
        """
        table = _table_for(entity_type)
        if not record_ids or not self.exists():
            return {}
        with self._connect() as con:
            if not self._table_exists(con, table):
                return {}
            found: dict[str, dict[str, Any]] = {}
            for i in range(0, len(record_ids), _INSERT_CHUNK):
                block = record_ids[i : i + _INSERT_CHUNK]
                placeholders = ", ".join("?" for _ in block)
                for record_id, payload in con.execute(
                    f"SELECT record_id, payload FROM {table} WHERE record_id IN ({placeholders})",
                    block,
                ).fetchall():
                    found[record_id] = json.loads(payload)
        return found

    # ── Derived collections (unified records, clusters) ──────────────
    # Same storage, different kind. These are outputs rather than inputs, so
    # they are written whole rather than appended batch by batch.

    def put_records(self, kind: str, entity_type: str, records: list[dict[str, Any]]) -> int:
        """Replace an entity's records of `kind`. Returns how many were stored."""
        table = _table_for(entity_type, kind)
        with self._connect() as con:
            con.execute(f"DROP TABLE IF EXISTS {table}")
            con.execute(f"CREATE TABLE {table} (seq BIGINT, payload JSON)")
            if records:
                rows = [(i, json.dumps(r, default=str)) for i, r in enumerate(records)]
                _bulk_insert(con, table, ["seq", "payload"], rows)
        return len(records)

    def get_records(self, kind: str, entity_type: str) -> list[dict[str, Any]]:
        """Every record of `kind` for an entity, in stored order."""
        return list(self.iter_records(kind, entity_type))

    def iter_records(
        self, kind: str, entity_type: str, chunk: int = 10000
    ) -> Iterator[dict[str, Any]]:
        """Stream records of `kind` without materialising them all at once."""
        table = _table_for(entity_type, kind)
        if not self.exists():
            return
        with self._connect() as con:
            if not self._table_exists(con, table):
                return
            offset = 0
            while True:
                block = con.execute(
                    f"SELECT payload FROM {table} ORDER BY seq "
                    f"LIMIT {int(chunk)} OFFSET {int(offset)}"
                ).fetchall()
                if not block:
                    return
                for (payload,) in block:
                    yield json.loads(payload)
                offset += len(block)

    def count_records(self, kind: str, entity_type: str) -> int:
        table = _table_for(entity_type, kind)
        if not self.exists():
            return 0
        with self._connect() as con:
            if not self._table_exists(con, table):
                return 0
            return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    # ── Internals ────────────────────────────────────────────────────

    @staticmethod
    def _table_exists(con: Any, table: str) -> bool:
        return bool(
            con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [table],
            ).fetchone()[0]
        )

    @classmethod
    def _ensure_table(cls, con: Any, table: str) -> None:
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            "  seq BIGINT,"
            "  record_id VARCHAR,"
            "  origin_id VARCHAR,"
            "  origin_table VARCHAR,"
            "  payload JSON"
            ")"
        )


# ── State accessors ──────────────────────────────────────────────────
# `extracted_data` holds one of two things per entity: a spool handle (the
# Phase N shape) or a plain list of rows (everything before it, and any
# caller that hands rows in directly, including most unit tests). Consumers
# go through these helpers so neither shape has to be special-cased at each
# use site, and so a list never silently turns into an empty result.

_HANDLE_KEY = "__spool__"


def make_handle(
    run_id: str, entity_type: str, row_count: int, kind: str = "rows"
) -> dict[str, Any]:
    """The value stored in state in place of an entity's records."""
    return {
        _HANDLE_KEY: True,
        "run_id": str(run_id),
        "entity": str(entity_type),
        "row_count": int(row_count),
        "kind": kind,
    }


def store_clusters(
    run_id: str,
    entity_type: str,
    clusters: list[dict[str, Any]],
    *,
    needs_review: int = 0,
    multi_record: int = 0,
) -> dict[str, Any]:
    """Spool an entity's clusters, returning a handle that carries the summary.

    The counts ride along because the graph's routers and the review gates
    only ever ask "is there anything to review, and how much" — making them
    read every cluster back to answer that would undo the saving.
    """
    RunSpool(run_id).put_records("clusters", entity_type, clusters)
    handle = make_handle(run_id, entity_type, len(clusters), kind="clusters")
    handle["needs_review"] = int(needs_review)
    handle["multi_record"] = int(multi_record)
    return handle


def clusters_for(state: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    """One entity's clusters, from a handle or an inline list."""
    return collection_records(state.get("entity_clusters") or {}, entity_type, kind="clusters")


def unified_for(state: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    """One entity's unified output records, from a handle or an inline list."""
    return collection_records(state.get("unified_entities") or {}, entity_type, kind="unified")


def store_unified(run_id: str, entity_type: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Spool an entity's unified records, returning the handle for state."""
    RunSpool(run_id).put_records("unified", entity_type, records)
    return make_handle(run_id, entity_type, len(records), kind="unified")


def spool_collection(
    state: dict[str, Any],
    channel: str,
    kind: str,
    run_id: str,
    entity_type: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write `records` to the spool and return the handle to store in state.

    Used for `unified_entities` and `entity_clusters` — the two channels that
    a measurement showed were 98% of what a checkpoint still carried after
    the extracted rows moved out.
    """
    spool = RunSpool(run_id)
    spool.put_records(kind, entity_type, records)
    return make_handle(run_id, entity_type, len(records), kind=kind)


def collection_records(
    container: Any, entity_type: str, kind: str = "rows"
) -> list[dict[str, Any]]:
    """Read one entity's records from a handle-or-list container."""
    value = (container or {}).get(entity_type)
    if is_handle(value):
        return RunSpool(value.get("run_id", "")).get_records(
            value.get("kind", kind), value.get("entity", entity_type)
        )
    return list(value or [])


def collection_count(container: Any, entity_type: str) -> int:
    """How many records an entity has, without reading them."""
    value = (container or {}).get(entity_type)
    if is_handle(value):
        return int(value.get("row_count", 0))
    return len(value or [])


def is_handle(value: Any) -> bool:
    return isinstance(value, dict) and value.get(_HANDLE_KEY) is True


def entity_rows(state: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    """Every row for an entity, whether spooled or inline."""
    value = (state.get("extracted_data") or {}).get(entity_type)
    if is_handle(value):
        return RunSpool(value.get("run_id", "")).read_all(value.get("entity", entity_type))
    return list(value or [])


def entity_row_count(state: dict[str, Any], entity_type: str) -> int:
    """How many rows an entity has, without reading them."""
    value = (state.get("extracted_data") or {}).get(entity_type)
    if is_handle(value):
        return int(value.get("row_count", 0))
    return len(value or [])


def entity_origin_counts(state: dict[str, Any], entity_type: str) -> dict[str, int]:
    """Per-origin row counts for an entity, without loading its rows."""
    value = (state.get("extracted_data") or {}).get(entity_type)
    if is_handle(value):
        return RunSpool(value.get("run_id", "")).per_origin_counts(value.get("entity", entity_type))
    counts: dict[str, int] = {}
    for row in value or []:
        origin = row.get("_origin_id", "unknown")
        counts[origin] = counts.get(origin, 0) + 1
    return counts


def entity_records_by_id(
    state: dict[str, Any],
    entity_type: str,
    record_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Look up specific records by their cross-node key.

    The review gates need the field values behind a handful of record ids.
    Reading only those, rather than every row of the entity, is what keeps a
    43M-row merge's review gate as cheap as a 1k-row one.
    """
    value = (state.get("extracted_data") or {}).get(entity_type)
    if is_handle(value):
        return RunSpool(value.get("run_id", "")).read_by_ids(
            value.get("entity", entity_type), record_ids
        )
    wanted = set(record_ids)
    found: dict[str, dict[str, Any]] = {}
    for row in value or []:
        key = f"{row.get('_origin_id')}:{row.get('_origin_table')}:{row.get('_origin_id_row')}"
        if key in wanted:
            found[key] = row
    return found


def cleanup_old_spools(
    keep_run_ids: set[str] | None = None,
    min_age_seconds: float = _SPOOL_MIN_AGE_SECONDS,
) -> int:
    """Delete stale spools. Returns how many went.

    Spools are per-run scratch space; without this they accumulate one file
    per merge until the disk preflight starts failing.

    Age is the primary guard, and membership only an extra reprieve — the
    reverse of what this used to do. Deleting everything outside the caller's
    keep-set assumed that set names every run whose rows still matter, and it
    does not: ``_active_workflows`` lives in memory, so after an app restart
    every run parked at a HITL gate looks unknown, and starting one new merge
    would delete the rows a human was about to come back and approve.

    Measured, not hypothesised: a 43M-row extraction lost 24,200,000 rows
    this way when two unrelated merges started while it was still writing.
    Nothing failed — the spool was simply recreated underneath it and the run
    reported success with 19,076,150 rows.
    """
    if not SPOOL_DIR.exists():
        return 0
    keep = {spool_path(r).name for r in (keep_run_ids or set())}
    now = time.time()
    removed = 0
    for path in SPOOL_DIR.glob("*.duckdb"):
        if path.name in keep:
            continue
        # Part 1.5 D.7: `_resolve_cache.duckdb` is not a per-run spool — it
        # deliberately outlives any single run (that is the whole point of a
        # cross-run matching-decision cache) and manages its own lifetime,
        # not this run-scoped sweep. A leading underscore is reserved for
        # exactly this: any spool-directory file that isn't one run's rows.
        if path.name.startswith("_"):
            continue
        try:
            # A file still being written to is by definition in use, whether
            # or not this process knows whose run it belongs to.
            if now - path.stat().st_mtime < min_age_seconds:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Cleaned up %d old spool file(s)", removed)
    return removed
