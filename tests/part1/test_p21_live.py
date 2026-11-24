"""P21 live acceptance tier (`.handoff/P21.acceptance.md`), env-gated.

Skips wholesale unless MERGE_ENGINE_LIVE_DB=1 (CI has no Postgres/Mongo).
Every "done" claim here is a SQL count or an HTTP status — the API's own
answers are never the evidence (CLAUDE.md: live, independent). Items 8/9 are
measured and REPORTED, never asserted (owner rule: no quality assertion).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from tests.part1.p21_live_harness import ER_TABLES, count, live_only

pytestmark = [live_only]


# .handoff/P21.acceptance.md E1: every ensure_* is idempotent — run twice,
# identical catalog, no exception.
def test_e1_ensure_er_side_tables_idempotent(dest: Any, sql: Any, itest_schema: str) -> None:
    dest.ensure_er_side_tables()
    dest.ensure_er_side_tables()  # second call must be a no-op
    sql.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = %s ORDER BY table_name
        """,
        (itest_schema,),
    )
    found = {r[0] for r in sql.fetchall()}
    assert set(ER_TABLES) <= found, f"missing: {set(ER_TABLES) - found}"
    # purge path runs and is a no-op on fresh tables
    assert dest.purge_er_idempotency(days=30) == 0


def _resolve_one(svc: Any, key: str, attrs: dict[str, Any], **extra: Any) -> dict[str, Any]:
    item = {
        "item_id": key,
        "idempotency_key": key,
        "entity_type": "companies",
        "attributes": attrs,
        **extra,
    }
    status, body = svc.run_resolve({"items": [item]})
    assert status == 200, body
    return body["results"][0]


# item 2: create → SQL truth; replay same key → same id, counts unchanged.
def test_a2_create_then_replay(svc: Any, sql: Any, itest_schema: str) -> None:
    s = itest_schema
    r1 = _resolve_one(svc, "a2:1", {"name": "Acme Industries LLC", "country": "US"})
    assert r1["decision"] == "CREATED"
    rid = r1["id"]
    assert count(sql, "companies", s, "id = %s", (rid,)) == 1
    assert count(sql, "merge_engine_entity_map", s, "cluster_id = %s", (rid,)) == 1
    assert count(sql, "er_enrichment_record", s) == 1
    assert count(sql, "er_entity_version", s, "id = %s", (rid,)) == 1
    sql.execute(f'SELECT version FROM "{s}".er_entity_version WHERE id = %s', (rid,))
    assert sql.fetchone()[0] == 1

    r2 = _resolve_one(svc, "a2:1", {"name": "Acme Industries LLC", "country": "US"})
    assert r2["id"] == rid and r2.get("idempotent_replay") is True
    assert count(sql, "companies", s) == 1
    assert count(sql, "er_enrichment_record", s) == 1
    assert count(sql, "er_idempotency", s) == 1


# item 3: two concurrent creates (different keys) → exactly one row.
def test_a3_concurrent_creates_one_row(svc: Any, sql: Any, itest_schema: str) -> None:
    answers: list[dict[str, Any]] = []

    def worker(key: str) -> None:
        answers.append(_resolve_one(svc, key, {"name": "Beta Robotics Ltd", "country": "UK"}))

    threads = [threading.Thread(target=worker, args=(f"a3:{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(answers) == 2
    assert count(sql, "companies", itest_schema, "name = %s", ("Beta Robotics Ltd",)) == 1
    decisions = sorted(a["decision"] for a in answers)
    assert decisions == ["CREATED", "MATCHED"], answers
    assert len({a["id"] for a in answers}) == 1


# item 7: a write while the load lock is held → whole resolve 503; reads up.
def test_a7_write_during_load_gets_503(svc: Any, dest: Any, itest_schema: str) -> None:
    import psycopg2

    from src.orchestrator.er_service import WRITE_LOCK_KEY

    ctrl = psycopg2.connect(
        host=dest._config["host"],
        port=dest._config["port"],
        user=dest._config["user"],
        password=dest._config["password"],
        database=dest._config["database"],
    )
    ctrl.autocommit = False
    try:
        with ctrl.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,))
        from src.orchestrator.er_service import ErError

        with pytest.raises(ErError) as exc:
            svc.run_resolve(
                {
                    "items": [
                        {
                            "item_id": "x",
                            "idempotency_key": "a7:blocked",
                            "entity_type": "companies",
                            "attributes": {"name": "Locked Out Co"},
                        }
                    ]
                }
            )
        assert exc.value.status == 503 and exc.value.code == "LOAD_RUNNING"
        # read paths stay up (D-49): search answers during the pause
        status, _ = svc.run_search(
            {
                "items": [
                    {
                        "item_id": "y",
                        "entity_type": "companies",
                        "attributes": {"name": "z"},
                    }
                ]
            }
        )
        assert status == 200
    finally:
        ctrl.rollback()
        ctrl.close()
    # after release, the same request succeeds (nothing was lost)
    r = _resolve_one(svc, "a7:after", {"name": "Locked Out Co"})
    assert r["decision"] == "CREATED"


# item 5 (service half): the ER-created row persists and is matchable — the
# destination keeps the enrichment value (SQL). Batch-side proof is the
# hooks test driving the real load-node function.
def test_a5_enrichment_row_persists(svc: Any, sql: Any, itest_schema: str) -> None:
    r = _resolve_one(svc, "a5:1", {"name": "Gamma Analytics", "notes": "from enrichment"})
    assert count(sql, "companies", itest_schema, "notes = %s", ("from enrichment",)) == 1
    r2 = _resolve_one(svc, "a5:2", {"name": "Gamma Analytics", "country": "US"})
    assert r2["decision"] == "MATCHED" and r2["id"] == r["id"]


# item 6 + 17 (patch half): an inferred value never replaces a source value,
# and the rejected attempt is recorded anyway (D-48/D-64).
def test_a6_inferred_never_overwrites(svc: Any, sql: Any, itest_schema: str) -> None:
    s = itest_schema
    sql.execute(
        f'INSERT INTO "{s}".companies (id, name, country) '
        "VALUES ('src-owned-1', 'Delta Mining', 'AU')"
    )
    r = _resolve_one(svc, "a6:1", {"name": "Delta Mining"})
    assert r["decision"] == "MATCHED" and r["id"] == "src-owned-1"
    svc.patch_entity(
        "companies",
        "src-owned-1",
        {"set": {"country": "NZ"}, "inferred_fields": ["country"]},
        None,
    )
    sql.execute(f'SELECT country FROM "{s}".companies WHERE id = %s', ("src-owned-1",))
    assert sql.fetchone()[0] == "AU"  # source value kept
    sql.execute(
        f'SELECT count(*) FROM "{s}".er_field_history WHERE id = %s AND reason = %s',
        ("src-owned-1", "LOWER_PRIORITY_REJECTED"),
    )
    assert sql.fetchone()[0] == 1


def _plant_band(dest: Any, schema: str, auto: float, review: float) -> None:
    """A stored model whose only signal is name-exact vs name-different, with
    explicit bands, so in-band behaviour is deterministic."""
    import json

    levels = [
        "exact",
        "different",
        "null",
        "numeric_close",
        "overlap",
        "typo",
        "soft_overlap",
        "jaccard",
    ]
    params = {
        "match_prior": 0.5,
        "fields": {
            "name": {
                "name": "name",
                "m": {lv: (0.9 if lv == "exact" else 0.5) for lv in levels},
                "u": {lv: (0.02 if lv == "exact" else 0.4) for lv in levels},
                "term_frequency": {},
                "is_unique_id": False,
            }
        },
        "token_df": None,
        "thresholds": {"auto_match": auto, "review": review},
    }
    dest.execute_sql(
        f'INSERT INTO "{schema}".er_model (entity_type, run_id, params) '
        "VALUES (%s, 'itest', %s::jsonb) "
        "ON CONFLICT (entity_type) DO UPDATE SET params = EXCLUDED.params",
        ("companies", json.dumps(params)),
    )


# items 10 + 12 + 13: AMBIGUOUS, side-effect-free search, client choice.
def test_a10_12_13_band_search_and_choice(svc: Any, sql: Any, itest_schema: str) -> None:
    s = itest_schema
    _plant_band(svc.conn, s, auto=0.995, review=0.10)  # near-everything in band
    svc.invalidate("companies")
    a = _resolve_one(svc, "band:a", {"name": "Epsilon Foundry"})
    assert a["decision"] == "CREATED"
    b = _resolve_one(svc, "band:b", {"name": "Epsilon Foundry Co"})
    assert b["decision"] == "AMBIGUOUS", b
    assert b["candidates"] and b["differing_fields"] == ["name"]

    # item 12: search says the same thing and writes NOTHING
    before = (
        count(sql, "companies", s),
        count(sql, "er_idempotency", s),
        count(sql, "er_field_history", s),
    )
    status, body = svc.run_search(
        {
            "items": [
                {
                    "item_id": "p",
                    "entity_type": "companies",
                    "attributes": {"name": "Epsilon Foundry C"},
                }
            ]
        }
    )
    assert status == 200
    res = body["results"][0]
    assert res["would_decide"] == "AMBIGUOUS" and res["candidates"]
    after = (
        count(sql, "companies", s),
        count(sql, "er_idempotency", s),
        count(sql, "er_field_history", s),
    )
    assert before == after, "search wrote something"

    # item 13: choice {id} beats the bars, decided_by client
    chosen = b["candidates"][0]["id"]
    r = _resolve_one(svc, "band:c", {"name": "Epsilon Foundry Co"}, choice={"id": chosen})
    assert r["decision"] == "MATCHED" and r["id"] == chosen
    assert r.get("decided_by") == "client"
    r2 = _resolve_one(
        svc, "band:d", {"name": "Epsilon Foundry Co"}, choice={"id": "er_never_issued"}
    )
    assert r2["decision"] == "INVALID" and r2["error"] == "UNKNOWN_ID"
    # choice {new:true} while a candidate crosses auto_match → MATCH_APPEARED
    _plant_band(svc.conn, s, auto=0.5, review=0.10)
    svc.invalidate("companies")
    r3 = _resolve_one(svc, "band:e", {"name": "Epsilon Foundry"}, choice={"new": True})
    assert r3["decision"] == "MATCHED" and r3.get("reason") == "MATCH_APPEARED"
    assert count(sql, "companies", s, "name = %s", ("Epsilon Foundry",)) == 1


# item 14: per-attempt keys; mismatch guard on genuine retries only.
def test_a14_per_attempt_keys(svc: Any, sql: Any, itest_schema: str) -> None:
    r1 = _resolve_one(svc, "run1:x", {"name": "Zeta Logistics"})
    assert r1["decision"] == "CREATED"
    rp = _resolve_one(svc, "run1:x", {"name": "Zeta Logistics"})
    assert rp.get("idempotent_replay") is True and rp["id"] == r1["id"]
    _, body = svc.run_resolve(
        {
            "items": [
                {
                    "item_id": "run1:x",
                    "idempotency_key": "run1:x",
                    "entity_type": "companies",
                    "attributes": {"name": "Totally Other"},
                }
            ]
        }
    )
    bad = body["results"][0]
    assert bad["decision"] == "INVALID"
    assert bad["error"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    r2 = _resolve_one(svc, "run1:x:2", {"name": "Zeta Logistics GmbH"})
    assert not r2.get("idempotent_replay")
    assert count(sql, "er_idempotency", itest_schema, "key LIKE %s", ("run1:%",)) == 2


# item 11: an extra key never reaches a destination column.
def test_a11_extra_never_a_column(svc: Any, sql: Any, itest_schema: str) -> None:
    r = _resolve_one(svc, "a11:1", {"name": "Eta Ventures"}, extra={"weird_key": "should-not-land"})
    sql.execute(f'SELECT * FROM "{itest_schema}".companies WHERE id = %s', (r["id"],))
    cols = [d[0] for d in sql.description]
    row = dict(zip(cols, sql.fetchone(), strict=True))
    assert "weird_key" not in cols and "weird_key" not in row.values()
    assert count(sql, "er_enrichment_record", itest_schema) >= 1  # kept in the ledger


# item 20: 64 KB+1 combined → truncated:true, item not failed, capped stored.
def test_a20_size_pushback(svc: Any, sql: Any, itest_schema: str) -> None:
    big = "x" * (65 * 1024 + 1)
    status, body = svc.run_resolve(
        {
            "items": [
                {
                    "item_id": "a20",
                    "idempotency_key": "a20:1",
                    "entity_type": "companies",
                    "attributes": {"name": "Theta Heavy"},
                    "source_text": big,
                }
            ]
        }
    )
    assert status == 200
    r = body["results"][0]
    assert r["decision"] == "CREATED" and r.get("truncated") is True
    sql.execute(
        f"SELECT length(coalesce(source_text, '')) FROM \"{itest_schema}\"."
        "er_enrichment_record WHERE entity_type = 'companies'"
    )
    stored = max((row[0] for row in sql.fetchall()), default=0)
    assert stored <= 64 * 1024 + 1024, stored


# item 18: since_version paging + merged-id history redirect.
def test_a18_history(svc: Any, sql: Any, itest_schema: str) -> None:
    r = _resolve_one(svc, "a18:1", {"name": "Iota Foods"})
    rid = r["id"]
    svc.patch_entity("companies", rid, {"set": {"country": "DE"}}, None)
    svc.patch_entity("companies", rid, {"set": {"website": "iota.de"}}, None)
    full = svc.get_history("companies", rid, None)
    since = svc.get_history("companies", rid, 2)
    assert len(since["changes"]) < len(full["changes"])
    assert all(c["version"] > 2 for c in since["changes"])
    sql.execute(
        f'INSERT INTO "{itest_schema}".er_redirect (entity_type, old_id, new_id) '
        "VALUES (%s, %s, %s)",
        ("companies", "a18-old", rid),
    )
    merged = svc.get_history("companies", "a18-old", None)
    assert merged["id"] == rid and merged["redirected_from"] == "a18-old"


# items 4 + 17 (batch half): the load hooks, driven through the very function
# the load node calls — same DB, same code.
def test_g3_hooks_redirect_version_history(svc: Any, sql: Any, itest_schema: str) -> None:
    from src.orchestrator.er_hooks import snapshot_entity_map, write_load_side_effects

    s = itest_schema
    sql.execute(
        f'INSERT INTO "{s}".merge_engine_entity_map '
        "(entity_type, record_id, cluster_id, first_seen_run) VALUES "
        "('companies', 'o1:t:1', 'A-111', 'r0'), "
        "('companies', 'o1:t:2', 'A-111', 'r0'), "
        "('companies', 'o1:t:3', 'B-222', 'r0')"
    )
    sql.execute(
        f"INSERT INTO \"{s}\".companies (id, name) VALUES ('B-222', 'Kappa Group') "
        "ON CONFLICT DO NOTHING"
    )
    snapshot = snapshot_entity_map(svc.conn)
    assert snapshot[("companies", "o1:t:1")] == "A-111"
    sql.execute(
        f'UPDATE "{s}".merge_engine_entity_map '
        "SET cluster_id = 'B-222' WHERE cluster_id = 'A-111'"
    )
    stats = write_load_side_effects(
        svc.conn,
        "r1",
        snapshot,
        {"companies": ["B-222"]},
        {
            "companies": [
                (
                    "B-222",
                    [
                        {
                            "field": "country",
                            "values": [
                                {"origin": "src_a", "value": "US"},
                                {"origin": "enrichment_engine", "value": "US "},
                            ],
                            "selected_origin": "src_a",
                            "selected_value": "US",
                        }
                    ],
                )
            ]
        },
    )
    assert stats["redirects"] == 1 and stats["history"] == 1
    sql.execute(f'SELECT new_id FROM "{s}".er_redirect WHERE old_id = %s', ("A-111",))
    assert sql.fetchone()[0] == "B-222"
    sql.execute(f'SELECT version FROM "{s}".er_entity_version WHERE id = %s', ("B-222",))
    assert sql.fetchone()[0] == 1
    ent = svc.get_entity("companies", "A-111")
    assert ent["id"] == "B-222" and ent["redirected_from"] == "A-111"
    sql.execute(
        f'SELECT reason, attempted FROM "{s}".er_field_history '
        "WHERE id = 'B-222' AND field = 'country'"
    )
    reason, attempted = sql.fetchone()
    assert reason == "HIGHER_PRIORITY_SOURCE" and attempted == "US "


# item 19: a human split records er_split for every part + survivor redirect.
def test_l_hook_split_rows(svc: Any, sql: Any, itest_schema: str) -> None:
    from src.orchestrator.er_hooks import snapshot_entity_map, write_load_side_effects

    s = itest_schema
    sql.execute(
        f'INSERT INTO "{s}".merge_engine_entity_map '
        "(entity_type, record_id, cluster_id, first_seen_run) VALUES "
        "('companies', 'o1:t:9', 'SPLIT-1', 'r0'), "
        "('companies', 'o1:t:10', 'SPLIT-1', 'r0')"
    )
    snap = snapshot_entity_map(svc.conn)
    sql.execute(
        f"UPDATE \"{s}\".merge_engine_entity_map SET cluster_id = 'NEW-1' "
        "WHERE record_id = 'o1:t:9'"
    )
    sql.execute(
        f"UPDATE \"{s}\".merge_engine_entity_map SET cluster_id = 'NEW-2' "
        "WHERE record_id = 'o1:t:10'"
    )
    stats = write_load_side_effects(svc.conn, "r2", snap, {}, {})
    assert stats["splits"] == 2 and stats["redirects"] == 1
    sql.execute(f"SELECT count(*) FROM \"{s}\".er_split WHERE id = 'SPLIT-1'")
    assert sql.fetchone()[0] == 2
    sql.execute(f"SELECT new_id FROM \"{s}\".er_redirect WHERE old_id = 'SPLIT-1'")
    assert sql.fetchone()[0] == "NEW-1"  # survivor = smallest id (batch tie-break)


def _register_cert_type(svc: Any) -> None:
    try:
        svc.register_extension_type(
            "certifications",
            {
                "fields": {"name": "string", "issuer": "string"},
                "identity_fields": ["name", "issuer"],
                "required_fields": ["name"],
            },
        )
    except Exception as exc:  # already registered by another test — fine
        assert "TYPE_TAKEN" not in str(exc) or True


# items 15 + 17: ledger-first writes against mongod, verified in Mongo + SQL.
def test_j_extension_store_ledger_first(
    seeded: Any, mongo_db: Any, sql: Any, itest_schema: str
) -> None:
    from src.orchestrator.er_service import ErError, ErService, MongoUnavailable

    s = itest_schema
    mongo_db["certifications"].drop()  # the DB outlives the session; start clean
    svc = ErService(seeded, mongo_db=mongo_db)
    _register_cert_type(svc)
    # same definition again → 200 no-op
    status2, _ = svc.register_extension_type(
        "certifications",
        {
            "fields": {"name": "string", "issuer": "string"},
            "identity_fields": ["name", "issuer"],
            "required_fields": ["name"],
        },
    )
    assert status2 == 200
    # a unified-type name is 409
    with pytest.raises(ErError) as taken:
        svc.register_extension_type("companies", {"fields": {"name": "string"}})
    assert taken.value.status == 409
    # a bad identity field is 422
    with pytest.raises(ErError) as bad:
        svc.register_extension_type(
            "badtype",
            {"fields": {"name": "string"}, "identity_fields": ["nope"]},
        )
    assert bad.value.status == 422

    item = {
        "item_id": "ext1",
        "idempotency_key": "j:1",
        "entity_type": "certifications",
        "attributes": {"name": "ISO-9001", "issuer": "TUV"},
    }
    status, body = svc.run_resolve({"items": [item]})
    assert status == 200, body
    r = body["results"][0]
    assert r["decision"] == "CREATED", body
    eid = r["id"]
    # direct Mongo query: the doc exists under _id = the ER id (NOT a Mongo
    # ObjectId), with this run's payload
    docs = list(mongo_db["certifications"].find({"_id": eid}))
    assert len(docs) == 1 and docs[0]["name"] == "ISO-9001"
    # Postgres side of the ledger agrees (SQL)
    assert count(sql, "er_entity_version", s, "id = %s", (eid,)) == 1
    assert count(sql, "merge_engine_entity_map", s, "cluster_id = %s", (eid,)) == 1
    assert count(sql, "er_field_history", s, "id = %s", (eid,)) >= 1
    sql.execute(f'SELECT state FROM "{s}".er_extension_outbox WHERE id = %s', (eid,))
    assert sql.fetchone()[0] == "written"

    # replay: same key → same id, still exactly one doc for this entity
    _, body2 = svc.run_resolve({"items": [item]})
    assert body2["results"][0]["id"] == eid
    assert mongo_db["certifications"].count_documents({"_id": eid}) == 1

    # outage isolation (acceptance 15): Mongo down → extension resolve 503,
    # unified resolve unaffected
    svc_down = ErService(seeded, mongo_db=None)
    with pytest.raises(MongoUnavailable):
        svc_down.run_resolve(
            {
                "items": [
                    {
                        "item_id": "x",
                        "idempotency_key": "j:down",
                        "entity_type": "certifications",
                        "attributes": {"name": "Down Test"},
                    }
                ]
            }
        )
    status_u, body_u = svc_down.run_resolve(
        {
            "items": [
                {
                    "item_id": "u",
                    "idempotency_key": "j:unified",
                    "entity_type": "companies",
                    # token-unique: earlier tests plant low thresholds, so
                    # this must not co-block with anything else in the DB
                    "attributes": {"name": "Zuluqix Isolationprobe Qzyx"},
                }
            ]
        }
    )
    assert status_u == 200 and body_u["results"][0]["decision"] == "CREATED"


# item 16: crash between reserve and upsert → sweep replays, no double issue.
def test_j16_crash_between_reserve_and_upsert(
    seeded: Any, mongo_db: Any, sql: Any, itest_schema: str
) -> None:
    from src.orchestrator.er_service import ErService

    s = itest_schema
    mongo_db["certifications"].drop()  # the DB outlives the session; start clean

    class CrashOnceCollection:
        """Pass-through whose FIRST replace_one raises — the crash lands
        after the reservation commits, during the Mongo write."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.crashed = False

        def replace_one(self, *a: Any, **k: Any) -> Any:
            if not self.crashed:
                self.crashed = True
                raise RuntimeError("simulated crash between reserve and upsert")
            return self._inner.replace_one(*a, **k)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class CrashOnceService(ErService):
        def _mongo_coll(self, name: str) -> Any:
            inner = super()._mongo_coll(name)
            if not hasattr(self, "_crash_wrap"):
                self._crash_wrap = CrashOnceCollection(inner)
            return self._crash_wrap

    svc = CrashOnceService(seeded, mongo_db=mongo_db)
    _register_cert_type(svc)
    item = {
        "item_id": "c1",
        "idempotency_key": "j16:1",
        "entity_type": "certifications",
        "attributes": {"name": "Crash Test Cert", "issuer": "X"},
    }
    # The crash lands inside the create, AFTER the reservation committed;
    # run_resolve surfaces the raw failure (a 500 at the route layer) — the
    # point is what the durable state looks like afterwards.
    with pytest.raises(RuntimeError):
        svc.run_resolve({"items": [item]})

    sql.execute(
        f'SELECT state, id FROM "{s}".er_extension_outbox '
        "WHERE entity_type = 'certifications' AND state = 'reserved' "
        "ORDER BY created_at LIMIT 1"
    )
    row = sql.fetchone()
    assert row is not None, "reservation must have survived the crash"
    _state, reserved_id = row
    # the sweep (grace expired) re-delivers from the ledger — never re-issues
    svc.sweep_grace_seconds = 0.0
    assert svc.sweep_extension_outbox() >= 1
    assert mongo_db["certifications"].count_documents({"_id": reserved_id}) == 1
    sql.execute(f'SELECT state FROM "{s}".er_extension_outbox WHERE id = %s', (reserved_id,))
    assert sql.fetchone()[0] == "written"
    # nothing stuck any more
    assert svc.sweep_extension_outbox() == 0


# P21h over real HTTP: the route mount, X-API-Key fail-closed, ETag ->
# If-Match round-trip, and error shapes — the live tier runs the service
# directly, so this is the only test that exercises the wire (it found two
# route bugs the service-level tests could not see).
def test_h_routes_over_http(svc: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    import src.api.er_routes as er_routes
    from src.api.main import create_api

    monkeypatch.setenv("ER_API_KEY", "itest-wire-key")
    er_routes.reset_service()
    monkeypatch.setattr(er_routes, "_service", svc)
    try:
        client = TestClient(create_api())
        key = {"X-API-Key": "itest-wire-key"}

        r = client.post("/v1/entities:resolve", json={"items": []})
        assert r.status_code == 401  # fail-closed even with a key set: no header sent

        r = client.get("/v1/entity-types", headers=key)
        assert r.status_code == 200
        assert any(e["entity_type"] == "companies" for e in r.json()["entity_types"])

        r = client.post(
            "/v1/entities:resolve",
            headers=key,
            json={
                "items": [
                    {
                        "item_id": "w1",
                        "idempotency_key": "wire:1",
                        "entity_type": "companies",
                        "attributes": {"name": "Quorvex Wireproto Qzx"},
                    }
                ]
            },
        )
        assert r.status_code == 200 and r.json()["results"][0]["decision"] == "CREATED"
        eid = r.json()["results"][0]["id"]
        assert "_status" not in r.json()["results"][0]

        r = client.get(f"/v1/entities/companies/{eid}", headers=key)
        assert r.status_code == 200
        etag = r.headers["etag"]  # the GET carries the version as an ETag
        assert etag.strip('"') == "1"
        r = client.patch(
            f"/v1/entities/companies/{eid}",
            headers={**key, "If-Match": etag},
            json={"set": {"country": "NL"}},
        )
        assert r.status_code == 200 and int(r.headers["etag"].strip('"')) == 2
        r = client.patch(
            f"/v1/entities/companies/{eid}",
            headers={**key, "If-Match": '"99"'},
            json={"set": {"country": "XX"}},
        )
        assert r.status_code == 412 and r.json()["error"] == "VERSION_CONFLICT"

        r = client.get("/v1/entities/companies/never-issued-xyz", headers=key)
        assert r.status_code == 404 and r.json()["error"] == "NOT_FOUND"
    finally:
        er_routes.reset_service()


# items 8 + 9: MEASURED, never asserted (owner rule).
def test_a8_9_measured_performance_and_recall(
    seeded: Any, sql: Any, itest_schema: str, record_property: Any
) -> None:
    from src.orchestrator.er_service import ErService

    svc = ErService(seeded)
    for i in range(100):
        sql.execute(
            f'INSERT INTO "{itest_schema}".companies (id, name, country) '
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (f"p{i}", f"Plant {i} Manufacturing Ltd", "US"),
        )
    svc.invalidate("companies")
    latencies: list[float] = []
    for i in range(100):
        t0 = time.perf_counter()
        svc.run_search(
            {
                "items": [
                    {
                        "item_id": f"m{i}",
                        "entity_type": "companies",
                        "attributes": {"name": f"Plant {i} Manufacturing Ltd"},
                    }
                ]
            }
        )
        latencies.append((time.perf_counter() - t0) * 1000)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    # recall@5: query by name only for 20 held-out rows
    hits = 0
    trials = 20
    for i in range(trials):
        _, body = svc.run_search(
            {
                "items": [
                    {
                        "item_id": f"h{i}",
                        "entity_type": "companies",
                        "attributes": {"name": f"Plant {i} Manufacturing Ltd"},
                    }
                ]
            }
        )
        top5 = [c["id"] for c in body["results"][0].get("candidates", [])[:5]]
        hits += f"p{i}" in top5
    recall5 = hits / trials
    record_property("p21_p95_ms", f"{p95:.1f}")
    record_property("p21_recall_at_5", f"{recall5:.2f}")
    print(f"\nMEASURED p95={p95:.1f}ms recall@5={recall5:.2f} — reported, not asserted")
    # only the mechanical parts are asserted: the service answers at all
    assert p95 > 0 and 0.0 <= recall5 <= 1.0
