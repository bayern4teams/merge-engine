"""Shared harness for the P21 live acceptance tier (tests/part1/test_p21_live.py).

Everything here is credential-free by construction: connection parameters come
ONLY from the gitignored .env / environment (`ER_TEST_PG_*`,
`ER_TEST_MONGO_URI`), and the tier is gated by `MERGE_ENGINE_LIVE_DB=1` so CI
(no Postgres, no Mongo services) skips it wholesale.

Schema discipline: every session works in its OWN Postgres schema
`p21_itest_<id>` — never `public` of the test database. The connector honours
an `er_schema` config key so its P21 side tables land in the same isolated
schema; the harness drops everything it created at teardown.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

LIVE = os.environ.get("MERGE_ENGINE_LIVE_DB") == "1"

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
    uri = os.environ.get("ER_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("live tier needs ER_TEST_MONGO_URI")
    return uri


@pytest.fixture(scope="session")
def itest_schema() -> Iterator[str]:
    """A private schema for the whole session; dropped at teardown."""
    import psycopg2

    p = pg_params()
    name = f"p21_itest_{uuid.uuid4().hex[:10]}"
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
def mongo_db() -> Any:
    """The throwaway local mongod (acceptance 15/16); None skips Mongo tests.

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
