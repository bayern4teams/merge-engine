"""
preflight node: prove the run *can* succeed before it starts doing work.

Every check here answers a question that used to be answered five minutes
in, halfway through an extraction, by an exception with no context: can we
reach this database, are these credentials good, may we create the tables we
are about to need, is the LLM actually answering, is there disk left for the
checkpoint. Each one is cheap; together they turn a class of late, confusing
failures into an immediate, specific message.

Checks are grouped into *blocking* (the run cannot possibly succeed) and
*warnings* (the run can proceed but a human should know). Only blocking
checks stop the pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path
from typing import Any

from src.orchestrator.utils.retry import UserInputError, classify_exception

logger = logging.getLogger("merge-engine.orchestrator.preflight")

# Probe timeouts, deliberately short. Preflight exists to fail fast; a
# database that needs longer than this to say hello will not survive a
# multi-batch extraction either.
_CONNECT_TIMEOUT_S = 10
_LLM_TIMEOUT_S = 45

# Enough headroom for the SQLite checkpoint plus this run's report files.
# Far less than a large extraction wants, but it catches a genuinely full
# disk before a half-written checkpoint corrupts the run's resume point.
_MIN_FREE_BYTES = 512 * 1024 * 1024

# Named so that anyone finding it in a database knows what it was and that
# it is safe to drop.
_PROBE_TABLE = "_merge_engine_preflight_probe"


class _Check:
    """One preflight result: what was tested, whether it passed, and why."""

    __slots__ = ("blocking", "detail", "name", "ok")

    def __init__(self, name: str, ok: bool, detail: str = "", blocking: bool = True) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.blocking = blocking

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "blocking": self.blocking,
        }


def _connection_of(origin: dict[str, Any]) -> dict[str, Any]:
    """Origins carry their credentials under `connection`; destinations inline."""
    conn = origin.get("connection")
    return dict(conn) if isinstance(conn, dict) else dict(origin)


def _probe_postgres(cfg: dict[str, Any]) -> None:
    """Connect and run the cheapest possible statement."""
    import psycopg2

    conn = psycopg2.connect(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port") or 5432),
        user=cfg.get("user", ""),
        password=cfg.get("password", ""),
        dbname=cfg.get("database") or "postgres",
        connect_timeout=_CONNECT_TIMEOUT_S,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        conn.close()


def _probe_clickhouse(cfg: dict[str, Any]) -> None:
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port") or 8123),
        username=cfg.get("user", ""),
        password=cfg.get("password", ""),
        database=cfg.get("database") or "default",
        connect_timeout=_CONNECT_TIMEOUT_S,
    )
    try:
        client.query("SELECT 1")
    finally:
        with contextlib.suppress(Exception):
            client.close()


def _probe_mongodb(cfg: dict[str, Any]) -> None:
    from pymongo import MongoClient

    user = cfg.get("user", "")
    password = cfg.get("password", "")
    credentials = f"{user}:{password}@" if user else ""
    uri = f"mongodb://{credentials}{cfg.get('host', 'localhost')}:{int(cfg.get('port') or 27017)}/"
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=_CONNECT_TIMEOUT_S * 1000,
        connectTimeoutMS=_CONNECT_TIMEOUT_S * 1000,
    )
    try:
        client.admin.command("ping")
    finally:
        client.close()


_PROBES = {
    "postgres": _probe_postgres,
    "postgresql": _probe_postgres,
    "clickhouse": _probe_clickhouse,
    "mongodb": _probe_mongodb,
}


def _probe_reachable(db_type: str, cfg: dict[str, Any]) -> None:
    """Raise if the database is unreachable or the credentials are refused."""
    probe = _PROBES.get((db_type or "").lower())
    if probe is None:
        raise UserInputError(
            f"Unsupported database type '{db_type}'. Supported: {', '.join(sorted(set(_PROBES)))}."
        )
    probe(cfg)


def _probe_postgres_writable(cfg: dict[str, Any]) -> None:
    """Prove we may CREATE a table here, without leaving one behind.

    PostgreSQL DDL is transactional, so creating inside a transaction and
    rolling back exercises the exact permission a load needs while
    guaranteeing the destination is untouched afterwards.
    """
    import psycopg2

    schema = cfg.get("schema") or "public"
    conn = psycopg2.connect(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port") or 5432),
        user=cfg.get("user", ""),
        password=cfg.get("password", ""),
        dbname=cfg.get("database") or "postgres",
        connect_timeout=_CONNECT_TIMEOUT_S,
    )
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(f'CREATE TABLE "{schema}"."{_PROBE_TABLE}" (id integer)')
            cur.execute(f'INSERT INTO "{schema}"."{_PROBE_TABLE}" (id) VALUES (1)')
        conn.rollback()
    finally:
        conn.close()


def _probe_clickhouse_writable(cfg: dict[str, Any]) -> None:
    """ClickHouse has no transactional DDL, so create then drop explicitly."""
    import clickhouse_connect

    database = cfg.get("database") or "default"
    client = clickhouse_connect.get_client(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port") or 8123),
        username=cfg.get("user", ""),
        password=cfg.get("password", ""),
        database=database,
        connect_timeout=_CONNECT_TIMEOUT_S,
    )
    try:
        client.command(
            f"CREATE TABLE IF NOT EXISTS {database}.{_PROBE_TABLE} "
            f"(id Int32) ENGINE = MergeTree ORDER BY id"
        )
        client.command(f"INSERT INTO {database}.{_PROBE_TABLE} VALUES (1)")
        client.command(f"DROP TABLE IF EXISTS {database}.{_PROBE_TABLE}")
    finally:
        with contextlib.suppress(Exception):
            client.close()


def _probe_writable(db_type: str, cfg: dict[str, Any]) -> None:
    kind = (db_type or "").lower()
    if kind in ("postgres", "postgresql"):
        _probe_postgres_writable(cfg)
    elif kind == "clickhouse":
        _probe_clickhouse_writable(cfg)
    else:
        raise UserInputError(
            f"'{db_type}' cannot be used as a destination. "
            "Supported destinations: postgres, clickhouse."
        )


async def _probe_llm(llm_config: dict[str, Any]) -> None:
    """Prove the model answers, not merely that a key is configured.

    An exhausted quota returns a 403 on the first real call — which, before
    this check existed, surfaced as an empty entity list and a run that
    reported success while writing nothing.
    """
    from src.llm.client import create_llm_client

    client = create_llm_client(llm_config)
    response = await asyncio.wait_for(
        client.call(
            system_prompt="Reply with the single word: ok",
            user_prompt="ping",
        ),
        timeout=_LLM_TIMEOUT_S,
    )
    if response is None:
        raise UserInputError(
            "The LLM returned no response to a trivial prompt. Check the "
            "model name, base URL, and API key in config/config.yaml."
        )


def _check_disk_space() -> _Check:
    """Is there room for the checkpoint database and this run's reports?"""
    try:
        target = Path("logs")
        target.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(target).free
    except Exception as exc:
        return _Check(
            "disk space",
            False,
            f"Could not determine free disk space: {exc}",
            blocking=False,
        )
    free_mb = free // (1024 * 1024)
    if free < _MIN_FREE_BYTES:
        return _Check(
            "disk space",
            False,
            f"Only {free_mb} MB free where checkpoints and run reports are "
            f"written; at least {_MIN_FREE_BYTES // (1024 * 1024)} MB is "
            "needed for the run to be resumable.",
        )
    return _Check("disk space", True, f"{free_mb} MB free")


async def preflight(state: dict[str, Any]) -> dict[str, Any]:
    """Validate everything the run depends on, before it depends on it.

    Returns a ``preflight_report`` describing every check, and — when a
    blocking check fails — a ``failure`` that routes the run to the error
    gate instead of letting it start work it cannot finish.
    """
    logger.debug("ENTER preflight")
    checks: list[_Check] = []

    user_request = (state.get("user_request") or "").strip()
    origins = state.get("origins") or []
    destination = state.get("destination") or {}
    llm_config = state.get("llm_config") or {}

    # ── The request itself ──────────────────────────────────────────
    checks.append(
        _Check(
            "merge request",
            bool(user_request),
            "" if user_request else "No merge request was given, so there is nothing to interpret.",
        )
    )
    checks.append(
        _Check(
            "origins configured",
            bool(origins),
            f"{len(origins)} origin(s)"
            if origins
            else "No origin databases are configured, so there is nothing to read.",
        )
    )
    checks.append(
        _Check(
            "destination configured",
            bool(destination.get("type")),
            f"{destination.get('type')} → {destination.get('database', '?')}"
            if destination.get("type")
            else "No destination database is configured, so there is nowhere to write.",
        )
    )

    # ── Origin reachability ─────────────────────────────────────────
    # Every origin is probed even after one fails, so the user sees the full
    # list of what is wrong in one pass instead of fixing them one per run.
    for origin in origins:
        origin_id = origin.get("id", "?") if isinstance(origin, dict) else "?"
        origin_type = origin.get("type", "") if isinstance(origin, dict) else ""
        label = f"origin '{origin_id}' reachable"
        try:
            await asyncio.to_thread(_probe_reachable, origin_type, _connection_of(origin))
            checks.append(_Check(label, True, f"{origin_type}"))
        except Exception as exc:
            classified = classify_exception(exc)
            checks.append(
                _Check(
                    label,
                    False,
                    f"{type(classified).__name__}: {classified}",
                )
            )

    # ── Destination reachability and write permission ───────────────
    dest_type = destination.get("type", "")
    if dest_type:
        try:
            await asyncio.to_thread(_probe_reachable, dest_type, destination)
            checks.append(_Check("destination reachable", True, dest_type))
        except Exception as exc:
            classified = classify_exception(exc)
            checks.append(
                _Check(
                    "destination reachable",
                    False,
                    f"{type(classified).__name__}: {classified}",
                )
            )
        else:
            # Only worth testing writes once we know we can connect at all.
            try:
                await asyncio.to_thread(_probe_writable, dest_type, destination)
                checks.append(
                    _Check(
                        "destination writable",
                        True,
                        "may create tables and insert rows",
                    )
                )
            except Exception as exc:
                classified = classify_exception(exc)
                checks.append(
                    _Check(
                        "destination writable",
                        False,
                        f"Cannot create tables in the destination — the merge "
                        f"would fail at the very last step. {classified}",
                    )
                )

    # ── LLM reachability ────────────────────────────────────────────
    try:
        await _probe_llm(llm_config)
        checks.append(
            _Check(
                "LLM reachable",
                True,
                f"{llm_config.get('provider', '?')}/{llm_config.get('model', '?')}",
            )
        )
    except TimeoutError:
        checks.append(
            _Check(
                "LLM reachable",
                False,
                f"No response within {_LLM_TIMEOUT_S}s from "
                f"{llm_config.get('base_url') or 'the configured endpoint'}.",
            )
        )
    except Exception as exc:
        checks.append(
            _Check(
                "LLM reachable",
                False,
                f"{type(classify_exception(exc)).__name__}: {exc}",
            )
        )

    checks.append(_check_disk_space())

    # ── Verdict ─────────────────────────────────────────────────────
    blocking = [c for c in checks if not c.ok and c.blocking]
    warnings = [c for c in checks if not c.ok and not c.blocking]
    report = {
        "checks": [c.as_dict() for c in checks],
        "passed": len([c for c in checks if c.ok]),
        "failed": len(blocking),
        "warnings": len(warnings),
    }

    for check in checks:
        logger.log(
            logging.INFO if check.ok else logging.ERROR,
            "preflight %-28s %s%s",
            check.name,
            "OK" if check.ok else "FAILED",
            f" — {check.detail}" if check.detail else "",
        )

    if blocking:
        summary = "; ".join(f"{c.name}: {c.detail}" for c in blocking)
        logger.error("Preflight failed %d check(s): %s", len(blocking), summary)
        return {
            "preflight_report": report,
            "failure": {
                "node": "preflight",
                "error": (
                    f"{len(blocking)} preflight check(s) failed, so the run "
                    f"was stopped before reading any data. {summary}"
                ),
                "error_class": "UserInputError",
                "disposition": "fail",
                "context": {"failed_checks": [c.as_dict() for c in blocking]},
            },
            "errors": [f"Preflight: {c.name} — {c.detail}" for c in blocking],
        }

    logger.info("Preflight passed all %d checks", len(checks))
    logger.debug("EXIT preflight")
    return {"preflight_report": report, "failure": {}}
