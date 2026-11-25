"""Shared harness for the P21 live acceptance tier (tests/part1/test_p21_live.py).

Everything here is credential-free by construction: connection parameters come
ONLY from the gitignored .env / environment (`ER_TEST_PG_*`,
`ER_TEST_MONGO_URI`), and the tier is gated by `MERGE_ENGINE_LIVE_DB=1` so CI
(no Postgres, no Mongo services) skips it wholesale.

Schema discipline: every session works in its OWN Postgres schema
`p21_itest_<id>` — never `public` of the test database. The connector honours
an `er_schema` config key so its P21 side tables land in the same isolated
schema; the harness drops everything it created at teardown.

Leak discipline (P21 review F5): a Ctrl-C'd or crashed session never runs
teardown, and a reviewer used to find the leftovers (stopped mongo containers,
orphan `p21_itest_*` schemas). So every footprint carries this session's uuid
(`p21_itest_<sid>`, `p21_mongo_<sid>`) AND the harness sweeps orphans of
prior interrupted sessions at STARTUP, before creating its own — the drop
does not depend on teardown approval or a clean exit.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

LIVE = os.environ.get("MERGE_ENGINE_LIVE_DB") == "1"

# One id per session; every resource the tier creates is named with it, so a
# sweep can tell its own leftovers from a concurrent session's.
SESSION_ID = uuid.uuid4().hex[:10]

_ORPHAN_SWEEP: list[str] = []  # idempotency guard: sweep runs ONCE per process


def _sweep_orphans(conn: Any) -> dict[str, int]:
    """Drop THIS pattern's leftovers from prior interrupted sessions.

    Runs at startup (not only at teardown): a session killed by Ctrl-C or a
    crash never gets to clean up, and its `p21_itest_*` schemas /
    `p21_mongo_*` containers used to pile up for the next human to find.
    Schemas whose suffix equals the CURRENT session id are never touched —
    that is a concurrent live session's active work, not an orphan. (True
    age-based selection is deferred; this is the sanctioned lazy bound:
    concurrent full-tier runs are unsupported anyway.)
    """
    swept = {"schemas": 0, "containers": 0}
    with conn.cursor() as cur:
        # Filter in Python: LIKE with escaped underscores needs psycopg2's
        # `%%` dance for the percent — one regex-free startswith is simpler
        # and there are never more than a handful of schemas.
        cur.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'p21%%' ORDER BY nspname")
        orphans = [
            n
            for (n,) in cur.fetchall()
            if n.startswith("p21_itest_") and n != f"p21_itest_{SESSION_ID}"
        ]
        for name in orphans:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            swept["schemas"] += len(orphans)
    try:
        out = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=p21_mongo_"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.split()
        if out:
            ids = " ".join(out)
            subprocess.run(
                f"docker rm -f {ids}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            swept["containers"] = len(out)
    except Exception as exc:  # docker absent/failed: orphan sweep is best-effort
        pytest.skip(f"docker unavailable for the orphan sweep: {exc}")
    return swept


def _live_pg_params() -> dict[str, Any]:
    """pg_params() without the skip path — the sweep must run (or skip with a
    clear docker/env message) before fixture-level skips decide the tier."""
    return {
        "host": os.environ["ER_TEST_PG_HOST"],
        "port": int(os.environ.get("ER_TEST_PG_PORT", "5432")),
        "user": os.environ["ER_TEST_PG_USER"],
        "password": os.environ.get("ER_TEST_PG_PASSWORD", ""),
        "database": os.environ["ER_TEST_PG_DB"],
    }


def _sweep_once() -> None:
    if _ORPHAN_SWEEP:
        return
    _ORPHAN_SWEEP.append("done")
    import psycopg2

    missing = [
        k for k in ("ER_TEST_PG_HOST", "ER_TEST_PG_DB", "ER_TEST_PG_USER") if not os.environ.get(k)
    ]
    if missing:
        pytest.skip(f"live tier needs env: {', '.join(missing)}")
    conn = psycopg2.connect(**_live_pg_params())
    conn.autocommit = True
    try:
        _sweep_orphans(conn)
    finally:
        conn.close()


live_only = pytest.mark.skipif(
    not LIVE,
    reason="live tier: set MERGE_ENGINE_LIVE_DB=1 (needs ER_TEST_PG_* / ER_TEST_MONGO_URI)",
)

ER_TABLES = [
    "er_entity_version",
    "er_redirect",
    "er_idempotency",
    "er_enrichment_record",
    "er_model",
    "er_review",
    "er_type_registry",
    "er_field_history",
    "er_split",
    "er_extension_outbox",
]


def pg_params() -> dict[str, Any]:
    """Destination-connector config from env only — never literals."""
    missing = [
        k for k in ("ER_TEST_PG_HOST", "ER_TEST_PG_DB", "ER_TEST_PG_USER") if not os.environ.get(k)
    ]
    if missing:
        pytest.skip(f"live tier needs env: {', '.join(missing)}")
    return {
        "host": os.environ["ER_TEST_PG_HOST"],
        "port": int(os.environ.get("ER_TEST_PG_PORT", "5432")),
        "user": os.environ["ER_TEST_PG_USER"],
        "password": os.environ.get("ER_TEST_PG_PASSWORD", ""),
        "database": os.environ["ER_TEST_PG_DB"],
    }


def mongo_uri() -> str:
    """The Mongo endpoint for this session: a harness-started container when
    one is up, else the operator-supplied `ER_TEST_MONGO_URI`."""
    if _SESSION_MONGO["uri"]:
        return str(_SESSION_MONGO["uri"])
    uri = os.environ.get("ER_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("live tier needs ER_TEST_MONGO_URI")
    return uri


# The tier's own mongod (F5): one-per-session, named `p21_mongo_<SESSION_ID>`,
# started by `p21_mongo_stack` and removed at teardown — and by the startup
# sweep if a previous session was interrupted before its teardown ran.
_SESSION_MONGO: dict[str, Any] = {"uri": "", "name": ""}


def _docker(cmd: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout, check=False
    )


@pytest.fixture(scope="session")
def p21_mongo_stack() -> Iterator[None]:
    """Own the mongod lifecycle so nothing outlives the session.

    NOT autouse: this is registered through the part1 conftest, so autouse
    here would skip EVERY tests/part1 test when the live env is absent. The
    `mongo_db` fixture depends on it instead — lazily started when the first
    Mongo test asks, removed at session teardown.

    `ER_TEST_MONGO_URI` stays the operator's escape hatch (an externally run
    mongod, which the harness must NOT rm); with nothing reachable at it, the
    harness starts its own named container. Startup sweep already removed any
    `p21_mongo_*` orphan by the time this runs.
    """
    _sweep_once()
    missing = [
        k for k in ("ER_TEST_PG_HOST", "ER_TEST_PG_DB", "ER_TEST_PG_USER") if not os.environ.get(k)
    ]
    if missing:
        yield  # live tier will skip on its own env skips; nothing to clean
        return
    name = f"p21_mongo_{SESSION_ID}"
    env_uri = os.environ.get("ER_TEST_MONGO_URI", "")
    if env_uri:
        import pymongo

        probe = pymongo.MongoClient(env_uri, serverSelectionTimeoutMS=1500)
        try:
            probe.admin.command("ping")
            alive = True
        except Exception:
            alive = False  # fall through and start our own named container
        finally:
            probe.close()
        if alive:
            yield  # externally provided store: never rm what we did not start
            return

    # Fixed port first (the documented shape); if it is taken, docker maps a
    # random one and we read it back.
    port_arg, port = "27017:27017", 27017
    run = _docker(f"docker run -d --name {name} -p {port_arg} mongo:7")
    if run.returncode != 0:
        run = _docker(f"docker run -d --name {name} -p 27017 mongo:7")
        if run.returncode != 0:
            pytest.skip(f"cannot start throwaway mongod: {run.stderr.strip()[:200]}")
        mapped = _docker(f"docker port {name} 27017/tcp").stdout.strip()
        port = int(mapped.rsplit(":", 1)[-1]) if mapped else port
    _SESSION_MONGO["name"] = name
    _SESSION_MONGO["uri"] = f"mongodb://127.0.0.1:{port}/"
    os.environ["ER_TEST_MONGO_URI"] = str(_SESSION_MONGO["uri"])
    try:
        import pymongo

        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                c = pymongo.MongoClient(str(_SESSION_MONGO["uri"]), serverSelectionTimeoutMS=1000)
                c.admin.command("ping")
                c.close()
                break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.skip("throwaway mongod never became reachable")
        yield
    finally:
        if _SESSION_MONGO["name"]:
            _docker(f"docker rm -f {_SESSION_MONGO['name']}")
            _SESSION_MONGO["name"] = ""
            _SESSION_MONGO["uri"] = ""


@pytest.fixture(scope="session")
def itest_schema() -> Iterator[str]:
    """A private schema for the whole session; dropped at teardown.

    Sweep-orphan-at-startup (F5): prior sessions' `p21_itest_*` leftovers are
    dropped BEFORE this session creates its own, so an interrupted teardown
    can never leave a permanent footprint. Teardown still drops eagerly on a
    clean run — the startup sweep is the backstop, not the plan.
    """
    import psycopg2

    _sweep_once()
    p = _live_pg_params()
    name = f"p21_itest_{SESSION_ID}"
    conn = psycopg2.connect(**p)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


@pytest.fixture()
def dest(itest_schema: str) -> Iterator[Any]:
    """A PostgreSQLDestinationConnector bound to the session schema.

    The connector puts its operational tables in the configured `schema`
    key, and P21's ER side tables in `ER_SCHEMA` — for isolation, the
    harness re-points ER_SCHEMA onto the session schema for the live tier
    (the connector instance is test-owned, so patching the class attribute
    on the INSTANCE is safe and keeps the public schema of the test DB
    untouched).
    """
    from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

    cfg = dict(pg_params())
    cfg["schema"] = itest_schema
    cfg["er_schema"] = itest_schema  # ER side tables isolated too
    connector = PostgreSQLDestinationConnector(cfg)
    connector.ensure_er_side_tables()
    try:
        yield connector
    finally:
        connector.close()


@pytest.fixture()
def mongo_db(p21_mongo_stack: None) -> Any:
    """The throwaway local mongod (acceptance 15/16); None skips Mongo tests.

    Depends on `p21_mongo_stack` so the container this session owns is up
    (and registered for teardown) before any Mongo test asks for a URI.

    serverSelectionTimeoutMS keeps an outage a fast, honest skip instead of
    a 30s hang."""
    import pymongo

    client = pymongo.MongoClient(mongo_uri(), serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except Exception:
        pytest.skip("ER_TEST_MONGO_URI unreachable — start the itest mongod")
    db = client["enrichment_entities_itest"]
    yield db
    client.close()


@pytest.fixture()
def seeded(dest: Any) -> Any:
    """The connector plus a `companies` destination table for the service to
    serve — the acceptance tests' one entity type. Created inside the session
    schema, so it drops with it."""
    with dest._ensure_connection().cursor() as cur:
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{dest._schema}".companies ('
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT, website TEXT, "
            "notes TEXT)"
        )
    dest._ensure_connection().commit()
    dest.ensure_entity_map()  # the batch's map table — hooks tests need it real
    return dest


@pytest.fixture()
def svc(seeded: Any) -> Any:
    """ErService over the seeded connector, no Mongo (extension tests inject
    their own)."""
    from src.orchestrator.er_service import ErService

    return ErService(seeded)


@pytest.fixture()
def sql(dest: Any) -> Iterator[Any]:
    """Raw cursor on the same isolated connection (independent verification)."""
    conn = dest._ensure_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()


def count(cur: Any, table: str, schema: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    """SQL truth for acceptance counts (schema = the session's isolated one)."""
    s = schema
    cur.execute(
        f'SELECT count(*) FROM "{s}"."{table}"' + (f" WHERE {where}" if where else ""), params
    )
    return int(cur.fetchone()[0])
