"""P10 attack r1 (verifier; commit aec9272) + r2 (commit 568bc8c, section "r2" at the end).  Beyond tests/part1/test_p10_acceptance.py.

Strict xfails = REAL defects only (each flips to XPASS, and the strict flag then fails the suite, when fixed: delete the mark).
Controls (plain tests) pin what was probed and found sound, so a regression shows.

Run:  P10_BASE_REV=4438fe3 TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p10_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from src.orchestrator.nodes import load_data as nld
from src.orchestrator.nodes.load_data import pre_load_gate
from tests.part1.test_p10_acceptance import (
    SCALES,
    World,
    make_world,
    replay,
    run,
)


def gate(state, conf=None):
    return pre_load_gate(copy.deepcopy(state), conf)


# -- 1. coverage is blind to an entity the mapper never reached ---------------------------------
def _drop_unmapped(state, entities):
    st = copy.deepcopy(state)
    for e in entities:
        st["field_mappings"][e] = {}
        st["unmapped_fields"][e] = []  # mapper recorded nothing at all (LLM error / skipped entity)
    return st


def test_half_the_entities_never_mapped_and_not_listed_unmapped_is_refused():
    """The report's own evidence ('zero field mappings for 8 of 16 entities') where map_fields recorded NOTHING for them."""
    w = make_world("f1", n_ent=8, per=40)
    assert gate(_drop_unmapped(w.state, ["ent_a", "ent_b", "ent_c", "ent_d"]))["would_fail"]


def test_run_with_no_mapping_at_all_is_refused():
    w = make_world("f1b", n_ent=8, per=40)
    st = copy.deepcopy(w.state)
    st["field_mappings"], st["unmapped_fields"] = {}, {}
    assert gate(st)["would_fail"]


def test_control_unmapped_listed_entities_are_measured():
    w = make_world("f1c", n_ent=8, per=40, zero_map=4)
    assert gate(w.state)["invariants"]["mapping_coverage"]["violated"]


@pytest.mark.parametrize("extra", ["company_id", "organization_type", "label", "email_verified"])
def test_null_names_are_counted_even_when_a_sibling_hint_column_is_populated(extra):
    w = make_world("f2", n_ent=8, per=100, null_ent=True, null_k=78)
    st = copy.deepcopy(w.state)
    for r in st["extracted_data"]["ent_a"]:
        r[extra] = f"v{r['id']}"
    assert gate(st)["invariants"]["null_identity_share"]["violated"], (
        "78% of the name column is NULL"
    )


# -- 3. false refusals / misses on skewed runs ---------------------------------------------------
def _with_lookup(n_lookup, n_null):
    big = make_world("big", n_ent=6, per=500, null_each=0)
    small = make_world("small", n_ent=1, per=n_lookup, null_each=n_null, seed=3)
    st = copy.deepcopy(big.state)
    for k in (
        "extracted_data",
        "entity_clusters",
        "unified_entities",
        "field_mappings",
        "unmapped_fields",
    ):
        st[k]["lookup"] = small.state[k]["ent_a"]
    return st


def test_small_healthy_entity_beside_large_ones_is_not_refused():
    """50-row lookup, clusters of <= 3 records (as in the large entities), no NULL names: healthy."""
    g = gate(_with_lookup(50, 0))
    assert not g["would_fail"], {
        k: (v["measured"], v["threshold"]) for k, v in g["invariants"].items() if v["violated"]
    }


def test_one_null_name_in_a_small_entity_is_not_a_run_failure():
    assert not gate(_with_lookup(50, 1))["invariants"]["null_identity_share"]["violated"]


def test_control_one_null_among_equal_sized_entities_passes():
    w = make_world("c", n_ent=8, per=40, null_each=0)
    st = copy.deepcopy(w.state)
    st["extracted_data"]["ent_c"][0]["name"] = None
    assert not gate(st)["would_fail"]


@pytest.mark.xfail(
    strict=True,
    reason="F4 ceiling (lead waiver r2): run-relative proposals cannot see uniform badness; entities ALL ~45% NULL "
    "(or all ~45% one cluster) pass the default; the operator sees the measured values at the gate and can tighten",
)
@pytest.mark.parametrize("what", ["null", "cluster"])
def test_every_entity_equally_bad_below_the_cap_is_refused(what):
    if what == "null":
        st = make_world("eq", n_ent=8, per=100, null_each=45).state
        inv = "null_identity_share"
    else:
        w = make_world("eq2", n_ent=2, per=100, giant_k=45)
        st = copy.deepcopy(w.state)
        for e in ("ent_a",) + tuple(f"ent_{c}" for c in "cdefgh"):
            for k in (
                "extracted_data",
                "entity_clusters",
                "unified_entities",
                "field_mappings",
                "unmapped_fields",
            ):
                st[k][e] = copy.deepcopy(w.state[k]["ent_b"])
        inv = "largest_cluster_share"
    g = gate(st)
    assert g["invariants"][inv]["violated"], (g["invariants"][inv], "45% bad in EVERY entity")


def test_control_controls_that_hold():
    """no merges at all, one entity, a uniformly sparse mapping, 60% in every entity: not refused / refused as designed."""
    assert not gate(make_world("one", n_ent=1, per=60).state)["would_fail"]
    assert not gate(make_world("sp", n_ent=8, per=40, mapped=("name",)).state)["would_fail"]
    assert gate(make_world("all60", n_ent=8, per=100, null_each=60).state)["would_fail"]


# -- 4. operator edit paths ----------------------------------------------------------------------
def test_control_edits_for_another_gate_are_ignored_and_stale_node_cannot_loosen():
    w = replay("s8x40")
    st = copy.deepcopy(w.state)
    st["human_feedback"] = {
        "node": "human_review_matches",
        "thresholds": {
            "mapping_coverage": 0.0,
            "null_identity_share": 1.0,
            "largest_cluster_share": 1.0,
        },
    }
    st["load_approval"] = True
    assert gate(st, nld._confirmed_thresholds(st))["would_fail"]
    st["human_feedback"]["node"] = "human_final_approval"
    assert not gate(st, nld._confirmed_thresholds(st))["would_fail"]


def test_control_partial_unknown_bool_text_negative_huge():
    w = replay("s8x40")
    base = gate(w.state)
    # unknown name: silently ignored (see F6, LOW)
    g = gate(w.state, {"null_identity": 1.0})
    assert g["would_fail"] and g["unusable_edits"] == []
    # bool / text / nan / inf: unusable -> fail closed
    for bad in (True, "0.5", float("nan"), float("inf"), None, [1]):
        g = gate(w.state, {k: bad for k in base["invariants"]})
        assert g["would_fail"], bad
    # negative floor = no floor; negative ceiling = everything violates; huge ceiling = disabled
    ok = gate(
        w.state,
        {
            "mapping_coverage": -1,
            "null_identity_share": 10**9,
            "largest_cluster_share": 10**9,
            "flagged_clusters": 10**9,
        },
    )
    assert not ok["would_fail"]
    assert gate(make_world("h", n_ent=4, per=40).state, {"null_identity_share": -1})["would_fail"]
    # non-dict `thresholds` is ignored, proposals bind
    assert gate(w.state, [1, 2])["would_fail"] and gate(w.state, "x")["would_fail"]


def test_the_feedback_endpoint_can_carry_confirmed_thresholds():
    from fastapi.testclient import TestClient

    from src.api import main as api

    seen = {}

    class FakeMgr:
        def get_workflow_status(self, tid):
            return {"status": "interrupted", "current_interrupt_node": "human_final_approval"}

        def resume_workflow(self, tid, feedback):
            seen.update(feedback)

    with patch.object(api, "get_manager", lambda: FakeMgr()):
        r = TestClient(api.create_api()).post(
            "/api/workflow/t1/feedback",
            json={"decision": "approve", "thresholds": {"mapping_coverage": 0.0}},
        )
    assert r.status_code == 200
    assert seen.get("thresholds") == {"mapping_coverage": 0.0}, (
        f"feedback reaching resume_workflow: {seen}"
    )


def test_the_ui_shows_the_gate_before_approval():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src/ui/frontend/src"
    assert any("pre_load_gate" in p.read_text() for p in src.rglob("*.ts*"))


def test_control_human_feedback_edit_survives_the_graph_and_decision_edit_works():
    w = replay("s8x40")
    nm = {"mapping_coverage": 0.0, "null_identity_share": 1.0, "largest_cluster_share": 1.0}
    out = run(w, nm, "thresholds")
    assert out.status == "completed" and out.dest.writes


def test_confirmed_thresholds_survive_a_load_retry_through_the_error_gate():
    import os
    import tempfile
    from pathlib import Path

    from src.mcp_server.tools import load_data as tld
    from src.orchestrator import graph as G
    from src.orchestrator.workflow_manager import WorkflowManager
    from tests.part1.test_p17_acceptance import FakeDest

    w = only_coverage()
    dest = FakeDest(w.tables)
    boom = {"on": True}
    real = dest.load_batch

    def flaky(*a, **k):
        if boom["on"]:
            raise RuntimeError("transient destination failure")
        return real(*a, **k)

    dest.load_batch = flaky
    with (
        patch.object(nld, "_get_destination_connector", lambda cfg: dest),
        patch.object(tld, "_get_destination_connector", lambda cfg: dest),
        patch.object(
            nld, "_RUN_REPORTS_DIR", Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        ),
    ):
        g = G.build_graph()
        tid = uuid.uuid4().hex
        cfg = {"configurable": {"thread_id": tid}}
        g.update_state(cfg, copy.deepcopy(w.state), as_node="unify_entities")
        with patch("src.orchestrator.workflow_manager.build_graph", return_value=g):
            mgr = WorkflowManager()
        mgr._continue_workflow = lambda *a, **k: None
        mgr._active_workflows[tid] = {
            "config": cfg,
            "current_interrupt_node": "human_final_approval",
            "status": "interrupted",
            "interrupt_data": None,
        }
        mgr.resume_workflow(
            tid,
            {
                "decision": "approve",
                "approved": True,
                "node": "human_final_approval",
                "thresholds": {"mapping_coverage": 0.0},
            },
        )
        asyncio.run(g.ainvoke(None, cfg))
        snap = g.get_state(cfg)
        assert snap.next == ("handle_error",), (
            f"precondition: the first load must fail into the error gate, next={snap.next}"
        )
        boom["on"] = False
        mgr._active_workflows[tid].update(
            current_interrupt_node="handle_error", status="interrupted"
        )
        mgr.resume_workflow(tid, {"decision": "retry", "approved": False, "node": "handle_error"})
        asyncio.run(g.ainvoke(None, cfg))
        final = g.get_state(cfg).values
    failure = final.get("failure") or {}
    assert not failure, (
        f"the operator's confirmed thresholds were forgotten on retry: {failure.get('error', '')[:200]}"
    )


def only_coverage() -> World:
    return make_world("only-cov", **SCALES["s8x40"], zero_map=4)


# -- 7. cost -------------------------------------------------------------------------------------
def test_control_gate_is_read_only_and_json_clean():
    w = replay("s8x40")
    st = copy.deepcopy(w.state)
    g = pre_load_gate(st)
    json.dumps(g, allow_nan=False)
    assert st == w.state


# =====================================================================================================
# r2 (HEAD 568bc8c): (a) r1 findings 1/2/3/6/5 end to end through api.Feedback -> real WorkflowManager -> real graph,
#                    (b) the NEW mechanisms (identity column, coverage width, small-entity slack, persistence).
# =====================================================================================================
from types import SimpleNamespace  # noqa: E402

from tests.part1.test_p10_acceptance import PROV, FakeDest, G, WorkflowManager, tld  # noqa: E402

_ALL_KEYS = (
    "extracted_data",
    "entity_clusters",
    "unified_entities",
    "field_mappings",
    "unmapped_fields",
)


def _tables(state):
    cols = {
        c
        for recs in state["extracted_data"].values()
        for r in recs
        for c in r
        if not c.startswith("_")
    }
    return {e: sorted(cols | {"id", "name", "val"}) + list(PROV) for e in state["extracted_data"]}


def _world(name, state):
    st = copy.deepcopy(state)
    st["run_id"] = "p10a-" + name
    return World(name, st, _tables(st), {})


def _drive(world, post, *, then=None):
    """Real graph paused at the final gate; ``post(client, tid)`` answers it through the HTTP API with the REAL manager."""
    from fastapi.testclient import TestClient

    from src.api import main as api

    dest, calls = FakeDest(world.tables), []
    with (
        patch.object(nld, "_get_destination_connector", lambda cfg: dest),
        patch.object(tld, "_get_destination_connector", lambda cfg: dest),
        patch.object(
            nld, "_RUN_REPORTS_DIR", Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
        ),
        patch.object(nld, "load_data_impl", _spy_calls(nld.load_data_impl, calls)),
        patch.object(tld, "load_data_impl", _spy_calls(tld.load_data_impl, calls)),
    ):
        g = G.build_graph()
        tid = uuid.uuid4().hex
        cfg = {"configurable": {"thread_id": tid}}
        g.update_state(cfg, copy.deepcopy(world.state), as_node="unify_entities")
        values = dict(g.get_state(cfg).values)
        live = WorkflowManager._build_interrupt_data_inner("human_final_approval", values)
        with patch("src.orchestrator.workflow_manager.build_graph", return_value=g):
            mgr = WorkflowManager()
        mgr._continue_workflow = lambda *a, **k: None
        mgr._active_workflows[tid] = {
            "config": cfg,
            "current_interrupt_node": "human_final_approval",
            "status": "interrupted",
            "interrupt_data": None,
        }
        seen = []
        real_resume = mgr.resume_workflow
        mgr.resume_workflow = lambda t, fb: (seen.append(dict(fb)), real_resume(t, fb))[1]
        with patch.object(api, "get_manager", lambda: mgr):
            client = TestClient(api.create_api())
            resp = post(client, tid, live)
            asyncio.run(g.ainvoke(None, cfg))
            snap = g.get_state(cfg)
            extra = then(client, tid, mgr, g, cfg, dest, snap) if then else None
        mgr._finalize_stream_status(tid)
        final = dict(g.get_state(cfg).values)
        return SimpleNamespace(
            resp=resp,
            seen=seen,
            live=live,
            final=final,
            failure=final.get("failure") or {},
            dest=dest,
            impl_calls=calls,
            status=mgr._active_workflows[tid]["status"],
            next=tuple(snap.next),
            extra=extra,
        )


def _spy_calls(real, calls):
    async def wrapper(*a, **k):
        calls.append(1)
        return await real(*a, **k)

    return wrapper


def _approve(body):
    return lambda c, tid, live: c.post(f"/api/workflow/{tid}/feedback", json=body)


def test_e2e_api_without_thresholds_refuses_the_replay_and_writes_nothing():
    out = _drive(
        replay_world := _world("e2e-default", replay("s8x40").state),
        _approve({"decision": "approve"}),
    )
    assert out.resp.status_code == 200 and "thresholds" not in out.seen[0]
    assert out.status == "failed" and out.failure.get("disposition") == "fail"
    assert out.dest.writes == [] and out.impl_calls == [] and replay_world


def test_e2e_api_thresholds_taken_verbatim_from_the_payload_are_what_the_ui_sends_and_change_nothing():
    """Gates.tsx sends EVERY displayed proposal back as `thresholds`: the run must behave exactly as with none."""

    def post(c, tid, live):
        inv = live["data"]["pre_load_gate"]["invariants"]
        return c.post(
            f"/api/workflow/{tid}/feedback",
            json={"decision": "approve", "thresholds": {k: v["threshold"] for k, v in inv.items()}},
        )

    out = _drive(_world("e2e-verbatim", replay("s8x40").state), post)
    assert out.seen[0]["thresholds"] and out.status == "failed" and out.dest.writes == []


def test_e2e_api_permissive_thresholds_load_the_replay():
    nm = {
        "mapping_coverage": 0.0,
        "null_identity_share": 1.0,
        "largest_cluster_share": 1.0,
        "flagged_clusters": 10**6,
    }
    out = _drive(
        _world("e2e-edit", replay("s8x40").state),
        _approve({"decision": "approve", "thresholds": nm}),
    )
    assert out.seen[0]["thresholds"] == nm
    assert out.status == "completed" and out.dest.writes and not out.failure


def test_e2e_api_a_partial_edit_does_not_disable_the_other_invariants():
    out = _drive(
        _world("e2e-partial", replay("s8x40").state),
        _approve({"decision": "approve", "thresholds": {"mapping_coverage": 0.0}}),
    )
    assert out.status == "failed" and out.dest.writes == []


def test_e2e_api_non_dict_thresholds_is_a_422_and_the_gate_stays_open():
    out = _drive(
        _world("e2e-422", replay("s8x40").state),
        _approve({"decision": "approve", "thresholds": [1, 2]}),
    )
    assert out.resp.status_code == 422 and out.seen == [] and out.dest.writes == []


def test_e2e_f1_half_the_entities_unmapped_and_unlisted_is_refused_through_the_graph():
    w = make_world("f1e", n_ent=8, per=40)
    st = _drop_unmapped(w.state, ["ent_a", "ent_b", "ent_c", "ent_d"])
    out = _drive(_world("e2e-f1", st), _approve({"decision": "approve"}))
    assert (
        out.status == "failed"
        and out.dest.writes == []
        and "mapping_coverage" in out.failure["error"]
    )


def test_e2e_f2_78_percent_null_names_beside_a_populated_company_id_is_refused_through_the_graph():
    w = make_world("f2e", n_ent=8, per=100, null_ent=True, null_k=78)
    st = copy.deepcopy(w.state)
    for r in st["extracted_data"]["ent_a"]:
        r["company_id"] = f"c{r['id']}"
    out = _drive(_world("e2e-f2", st), _approve({"decision": "approve"}))
    assert (
        out.status == "failed"
        and out.dest.writes == []
        and "null_identity_share" in out.failure["error"]
    )


def test_e2e_f3_a_small_healthy_lookup_beside_large_entities_loads_through_the_graph():
    st = _with_lookup(50, 0)
    out = _drive(_world("e2e-f3", st), _approve({"decision": "approve"}))
    assert out.status == "completed" and not out.failure and out.dest.rows.get("lookup")


def test_e2e_f6_api_error_gate_retry_keeps_the_confirmed_thresholds():
    w = _world("e2e-f6", only_coverage().state)
    box = {}

    def post(c, tid, live):
        return c.post(
            f"/api/workflow/{tid}/feedback",
            json={"decision": "approve", "thresholds": {"mapping_coverage": 0.0}},
        )

    # break the first load by wrapping FakeDest.load_batch (class-level patch for this test only)
    real = FakeDest.load_batch
    state = {"on": True}

    def flaky(self, *a, **k):
        if state["on"]:
            raise RuntimeError("transient destination failure")
        return real(self, *a, **k)

    def then(client, tid, mgr, g, cfg, dest, snap):
        box["next"] = tuple(snap.next)
        state["on"] = False
        mgr._active_workflows[tid].update(
            current_interrupt_node="handle_error", status="interrupted"
        )
        r = client.post(f"/api/workflow/{tid}/feedback", json={"decision": "retry"})
        box["retry_status"] = r.status_code
        asyncio.run(g.ainvoke(None, cfg))
        return dict(g.get_state(cfg).values)

    with patch.object(FakeDest, "load_batch", flaky):
        out = _drive(w, post, then=then)
    assert box["next"] == ("handle_error",), box
    assert box["retry_status"] == 200
    assert not (out.extra.get("failure") or {}), out.extra.get("failure", {}).get("error", "")[:200]
    assert out.extra.get("pre_load_thresholds") == {"mapping_coverage": 0.0}


def test_control_a_fresh_run_never_inherits_an_earlier_runs_confirmation():
    nm = {
        "mapping_coverage": 0.0,
        "null_identity_share": 1.0,
        "largest_cluster_share": 1.0,
        "flagged_clusters": 10**6,
    }
    a = _drive(
        _world("fresh-a", replay("s8x40").state),
        _approve({"decision": "approve", "thresholds": nm}),
    )
    assert a.status == "completed" and a.final.get("pre_load_thresholds") == nm
    b = _drive(_world("fresh-b", replay("s8x40").state), _approve({"decision": "approve"}))
    assert b.status == "failed" and "pre_load_thresholds" not in b.final and b.dest.writes == []


def test_control_confirmed_thresholds_precedence_new_feedback_beats_stored_stored_beats_error_gate_feedback():
    st = {"pre_load_thresholds": {"a": 1}}
    assert nld._confirmed_thresholds(st) == {"a": 1}
    st["human_feedback"] = {"node": "handle_error", "decision": "retry", "thresholds": {"a": 99}}
    assert nld._confirmed_thresholds(st) == {
        "a": 1
    }  # an error-gate feedback can never loosen/replace
    st["human_feedback"] = {"node": "human_final_approval", "thresholds": {"a": 2}}
    assert nld._confirmed_thresholds(st) == {"a": 2}  # a re-approval's new numbers win
    st["human_feedback"] = {"node": "human_final_approval"}
    assert nld._confirmed_thresholds(st) == {
        "a": 1
    }  # no numbers on the re-approval: the stored ones (not in-flow reachable)
    assert nld._confirmed_thresholds({}) is None


# -- r2 mechanism 1: ONE identity column per entity ---------------------------------------------------
def _rename(state, ent, fn):
    st = copy.deepcopy(state)
    for i, r in enumerate(st["extracted_data"][ent]):
        fn(i, r)
    st["field_mappings"][ent] = {
        c: {"dest_column": c, "requires_schema_change": False}
        for c in {c for r in st["extracted_data"][ent] for c in r if not c.startswith("_")}
    }
    st["unmapped_fields"][ent] = []  # every carried field mapped: coverage cannot be what trips
    return st


def _null_inv(state):
    return gate(state)["invariants"]["null_identity_share"]


def test_a_healthy_mixed_origin_entity_is_not_a_null_identity_failure():
    base = make_world("mo", n_ent=8, per=100, null_each=0)

    def fn(i, r):
        r["org_title" if i % 5 == 0 else "company_name"] = r.pop("name")

    st = _rename(base.state, "ent_a", fn)
    inv = _null_inv(st)
    assert not inv["violated"], (inv["measured"], inv["threshold"], "every name is populated")


def test_a_sparse_optional_nickname_does_not_make_a_person_table_a_null_identity_failure():
    base = make_world("nick", n_ent=8, per=100, null_each=0)

    def fn(i, r):
        n = r.pop("name")
        r["first_name"], r["last_name"], r["nickname"] = n, n.title(), (n if i % 10 == 0 else None)

    inv = _null_inv(_rename(base.state, "ent_a", fn))
    assert not inv["violated"], (
        inv["measured"],
        inv["threshold"],
        "first_name/last_name are always populated",
    )


def test_an_id_column_does_not_hide_a_null_label():
    base = make_world("idl", n_ent=8, per=100, null_each=0)

    def fn(i, r):
        r.pop("name")
        r["company_id"], r["label"] = f"c{i}", (None if i < 78 else f"L{i}")

    inv = _null_inv(_rename(base.state, "ent_a", fn))
    assert inv["violated"], (inv["measured"], inv["threshold"], "78 of 100 labels are NULL")


def test_control_literal_name_column_always_wins_and_id_columns_are_last_among_equal_rank():
    base = make_world("ctl", n_ent=8, per=100, null_each=0)

    def fn(i, r):
        r["name"] = None if i < 78 else r["name"]
        r["name_id"], r["company_id"], r["org_id"], r["email"], r["label"], r["username"] = (
            i,
            i,
            i,
            f"e{i}",
            f"l{i}",
            f"u{i}",
        )

    assert _null_inv(_rename(base.state, "ent_a", fn))["measured"] == 0.78

    # same rank, id-like column loses: company_name (12) before company_id (10)
    def fn2(i, r):
        r.pop("name")
        r["company_name"] = None if i < 78 else f"c{i}"
        r["company_id"] = i

    assert _null_inv(_rename(base.state, "ent_a", fn2))["measured"] == 0.78


# -- r2 mechanism 2: coverage width = max(listed, carried fields) -------------------------------------
_AUDIT = (
    "created_at",
    "updated_at",
    "deleted_at",
    "etl_batch_id",
    "row_hash",
    "source_system",
    "ingested_at",
    "modified_by",
)


def _all_mapped_world(n_audit):
    base = make_world("cov", n_ent=8, per=100, null_each=0, mapped=("id", "name", "val", "extra"))
    st = copy.deepcopy(base.state)
    for e in st["unmapped_fields"]:
        st["unmapped_fields"][e] = []
    for r in st["extracted_data"]["ent_a"]:
        for c in _AUDIT[:n_audit]:
            r[c] = (
                "x"  # P12 metadata: the mapper's schema build skips it (metadata_only), neither mapped nor listed
            )
    return st


def test_a_fully_mapped_entity_with_audit_columns_is_not_a_coverage_failure():
    g = gate(_all_mapped_world(8))["invariants"]["mapping_coverage"]
    assert not g["violated"], (g["measured"], g["threshold"], "4 of 4 business fields are mapped")


def test_control_coverage_blind_spot_closed_and_a_few_audit_columns_are_tolerated():
    for n in (0, 2, 4):
        assert not gate(_all_mapped_world(n))["invariants"]["mapping_coverage"]["violated"], n
    # a field that is carried, not mapped, not listed IS a gap now (that is the r1 finding 2 fix)
    base = make_world("cov2", n_ent=8, per=40, mapped=("id", "name", "val", "extra"))
    st = copy.deepcopy(base.state)
    for e in st["unmapped_fields"]:
        st["unmapped_fields"][e] = []
    st["field_mappings"]["ent_a"] = {}
    assert gate(st)["invariants"]["mapping_coverage"]["measured"] == 0.0


# -- r2 mechanism 3: small-entity slack ---------------------------------------------------------------
def _lookup_state(n_lookup, *, big_null=0, giant=0):
    st = _with_lookup(n_lookup, 0)
    if big_null:
        w = make_world("nn", n_ent=1, per=500, null_ent=True, null_k=big_null)
        for k in ("extracted_data", "entity_clusters", "unified_entities"):
            st[k]["ent_a"] = w.state[k]["ent_a"]
    if giant:
        w = make_world("gg", n_ent=2, per=500, giant_k=giant, null_each=0)
        for k in ("extracted_data", "entity_clusters", "unified_entities"):
            st[k]["ent_b"] = w.state[k]["ent_b"]
    return st


def test_a_tiny_lookup_does_not_hide_a_giant_cluster_in_another_entity():
    inv = gate(_lookup_state(5, giant=225))["invariants"]["largest_cluster_share"]
    assert inv["violated"], (inv["measured"], inv["threshold"])


def test_a_tiny_lookup_does_not_hide_a_null_burst_in_another_entity():
    inv = gate(_lookup_state(2, big_null=225))["invariants"]["null_identity_share"]
    assert inv["violated"], (inv["measured"], inv["threshold"])


def test_control_without_the_tiny_lookup_both_bursts_are_caught_and_big_enough_lookups_do_not_lift_them():
    assert gate(_lookup_state(500, giant=225))["invariants"]["largest_cluster_share"]["violated"]
    assert gate(_lookup_state(500, big_null=225))["invariants"]["null_identity_share"]["violated"]
    assert gate(_lookup_state(200, giant=225))["invariants"]["largest_cluster_share"]["violated"]
    assert gate(_lookup_state(50, big_null=225))["invariants"]["null_identity_share"]["violated"]


def test_control_slack_never_exceeds_the_cap_so_the_replay_still_fails_beside_a_one_row_lookup():
    st = _lookup_state(1, big_null=390, giant=300)
    g = gate(st)["invariants"]
    assert g["null_identity_share"]["violated"] and g["largest_cluster_share"]["violated"]
    assert all(
        v["threshold"] <= 0.5
        for k, v in g.items()
        if k in ("null_identity_share", "largest_cluster_share")
    )


def test_a_one_row_entity_with_a_null_name_is_one_null_record_of_slack():
    assert not gate(_with_lookup(1, 1))["invariants"]["null_identity_share"]["violated"]


# =====================================================================================================
# r3 (HEAD d7258a7): the r2 findings end to end, and the NEW mechanisms (per-entity slack, per-shape identity,
# listed coverage width, id-like-last ranking, "a confirmed number is used as given").
# =====================================================================================================
import ast  # noqa: E402
import inspect  # noqa: E402


def _verbatim(st):
    """What Gates.tsx sends: EVERY displayed proposal, unedited, as `thresholds`."""
    return {k: v["threshold"] for k, v in gate(st)["invariants"].items()}


def _giant_beside_lookup_world(n_lookup):
    return _world(f"r3-giant-{n_lookup}", _lookup_state(n_lookup, giant=225))


def test_r3_e2e_a_giant_cluster_beside_a_tiny_lookup_is_refused_through_the_graph():
    """r2 finding 1 end to end (was: 5-row lookup lifted the ceiling to the 1/2 cap, the 45% cluster loaded)."""
    out = _drive(_giant_beside_lookup_world(5), _approve({"decision": "approve"}))
    assert out.status == "failed" and out.failure.get("disposition") == "fail"
    assert "largest_cluster_share" in out.failure["error"]
    assert out.dest.writes == [] and out.impl_calls == []


def test_r3_e2e_a_null_burst_beside_a_two_row_lookup_is_refused_through_the_graph():
    out = _drive(
        _world("r3-null-2", _lookup_state(2, big_null=225)), _approve({"decision": "approve"})
    )
    assert out.status == "failed" and "null_identity_share" in out.failure["error"]
    assert out.dest.writes == []


def test_r3_e2e_a_mixed_origin_entity_with_every_name_populated_loads():
    """r2 finding 2 end to end."""
    base = make_world("mo3", n_ent=8, per=100, null_each=0)

    def fn(i, r):
        r["org_title" if i % 5 == 0 else "company_name"] = r.pop("name")

    out = _drive(
        _world("r3-mixed", _rename(base.state, "ent_a", fn)), _approve({"decision": "approve"})
    )
    assert out.status == "completed" and not out.failure, out.failure


def test_r3_per_entity_slack_catches_what_the_run_wide_minimum_hid():
    """The failure lists name the entity that violates, not just the worst."""
    g = gate(_lookup_state(5, giant=225))
    assert g["entities_violating"]["largest_cluster_share"] == ["ent_b"]
    g = gate(_lookup_state(2, big_null=225))
    assert g["entities_violating"]["null_identity_share"] == ["ent_a"]


def test_r3_control_slack_is_per_entity_and_still_eases_a_healthy_lookup():
    st = _with_lookup(5, 1)  # 1 NULL name in 5 rows, clusters like the large entities
    g = gate(st)
    assert not g["would_fail"], {k: v for k, v in g["invariants"].items() if v["violated"]}
    # two NULLs in 5 is no longer one record of slack
    assert gate(_with_lookup(5, 2))["invariants"]["null_identity_share"]["violated"]
    # a 12-record cluster in a 50-row lookup is more than twice the median entity's largest
    st = _with_lookup(50, 0)
    w = make_world("g12", n_ent=2, per=50, giant_k=12, null_each=0)
    for k in ("extracted_data", "entity_clusters", "unified_entities"):
        st[k]["lookup"] = w.state[k]["ent_b"]
    assert gate(st)["invariants"]["largest_cluster_share"]["violated"]


@pytest.mark.xfail(
    strict=True,
    reason="r3 MEDIUM-HIGH: slack applies only `if name not in edits`, and Gates.tsx ALWAYS sends every invariant "
    "(proposal or edit) as `thresholds`, so through the UI no entity ever gets its slack: approving the displayed, "
    "unedited proposals refuses the healthy 50-row lookup that the no-thresholds path (and the payload's would_fail) load",
)
def test_r3_approving_the_displayed_proposals_unedited_equals_sending_none():
    st = _with_lookup(50, 1)
    assert not gate(st)["would_fail"]
    g = gate(st, _verbatim(st))
    assert not g["would_fail"], {
        k: (v["measured"], v["threshold"]) for k, v in g["invariants"].items() if v["violated"]
    }


@pytest.mark.xfail(
    strict=True,
    reason="r3 MEDIUM-HIGH (same cause, end to end): F3 (healthy lookup beside large entities) loads with no "
    "thresholds but fails when the UI's verbatim proposals ride on the approval",
)
def test_r3_e2e_f3_still_loads_when_the_ui_sends_the_proposals_verbatim():
    st = _with_lookup(50, 0)

    def post(c, tid, live):
        inv = live["data"]["pre_load_gate"]["invariants"]
        return c.post(
            f"/api/workflow/{tid}/feedback",
            json={"decision": "approve", "thresholds": {k: v["threshold"] for k, v in inv.items()}},
        )

    out = _drive(_world("r3-f3-verbatim", st), post)
    assert out.status == "completed" and out.dest.rows.get("lookup")


def test_r3_control_a_confirmed_number_is_used_as_given_and_loosening_it_loads():
    st = _with_lookup(1, 1)  # a 1-row lookup whose name is NULL: share 1.0
    assert not gate(st)["would_fail"]  # the proposal's slack eases it
    assert gate(st, {"null_identity_share": 0.3})["invariants"]["null_identity_share"]["violated"]
    assert not gate(st, {"null_identity_share": 1.0})["invariants"]["null_identity_share"][
        "violated"
    ]
    # an edit to ONE invariant keeps the slack of the others
    g = gate(_with_lookup(5, 1), {"mapping_coverage": 0.0})
    assert not g["invariants"]["null_identity_share"]["violated"]


# -- per-shape identity: a source that OMITS an absent key (MongoDBConnector._flatten_doc) --------------
@pytest.mark.xfail(
    strict=True,
    reason="r3 MEDIUM: records are grouped by (table, key set); a Mongo document with no `name` key is its own shape "
    "whose identity is its next-best column or none at all, and a shape with no name column is not judged (not in the "
    "denominator). 78 of 100 documents lacking `name` measure 0.0 NULL (r2's union column measured 0.78). The code "
    "comment concedes it, but MongoDBConnector rows omit absent fields and are spooled as-is (extract_data -> spool.append)",
)
@pytest.mark.parametrize("extra", [None, "company_id", "email"])
def test_r3_documents_that_omit_the_name_key_are_null_identities(extra):
    base = make_world("mongo", n_ent=8, per=100, null_each=0)

    def fn(i, r):
        if i < 78:
            r.pop("name")
            if extra:
                r[extra] = f"v{i}"

    inv = _null_inv(_rename(base.state, "ent_a", fn))
    assert inv["violated"], (inv["measured"], inv["threshold"], "78 of 100 documents have no name")


def test_r3_control_carried_none_and_empty_string_names_are_still_counted():
    base = make_world("sqlnull", n_ent=8, per=100, null_each=0)
    for val in (None, "", "   "):

        def fn(i, r, val=val):
            if i < 78:
                r["name"] = val

        assert _null_inv(_rename(base.state, "ent_a", fn))["measured"] == 0.78, repr(val)


# -- listed coverage width ----------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=True,
    reason="r3 LOW-MEDIUM: width = mapped+unmapped when the mapper listed ANY field, so an entity whose mapper listed "
    "only the mapped field (7 of 8 carried fields never mentioned) measures coverage 1.0. Real producers: the mapper "
    "samples only records[:_MAX_SAMPLE_RECORDS] (a field first seen later is carried, never listed) and a pinned "
    "profile mapping (field_mappings reused verbatim, new source columns never listed). r1 finding 2 survives in part",
)
def test_r3_an_entity_whose_mapper_listed_one_of_eight_carried_fields_is_a_coverage_failure():
    st = _all_mapped_world(0)
    for r in st["extracted_data"]["ent_a"]:
        for k in "abcd":
            r["x_" + k] = "v"
    st["field_mappings"]["ent_a"] = {
        "name": {"dest_column": "name", "requires_schema_change": False}
    }
    st["unmapped_fields"]["ent_a"] = []
    g = gate(st)["invariants"]["mapping_coverage"]
    assert g["violated"], (
        g["measured"],
        g["threshold"],
        "1 of 8 fields mapped, 7 silently dropped",
    )


def test_r3_control_listed_width_ignores_audit_columns_and_the_unlisted_replay_is_still_caught():
    assert not gate(_all_mapped_world(8))["invariants"]["mapping_coverage"]["violated"]
    w = make_world("lw", n_ent=8, per=40)
    assert gate(_drop_unmapped(w.state, ["ent_a", "ent_b", "ent_c", "ent_d"]))["would_fail"]
    # an unlisted entity: carried fields minus P12 metadata-only columns (flagged in the discovered schema)
    st = copy.deepcopy(w.state)
    st["field_mappings"]["ent_a"], st["unmapped_fields"]["ent_a"] = {}, []
    for r in st["extracted_data"]["ent_a"]:
        r["created_at"] = r["etl_batch_id"] = "x"
    cols = sorted({c for r in st["extracted_data"]["ent_a"] for c in r if not c.startswith("_")})
    st["schemas"] = {
        "o1": {
            "tables": [
                {
                    "name": "ent_a",
                    "full_name": "ent_a",
                    "columns": [{"name": c} for c in cols],
                    "metadata_columns": ["created_at", "etl_batch_id"],
                }
            ]
        }
    }
    st["entity_tables"] = {"ent_a": [{"origin_id": "o1", "table": "ent_a"}]}
    m = nld._entity_measures(st, "ent_a")
    assert m["fields"] == len(cols) - 2 and m["mapping_coverage"] == 0.0


# -- identity ranking ---------------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=True,
    reason="r3 LOW: the buried-hint flag is only the THIRD sort key, after hint rank, so `nickname`/`username`/`filename` "
    "(contain `name`, rank 0) still beat a populated `email`/`title`/`company`; a users table (email always set, "
    "optional nickname 90% NULL) measures 0.90 NULL identity and is refused",
)
def test_r3_a_sparse_optional_nickname_does_not_hide_a_populated_email():
    base = make_world("nick2", n_ent=8, per=100, null_each=0)

    def fn(i, r):
        r["email"] = r.pop("name").lower() + "@x.io"
        r["nickname"] = f"n{i}" if i % 10 == 0 else None

    inv = _null_inv(_rename(base.state, "ent_a", fn))
    assert not inv["violated"], (inv["measured"], inv["threshold"], "email is always populated")


def test_r3_control_ranking_orders_that_hold():
    f = nld._identity_column
    assert f({"id", "company_id", "label"}) == "label"  # id-like last
    assert f({"first_name", "nickname", "username"}) == "first_name"  # whole word before buried
    assert f({"company_name", "company_id"}) == "company_name"
    assert f({"org_id", "name_id"}) == "name_id"  # only id-likes: still one
    assert f({"id", "val"}) is None and f(set()) is None


# -- no fitted constants (plan 2.4) -------------------------------------------------------------------
def test_r3_control_no_numeric_or_corpus_literal_in_the_gate_code():
    allowed = {0, 1, 2, -1}
    corpus = ("78", "465", "9556382", "99", "pagila", "northwind", "company_name", "acme", "staff")
    for fn in (
        nld._identity_column,
        nld._entity_measures,
        nld.pre_load_gate,
        nld._pre_load_failure,
    ):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant):
                if isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                    assert n.value in allowed, (fn.__name__, n.value)
                if isinstance(n.value, str):
                    assert not any(c in n.value.lower() for c in corpus), (fn.__name__, n.value)
