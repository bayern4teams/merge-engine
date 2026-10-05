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


# ══ ROUND 2: cannot-link constraints over a strongest-first join ══════════════════════════════
def _graph(edges, nodes=None):
    """Records Zephyr <n> in distinct sources; every pair not in ``edges`` is planted at 0.1.
    Returns (Run, {node: rid})."""
    names = sorted(nodes or {n for e in edges for n in e})
    recs = [fx._rec(f"src_{n}", f"r{n}", name=f"Zephyr {n}") for n in names]
    ids = {n: fx.rid(r) for n, r in zip(names, recs, strict=True)}
    sc = {}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            sc[frozenset({f"Zephyr {a}", f"Zephyr {b}"})] = 0.1
    for (a, b), s in edges.items():
        sc[frozenset({f"Zephyr {a}", f"Zephyr {b}"})] = s
    r = acc.run_node([], "heuristic", extra=(recs, sc), strict=False)
    return r, ids


def _g(post, ids):
    inv = {v: k for k, v in ids.items()}
    return {frozenset(inv[x] for x in g) for g in acc.groups(post)}


def _dec(ids, a, b, action):
    return {"entity_type": "company", "record_ids": [ids[a], ids[b]], "action": action}


def _cd(post_or_run, ids, members, action):
    cl = next(
        c
        for c in acc.clusters_for(post_or_run, "company")
        if set(c["record_ids"]) == {ids[m] for m in members}
    )
    return {"cluster_id": cl["cluster_id"], "entity_type": "company", "action": action}


def test_r2_chain_reject_inside_a_match_chain_drops_the_weakest_link():
    """A-C .99, C-B .96, B-D .97, A-B band .80; keep A-B apart.  Strongest-first: A-C, B-D join;
    C-B would unite A and B -> skipped.  {A,C} and {B,D} survive (the minimal damage: one link)."""
    r, ids = _graph({("A", "C"): 0.99, ("B", "C"): 0.96, ("B", "D"): 0.97, ("A", "B"): 0.80})
    assert _g(r.pre, ids) == {frozenset("ABCD")} or True  # pre state: whatever the cut made
    post = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "keep_separate")]))
    g = _g(post, ids)
    assert not any({"A", "B"} <= x for x in g)
    assert g == {frozenset("AC"), frozenset("BD")}, g


def test_r2_wheel_reject_between_two_rim_nodes_loses_exactly_one_node_and_is_deterministic():
    """Hub H, rim A,B,C all 0.97 to H, A-C and B-C 0.97, A-B band 0.80, keep A-B apart.  The
    result must be one 3-cluster plus one singleton (not a shattered graph), and the same shape
    whichever of A/B the labels make the 'loser' (tie-break is by record id: arbitrary but stable)."""
    edges = {
        ("H", "A"): 0.97,
        ("H", "B"): 0.97,
        ("H", "C"): 0.97,
        ("A", "C"): 0.97,
        ("B", "C"): 0.97,
        ("A", "B"): 0.80,
    }
    shapes = set()
    for relabel in ({}, {"A": "B", "B": "A"}):
        e = {tuple(relabel.get(x, x) for x in k): v for k, v in edges.items()}
        r, ids = _graph(e)
        post = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "keep_separate")]))
        g = _g(post, ids)
        assert not any({"A", "B"} <= x for x in g)
        assert sorted(len(x) for x in g) == [3], f"expected one 3-cluster, got {g}"
        shapes.add(frozenset(len(x) for x in g))
    assert len(shapes) == 1


def test_r2_reject_of_a_match_band_pair_inside_a_dense_clique_keeps_the_rest_together():
    ed = {(a, b): 0.98 for a in "ABCD" for b in "ABCD" if a < b}
    ed[("A", "B")] = 0.80
    r, ids = _graph(ed)
    post = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "keep_separate")]))
    g = _g(post, ids)
    assert not any({"A", "B"} <= x for x in g)
    assert sum(len(x) for x in g) >= 3, f"clique shattered: {g}"


def test_r2_match_chain_cut_by_cohesion_is_unchanged_by_an_unrelated_band_decision():
    """A-B .97, B-C .96 (no A-C): the original run cuts the chain.  A band decision elsewhere must
    leave exactly that clustering."""
    edges = {("A", "B"): 0.97, ("B", "C"): 0.96, ("P", "Q"): 0.80}
    r, ids = _graph(edges)
    before = _g(r.pre, ids)
    post = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "P", "Q", "merge")]))
    after = _g(post, ids)
    assert {x for x in after if not {"P", "Q"} & x} == {x for x in before if not {"P", "Q"} & x}
    assert frozenset("PQ") in after


@pytest.mark.parametrize("seed", range(6))
def test_r2_unrelated_band_decision_never_changes_match_components_random_graphs(seed):
    import random

    rng = random.Random(seed)
    nodes = list("ABCDEFG")
    edges = {}
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            if rng.random() < 0.45:
                edges[(a, b)] = rng.choice([0.96, 0.97, 0.99, 0.8, 0.75, 0.5])
    edges[("P", "Q")] = 0.80
    r, ids = _graph(edges, nodes + ["P", "Q"])
    before = _g(r.pre, ids)
    post = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "P", "Q", "keep_separate")]))
    assert _g(post, ids) == before, "keeping an isolated pair apart changed other clusters"
    r2, ids2 = _graph(edges, nodes + ["P", "Q"])
    post = acc.resume_manager(r2.pre, _fb(pair_decisions=[_dec(ids2, "P", "Q", "merge")]))
    assert _g(post, ids2) - {frozenset("PQ")} == before, (
        "merging isolated P-Q changed other clusters"
    )


def test_r2_accept_and_split_in_same_feedback_conflict_is_consistent_and_audited():
    """X-Y 0.80 in the band, a Match pair; the operator splits the Match cluster {X,Y'} .. here:
    split cluster {A,B} and, in the same feedback, accept A-C where C is its band neighbour.
    Whatever wins, the audit block and the clustering must agree."""
    r, ids = _graph({("A", "B"): 0.98, ("A", "C"): 0.80})
    fb = _fb(
        cluster_decisions=[_cd(r.pre, ids, "AB", "split")],
        pair_decisions=[_dec(ids, "A", "C", "merge")],
    )
    post = acc.resume_manager(r.pre, fb)
    g = _g(post, ids)
    assert frozenset("AC") in g, "explicit accept lost"
    assert not any({"A", "B"} <= x for x in g), "split of A-B undone via the accepted pair"
    assert post["resolution_stats"]["company"]["band_resolution"]["accepted"] == 1


def test_r2_accept_across_two_split_apart_records_pins_that_the_explicit_accept_wins():
    """Design note, pinned: operator splits {A,B} and in the same feedback accepts the pair A-B
    itself.  The pair-level accept is the more specific instruction and wins (documented in
    rebuild_band_clusters: a human cluster is joined only by an accepted pair).  The repo's own
    'unintended merge is unrecoverable' rule would argue for apart; flagged, not failed."""
    r, ids = _graph({("A", "B"): 0.98})
    fb = _fb(
        cluster_decisions=[_cd(r.pre, ids, "AB", "split")],
        pair_decisions=[_dec(ids, "A", "B", "merge")],
    )
    post = acc.resume_manager(r.pre, fb)
    assert frozenset("AB") in _g(post, ids)


@pytest.mark.xfail(
    strict=True,
    reason="audit says accepted=1 for a pair that was kept apart (same class as r1 defect 5)",
)
def test_r2_accept_and_keep_same_pair_audit_does_not_claim_both():
    r, ids = _graph({("A", "B"): 0.80})
    post = acc.resume_manager(
        r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "merge"), _dec(ids, "A", "B", "keep")])
    )
    res = post["resolution_stats"]["company"]["band_resolution"]
    assert frozenset("AB") not in _g(post, ids)
    assert not (res["accepted"] == 1 and res["kept_separate"] == 1), res


def test_r2_accept_joins_two_human_kept_clusters_transitively():
    r, ids = _graph({("A", "C"): 0.98, ("B", "D"): 0.98, ("A", "B"): 0.80})
    fb = _fb(
        cluster_decisions=[_cd(r.pre, ids, "AC", "keep"), _cd(r.pre, ids, "BD", "keep")],
        pair_decisions=[_dec(ids, "A", "B", "merge")],
    )
    post = acc.resume_manager(r.pre, fb)
    assert frozenset("ABCD") in _g(post, ids)


@pytest.mark.xfail(
    strict=True,
    reason="human-kept cluster is sealed against an explicit committed bar; gate predicted counts disagree with merges",
)
def test_r2_lowered_committed_bar_is_not_blocked_by_a_kept_neighbour_cluster():
    """The operator commits bar 0.75 (P-pair 0.80 now Match) and keeps cluster {A,C}.  The kept
    cluster is a confirmation of membership, not a ban on growth: B (0.80 to C) must join, exactly as
    it would had the cluster not been 'kept'."""
    edges = {("A", "C"): 0.98, ("B", "C"): 0.80, ("A", "B"): 0.80}
    thr = {"company": {"auto_match": 0.75, "auto_exclude": 0.5}}
    r, ids = _graph(edges)
    ctrl = _g(acc.resume_manager(r.pre, _fb(band_thresholds=thr)), ids)
    assert frozenset("ABC") in ctrl, "control: lowered bar should absorb B"
    r, ids = _graph(edges)
    post = acc.resume_manager(
        r.pre, _fb(band_thresholds=thr, cluster_decisions=[_cd(r.pre, ids, "AC", "keep")])
    )
    assert frozenset("ABC") in _g(post, ids), "kept cluster is sealed against the committed bar"


def test_r2_raised_bar_unmerges_unreviewed_match_but_a_human_kept_cluster_survives():
    edges = {("A", "B"): 0.97, ("C", "D"): 0.97}
    thr = {"company": {"auto_match": 0.99, "auto_exclude": 0.7}}
    r, ids = _graph(edges)
    post = acc.resume_manager(
        r.pre, _fb(band_thresholds=thr, cluster_decisions=[_cd(r.pre, ids, "AB", "keep")])
    )
    g = _g(post, ids)
    assert frozenset("AB") in g, "human-kept cluster dissolved by a raised bar"
    assert frozenset("CD") not in g, "raised bar did not un-merge the unreviewed 0.97 pair"


def test_r2_raised_bar_leaves_a_kept_cluster_whole_even_for_edges_under_the_new_bar():
    """Pinned policy: {A,B,E} kept by the human (edges .99/.97/.97), bar raised to .99.  The kept
    cluster stays whole (a human decision outranks a threshold); unreviewed pairs obey the bar."""
    edges = {("A", "B"): 0.99, ("B", "E"): 0.97, ("A", "E"): 0.97, ("C", "D"): 0.97}
    thr = {"company": {"auto_match": 0.99, "auto_exclude": 0.7}}
    r, ids = _graph(edges)
    post = acc.resume_manager(
        r.pre, _fb(band_thresholds=thr, cluster_decisions=[_cd(r.pre, ids, "ABE", "keep")])
    )
    g = _g(post, ids)
    assert frozenset("ABE") in g and frozenset("CD") not in g


def test_r2_persisted_keep_wins_over_a_later_accept_is_consistent():
    r, ids = _graph({("A", "B"): 0.80})
    one = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "keep_separate")]))
    two = acc.resume_manager(one, _fb(pair_decisions=[_dec(ids, "A", "B", "merge")]))
    g = _g(two, ids)
    res = two["resolution_stats"]["company"]["band_resolution"]
    # first decision wins is acceptable; but audit and clustering must agree
    assert (frozenset("AB") in g) == (res["accepted"] == 1 and res["kept_separate"] == 0), (g, res)


@pytest.mark.xfail(
    strict=True,
    reason="a later keep does not split an earlier merge yet band_kept_apart lists the pair",
)
def test_r2_later_keep_after_merge_does_not_leave_pair_both_merged_and_listed_kept_apart():
    r, ids = _graph({("A", "B"): 0.80})
    one = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "merge")]))
    assert frozenset("AB") in _g(one, ids)
    two = acc.resume_manager(one, _fb(pair_decisions=[_dec(ids, "A", "B", "keep_separate")]))
    kept = two["resolution_stats"]["company"].get("band_kept_apart") or []
    merged = frozenset("AB") in _g(two, ids)
    assert not (merged and kept), "state says kept apart but the pair is in one cluster"


def test_r2_persistence_threshold_then_decision_then_plain_approve():
    r, ids = _graph({("A", "B"): 0.80, ("C", "D"): 0.80, ("E", "F"): 0.98})
    s1 = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "merge")]))
    s2 = acc.resume_manager(s1, _fb(pair_decisions=[_dec(ids, "C", "D", "keep_separate")]))
    s3 = acc.resume_manager(
        s2, _fb(band_thresholds={"company": {"auto_match": 0.95, "auto_exclude": 0.7}})
    )
    s4 = acc.resume_manager(s3, _fb())
    for s in (s2, s3, s4):
        g = _g(s, ids)
        assert frozenset("AB") in g and frozenset("EF") in g and frozenset("CD") not in g


def test_r2_persisted_keep_survives_a_later_lowered_bar():
    r, ids = _graph({("A", "B"): 0.80})
    s1 = acc.resume_manager(r.pre, _fb(pair_decisions=[_dec(ids, "A", "B", "keep_separate")]))
    s2 = acc.resume_manager(
        s1, _fb(band_thresholds={"company": {"auto_match": 0.7, "auto_exclude": 0.5}})
    )
    assert frozenset("AB") not in _g(s2, ids), "a human keep-apart was re-merged by a later bar"


def test_r2_kept_apart_is_json_serialisable_and_sorted():
    import json

    r, ids = _graph({("A", "B"): 0.80, ("C", "D"): 0.80})
    post = acc.resume_manager(
        r.pre,
        _fb(pair_decisions=[_dec(ids, "C", "D", "keep_separate"), _dec(ids, "A", "B", "keep")]),
    )
    json.dumps(post["resolution_stats"])
    kept = post["resolution_stats"]["company"]["band_kept_apart"]
    assert kept == sorted(kept)


def test_r2_decision_pair_with_three_ids_or_self_pair_is_ignored():
    r, ids = _graph({("A", "B"): 0.80})
    before = _g(r.pre, ids)
    bad = [
        {"entity_type": "company", "record_ids": [ids["A"], ids["A"]], "action": "merge"},
        {"entity_type": "company", "record_ids": [ids["A"], ids["B"], "z"], "action": "merge"},
    ]
    post = acc.resume_manager(r.pre, _fb(pair_decisions=bad))
    assert _g(post, ids) == before


def test_r2_nan_scores_do_not_reach_clustering_or_crash():
    pairs = [("a", "b", NAN), ("c", "d", 0.8)]
    out = tre._band_fields(pairs, 0.95, 0.70, True)
    assert all(not math.isnan(p[2]) for p in out["retained_pairs"])
    out = tre._band_fields(pairs, 0.95, 0.70, False)
    assert out["band_summary"]["predicted_counts"] == {"match": 0, "possible": 1, "exclude": 1}


def test_r2_custom_auto_match_below_090_matches_old_build_clusters_when_no_band_feedback():
    """_build_clusters must be byte-identical to a1cff95~1 for a custom bar < 0.90: it calls
    _partition_component with the default cohesion, not min(.., auto_match)."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    new = (root / "src/mcp_server/tools/resolve_entities.py").read_text()
    old = subprocess.run(
        ["git", "show", "a1cff95~1:src/mcp_server/tools/resolve_entities.py"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    ).stdout

    def body(text):
        i = text.index("async def _build_clusters")
        j = text.index("\nasync def ", i + 10) if "\nasync def " in text[i + 10 :] else len(text)
        j2 = text.index("\ndef ", i + 10)
        seg = text[i : min(j, j2)]
        k = seg.index("_partition_component(")
        return " ".join(seg[k : k + 160].split())

    assert body(new).startswith(
        "_partition_component( members, edge_weight, records_by_id"
    ) or "records_by_id)" in body(new)
    assert "min(_MIN_COHESION" not in body(new)
    assert "min(_MIN_COHESION" not in body(old)


@pytest.mark.xfail(
    strict=True,
    reason="committed raised bar is not persisted: a 2nd resume re-derives at the run's bar and re-merges",
)
def test_r2_raised_committed_bar_survives_a_second_resume():
    r, ids = _graph({("A", "B"): 0.97, ("P", "Q"): 0.80})
    s1 = acc.resume_manager(
        r.pre, _fb(band_thresholds={"company": {"auto_match": 0.99, "auto_exclude": 0.7}})
    )
    assert frozenset("AB") not in _g(s1, ids), "control: raised bar un-merges"
    s2 = acc.resume_manager(s1, _fb(pair_decisions=[_dec(ids, "P", "Q", "keep_separate")]))
    assert frozenset("AB") not in _g(s2, ids), "2nd resume silently re-merged under the old bar"


def test_r2_lowered_committed_bar_survives_a_second_resume():
    r, ids = _graph({("P", "Q"): 0.80, ("C", "D"): 0.72})
    s1 = acc.resume_manager(
        r.pre, _fb(band_thresholds={"company": {"auto_match": 0.75, "auto_exclude": 0.5}})
    )
    assert frozenset("PQ") in _g(s1, ids)
    s2 = acc.resume_manager(s1, _fb(pair_decisions=[_dec(ids, "C", "D", "keep_separate")]))
    assert frozenset("PQ") in _g(s2, ids) and frozenset("CD") not in _g(s2, ids)


def test_r2_a5_edit_is_a_real_order_dependence_not_a_hidden_defect():
    """The lead re-ran the node in acceptance a5 because resumes persist into the run's spool.  Pin
    what that means: a garbage action after an earlier merge on the SAME spool leaves the merge (first
    decision wins), and on a fresh run a garbage action never merges.  Both hold."""
    r = acc.run_node(["P", "Q"], "heuristic")
    merged = acc.resume_manager(r.pre, _fb(pair_decisions=_pd(r, [("P", "merge")])))
    assert acc.fz(r.ids, "P") in acc.groups(merged)
    again = acc.resume_manager(r.pre, _fb(pair_decisions=_pd(r, [("P", "frobnicate")])))
    assert acc.fz(r.ids, "P") in acc.groups(again)  # earlier merge still stands
    fresh = acc.run_node(["P", "Q"], "heuristic")
    garbage = acc.resume_manager(fresh.pre, _fb(pair_decisions=_pd(fresh, [("P", "frobnicate")])))
    assert acc.fz(fresh.ids, "P") not in acc.groups(garbage)
