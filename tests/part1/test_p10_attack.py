"""P10 attack r1 (verifier; commit aec9272).  Beyond tests/part1/test_p10_acceptance.py.

Strict xfails = REAL defects only (each flips to XPASS, and the strict flag then fails the suite, when fixed: delete the mark).
Controls (plain tests) pin what was probed and found sound, so a regression shows.

Run:  P10_BASE_REV=4438fe3 TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p10_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
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
    reason="F4: ceilings are 2x the median capped at 1/2: entities ALL ~45% NULL (or all ~45% one cluster) pass",
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
