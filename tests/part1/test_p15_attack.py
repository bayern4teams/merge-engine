"""P15 attack (adversarial, written after the implementation): edge cases the derived
acceptance suite does not cover.  Strict xfail = a defect found; the reason names it.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p15_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import math

import pytest

import src.orchestrator.spool as spool_mod
from src.mcp_server.tools import resolve_entities as tre
from tests.part1 import fixtures_matching as fx
from tests.part1 import test_p15_acceptance as acc

node = acc.node
NAN = float("nan")


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


def _fb(**kw):
    return {"decision": "approve", "approved": True, **kw}


def _pd(r, items):
    return [{"entity_type": "company", "record_ids": list(r.ids[k]), "action": a} for k, a in items]


# ── _band_fields: degenerate inputs ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("log_odds", [False, True])
def test_band_fields_empty_input(log_odds):
    out = tre._band_fields([], 0.95, 0.70, log_odds)
    s = out["band_summary"]
    assert s["predicted_counts"] == {"match": 0, "possible": 0, "exclude": 0}
    assert sum(s["distribution"]["counts"]) == 0
    assert out["retained_pairs"] == []


@pytest.mark.parametrize("log_odds", [False, True])
def test_band_fields_every_pair_in_band(log_odds):
    pairs = [(f"a{i}", f"b{i}", 0.70 + i * 0.02) for i in range(10)]  # 0.70 .. 0.88
    s = tre._band_fields(pairs, 0.95, 0.70, log_odds)["band_summary"]
    assert s["predicted_counts"] == {"match": 0, "possible": 10, "exclude": 0}
    assert sum(s["distribution"]["counts"]) == 10


@pytest.mark.parametrize("log_odds", [False, True])
def test_band_fields_all_identical_and_extreme_scores(log_odds):
    same = [(f"a{i}", f"b{i}", 0.8) for i in range(5)]
    s = tre._band_fields(same, 0.95, 0.70, log_odds)["band_summary"]
    assert sum(s["distribution"]["counts"]) == 5
    extreme = [("a", "b", 0.0), ("c", "d", 1.0), ("e", "f", 0.8)]  # log-odds would be +-inf
    s = tre._band_fields(extreme, 0.95, 0.70, log_odds)["band_summary"]
    assert sum(s["distribution"]["counts"]) == 3
    assert all(math.isfinite(e) for e in s["distribution"]["bin_edges"])
    assert s["predicted_counts"] == {"match": 1, "possible": 1, "exclude": 1}


@pytest.mark.parametrize("log_odds", [False, True])
def test_band_fields_nan_score_does_not_crash_or_vanish(log_odds):
    pairs = [("a", "b", 0.8), ("c", "d", NAN), ("e", "f", 0.99)]
    s = tre._band_fields(pairs, 0.95, 0.70, log_odds)["band_summary"]
    assert sum(s["predicted_counts"].values()) == 3
    assert sum(s["distribution"]["counts"]) == 3


def test_band_fields_json_serialisable():
    import json

    pairs = [("a", "b", 0.0), ("c", "d", 1.0), ("e", "f", 0.8)]
    json.dumps(tre._band_fields(pairs, 0.95, 0.70, True))


# ── thresholds committed by the operator: inverted / out of range / non-numeric ──────────────
@pytest.mark.parametrize(
    "bad",
    [
        {"auto_match": 0.5, "auto_exclude": 0.9},  # inverted
        {"auto_match": 0.8, "auto_exclude": 0.8},  # equal
        {"auto_match": 1.5, "auto_exclude": 0.7},  # > 1
        {"auto_match": 0.9, "auto_exclude": -0.1},  # < 0
        {"auto_match": NAN, "auto_exclude": 0.7},
        {"auto_match": float("inf"), "auto_exclude": 0.7},
        {"auto_match": True, "auto_exclude": 0.0},  # bool is not a number here
        {"auto_match": "0.9", "auto_exclude": "0.7"},
        {"auto_match": None, "auto_exclude": None},
        {"auto_match": 0.9},  # missing
        [0.9, 0.7],  # not a dict
        "garbage",
    ],
)
def test_bad_committed_thresholds_are_refused_and_do_not_merge_the_band(bad):
    r = acc.run_node(["P", "M", "X"], "heuristic")
    post = acc.resume_manager(r.pre, _fb(band_thresholds={"company": bad}))
    assert acc.fz(r.ids, "P") not in acc.groups(post), f"{bad!r} merged the band pair"
    assert acc.fz(r.ids, "M") in acc.groups(post)
    shown = post["resolution_stats"]["company"]["band_resolution"]["thresholds"]
    assert shown == {"auto_match": 0.95, "auto_exclude": 0.70}, "refused thresholds must not stick"


def test_non_dict_band_thresholds_value_does_not_crash():
    r = acc.run_node(["P", "M"], "heuristic")
    for bad in (["x"], "x", 5, True):
        post = acc.resume_manager(
            r.pre, _fb(band_thresholds=bad, pair_decisions=_pd(r, [("P", "merge")]))
        )
        assert acc.fz(r.ids, "P") in acc.groups(post)


def test_committed_thresholds_for_unknown_entity_are_ignored():
    r = acc.run_node(["P", "M"], "heuristic")
    post = acc.resume_manager(
        r.pre, _fb(band_thresholds={"nosuch": {"auto_match": 0.5, "auto_exclude": 0.1}})
    )
    assert acc.fz(r.ids, "P") not in acc.groups(post)


def test_boundary_commit_match_bar_equals_pair_score_merges_it():
    r = acc.run_node(["P", "M"], "heuristic")  # P = 0.80
    post = acc.resume_manager(
        r.pre, _fb(band_thresholds={"company": {"auto_match": 0.80, "auto_exclude": 0.7}})
    )
    assert acc.fz(r.ids, "P") in acc.groups(post)  # >= bar, same convention as the default


def test_committed_bar_far_below_the_runs_exclude_bar_is_safe():
    r = acc.run_node(["P", "X"], "heuristic")  # X = 0.40 is not retained
    post = acc.resume_manager(
        r.pre, _fb(band_thresholds={"company": {"auto_match": 0.2, "auto_exclude": 0.1}})
    )
    assert acc.fz(r.ids, "X") not in acc.groups(post), (
        "a pair the run never retained must not merge"
    )
    assert acc.fz(r.ids, "P") in acc.groups(post)


# ── decisions: not in band, stale ids, duplicates, conflicts ─────────────────────────────────
def test_stale_and_garbage_decisions_do_not_crash_or_change_anything():
    r = acc.run_node(["P", "M", "X"], "heuristic")
    before = acc.groups(r.pre)
    junk = [
        {"entity_type": "company", "record_ids": ["no:such:1", "no:such:2"], "action": "merge"},
        {"entity_type": "company", "record_ids": ["only-one"], "action": "merge"},
        {"entity_type": "company", "record_ids": [], "action": "merge"},
        {"entity_type": "company", "record_ids": list(r.ids["P"]) + ["x"], "action": "merge"},
        {"entity_type": "company", "record_ids": None, "action": "merge"},
        {"entity_type": "company", "record_ids": list(r.ids["P"]), "action": None},
        "not-a-dict",
        None,
        {"entity_type": "nosuch", "record_ids": list(r.ids["P"]), "action": "merge"},
    ]
    post = acc.resume_manager(r.pre, _fb(pair_decisions=junk))
    assert acc.groups(post) == before, "junk decisions changed the clustering"


def test_decision_for_an_exclude_pair_is_the_operators_call_but_is_audited():
    """Operator may merge a pair the run excluded (X, 0.40): the helper allows it. Recorded as
    accepted so the audit shows it.  Documented behaviour, not a defect, but pin it."""
    r = acc.run_node(["P", "X"], "heuristic")
    post = acc.resume_manager(r.pre, _fb(pair_decisions=_pd(r, [("X", "merge")])))
    res = post["resolution_stats"]["company"]["band_resolution"]
    assert res["accepted"] == 1
    assert acc.fz(r.ids, "X") in acc.groups(post)


def test_conflicting_duplicate_decisions_never_merge():
    """merge then keep for the same pair (and vice versa): the unrecoverable action must lose."""
    r = acc.run_node(["P"], "heuristic")
    for order in (("merge", "keep_separate"), ("keep_separate", "merge")):
        post = acc.resume_manager(
            r.pre, _fb(pair_decisions=_pd(r, [("P", order[0]), ("P", order[1])]))
        )
        assert acc.fz(r.ids, "P") not in acc.groups(post), f"order {order} merged"


def test_pair_order_and_case_of_action_do_not_matter():
    r = acc.run_node(["P"], "heuristic")
    a, b = r.ids["P"]
    d = [{"entity_type": "company", "record_ids": [b, a], "action": " MERGE "}]
    post = acc.resume_manager(r.pre, _fb(pair_decisions=d))
    assert acc.fz(r.ids, "P") in acc.groups(post)


def test_resume_twice_is_idempotent():
    r = acc.run_node(["P", "Q", "M"], "heuristic")
    fb = _fb(pair_decisions=_pd(r, [("P", "merge"), ("Q", "keep_separate")]))
    once = acc.resume_manager(r.pre, fb)
    twice = acc.resume_manager(once, fb)
    assert acc.groups(once) == acc.groups(twice)
    assert acc.pending_pairs(twice) == set()


def test_second_resume_with_different_feedback_does_not_undo_the_first():
    r = acc.run_node(["P", "M"], "heuristic")
    once = acc.resume_manager(r.pre, _fb(pair_decisions=_pd(r, [("P", "merge")])))
    thr = acc.resume_manager(
        once, _fb(band_thresholds={"company": {"auto_match": 0.95, "auto_exclude": 0.7}})
    )
    assert acc.fz(r.ids, "P") in acc.groups(thr), "a later resume undid a merged band pair"


def test_plain_approve_with_pending_pair_keeps_it_apart_and_does_not_rebuild():
    r = acc.run_node(["P", "M"], "heuristic")
    post = acc.resume_manager(r.pre, _fb())
    assert acc.fz(r.ids, "P") not in acc.groups(post)
    assert acc.fz(r.ids, "M") in acc.groups(post)


def test_old_checkpoint_without_retained_pairs_does_not_claim_a_merge_it_did_not_do():
    """Pre-P15 checkpoint (no retained_pairs / band_summary): a merge decision cannot be applied.
    The audit block must not say it was."""
    r = acc.run_node(["P", "M"], "heuristic")
    stats = {
        k: v
        for k, v in r.pre["resolution_stats"]["company"].items()
        if k not in ("retained_pairs", "band_summary")
    }
    pre = {**r.pre, "resolution_stats": {"company": stats}}
    post = acc.resume_manager(pre, _fb(pair_decisions=_pd(r, [("P", "merge")])))
    merged = acc.fz(r.ids, "P") in acc.groups(post)
    res = post["resolution_stats"]["company"].get("band_resolution", {})
    assert merged or res.get("accepted", 0) == 0, "audit says accepted=1 but nothing merged"


# ── a human 'keep apart' must hold even when other evidence links the pair ───────────────────
def _triangle(ab=0.80, ac=0.97, bc=0.97):
    a = fx._rec("src_a", "t1", name="Zephyr Co")
    b = fx._rec("src_b", "t2", name="Zephyr Co Beta")
    c = fx._rec("src_c", "t3", name="Zephyr Co Gamma")
    sc = {
        frozenset({"Zephyr Co", "Zephyr Co Beta"}): ab,
        frozenset({"Zephyr Co", "Zephyr Co Gamma"}): ac,
        frozenset({"Zephyr Co Beta", "Zephyr Co Gamma"}): bc,
    }
    return [a, b, c], sc, [fx.rid(a), fx.rid(b), fx.rid(c)]


def test_reject_of_a_band_pair_does_not_destroy_the_match_pairs_around_it():
    recs, sc, ids = _triangle()
    r = acc.run_node([], "heuristic", extra=(recs, sc), strict=False)
    a, b, c = ids
    d = [{"entity_type": "company", "record_ids": [a, b], "action": "keep_separate"}]
    post = acc.resume_manager(r.pre, _fb(pair_decisions=d))
    together = any({a, b} <= g for g in acc.groups(post))
    assert not together, "operator said keep A and B apart; they are still in one cluster"
    assert any(c in g for g in acc.groups(post)), (
        "C (Match 0.97 to both) ended up a singleton: the reject destroyed the Match merges"
    )


def test_human_split_in_same_feedback_is_not_undone_by_the_band_rebuild():
    r = acc.run_node(["P", "M"], "heuristic")
    m_cluster = next(
        c for c in acc.clusters_for(r.pre, "company") if set(c["record_ids"]) == set(r.ids["M"])
    )
    fb = _fb(
        cluster_decisions=[
            {"cluster_id": m_cluster["cluster_id"], "entity_type": "company", "action": "split"}
        ],
        pair_decisions=_pd(r, [("P", "merge")]),
    )
    post = acc.resume_manager(r.pre, fb)
    assert acc.fz(r.ids, "P") in acc.groups(post)
    assert acc.fz(r.ids, "M") not in acc.groups(post), "operator's split of M was re-merged"


def test_human_split_alone_without_band_feedback_still_works():
    r = acc.run_node(["P", "M"], "heuristic")
    m_cluster = next(
        c for c in acc.clusters_for(r.pre, "company") if set(c["record_ids"]) == set(r.ids["M"])
    )
    fb = _fb(
        cluster_decisions=[
            {"cluster_id": m_cluster["cluster_id"], "entity_type": "company", "action": "split"}
        ]
    )
    post = acc.resume_manager(r.pre, fb)
    assert acc.fz(r.ids, "M") not in acc.groups(post)


# ── Match / Exclude untouched when the operator only decides the band ────────────────────────
def test_match_and_exclude_clusters_survive_unchanged_object_for_object():
    r = acc.run_node(["P", "M", "X", "R"], "heuristic")
    before = {c["cluster_id"]: c for c in acc.clusters_for(r.pre, "company")}
    post = acc.resume_manager(r.pre, _fb(pair_decisions=_pd(r, [("P", "merge")])))
    after = {c["cluster_id"]: c for c in acc.clusters_for(post, "company")}
    for key in ("M", "R", "X"):
        for rid in r.ids[key]:
            old = next(c for c in before.values() if rid in c["record_ids"])
            new = next(c for c in after.values() if rid in c["record_ids"])
            assert old == new, f"{key}: cluster for {rid} changed by a band decision"


def test_exact_boundaries_end_to_end_unchanged_for_match():
    """0.95 exactly is Match, 0.9496 is band, 0.70 is band, 0.6996 is Exclude."""
    r = acc.run_node(["D", "C", "B", "E"], "heuristic")
    assert acc.fz(r.ids, "D") in acc.groups(r.pre)
    pend = acc.pending_pairs(r.pre)
    assert acc.fz(r.ids, "C") in pend and acc.fz(r.ids, "B") in pend
    assert acc.fz(r.ids, "E") not in pend and acc.fz(r.ids, "E") not in acc.groups(r.pre)
    s = r.pre["resolution_stats"]["company"]["band_summary"]["predicted_counts"]
    assert s == {"match": 1, "possible": 2, "exclude": 1}


# ── band counts at the gate agree with what the gate would then do ───────────────────────────
def test_predicted_possible_count_equals_pending_when_under_the_display_cap():
    r = acc.run_node(["P", "Q", "B", "C", "M", "X"], "heuristic")
    pc = acc.payload(r.pre)["band_summary"]["company"]["predicted_counts"]
    assert pc["possible"] == len(acc.payload(r.pre)["pending_merge_candidates"]) == 4


def test_gate_payload_is_json_serialisable():
    import json

    r = acc.run_node(["P", "M", "X"], "fitted")
    json.dumps(acc.payload(r.pre))
    json.dumps(r.pre["resolution_stats"])


# ── no module-level mutable state added in src/mcp_server/tools ──────────────────────────────
def test_no_new_module_level_mutable_state_in_tools():
    import ast
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "src/mcp_server/tools/resolve_entities.py").read_text()
    old = subprocess.run(
        ["git", "show", "a1cff95~1:src/mcp_server/tools/resolve_entities.py"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    ).stdout

    def mut(text):
        out = set()
        for n in ast.parse(text).body:
            tgt = (
                n.targets
                if isinstance(n, ast.Assign)
                else [n.target]
                if isinstance(n, ast.AnnAssign)
                else []
            )
            val = getattr(n, "value", None)
            if isinstance(val, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp)):
                out |= {t.id for t in tgt if isinstance(t, ast.Name)}
        return out

    assert mut(src) <= mut(old), f"new module-level mutable: {mut(src) - mut(old)}"
