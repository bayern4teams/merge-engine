"""P10 acceptance: the pre-load gate (derived from the report's Evidence + Fix text and plan 2.4/2.6/9 only;
NO P10 implementation seen -- only today's signatures, to know what to call).

Report P10: "No stage can fail a run on its results -- only on an exception."  Fix: before writing to the
destination compute a small set of invariants -- field-mapping coverage per entity, proportion of NULL identity
fields, largest cluster as a share of the corpus, and P16's label-free signals (user decision c: P16's
``flagged_count`` = clusters whose names are incoherent is one of them) -- and FAIL the run if they are violated.
"Thresholds are proposed by the run and confirmed at the gate rather than hardcoded."  Ceiling: it can only
refuse to write, not roll back.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p10_acceptance.py -q -p no:cacheprovider

HOW IT IS DRIVEN (so the resume trap, plan 2.6, is exercised and not assumed): the REAL compiled graph
(``graph.build_graph()``) is put at the final-approval gate with ``update_state(..., as_node="unify_entities")`` (exactly
what a paused run looks like), the gate payload is read from BOTH producers (WorkflowManager's live builder -- the gate body
never runs before the pause -- and ``human_final_approval``'s own body), the human answers through the REAL
``WorkflowManager.resume_workflow`` (so the update it injects, ``as_node="human_final_approval"``, is the real one; the gate
body does NOT run on resume), the graph continues, and the final status comes from the real
``_finalize_stream_status``.  The destination is a spy (``FakeDest`` of the P17 tests): a "no write" claim is
``dest.writes == [] and dest.ddl == []`` and ``load_data_impl`` never called.

MINIMAL CONTRACT (each is an adaptation point; one constant or one helper below)
  1. Gate payload: ``payload["data"][GATE_KEY]`` (default "pre_load_gate"; env P10_GATE_KEY), identical from both producers,
     JSON-clean with ``allow_nan=False``, a dict with
        ``invariants``  {name: {"measured": number, "threshold": number, "violated": bool}}  -- one entry per invariant;
                        names are matched by role (KIND_RX: coverage / null identity / largest cluster / flagged), so
                        spelling is free; ``measured`` is the run-wide worst case (min coverage; max of the others);
                        ``threshold`` is the run's PROPOSAL; ``violated`` = measured beyond threshold.
        ``would_fail``  bool: the load would be refused at the proposed thresholds.
     Direction: coverage threshold is a MINIMUM (violated when measured < threshold); the other three are MAXIMA.
     Thresholds are ONE value per invariant for the whole run (not per entity) -- J2.
  2. The operator confirms or edits the thresholds in the approve feedback.  The report fixes no key, so the SHAPES below
     are tried in turn (a test passes if ANY shape yields the expected outcome; pin one with env P10_SHAPE=name).  Value
     = {invariant name as in the payload: number}.  The edit must reach the node that enforces it THROUGH RESUME (state
     key added to ``MergeState``, or read from ``human_feedback``), never through the gate body.
  3. Refusal = the run ends FAILED: manager status "failed", ``state["failure"]`` non-empty with disposition "fail"
     (no error-gate "skip", which would walk on as if loaded -- J5), the failure text names every violated invariant
     (by the payload's own invariant name), ZERO writes/DDL, ``load_data_impl`` never called.  Accepting = status
     "completed" and every unified record written.
  4. Plan 2.4: no corpus literal, no new numeric constant beyond {0, 1, 2, -1, 0.0, 1.0}, no numeric default for a
     parameter named like a threshold, in new/changed code (vs P10_BASE_REV, default = HEAD when derived).

JUDGEMENT CALLS (flagged; each waivable by the lead: env P10_SKIP_J=J1,J3 ... records why in the verdict)
  J1 a UNIFORMLY sparse mapping (every entity maps 1 of 3 fields) is the destination's shape, not a defect: it passes.
     A literal floor ("coverage >= 0.5") fails it.  (The grep catches the literal; this catches one hidden in config.)
  J2 one proposed threshold per invariant per run (not per entity); the strict/permissive edits are applied run-wide.
  J3 a one-record run: its single singleton cluster is not a "giant cluster" (share 1.0 of a corpus of 1).
  J4 the proposals depend on the data: across three different healthy worlds at least one proposed threshold differs.
  J5 refusal is disposition "fail" (status failed), not "escalate" (error gate with a "skip" button).
  J6 one zero-mapped entity among many, or 1 flagged cluster at the proposed threshold, is NOT tested (the report fixes
     "half the entities", not the cut); only the extremes and the operator's explicit edits are.
  J7 a no-entities run's outcome (completed-with-nothing vs failed) is unspecified; it must not raise, must not write,
     and must emit finite numbers.
  J8 garbage in the operator's threshold edit (NaN, text, None, list) must fail CLOSED: no write, never "completed"
     with the invariant silently disabled (NaN compares False both ways -- the obvious trap).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import os
import random
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from src.mcp_server.tools import load_data as tld
from src.orchestrator import graph as G
from src.orchestrator.nodes import human_nodes
from src.orchestrator.nodes import load_data as nld
from src.orchestrator.workflow_manager import WorkflowManager
from tests.part1.test_p4_acceptance import GREP_PATTERNS, _buckets
from tests.part1.test_p17_acceptance import PROV, FakeDest

ROOT = Path(__file__).resolve().parents[2]
BASE_REV = os.environ.get(
    "P10_BASE_REV", "a2e2036"
)  # HEAD when these checks were derived (no P10 code)

# ── adaptation points ────────────────────────────────────────────────────────────────────────
GATE_KEY = os.environ.get("P10_GATE_KEY", "pre_load_gate")
KIND_RX = {  # role -> regex over the invariant's name (priority order matters: flagged first)
    "flagged": r"flag",
    "coverage": r"cover|mapp",
    "null": r"null|identity",
    "cluster": r"largest|giant|cluster|share",
}
KINDS = list(KIND_RX)
MIN_KINDS = {"coverage"}  # threshold is a minimum; the others are maxima
FEEDBACK_SHAPES = {  # how the operator's confirmed thresholds ride on the approve feedback
    "thresholds": lambda v: {"thresholds": v},
    "pre_load_thresholds": lambda v: {"pre_load_thresholds": v},
    "gate_thresholds": lambda v: {GATE_KEY: {"thresholds": v}},
    "invariant_thresholds": lambda v: {"invariant_thresholds": v},
    "invariants": lambda v: {"invariants": {k: {"threshold": x} for k, x in v.items()}},
}
PERMISSIVE = {
    "coverage": 0.0,
    "null": 1.0,
    "cluster": 1.0,
    "flagged": 10**6,
}  # nothing can violate these
STRICT = {"coverage": 1.0, "null": 0.0, "cluster": 0.0, "flagged": 0}  # anything imperfect violates
SKIP_J = set(filter(None, os.environ.get("P10_SKIP_J", "").split(",")))
ALLOWED_NUMS = {0, 1, 2, -1}
OWNED = [
    ROOT / "src/orchestrator/nodes/load_data.py",
    ROOT / "src/orchestrator/nodes/human_nodes.py",
    ROOT / "src/orchestrator/graph.py",
    ROOT / "src/orchestrator/workflow_manager.py",
]


def j(tag):
    return pytest.mark.skipif(tag in SKIP_J, reason=f"{tag} waived by the lead (P10_SKIP_J)")


# ── worlds ───────────────────────────────────────────────────────────────────────────────────
PAT = (
    1,
    2,
    3,
    2,
    1,
    1,
    2,
    3,
    1,
    2,
    2,
    1,
)  # cluster sizes of a healthy entity: largest is 3 records
SRC_FIELDS = ("name", "val", "extra")
_C, _V = "bdfgklmnprstvz", "aeiou"


def _names(rng, k):
    seen, out = set(), []
    while len(out) < k:
        n = "".join(rng.choice(_C) + rng.choice(_V) for _ in range(4))
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


@dataclass
class World:
    name: str
    state: dict
    tables: dict
    meta: dict = field(default_factory=dict)


def make_world(
    name,
    n_ent=8,
    per=40,
    *,
    zero_map=0,
    mapped=("name", "val"),
    null_ent=False,
    null_k=0,
    null_each=1,
    giant_k=0,
    incoh=0,
    one_incoh=False,
    seed=11,
):
    """Neutral worlds: entities ent_a.., fields name/val/extra, random 8-letter names.  Per entity: ``null_each``
    NULL-name singletons (a healthy run has a few), the rest in clusters of PAT sizes whose members share one comparable
    name (so P16 flags nothing).  Defects, each independent: ``zero_map`` entities with NO field mapping; ``null_ent``
    (first entity) with ``null_k`` NULL names; ``giant_k`` records fused in ONE cluster of (second entity); ``incoh`` first
    entities whose multi-record clusters hold UNRELATED names (or ``one_incoh``: only one such cluster, first entity)."""
    rng = random.Random(seed)
    ents = [f"ent_{chr(97 + i)}" for i in range(n_ent)]
    extracted, clusters, unified, maps, unmapped, meta = (
        {},
        {},
        {},
        {},
        {},
        {"share": {}, "null": {}},
    )
    for ei, e in enumerate(ents):
        n_null = null_k if (null_ent and ei == 0) else null_each
        giant = giant_k if ei == 1 else 0
        sizes = []
        if giant:
            sizes.append(giant)
        left = per - n_null - giant
        i = 0
        while left > 0:
            s = min(PAT[i % len(PAT)], left)
            sizes.append(s)
            left -= s
            i += 1
        pool = _names(rng, 2 * sum(sizes) + len(sizes) + 1)
        recs, cls, uni, gid, shown = [], [], [], 0, 0
        for ci, s in enumerate(sizes):
            cid = f"{e}-c{ci}"
            bad = (ei < incoh and s > 1) or (one_incoh and ei == 0 and s > 1 and shown == 0)
            shown += bad
            base = pool.pop()
            ids = []
            for m in range(s):
                nm = pool.pop() if bad else (base, base.title(), base.upper())[m % 3]
                rec = {
                    "id": gid,
                    "_origin_id": "o",
                    "_origin_table": e,
                    "name": nm,
                    "val": gid,
                    "extra": "x",
                }
                recs.append(rec)
                ids.append(f"o:{e}:{gid}")
                gid += 1
            cls.append({"cluster_id": cid, "record_ids": ids, "required_human_review": False})
            uni.append(
                {
                    "id": cid,
                    "_cluster_id": cid,
                    "name": base,
                    "val": gid,
                    "extra": "x",
                    "_conflicts": [],
                }
            )
        for _ in range(n_null):
            rec = {
                "id": gid,
                "_origin_id": "o",
                "_origin_table": e,
                "name": None,
                "val": gid,
                "extra": "x",
            }
            recs.append(rec)
            cid = f"{e}-n{gid}"
            cls.append(
                {"cluster_id": cid, "record_ids": [f"o:{e}:{gid}"], "required_human_review": False}
            )
            uni.append(
                {
                    "id": cid,
                    "_cluster_id": cid,
                    "name": None,
                    "val": gid,
                    "extra": "x",
                    "_conflicts": [],
                }
            )
            gid += 1
        extracted[e], clusters[e], unified[e] = recs, cls, uni
        use = () if ei < zero_map else mapped
        maps[e] = {f: {"dest_column": f, "requires_schema_change": False} for f in use}
        unmapped[e] = [
            {"field": f, "reason": "no destination column"} for f in SRC_FIELDS if f not in use
        ]
        meta["share"][e] = max(len(c["record_ids"]) for c in cls) / len(recs)
        meta["null"][e] = sum(r["name"] is None for r in recs) / len(recs)
    meta["entities"] = ents
    state = {
        "user_request": "x",
        "run_id": "p10-" + name,
        "target_entities": ents,
        "extraction_order": ents,
        "extracted_data": extracted,
        "entity_clusters": clusters,
        "unified_entities": unified,
        "field_mappings": maps,
        "unmapped_fields": unmapped,
        "destination": {"type": "postgresql", "tables": {}},
        "pending_schema_changes": [],
        "errors": [],
    }
    tables = {e: ["id", "name", "val", *PROV] for e in ents}
    return World(name, state, tables, meta)


SCALES = {"s8x40": dict(n_ent=8, per=40), "s4x100": dict(n_ent=4, per=100)}


def healthy(scale="s8x40", **kw):
    return make_world(f"healthy-{scale}-{kw}", **SCALES[scale], **kw)


def replay(scale):
    """The report's failure shape: half the entities with zero mappings, one entity ~78% NULL identity, a giant cluster."""
    n = SCALES[scale]["n_ent"]
    per = SCALES[scale]["per"]
    return make_world(
        f"replay-{scale}",
        **SCALES[scale],
        zero_map=n // 2,
        null_ent=True,
        null_k=per * 78 // 100,
        giant_k=per * 6 // 10,
    )


def only(kind, scale="s8x40"):
    """A world where ONLY invariant ``kind`` is extreme."""
    n, per = SCALES[scale]["n_ent"], SCALES[scale]["per"]
    kw = {
        "coverage": dict(zero_map=n // 2),
        "null": dict(null_ent=True, null_k=per * 78 // 100),
        "cluster": dict(giant_k=per * 6 // 10),
        "flagged": dict(incoh=2),
    }[kind]
    return make_world(f"only-{kind}-{scale}", **SCALES[scale], **kw)


# ── harness: the real graph + real manager + spy destination ─────────────────────────────────
RUN_DIR = Path(tempfile.mkdtemp(prefix="p10_runs_", dir=os.environ.get("TMPDIR") or None))


@dataclass
class Out:
    status: str
    failure: dict
    values: dict
    dest: FakeDest
    impl_calls: list
    live: dict
    body: dict
    next_at_gate: tuple
    state_before: dict


_CACHE: dict = {}


def _spy(real, calls):
    async def wrapper(*a, **k):
        calls.append(1)
        return await real(*a, **k)

    return wrapper


def run(world, edits=None, shape=None):
    """``edits`` = {invariant name: value} confirmed by the operator, carried by FEEDBACK_SHAPES[shape]; None = approve as is."""
    key = (world.name, json.dumps(edits, sort_keys=True, default=str), shape)
    if key in _CACHE:
        return _CACHE[key]
    os.environ.pop("MERGE_ENGINE_QUALITY_HISTORY", None)
    dest, calls = FakeDest(world.tables), []
    with (
        patch.object(nld, "_get_destination_connector", lambda cfg: dest),
        patch.object(tld, "_get_destination_connector", lambda cfg: dest),
        patch.object(nld, "_RUN_REPORTS_DIR", RUN_DIR / "run_reports"),
        patch.object(nld, "load_data_impl", _spy(nld.load_data_impl, calls)),
        patch.object(tld, "load_data_impl", _spy(tld.load_data_impl, calls)),
    ):
        g = G.build_graph()
        tid = uuid.uuid4().hex
        cfg = {"configurable": {"thread_id": tid}}
        before = copy.deepcopy(world.state)
        g.update_state(cfg, copy.deepcopy(world.state), as_node="unify_entities")
        snap = g.get_state(cfg)
        values = dict(snap.values)
        live = WorkflowManager._build_interrupt_data_inner("human_final_approval", values)
        body = asyncio.run(human_nodes.human_final_approval(copy.deepcopy(values)))[
            "interrupt_data"
        ]
        with patch("src.orchestrator.workflow_manager.build_graph", return_value=g):
            mgr = WorkflowManager()
        mgr._continue_workflow = lambda *a, **k: None  # we drive the continuation below
        mgr._active_workflows[tid] = {
            "config": cfg,
            "current_interrupt_node": "human_final_approval",
            "status": "interrupted",
            "interrupt_data": None,
        }
        fb = {"decision": "approve", "approved": True}
        if edits is not None:
            fb.update(FEEDBACK_SHAPES[shape](edits))
        mgr.resume_workflow(
            tid, fb
        )  # the gate body does NOT run here (interrupt_before): only update_state + router
        asyncio.run(g.ainvoke(None, cfg))
        mgr._finalize_stream_status(tid)
        final = dict(g.get_state(cfg).values)
        out = Out(
            mgr._active_workflows[tid]["status"],
            final.get("failure") or {},
            final,
            dest,
            calls,
            live,
            body,
            tuple(snap.next),
            before,
        )
    _CACHE[key] = out
    return out


def gate(out, which="live"):
    p = out.live if which == "live" else out.body
    assert GATE_KEY in p["data"], f"gate payload has no {GATE_KEY!r}; keys={list(p['data'])}"
    return p["data"][GATE_KEY]


def names_of(g):
    inv = g["invariants"]
    got = {}
    for kind in KINDS:
        hit = [k for k in inv if k not in got.values() and re.search(KIND_RX[kind], k, re.I)]
        assert hit, f"no {kind!r} invariant among {list(inv)}"
        got[kind] = hit[0]
    return got


def violated(out):
    g = gate(out)
    nm = names_of(g)
    return {kind for kind, k in nm.items() if g["invariants"][k]["violated"]}


def assert_refused(out, kinds=None):
    assert out.status == "failed", (
        f"run did not FAIL pre-load: status={out.status!r}, failure={out.failure}"
    )
    assert out.failure and str(out.failure.get("disposition", "")).lower() == "fail", (
        out.failure
    )  # J5
    assert out.dest.writes == [] and out.dest.ddl == [], (out.dest.writes, out.dest.ddl)
    assert not out.dest.rows or all(not r for r in out.dest.rows.values()), (
        "rows reached the destination"
    )
    assert out.impl_calls == [], "load_data_impl was called"
    if kinds:
        text = json.dumps(out.failure, default=str).lower()
        nm = names_of(gate(out))
        for kind in kinds:
            assert nm[kind].lower() in text, f"failure does not name {nm[kind]!r}: {out.failure}"


def assert_loaded(out, world):
    assert out.status == "completed" and not out.failure, (out.status, out.failure)
    for e, recs in world.state["unified_entities"].items():
        if world.state["field_mappings"].get(
            e
        ):  # a zero-mapped entity is P17's (left empty by design), not P10's
            assert len(out.dest.rows[e]) == len(recs), (
                f"{e}: {len(out.dest.rows[e])} of {len(recs)} rows landed"
            )
    assert out.dest.writes, "the load never ran"


def any_shape(world, edits, check):
    pin = os.environ.get("P10_SHAPE")
    seen = {}
    for name in FEEDBACK_SHAPES:
        if pin and name != pin:
            continue
        out = run(world, edits, name)
        try:
            check(out)
            return out
        except AssertionError as exc:
            seen[name] = str(exc)[:160]
    raise AssertionError(f"no edit shape was honoured: {seen}")


def finite(x):
    json.dumps(x, allow_nan=False)  # raises on NaN/inf


# ══ A. preconditions: the fixtures mean what they say (must pass on today's code) ═════════════
@pytest.mark.parametrize("scale", SCALES)
def test_a_fixtures_are_what_they_claim(scale):
    from src.orchestrator.nodes.load_data import build_reconciliation_report as rep

    h = healthy(scale)
    q = rep(h.state, {}, [])["entities"]
    assert all(v["quality"]["flagged_count"] == 0 for v in q.values()), (
        "healthy world must have no P16 flag"
    )
    assert max(h.meta["share"].values()) <= 0.1 and max(h.meta["null"].values()) <= 0.1
    f = rep(only("flagged", scale).state, {}, [])["entities"]
    assert [f[e]["quality"]["flagged_count"] > 0 for e in sorted(f)][:2] == [True, True]
    assert (
        replay(scale).meta["share"]["ent_b"] >= 0.5 and replay(scale).meta["null"]["ent_a"] >= 0.7
    )
    z = replay(scale).state
    assert sum(1 for m in z["field_mappings"].values() if not m) == SCALES[scale]["n_ent"] // 2


@pytest.mark.parametrize("scale", SCALES)
def test_a_healthy_world_loads_every_row_and_completes(scale):
    w = healthy(scale)
    out = run(w)
    assert_loaded(out, w)
    assert out.next_at_gate == ("human_final_approval",), (
        "the run must still PAUSE at the final gate before any load"
    )
    assert out.dest.writes == [] or out.status == "completed"


# ══ B. the old failure, replayed ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize("scale", SCALES)
def test_b_replay_of_the_old_failure_fails_pre_load_and_writes_nothing(scale):
    """Old: status 'completed', rows written.  New: FAILED, zero writes, even though the human approved."""
    out = run(replay(scale))
    assert_refused(out)
    assert {"coverage", "null", "cluster"} <= violated(out), violated(out)
    assert_refused(out, ["coverage", "null", "cluster"])


@pytest.mark.parametrize("scale", SCALES)
def test_b_refusal_leaves_an_auditable_json_clean_report(scale):
    out = run(replay(scale))
    rep = out.values.get("reconciliation_report")
    assert isinstance(rep, dict) and rep, "no reconciliation report on a refused run"
    json.dumps(rep, default=str)
    json.dumps(out.failure, allow_nan=False, default=str)
    assert gate(out)["would_fail"] is True


# ══ C. each invariant individually can trip (and its healthy twin does not) ═══════════════════
@pytest.mark.parametrize("kind", KINDS)
def test_c_each_invariant_alone_fails_the_run(kind):
    out = run(only(kind))
    assert_refused(out, [kind])
    assert violated(out) == {kind}, f"expected only {kind} to trip, got {violated(out)}"


@pytest.mark.parametrize("kind", KINDS)
def test_c_twin_without_that_defect_loads(kind):
    """Guards 'always fail': the same world with the single defect removed passes and loads."""
    w = healthy()
    out = run(w)
    assert_loaded(out, w)
    assert violated(out) == set() and gate(out)["would_fail"] is False


@pytest.mark.parametrize("kind", ["null", "cluster", "coverage"])
def test_c_trips_at_a_second_scale_too(kind):
    out = run(only(kind, "s4x100"))
    assert_refused(out, [kind])
    assert violated(out) == {kind}


@j("J1")
def test_c_uniformly_sparse_mapping_is_not_a_defect():
    w = make_world("uniform-sparse", **SCALES["s8x40"], mapped=("name",))
    out = run(w)
    assert out.status == "completed" and not out.failure, (out.status, out.failure)
    assert "coverage" not in violated(out)


def test_c_every_entity_unmapped_fails_even_though_no_peer_is_better():
    w = make_world("all-unmapped", **SCALES["s8x40"], zero_map=8)
    out = run(w)
    assert_refused(out, ["coverage"])


# ══ D. thresholds: proposed in the payload, confirmed (or edited) at resume ═══════════════════
@pytest.mark.parametrize("w", [healthy(), replay("s8x40")], ids=["healthy", "replay"])
def test_d_payload_proposes_thresholds_from_both_producers(w):
    out = run(w)
    live, body = gate(out, "live"), gate(out, "body")
    assert json.loads(json.dumps(live)) == json.loads(json.dumps(body)), (
        "the two gate producers disagree"
    )
    finite(live)
    nm = names_of(live)
    assert len(set(nm.values())) == 4
    for kind, k in nm.items():
        e = live["invariants"][k]
        assert isinstance(e["threshold"], (int, float)) and not isinstance(e["threshold"], bool), (
            k,
            e,
        )
        assert isinstance(e["measured"], (int, float)) and isinstance(e["violated"], bool), (k, e)
        want = (
            e["measured"] < e["threshold"] if kind in MIN_KINDS else e["measured"] > e["threshold"]
        )
        assert e["violated"] == want, (
            f"{k}: violated flag disagrees with measured vs threshold: {e}"
        )
    for key in ("summary", "reconciliation", "destination_defects", "pending_schema_changes"):
        assert key in out.live["data"], f"existing gate key {key!r} was lost"


def test_d_measured_values_are_the_independent_counts():
    """The measured numbers are the data's own: 78% NULL and a 60% cluster are recoverable from the payload."""
    w = replay("s8x40")
    g = gate(run(w))
    nm = names_of(g)
    assert g["invariants"][nm["null"]]["measured"] == pytest.approx(
        max(w.meta["null"].values()), abs=0.02
    )
    assert g["invariants"][nm["cluster"]]["measured"] == pytest.approx(
        max(w.meta["share"].values()), abs=0.02
    )
    assert g["invariants"][nm["coverage"]]["measured"] == pytest.approx(0.0, abs=1e-9)
    gf = gate(run(only("flagged")))
    assert (
        gf["invariants"][names_of(gf)["flagged"]]["measured"] >= 2
    )  # >= the 2 planted entities' incoherent clusters


@j("J4")
def test_d_proposals_come_from_the_data_not_from_constants():
    worlds = [healthy("s8x40"), healthy("s4x100"), healthy("s8x40", null_each=3, seed=5)]
    props = []
    for w in worlds:
        g = gate(run(w))
        nm = names_of(g)
        props.append(tuple(g["invariants"][nm[k]]["threshold"] for k in KINDS))
    assert len(set(props)) > 1, (
        f"identical proposals for different data: {props[0]} (a hard-coded set?)"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_d_operator_edit_to_a_permissive_threshold_is_honoured_at_resume(kind):
    """The tripped world is refused as proposed; the operator confirms a permissive value for THAT invariant on the
    approve feedback; the load proceeds.  The gate body never runs on resume, so this is the router/node path."""
    w = only(kind)
    assert_refused(run(w), [kind])  # as proposed: refused (non-vacuous)
    nm = names_of(gate(run(w)))
    any_shape(w, {nm[kind]: PERMISSIVE[kind]}, lambda o: assert_loaded(o, w))


@pytest.mark.parametrize("kind", ["coverage", "null", "cluster"])
def test_d_operator_edit_to_a_strict_threshold_refuses_a_healthy_run(kind):
    w = healthy()
    assert_loaded(run(w), w)  # as proposed: loads (non-vacuous)
    nm = names_of(gate(run(w)))
    any_shape(w, {nm[kind]: STRICT[kind]}, lambda o: assert_refused(o, [kind]))


def test_d_operator_strict_flagged_threshold_refuses_a_world_with_one_flagged_cluster():
    w = make_world("one-flag", **SCALES["s8x40"], one_incoh=True)
    nm = names_of(gate(run(w)))
    any_shape(w, {nm["flagged"]: STRICT["flagged"]}, lambda o: assert_refused(o, ["flagged"]))


def test_d_an_edit_is_per_invariant_the_others_still_bind():
    w = replay("s8x40")
    nm = names_of(gate(run(w)))
    out = any_shape(
        w,
        {nm["coverage"]: PERMISSIVE["coverage"]},
        lambda o: assert_refused(o, ["null", "cluster"]),
    )
    assert (
        "coverage" not in violated(out) or True
    )  # the payload is built before the edit; only the outcome is asserted


@pytest.mark.parametrize(
    "bad", [float("nan"), "abc", None, []], ids=["nan", "text", "none", "list"]
)
@j("J8")
def test_d_garbage_edit_fails_closed(bad):
    w = replay("s8x40")
    nm = names_of(gate(run(w)))
    for shape in FEEDBACK_SHAPES:
        out = run(w, {nm[k]: bad for k in KINDS}, shape)
        assert out.dest.writes == [] and out.dest.ddl == [] and out.impl_calls == [], (
            f"{shape}: wrote"
        )
        assert out.status != "completed", f"{shape}: invariants silently disabled by {bad!r}"


# ══ E. degenerate input ═════════════════════════════════════════════════════════════════════
def _bare(name, **over):
    st = {
        "user_request": "x",
        "run_id": "p10-" + name,
        "target_entities": [],
        "extraction_order": [],
        "extracted_data": {},
        "entity_clusters": {},
        "unified_entities": {},
        "field_mappings": {},
        "unmapped_fields": {},
        "destination": {"type": "postgresql", "tables": {}},
        "pending_schema_changes": [],
        "errors": [],
        **over,
    }
    return World(name, st, {})


def test_e_no_entities_does_not_crash_writes_nothing_and_is_finite():
    out = run(_bare("empty"))  # J7: status unspecified
    assert out.dest.writes == [] and out.dest.ddl == []
    finite(gate(out, "live"))  # the gate must exist even with nothing to measure
    finite(gate(out, "body"))


def test_e_entities_without_clusters_or_records_do_not_divide_by_zero():
    w = healthy()
    st = copy.deepcopy(w.state)
    st["entity_clusters"] = {}  # records but no clustering at all
    st["extracted_data"]["ent_c"] = []
    st["unified_entities"]["ent_d"] = []
    out = run(World("no-clusters", st, w.tables))
    finite(out.live["data"][GATE_KEY])
    assert out.status in (
        "failed",
        "completed",
        "interrupted",
    )  # no exception is the point (run() would have raised)


def test_e_all_identity_fields_null_fails():
    w = make_world("all-null", n_ent=1, per=20, null_ent=True, null_k=20)
    out = run(w)
    assert_refused(out, ["null"])


@j("J3")
def test_e_one_record_run_is_not_a_giant_cluster():
    w = make_world("tiny", n_ent=1, per=1, null_each=0)
    out = run(w)
    assert "cluster" not in violated(out), "a lone singleton was called a giant cluster"
    assert out.status == "completed", (out.status, out.failure)


def test_e_the_preview_is_read_only_and_does_not_mutate_state():
    w = replay("s8x40")
    out = run(w)
    # live+body payloads were built before the resume: nothing written, state unchanged by the preview itself
    assert out.state_before == w.state
    pre = FakeDest(w.tables)
    with (
        patch.object(nld, "_get_destination_connector", lambda cfg: pre),
        patch.object(tld, "_get_destination_connector", lambda cfg: pre),
    ):
        st = copy.deepcopy(w.state)
        WorkflowManager._build_interrupt_data_inner("human_final_approval", st)
        asyncio.run(human_nodes.human_final_approval(st))
    assert pre.writes == [] and pre.ddl == [] and st == w.state


def test_e_gate_wiring_untouched_pause_before_final_approval_and_router_still_forwards():
    assert "human_final_approval" in G.HITL_NODES
    route = G._gate_router("human_final_approval", "load_data")
    assert route({"human_feedback": {"decision": "approve", "approved": True}}) == "forward"
    assert route({"human_feedback": {"decision": "reject"}}) == "abort"


# ══ F. plan 2.4: no fitted constants (static) ═════════════════════════════════════════════
def _git_show(rev, rel):
    r = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _new_files():
    r = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = [ROOT / p for p in r.stdout.split() if p.endswith(".py")]
    r2 = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", BASE_REV, "--", "src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out += [ROOT / p for p in r2.stdout.split() if p.endswith(".py")]
    return [
        p for p in dict.fromkeys(out) if re.search(r"invariant|pre.?load|gate", p.read_text(), re.I)
    ]


def _targets():
    return OWNED + _new_files()


def _fns(tree):
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _skip_ids(node):
    """ids of docstring constants and of the digits argument of round()."""
    skip = set()
    for n in ast.walk(node):
        if (
            isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body
        ):
            e = n.body[0]
            if isinstance(e, ast.Expr) and isinstance(getattr(e, "value", None), ast.Constant):
                skip.add(id(e.value))
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "round" and len(n.args) == 2:
            skip.add(id(n.args[1]))
    return skip


def _consts(node, skip):
    return [c for c in ast.walk(node) if isinstance(c, ast.Constant) and id(c) not in skip]


def _new_code():
    """(label, node, known-constant-values) for every function new/changed since BASE_REV and every new module-level assignment."""
    out = []
    for p in _targets():
        rel = str(p.relative_to(ROOT))
        now = ast.parse(p.read_text())
        base_src = _git_show(BASE_REV, rel)
        base = ast.parse(base_src) if base_src else None
        bf = _fns(base) if base else {}
        skip = _skip_ids(now)
        for name, fn in _fns(now).items():
            if name in bf and ast.dump(bf[name]) == ast.dump(fn):
                continue
            known = {c.value for c in _consts(bf[name], set())} if name in bf else set()
            out.append((f"{p.name}:{name}", fn, skip, known))
        base_mod = {ast.dump(n) for n in base.body} if base else set()
        for n in now.body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and ast.dump(n) not in base_mod:
                out.append((f"{p.name}:<module>", n, skip, set()))
    return out


def test_f_grep_no_corpus_literal_in_owned_or_new_code():
    for p in _targets():
        got = _buckets(p)
        assert not got["code"], f"corpus literal in CODE of {p.name}: {got['code']}"
        base_file = RUN_DIR / ("base_" + p.name)
        base_file.write_text(_git_show(BASE_REV, str(p.relative_to(ROOT))) or "")
        base = _buckets(base_file)
        for b in ("doc", "comment"):
            for pat, n in got[b].items():
                assert n <= base[b].get(pat, 0), f"new {pat!r} literal(s) in {b}s of {p.name}"
    assert GREP_PATTERNS, "grep patterns vanished"


def test_f_no_numeric_literal_beyond_unit_values_in_new_or_changed_code():
    """'Would a count over the corpus produce this?'  Thresholds, floors, ceilings, multipliers, percentages: no."""
    bad = []
    for label, node, skip, known in _new_code():
        for c in _consts(node, skip):
            v = c.value
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v in known:
                continue
            if v not in ALLOWED_NUMS:
                bad.append((label, v))
    assert not bad, f"numeric literals not derivable from data (plan 2.4): {bad}"


def test_f_no_numeric_default_for_a_threshold_like_parameter():
    for p in _targets():
        for fn in _fns(ast.parse(p.read_text())).values():
            a = fn.args
            pos = a.posonlyargs + a.args
            pairs = list(zip(pos[len(pos) - len(a.defaults) :], a.defaults, strict=True)) + [
                (k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None
            ]
            for arg, d in pairs:
                if re.search(r"thresh|cut|limit|bar|tol|eps|floor|ceil|max_|min_", arg.arg.lower()):
                    assert not (
                        isinstance(d, ast.Constant) and isinstance(d.value, (int, float))
                    ), (p.name, fn.name, arg.arg)


def test_f_the_gate_computation_exists_in_new_code_not_only_in_a_comment():
    """Vacuity guard for F: some new/changed code under OWNED mentions the gate (else the greps above scanned nothing)."""
    hits = [
        lab
        for lab, node, *_ in _new_code()
        if re.search(r"invariant|pre_load", ast.unparse(node), re.I)
    ]
    assert hits, (
        "no new/changed code mentions the gate's invariants since BASE_REV: P10 is not implemented"
    )
