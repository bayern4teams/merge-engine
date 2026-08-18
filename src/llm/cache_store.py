"""Persistent (SQLite-backed) LLM response cache — Phase A5.

``cost.SemanticCache`` (the in-process LRU) dies with the process: every
fresh ``python -m src.main`` run starts with zero cache entries, so a repeat
merge over unchanged data pays for every naming/mapping/matching call again
even though the exact same questions were already answered minutes or days
earlier. This module is the second, durable layer underneath it — same
cache-key algorithm (``cost.cache_key``), same "never serve a different
answer than the model would have given" guarantee, but the entries survive
a restart.

Deliberately NOT a redesign of the caching contract: ``LLMClient.call``
checks the in-memory cache first (cheapest, no I/O), falls through to this
store on a miss, and populates both layers on a real LLM call. A caller that
never sees this module at all still behaves exactly as before.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("merge-engine.llm.cache_store")

# Bumped whenever the on-disk row shape changes incompatibly. A version
# mismatch means "start over", not "crash on an unrecognised column" -- see
# _ensure_schema.
CACHE_SCHEMA_VERSION = 1

_DEFAULT_DB_PATH = Path("config") / ".llm_cache.sqlite3"
_DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_DEFAULT_MAX_ROWS = 50_000


class PersistentLLMCache:
    """A durable key/value store for validated LLM responses.

    Thread-safe (guards the connection with a lock) since the workflow runs
    on a background thread while the API serves status reads on another —
    the same reason ``cost.SemanticCache`` needs the same guarantee.
    """

    def __init__(
        self,
        db_path: Path | str = _DEFAULT_DB_PATH,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_rows: int = _DEFAULT_MAX_ROWS,
    ) -> None:
        self._path = Path(db_path)
        self._ttl_seconds = ttl_seconds
        self._max_rows = max_rows
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.hits = 0
        self.misses = 0

    def configure(self, *, ttl_days: float | None = None, max_rows: int | None = None) -> None:
        """Update TTL/capacity in place, e.g. from config.yaml's
        ``llm.persistent_cache_*`` keys at LLMClient construction time.
        Idempotent -- safe to call on every LLMClient init even with
        unchanged values."""
        with self._lock:
            if ttl_days is not None:
                self._ttl_seconds = max(0, int(ttl_days * 24 * 3600))
            if max_rows is not None:
                self._max_rows = max(1, int(max_rows))

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema(conn)
        self._conn = conn
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)
            """
        )
        row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (CACHE_SCHEMA_VERSION,))
        elif row[0] != CACHE_SCHEMA_VERSION:
            # A schema-version mismatch means "these rows were written by a
            # different key/row format" -- never attempt to interpret them
            # under the new one. Dropping and recreating is cheap: this is a
            # cache, not a source of truth, so losing it just means the next
            # few calls pay for what would otherwise have been a hit.
            logger.warning(
                "LLM cache schema version changed (%s -> %s); clearing cache",
                row[0],
                CACHE_SCHEMA_VERSION,
            )
            conn.execute("DROP TABLE IF EXISTS cache_entries")
            conn.execute("UPDATE schema_meta SET version = ?", (CACHE_SCHEMA_VERSION,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                key TEXT PRIMARY KEY,
                model TEXT,
                created_at REAL NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_entries_created_at ON cache_entries(created_at)"
        )
        conn.commit()

    def get(self, key: str) -> tuple[bool, Any]:
        """``(found, value)`` -- ``None`` is a legal cached value, so
        presence is reported separately from the value itself."""
        with self._lock:
            conn = self._ensure_conn()
            row = conn.execute(
                "SELECT payload, created_at FROM cache_entries WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                self.misses += 1
                return False, None
            payload, created_at = row
            if self._ttl_seconds and (time.time() - created_at) > self._ttl_seconds:
                conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                conn.commit()
                self.misses += 1
                return False, None
            conn.execute("UPDATE cache_entries SET hits = hits + 1 WHERE key = ?", (key,))
            conn.commit()
            self.hits += 1
            try:
                return True, json.loads(payload)
            except json.JSONDecodeError:
                # A response cached as a raw string (call()'s content is
                # always a str) round-trips through json.dumps/loads either
                # way; a decode failure here means a corrupted row, not a
                # legitimate value -- treat it as a miss rather than
                # returning garbage.
                conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                conn.commit()
                self.misses += 1
                return False, None

    def put(self, key: str, value: Any, *, model: str = "") -> None:
        with self._lock:
            conn = self._ensure_conn()
            conn.execute(
                """
                INSERT INTO cache_entries (key, model, created_at, hits, payload)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(key) DO UPDATE SET
                    model = excluded.model,
                    created_at = excluded.created_at,
                    payload = excluded.payload
                """,
                (key, model, time.time(), json.dumps(value, default=str)),
            )
            self._evict_if_over_capacity(conn)
            conn.commit()

    def _evict_if_over_capacity(self, conn: sqlite3.Connection) -> None:
        (count,) = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()
        if count <= self._max_rows:
            return
        # Oldest-first eviction, same rationale as SemanticCache's LRU: a
        # cache is only useful while it reflects what is likely to recur,
        # and the oldest entries are the least likely to.
        excess = count - self._max_rows
        conn.execute(
            """
            DELETE FROM cache_entries WHERE key IN (
                SELECT key FROM cache_entries ORDER BY created_at ASC LIMIT ?
            )
            """,
            (excess,),
        )

    def clear(self) -> None:
        with self._lock:
            conn = self._ensure_conn()
            conn.execute("DELETE FROM cache_entries")
            conn.commit()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._ensure_conn()
            (entries,) = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "lookups": total,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "entries": entries,
        }

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


# Process-wide singleton, mirroring cost.SEMANTIC_CACHE -- callers needing
# an isolated instance (tests, a non-default path) construct their own
# PersistentLLMCache directly instead of using this one.
PERSISTENT_CACHE = PersistentLLMCache()
