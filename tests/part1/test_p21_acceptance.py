"""P21 mocked acceptance tier — `.handoff/P21.acceptance.md` E2/E3/E4.

Runs with NO Postgres, NO Mongo and no env beyond monkeypatched keys, like
the rest of the mocked tiers: the ER service runs over an in-memory fake of
the destination connector (and a fake Mongo for extension-store cases), and
the /v1 routes run over a real FastAPI TestClient. This is the tier the
pre-registered acceptance doc promised and review F4 demanded; the live tier
(test_p21_live.py) proves the same gates against real stores.

CI reach: scripts/check.py `part1` runs the tests/part1/test_p21_*.py subset
(live tier self-skips without MERGE_ENGINE_LIVE_DB), and CI runs check.py all.

Covered here:
  E2  fail-closed auth: `ER_API_KEY` unset → every /v1 route 503 (disabled);
      missing/wrong key → 401 before any store touch.
  E3  GET /v1/entity-types shape: unified entry (store/table/fields/
      identity/required/reference fields + threshold bars, provisional
      honest), extension entry (store='enrichment', Mongo collection,
      provisional bars).
  E4  relaxed PATCH: a client value that DIFFERS from what's stored is
      accepted-or-rejected by survivorship, never blindly applied (the held
      value stays + LOWER_PRIORITY_REJECTED history); stale If-Match → 412
      VERSION_CONFLICT; add_aliases on a type without an aliases field →
      422; on a type WITH one → accepted (extension store, fake Mongo).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

import src.api.er_routes as er_routes
from src.api.main import create_api
from src.orchestrator.er_service import PROVISIONAL_THRESHOLDS, ErService

COMPANIES: dict[str, Any] = {
    "table": "companies",
    "fields": {"name": "string", "country": "string", "notes": "string"},
    "identity_fields": ["name"],
    "required_fields": ["name"],
    "reference_fields": {},
    "table_columns": [
        {"name": "id"},
        {"name": "name"},
        {"name": "country"},
        {"name": "notes"},
    ],
}

CERTS: dict[str, Any] = {
    "fields": {"name": "string", "issuer": "string", "aliases": "string[]"},
    "identity_fields": ["name"],
    "required_fields": ["name"],
    "reference_fields": {},
}


# ── the fake destination ─────────────────────────────────────────────────
class _FakeCursor:
    """Pattern-matched reads over the fake's dicts. The ER service's own
    INSERTs land here and are asserted against the service's answers — the
    fake is the SQL layer, so nothing in these tests needs a real database.
    """

    def __init__(self, owner: FakeErConn) -> None:
        self._o = owner
        self._result: list[Any] = []

    @property
    def description(self) -> Any:
        # Only `SELECT * FROM companies` is ever described (patch_entity's
        # unified path); every other read goes through execute_sql tuples.
        if "SELECT * FROM" in self._o._last_sql:
            return [("id",), ("name",), ("country",), ("notes",)]
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if isinstance(sql, bytes):  # execute_batch joins mogrified chunks
            sql = sql.decode()
        self._result = self._o._query(sql, params)

    def mogrify(self, sql: str, params: Any = None) -> bytes:
        # psycopg2.extras.execute_batch mogrifies one row then executes the
        # join. Record history rows HERE (params are real), so the joined
        # execute can be a no-op.
        if "er_field_history" in sql and params:
            self._o.history.append(tuple(params))
            return b""
        if params:
            return b""
        return sql.encode()

    def fetchone(self) -> Any:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[Any]:
        return self._result

    def close(self) -> None:
        return None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeErConn:
    """The slice of PostgreSQLDestinationConnector the read/PATCH paths use."""

    def __init__(self) -> None:
        self._schema = "itest"
        self._er_schema = "itest"
        self.rows: dict[str, Any] = {"companies": {}}
        self.versions: dict[str, int] = {}
        self.history: list[Any] = []
        self.enrichment_records: list[Any] = []
        self.map_rows: list[Any] = []
        self.registry: dict[str, Any] = {"certifications": CERTS}
        self.executed: list[str] = []
        self._last_sql = ""

    # connector surface the service touches
    def _ensure_connection(self) -> FakeErConn:
        return self

    def commit(self) -> None:
        return None

    @contextmanager
    def atomic(self, dry_run: bool = False) -> Iterator[FakeErConn]:
        yield self

    def close(self) -> None:
        return None

    def execute_sql(self, sql: str, params: Any = ()) -> list[Any]:
        return self._query(sql, params)

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    # read-side routing (order matters: specific first, INSERT append-only
    # handlers next, catch-all SELECT last)
    def _query(self, sql: str, params: Any) -> list[Any]:
        params = tuple(params or ())
        self._last_sql = sql
        self.executed.append(sql.split("\n", 1)[0].strip()[:120])
        if "er_type_registry" in sql and sql.lstrip().startswith("SELECT"):
            if params:  # registry_row(entity_type)
                if params[0] in self.registry:
                    return [("enrichment", dict(PROVISIONAL_THRESHOLDS), params[0])]
                return []
            return [
                (
                    et,
                    defn["fields"],
                    defn["identity_fields"],
                    defn["required_fields"],
                    defn["reference_fields"],
                    dict(PROVISIONAL_THRESHOLDS),
                    et,
                )
                for et, defn in sorted(self.registry.items())
            ]
        if "pg_try_advisory_lock" in sql or "pg_advisory_lock" in sql:
            return [(True,)]
        if "pg_advisory_unlock" in sql:
            return [(True,)]
        if "er_entity_version" in sql and "SELECT" in sql:
            v = self.versions.get(str(params[-1]) if params else "")
            return [(v,)] if v else []
        if "er_redirect" in sql:
            return []
        if "information_schema.columns" in sql:
            return [("id",), ("name",), ("country",), ("notes",)]
        if "SELECT * FROM" in sql and "companies" in sql:
            row = self.rows["companies"].get(str(params[0]))
            if row is None:
                return []
            return [(row["id"], row.get("name"), row.get("country"), row.get("notes"))]
        if "UPDATE" in sql and "companies" in sql and "WHERE id" in sql:
            row = self.rows["companies"].get(str(params[-1]))
            if row is not None:
                sets = re.findall(r'SET "([a-z_]+)" = ', sql) or re.findall(
                    r'"([a-z_]+)" = %s', sql
                )
                for i, col in enumerate(sets):
                    row[col] = params[i]
            return []
        if not sql.strip().strip(";").strip():  # joined empty batch from mogrify
            return []
        if "INSERT" in sql:
            self._append(sql, params)
            return []
        if sql.lstrip().startswith("SELECT"):
            return []  # unknown read: honest empty
        raise AssertionError(f"unexpected SQL on the fake: {sql[:200]}")

    def _append(self, sql: str, params: Any) -> None:
        params = tuple(params or ())
        if "er_field_history" in sql:
            self.history.append(params)
        elif "er_enrichment_record" in sql:
            self.enrichment_records.append(params)
        elif "merge_engine_entity_map" in sql:
            self.map_rows.append(params)
        elif "er_entity_version" in sql and params:
            self.versions[str(params[1])] = int(params[2])
        elif "er_idempotency" in sql or "er_extension_outbox" in sql:
            pass
        else:
            raise AssertionError(f"unexpected INSERT on the fake: {sql[:200]}")


class FakeMongoColl:
    def __init__(self) -> None:
        self.docs: dict[str, Any] = {}

    def find_one(self, q: dict[str, Any]) -> Any:
        return self.docs.get(str(q.get("_id")))

    def update_one(self, q: dict[str, Any], update: dict[str, Any]) -> None:
        doc = self.docs.setdefault(str(q.get("_id")), {"_id": str(q.get("_id"))})
        doc.update(update.get("$set") or {})

    def replace_one(self, q: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        self.docs[str(q.get("_id"))] = doc


class FakeMongo:
    def __init__(self) -> None:
        self._colls: dict[str, FakeMongoColl] = {}

    def __getitem__(self, name: str) -> FakeMongoColl:
        return self._colls.setdefault(name, FakeMongoColl())


def make_service(mongo: Any = None) -> ErService:
    conn = FakeErConn()
    conn.rows["companies"]["c-1"] = {
        "id": "c-1",
        "name": "Acme Industries LLC",
        "country": "DE",
        "notes": None,
    }
    conn.versions["c-1"] = 1
    svc = ErService(
        conn,
        entity_types_provider=lambda: {"companies": dict(COMPANIES)},
        mongo_db=mongo,
    )
    return svc


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """/v1 over a real app + the fake-backed service; keys per test."""
    er_routes.reset_service()
    monkeypatch.setattr(er_routes, "_service", make_service(mongo=FakeMongo()))
    yield TestClient(create_api())
    er_routes.reset_service()


# ── E2: fail-closed auth ─────────────────────────────────────────────────
def test_e2_unset_er_api_key_disables_v1(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unset: EVERY /v1 route answers 503 — a keyless deployment ships no
    # write surface, even for the health read, even when a key is presented.
    monkeypatch.delenv("ER_API_KEY", raising=False)
    for method, url in (
        ("get", "/v1/health"),
        ("get", "/v1/entity-types"),
        ("post", "/v1/entities:resolve"),
        ("patch", "/v1/entities/companies/c-1"),
    ):
        r = client.request(method, url, headers={"X-API-Key": "whatever"}, json={})
        assert r.status_code == 503, (method, url, r.status_code)
        assert "ER_API_KEY" in r.text


def test_e2_missing_or_wrong_key_is_401_before_any_store_touch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ER_API_KEY", "right-key")
    r = client.get("/v1/health")
    assert r.status_code == 401  # header missing entirely
    r = client.get("/v1/health", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401
    svc: Any = er_routes._service
    assert svc.conn.executed == []  # 401 happens BEFORE any store touch
    r = client.get("/v1/entity-types", headers={"X-API-Key": "right-key"})
    assert r.status_code == 200  # and a right key actually works


# ── E3: /v1/entity-types shape ───────────────────────────────────────────
def test_e3_entity_types_shape_with_provisional_bars(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ER_API_KEY", "k")
    body = client.get("/v1/entity-types", headers={"X-API-Key": "k"}).json()
    entries = {e["entity_type"]: e for e in body["entity_types"]}
    assert set(entries) == {"companies", "certifications"}

    uni = entries["companies"]
    assert uni["store"] == "unified" and uni["table"] == "companies"
    assert uni["fields"] == {"name": "string", "country": "string", "notes": "string"}
    assert uni["identity_fields"] == ["name"] and uni["required_fields"] == ["name"]
    assert uni["reference_fields"] == {}
    # No fitted model was ever loaded here → the honest provisional bars.
    assert uni["provisional"] is True
    assert uni["thresholds"] == dict(PROVISIONAL_THRESHOLDS)

    ext = entries["certifications"]
    assert ext["store"] == "enrichment"
    assert ext["collection"] == "enrichment_entities.certifications"
    assert ext["fields"] == CERTS["fields"]
    assert ext["provisional"] is True
    assert ext["thresholds"] == dict(PROVISIONAL_THRESHOLDS)


# ── E4: relaxed PATCH / 412 / add_aliases ────────────────────────────────
def test_e4_relaxed_patch_survivorship_decides(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ER_API_KEY", "k")
    key = {"X-API-Key": "k"}
    # Client sends a DIFFERING value for a non-empty field: relaxed contract
    # — the request is ACCEPTED (2xx, never a validation failure) and
    # survivorship decides: the held source value stays, the attempt is
    # recorded as LOWER_PRIORITY_REJECTED.
    r = client.patch("/v1/entities/companies/c-1", headers=key, json={"set": {"country": "FR"}})
    assert r.status_code == 200, r.text
    assert r.json()["attributes"]["country"] == "DE"
    svc: Any = er_routes._service
    rejected = [h for h in svc.conn.history if "LOWER_PRIORITY_REJECTED" in [str(x) for x in h]]
    assert rejected and rejected[0][3] == "country"  # field slot of the history row
    # An EMPTY field is filled — that is the enrichment value arriving.
    r = client.patch("/v1/entities/companies/c-1", headers=key, json={"set": {"notes": "from er"}})
    assert r.status_code == 200 and r.json()["attributes"]["notes"] == "from er"
    assert svc.conn.rows["companies"]["c-1"]["notes"] == "from er"


def test_e4_stale_if_match_is_412(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ER_API_KEY", "k")
    r = client.patch(
        "/v1/entities/companies/c-1",
        headers={"X-API-Key": "k", "If-Match": '"99"'},
        json={"set": {"country": "FR"}},
    )
    assert r.status_code == 412 and r.json()["error"] == "VERSION_CONFLICT"
    # a non-numeric If-Match is a 400 at the route (shape error, not conflict)
    r = client.patch(
        "/v1/entities/companies/c-1",
        headers={"X-API-Key": "k", "If-Match": 'W/"x"'},
        json={"set": {"country": "FR"}},
    )
    assert r.status_code == 400


def test_e4_add_aliases_gated_by_the_field(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ER_API_KEY", "k")
    key = {"X-API-Key": "k"}
    # unified `companies` declares no aliases field → 422 (contract §5.6)
    r = client.patch("/v1/entities/companies/c-1", headers=key, json={"add_aliases": ["AcmeCo"]})
    assert r.status_code == 422 and r.json()["error"] == "VALIDATION_FAILED"
    assert "add_aliases" in json.dumps(r.json())
    # extension `certifications` DOES declare aliases → accepted (fake Mongo)
    svc: Any = er_routes._service
    svc.conn.rows  # noqa: B018 - documents that only the extension path runs below
    svc._mongo_coll("certifications").docs["x-1"] = {
        "_id": "x-1",
        "name": "ISO-9001",
        "issuer": "TUV",
        "_version": 1,
    }
    r = client.patch("/v1/entities/certifications/x-1", headers=key, json={"add_aliases": ["ISO9"]})
    assert r.status_code == 200, r.text
    doc = svc._mongo_coll("certifications").docs["x-1"]
    assert doc.get("aliases") == ["ISO9"]


def test_e4_patch_over_the_wire_carries_the_etag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ER_API_KEY", "k")
    key = {"X-API-Key": "k"}
    r = client.patch("/v1/entities/companies/c-1", headers=key, json={"set": {"country": "FR"}})
    assert r.status_code == 200
    assert re.fullmatch(r'"\d+"', r.headers.get("etag", "")), "PATCH must carry the ETag"
    assert int(r.headers["etag"].strip('"')) == 2  # version advanced exactly once
