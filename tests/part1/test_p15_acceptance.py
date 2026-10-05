"""P15 acceptance: consume the review band (derived from the report's Evidence + Fix text only; no P15 code seen).

Report P15: scores in [0.70, 0.95) are neither merged nor recorded.  Fix: score < 0.70 -> Exclude;
0.70 <= score < 0.95 -> POSSIBLE MATCH -> the existing HITL gate; score >= 0.95 -> Match.  Stop treating
0.70/0.95 as arbitrary: show the log m/u distribution and the PREDICTED MATCH COUNTS at the gate and let
the operator COMMIT thresholds (defaults stay 0.95/0.70).  "In Part 1 the band is routed to the existing
gate and resolved within the run."  Plan 2.6: interrupt_before means a gate node's body never runs on
resume -- only the router does -- so the payload must be built from state and the decision applied where
the resume really happens (WorkflowManager.resume_workflow -> update_state(as_node=...)).

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p15_acceptance.py -q -p no:cacheprovider

HOW THE SCORE IS CONTROLLED: the band logic consumes a pair's FINAL score, however produced.  The tests wrap
``_score_pair`` (module global, looked up by ``_resolve_core`` at call time) and plant the score of named
pairs; every other pair keeps its natural score.  A ledger of every pair's final score is the independent
ground truth for band counts.  Two paths: "heuristic" (< 30 candidate pairs, no model) and "fitted" (the
p6 fillers push candidate pairs past _MIN_PAIRS_FOR_MODEL; the harness asserts a model really fitted).
A third engine (Splink, scoring patched) is exercised where the band logic is shared.

ADAPTATION POINTS (the report fixes semantics, not key names; one place each):
  SHAPES / threshold_feedbacks   how the operator's decisions are shaped in ``human_feedback``.  The report
                                 does not name keys, so several plausible shapes are tried and a test passes if
                                 ANY shape yields the OUTCOME (merged / apart / resolved).  Pin one with
                                 P15_SHAPE=<name> once the implementation has chosen.  Outcomes, not keys, are asserted.
  band_counts / histograms       find the per-band counts and the distribution anywhere in the gate payload
                                 (regexes below), because the report does not fix the payload layout.
  payload "pending_merge_candidates"   the EXISTING gate key (human_nodes._build_matches_interrupt) is relied on
                                 as the list of still-undecided band pairs; it must be empty once resolved.
  Resume harness                 resume_manager() drives the real WorkflowManager.resume_workflow with a mocked
                                 graph and reads the dict handed to graph.update_state(as_node="human_review_matches").
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.orchestrator.spool as spool_mod
from src.mcp_server.tools import resolve_entities as tre
from src.orchestrator.nodes import human_nodes as hn
from src.orchestrator.spool import clusters_for
from src.orchestrator.workflow_manager import WorkflowManager
from tests.part1 import p15_fixtures as fx

node = importlib.import_module("src.orchestrator.nodes.resolve_entities")
ROOT = Path(__file__).resolve().parents[2]

SCORE = {
    "P": 0.80,
    "Q": 0.85,
    "M": 0.98,
    "X": 0.40,
    "R": 0.96,
    "E": 0.6996,
    "B": 0.70,
    "C": 0.9496,
    "D": 0.95,
}
PATHS = ["heuristic", "fitted"]


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


# ── harness: run the real node with planted scores ───────────────────────────────────────────
class Run:
    def __init__(self, pre, ids, ledger, fitted):
        self.pre, self.ids, self.ledger, self.fitted = pre, ids, ledger, fitted


def _band(score, am=0.95, ae=0.70):
    """The report's table, written independently of the code under test."""
    return "match" if score >= am else ("possible" if score >= ae else "exclude")


def run_node(keys, path="heuristic", scores=None, rules=None, extra=None, engine=None):
    """Run node.resolve_entities on the planted pairs; return a Run (pre-gate state)."""
    scores = {**SCORE, **(scores or {})}
    recs, ids = fx.planted(keys)
    if extra:
        recs = recs + extra[0]
    if path == "fitted":
        recs = fx.with_fillers(recs)
    planted_scores = {frozenset(fx.NAMES[k]): scores[k] for k in keys}
    if extra:
        planted_scores.update(extra[1])
    ledger, fitted = {}, []
    orig_score, orig_fit = tre._score_pair, tre._fit_match_model

    def spy_score(r1, r2, *a, **k):
        s = planted_scores.get(frozenset({r1["name"], r2["name"]}))
        s = orig_score(r1, r2, *a, **k) if s is None else s
        ledger[frozenset({fx.rid(r1), fx.rid(r2)})] = s
        return s

    def spy_fit(*a, **k):
        r = orig_fit(*a, **k)
        fitted.append(r[0] is not None)
        return r

    state = {
        "run_id": "p15run",
        "target_entities": ["company"],
        "extracted_data": {"company": recs},
        "entity_clusters": {},
        "errors": [],
    }
    if rules is not None:
        state["matching_rules"] = {"company": rules}
    ctx = [
        patch.object(tre, "_score_pair", spy_score),
        patch.object(tre, "_fit_match_model", spy_fit),
    ]
    if engine == "splink":
        by_name = {}
        for r in recs:
            by_name.setdefault(r["name"], []).append(fx.rid(r))
        sp_pairs = []
        for names, s in planted_scores.items():
            a, b = sorted(names)
            sp_pairs.append((by_name[a][0], by_name[b][0], s))
            ledger[frozenset({by_name[a][0], by_name[b][0]})] = s
        state["matching_rules"] = {"company": {**(rules or {}), "engine": "splink"}}
        ctx.append(
            patch(
                "src.mcp_server.tools.splink_matcher.score_pairs_with_splink",
                AsyncMock(return_value=sp_pairs),
            )
        )
    for c in ctx:
        c.start()
    try:
        out = asyncio.run(node.resolve_entities(state))
    finally:
        for c in ctx:
            c.stop()
    if engine != "splink":
        assert bool(fitted and fitted[0]) == (path == "fitted"), (
            f"harness: wanted {path}, fitted={fitted}"
        )
    return Run({**state, **out}, ids, ledger, bool(fitted and fitted[0]))


def groups(post, entity="company"):
    return {
        frozenset(c["record_ids"]) for c in clusters_for(post, entity) if len(c["record_ids"]) > 1
    }


def payload(post):
    return hn._build_matches_interrupt(post)["data"]


def pending_pairs(post):
    return {frozenset(c["record_ids"]) for c in payload(post).get("pending_merge_candidates", [])}


def fz(ids, key):
    return frozenset(ids[key])


# ── resume harness: the REAL WorkflowManager.resume_workflow, mocked graph ───────────────────
def resume_manager(pre, feedback):
    with patch("src.orchestrator.workflow_manager.build_graph") as mb:
        mb.return_value = MagicMock()
        mgr = WorkflowManager()
    tid = "p15thread"
    mgr.graph.get_state.return_value = MagicMock(values=dict(pre), next=())
    mgr._active_workflows[tid] = {
        "config": {"configurable": {"thread_id": tid}},
        "current_interrupt_node": "human_review_matches",
        "status": "interrupted",
        "interrupt_data": None,
    }
    mgr._continue_workflow = lambda *a, **k: None
    mgr.resume_workflow(tid, feedback)
    call = mgr.graph.update_state.call_args
    assert call is not None, f"resume never reached update_state: {mgr._active_workflows[tid]}"
    assert call.kwargs.get("as_node") == "human_review_matches"
    return {**pre, **call.args[1]}


def _pd(entity, items):
    return [{"entity_type": entity, "record_ids": list(i), "action": a} for i, a in items]


# name -> builder(entity, [(pair_ids, "merge"|"keep")]) -> human_feedback
SHAPES = {
    "pair_decisions": lambda e, items: {
        "decision": "approve",
        "approved": True,
        "pair_decisions": _pd(
            e, [(i, "merge" if a == "merge" else "keep_separate") for i, a in items]
        ),
    },
    "cluster_decisions_vocab": lambda e, items: {
        "decision": "approve",
        "approved": True,
        "cluster_decisions": _pd(e, [(i, "merge" if a == "merge" else "split") for i, a in items]),
    },
    "pending_decisions": lambda e, items: {
        "decision": "approve",
        "approved": True,
        "pending_decisions": [
            {
                "entity_type": e,
                "record_ids": list(i),
                "decision": "accept" if a == "merge" else "reject",
            }
            for i, a in items
        ],
    },
}
THRESHOLD_SHAPES = {
    "band_thresholds": lambda e, m, x: {
        "band_thresholds": {e: {"auto_match": m, "auto_exclude": x}}
    },
    "thresholds": lambda e, m, x: {"thresholds": {e: {"auto_match": m, "auto_exclude": x}}},
    "flat": lambda e, m, x: {"auto_match": m, "auto_exclude": x},
    "matching_rules": lambda e, m, x: {"matching_rules": {e: {"auto_match": m, "auto_exclude": x}}},
    "band_flat": lambda e, m, x: {"band": {"auto_match": m, "auto_exclude": x}},
}


def _approve(extra):
    return {"decision": "approve", "approved": True, **extra}


def any_shape(pre, feedbacks, check):
    """Pass if ANY shape produces an outcome that satisfies ``check(post)`` (AssertionError = no)."""
    pin = os.environ.get("P15_SHAPE")
    errs = {}
    for name, fb in feedbacks.items():
        if pin and name != pin:
            continue
        try:
            post = resume_manager(pre, fb)
            check(post)
            return post
        except AssertionError as exc:
            errs[name] = str(exc)[:200]
    pytest.fail("no feedback shape produced the expected outcome: " + json.dumps(errs, indent=1))


def decisions(entity, items):
    return {n: b(entity, items) for n, b in SHAPES.items()}


def thresholds(entity, m, x):
    return {n: _approve(b(entity, m, x)) for n, b in THRESHOLD_SHAPES.items()}


# ── payload readers (layout is not fixed by the report) ──────────────────────────────────────
POSSIBLE_RE = re.compile(r"possible|review|clerical|uncertain|pending|borderline|band", re.I)
EXCLUDE_RE = re.compile(r"exclu|non_?match|unmatch|reject|separate|no_?match|distinct", re.I)
MATCH_RE = re.compile(r"match|merge", re.I)


def _int_of(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, dict):
        for k in ("count", "pairs", "n", "predicted", "total"):
            if isinstance(v.get(k), int) and not isinstance(v.get(k), bool):
                return v[k]
    return None


def _walk(o):
    yield o
    if isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def band_counts(data):
    """(match, possible, exclude) predicted counts at the default thresholds, wherever the payload keeps them."""
    for d in _walk(data):
        if isinstance(d, dict):
            cls = {}
            for k, v in d.items():
                n = _int_of(v)
                if n is None:
                    continue
                if POSSIBLE_RE.search(k):
                    cls.setdefault("p", n)
                elif EXCLUDE_RE.search(k):
                    cls.setdefault("x", n)
                elif MATCH_RE.search(k):
                    cls.setdefault("m", n)
            if len(cls) == 3:
                return cls["m"], cls["p"], cls["x"]
        if isinstance(d, list) and d and all(isinstance(i, dict) for i in d):
            cls = {}
            for i in d:
                label = next((v for v in i.values() if isinstance(v, str)), "")
                n = next((_int_of(v) for v in i.values() if _int_of(v) is not None), None)
                if n is None:
                    continue
                if POSSIBLE_RE.search(label):
                    cls.setdefault("p", n)
                elif EXCLUDE_RE.search(label):
                    cls.setdefault("x", n)
                elif MATCH_RE.search(label):
                    cls.setdefault("m", n)
            if len(cls) == 3:
                return cls["m"], cls["p"], cls["x"]
    return None


def _nums(o):
    out = []
    for v in o if isinstance(o, list) else []:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def histograms(data):
    """Yield (counts, axis_numbers, context_text) for every histogram-looking structure in the payload."""
    for d in _walk(data):
        if isinstance(d, dict):
            for k, v in d.items():
                if (
                    re.search(r"count|bin|hist|freq", k, re.I)
                    and isinstance(v, list)
                    and len(v) >= 3
                    and all(isinstance(i, int) and not isinstance(i, bool) for i in v)
                ):
                    axis = []
                    for k2, v2 in d.items():
                        if (
                            k2 != k
                            and isinstance(v2, list)
                            and _nums(v2)
                            and len(_nums(v2)) == len(v2)
                        ):
                            axis += _nums(v2)
                    yield list(v), axis, json.dumps(d, default=str)
            ints = {k: _int_of(v) for k, v in d.items()}
            if len(d) >= 3 and all(i is not None for i in ints.values()):
                axis = []
                for k in d:
                    try:
                        axis.append(float(k))
                    except ValueError:
                        pass
                yield list(ints.values()), axis, json.dumps(list(d), default=str)
        if isinstance(d, list) and len(d) >= 3 and all(isinstance(i, dict) for i in d):
            counts, axis = [], []
            for i in d:
                n = next(
                    (
                        v
                        for k, v in i.items()
                        if re.search(r"count|^n$|pairs|freq", k, re.I) and _int_of(v) is not None
                    ),
                    None,
                )
                if n is None:
                    counts = None
                    break
                counts.append(_int_of(n))
                axis += [
                    float(v)
                    for k, v in i.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and _int_of(v) != n
                ]
            if counts:
                yield counts, axis, json.dumps(d[:2], default=str)


def distribution(data, n_pairs):
    for counts, axis, ctx in histograms(data):
        if sum(counts) == n_pairs:
            return counts, axis, ctx
    return None


def numbers_in(data):
    out = set()
    for d in _walk(data):
        if (isinstance(d, (int, float)) and not isinstance(d, bool)) or (
            isinstance(d, str) and re.fullmatch(r"\d*\.\d+", d.strip())
        ):
            out.add(round(float(d), 6))
    return out


def expected_counts(ledger, am=0.95, ae=0.70):
    c = {"match": 0, "possible": 0, "exclude": 0}
    for s in ledger.values():
        c[_band(s, am, ae)] += 1
    return c["match"], c["possible"], c["exclude"]


# ══ A. A band pair produces a gate item and is RESOLVED within the run ═══════════════════════
@pytest.mark.parametrize("path", PATHS)
def test_a1_band_pair_surfaces_at_gate_and_is_not_merged(path):
    r = run_node(["P", "M", "X"], path)
    assert fz(r.ids, "P") in pending_pairs(r.pre), "the ~0.8 pair is not a gate item"
    assert fz(r.ids, "M") not in pending_pairs(r.pre) and fz(r.ids, "X") not in pending_pairs(r.pre)
    assert fz(r.ids, "P") not in groups(r.pre), (
        "a Possible Match must not merge before a human decides"
    )
    conf = {
        frozenset(c["record_ids"]): c["confidence"]
        for c in payload(r.pre)["pending_merge_candidates"]
    }
    assert math.isclose(conf[fz(r.ids, "P")], 0.80, abs_tol=1e-3)
    assert node.has_low_confidence_clusters(r.pre) is True


@pytest.mark.parametrize("path", PATHS)
def test_a2_accept_merges_within_the_run(path):
    r = run_node(["P", "M", "X"], path)

    def check(post):
        assert fz(r.ids, "P") in groups(post), "accepted band pair is not in a merged cluster"
        assert fz(r.ids, "M") in groups(post), "the Match pair lost its merge"
        assert fz(r.ids, "X") not in groups(post)
        assert not pending_pairs(post), (
            f"band pair still pending after the decision: {pending_pairs(post)}"
        )
        assert node.has_low_confidence_clusters(post) is False, (
            "the gate would fire again: not resolved in-run"
        )

    any_shape(r.pre, decisions("company", [(r.ids["P"], "merge")]), check)


@pytest.mark.parametrize("path", PATHS)
def test_a3_reject_keeps_apart_and_resolves(path):
    r = run_node(["P", "M", "X"], path)

    def check(post):
        assert fz(r.ids, "P") not in groups(post)
        assert fz(r.ids, "M") in groups(post)
        assert not pending_pairs(post), "rejected pair is still listed as pending (not resolved)"
        assert node.has_low_confidence_clusters(post) is False

    any_shape(r.pre, decisions("company", [(r.ids["P"], "keep")]), check)


@pytest.mark.parametrize("path", PATHS)
def test_a4_decisions_are_per_pair_not_all_or_nothing(path):
    r = run_node(["P", "Q", "M"], path)
    both = [(r.ids["P"], "merge"), (r.ids["Q"], "keep")]

    def check(post):
        assert fz(r.ids, "P") in groups(post)
        assert fz(r.ids, "Q") not in groups(post), "reject of Q was ignored (approve-all behaviour)"
        assert not pending_pairs(post)

    any_shape(r.pre, decisions("company", both), check)


@pytest.mark.parametrize("path", PATHS)
def test_a5_undecided_and_unrecognised_decisions_never_merge(path):
    """An unintended merge is not recoverable (cluster_edits' own rule): undecided or garbage -> apart."""
    r = run_node(["P", "Q"], path)
    only_p = decisions("company", [(r.ids["P"], "merge")])
    pin = os.environ.get("P15_SHAPE")
    ok = False
    for name, fb in only_p.items():
        if pin and name != pin:
            continue
        post = resume_manager(r.pre, fb)
        if fz(r.ids, "P") in groups(post):  # this shape is understood
            ok = True
            assert fz(r.ids, "Q") not in groups(post), f"{name}: undecided Q merged"
    assert ok, "no shape merged P, cannot test the undecided case (see test_a2)"
    garbage = decisions("company", [(r.ids["P"], "merge")])
    for fb in garbage.values():
        for key in ("pair_decisions", "cluster_decisions", "pending_decisions"):
            for d in fb.get(key, []):
                d["action" if "action" in d else "decision"] = "frobnicate"
        post = resume_manager(r.pre, fb)
        assert fz(r.ids, "P") not in groups(post), "an unrecognised action merged a pair"


def test_a6_splink_engine_band_pair_is_a_gate_item_and_resolvable():
    r = run_node(["P", "M", "X"], "heuristic", engine="splink")
    assert fz(r.ids, "P") in pending_pairs(r.pre), "Splink path: band pair is not a gate item"
    assert fz(r.ids, "P") not in groups(r.pre)

    def check(post):
        assert fz(r.ids, "P") in groups(post) and fz(r.ids, "M") in groups(post)
        assert not pending_pairs(post)

    any_shape(r.pre, decisions("company", [(r.ids["P"], "merge")]), check)


# ══ B. Match / Exclude unchanged; the band's edges are exactly the report's ══════════════════
@pytest.mark.parametrize("path", PATHS)
def test_b1_match_and_exclude_behaviour_unchanged(path):
    r = run_node(["M", "X", "R"], path)
    merged = groups(r.pre)
    assert {fz(r.ids, "M"), fz(r.ids, "R")} <= merged, "Match (>=0.95) pairs must merge"
    assert fz(r.ids, "X") not in merged, "an Exclude pair must stay apart"
    if path == "heuristic":
        assert merged == {fz(r.ids, "M"), fz(r.ids, "R")}
    assert not pending_pairs(r.pre), "a run with only Match/Exclude pairs must have no band items"
    assert node.has_low_confidence_clusters(r.pre) is False, (
        "no band pair: the gate must still be skipped"
    )
    st = r.pre["resolution_stats"]["company"]
    assert st["auto_matched"] >= 2 and st["auto_excluded"] >= 1
    assert not st.get("pending_merge_candidates")
    assert all(not c.get("required_human_review") for c in clusters_for(r.pre, "company"))


def test_b2_band_edges_are_the_reports_table():
    """0.70 <= s < 0.95 is Possible: 0.70 is IN, 0.6996 is OUT, 0.9496 is IN, 0.95 is OUT (Match)."""
    pend = lambda s: [p["record_ids"] for p in tre._pending_merge_candidates([("a", "b", s)])]  # noqa: E731
    assert pend(0.70) == [["a", "b"]], (
        "0.70 is the band's inclusive lower bound (report: 0.70 <= score)"
    )
    assert pend(0.6996) == []
    assert pend(0.9496) == [["a", "b"]], "banding must use the unrounded score"
    assert pend(0.95) == []
    assert pend(0.99) == [] and pend(0.10) == []


@pytest.mark.parametrize("path", PATHS)
def test_b2b_band_edges_end_to_end(path):
    r = run_node(["E", "B", "C", "D"], path)
    assert pending_pairs(r.pre) == {fz(r.ids, "B"), fz(r.ids, "C")}, pending_pairs(r.pre)
    assert groups(r.pre) == {fz(r.ids, "D")}


def test_b3_defaults_stay_095_and_070():
    assert tre._AUTO_MATCH == 0.95 and tre._AUTO_EXCLUDE == 0.70
    assert tre._thresholds_for(None) == (0.95, 0.70) and tre._thresholds_for({}) == (0.95, 0.70)
    assert tre._thresholds_for({"strictness": "loose"}) == (0.90, 0.70)
    assert tre._thresholds_for({"strictness": "strict"}) == (0.98, 0.70)
    assert tre._thresholds_for({"auto_match": 0.9, "strictness": "strict"}) == (0.9, 0.70)
    assert tre._thresholds_for({"auto_match": 0.6}) == (0.95, 0.70), (
        "match bar inside the band must fall back"
    )
    assert tre._thresholds_for({"auto_match": 0.9, "auto_exclude": 0.8}) == (0.9, 0.8)
    importlib.import_module(
        "src.mcp_server.tools.splink_matcher"
    )  # still imports _pending_merge_candidates/_thresholds_for


@pytest.mark.parametrize("path", PATHS)
def test_b4_strictness_moves_the_match_bar_per_entity(path):
    r = run_node(["R", "Q", "P"], path, scores={"R": 0.92}, rules={"strictness": "loose"})
    assert fz(r.ids, "R") in groups(r.pre), "0.92 merges under 'loose' (0.90)"
    assert pending_pairs(r.pre) == {fz(r.ids, "Q"), fz(r.ids, "P")}
    nums = numbers_in(payload(r.pre))
    assert 0.9 in nums, "the effective (per-entity) match bar must be shown, not the module default"
    assert 0.95 not in nums


# ══ C. The gate payload shows the distribution and predicted counts per band ═════════════════
@pytest.mark.parametrize("path", PATHS)
def test_c1_predicted_counts_per_band_equal_the_scored_pairs(path):
    r = run_node(["P", "Q", "M", "X"], path)
    got = band_counts(payload(r.pre))
    assert got is not None, "no (match, possible, exclude) counts anywhere in the gate payload"
    assert got == expected_counts(r.ledger), (
        f"payload {got} != independent count {expected_counts(r.ledger)}"
    )
    assert got[1] == 2


def test_c2_counts_come_from_the_data_not_constants():
    a = run_node(["P", "M", "X"], "heuristic")
    b = run_node(["P", "Q", "B", "M", "R", "X", "E"], "heuristic")
    ca, cb = band_counts(payload(a.pre)), band_counts(payload(b.pre))
    assert ca == expected_counts(a.ledger) and cb == expected_counts(b.ledger)
    assert ca != cb and cb == (2, 3, 2)


@pytest.mark.parametrize("path", PATHS)
def test_c3_distribution_covers_every_scored_pair(path):
    r = run_node(["P", "Q", "M", "X", "E"], path)
    got = distribution(payload(r.pre), len(r.ledger))
    assert got is not None, (
        f"no histogram in the payload whose bins sum to the {len(r.ledger)} scored pairs "
        f"(every candidate pair, not only the band)"
    )
    counts, axis, ctx = got
    assert len(counts) >= 3 and all(c >= 0 for c in counts)
    assert sum(1 for c in counts if c) >= 2, "all pairs in one bin: not a distribution"
    if path == "fitted":
        assert re.search(r"log|weight|m\s*/\s*u|bits", ctx, re.I), (
            "fitted path: distribution is not labelled log m/u"
        )
        assert axis and min(axis) < 0 < max(axis), (
            f"log m/u axis must span negative and positive: {axis[:6]}"
        )


def test_c3b_distribution_changes_with_the_data():
    a = run_node(["P", "M", "X"], "heuristic")
    b = run_node(["P", "Q", "B", "M", "R", "X", "E"], "heuristic")
    da, db = (
        distribution(payload(a.pre), len(a.ledger)),
        distribution(payload(b.pre), len(b.ledger)),
    )
    assert da and db and da[0] != db[0], "same histogram for different data: a constant"


@pytest.mark.parametrize("path", PATHS)
def test_c4_the_two_thresholds_are_shown_and_not_hardcoded(path):
    r = run_node(["P", "M", "X"], path)
    nums = numbers_in(payload(r.pre))
    assert 0.95 in nums and 0.7 in nums, (
        "the gate must show the thresholds the counts were computed at"
    )
    r2 = run_node(["P", "M", "X"], path, rules={"auto_match": 0.9, "auto_exclude": 0.6})
    n2 = numbers_in(payload(r2.pre))
    assert 0.9 in n2 and 0.6 in n2 and 0.95 not in n2, (
        "shown thresholds ignore the entity's own rules"
    )


def test_c5_more_band_pairs_than_the_display_cap_are_counted_and_all_resolvable():
    cap = tre._MAX_PENDING_CANDIDATES
    n = cap + 10
    recs, ids, names = fx.many_pairs(n)
    r = run_node([], "heuristic", extra=(recs, {k: 0.80 for k in names}))
    assert band_counts(payload(r.pre))[1] == n, (
        "the band count must be the real count, not the display cap"
    )

    def check(post):
        assert len(groups(post)) == n, (
            f"{len(groups(post))} of {n} band pairs resolved: the overflow was dropped"
        )

    any_shape(r.pre, thresholds("company", 0.75, 0.70), check)


def test_c6_old_state_and_empty_runs_do_not_crash_the_payload():
    old = {  # a checkpoint written before P15: stats without any band fields, no scores
        "entity_clusters": {"company": []},
        "resolution_stats": {"company": {"auto_matched": 0, "pending_merge_candidates": []}},
    }
    assert payload(old)["pending_merge_candidates"] == []
    assert payload({})["low_confidence_clusters"] == []
    one = {
        "run_id": "p15one",
        "target_entities": ["company"],
        "entity_clusters": {},
        "errors": [],
        "extracted_data": {"company": [fx.fx._rec("src_a", "1", name="Solo Corp")]},
    }
    out = asyncio.run(node.resolve_entities(one))
    assert payload({**one, **out})["pending_merge_candidates"] == []
    assert node.has_low_confidence_clusters({**one, **out}) is False


# ══ D. The operator can commit different thresholds at the gate ══════════════════════════════
@pytest.mark.parametrize("path", PATHS)
def test_d1_lowering_the_match_bar_merges_the_band_pair(path):
    r = run_node(["P", "R", "X"], path)

    def check(post):
        assert fz(r.ids, "P") in groups(post), (
            "0.80 pair is not merged under a committed match bar of 0.75"
        )
        assert fz(r.ids, "R") in groups(post) and fz(r.ids, "X") not in groups(post)
        assert not pending_pairs(post)

    any_shape(r.pre, thresholds("company", 0.75, 0.70), check)


@pytest.mark.parametrize("path", PATHS)
def test_d2_raising_the_exclude_bar_excludes_the_band_pair(path):
    r = run_node(["P", "R", "X"], path)

    def check(post):
        assert fz(r.ids, "P") not in groups(post)
        assert fz(r.ids, "R") in groups(post), "raising the exclude bar must not unmerge a Match"
        assert not pending_pairs(post), (
            "0.80 < committed exclude bar 0.85: it is Exclude, no longer a gate item"
        )

    any_shape(r.pre, thresholds("company", 0.95, 0.85), check)


@pytest.mark.parametrize("path", PATHS)
def test_d3_raising_the_match_bar_never_leaves_a_silent_merge(path):
    r = run_node(["R", "P", "M"], path)  # R=0.96 merged under the defaults, M=0.98

    def check(post):
        assert fz(r.ids, "M") in groups(post), "0.98 stays a Match under a 0.97 bar"
        assert fz(r.ids, "R") not in groups(post), (
            "0.96 < committed bar 0.97 but the pair is still merged without a human decision"
        )

    any_shape(r.pre, thresholds("company", 0.97, 0.70), check)


@pytest.mark.parametrize("path", PATHS)
def test_d4_nonsense_thresholds_are_refused_not_applied(path):
    """A bar inside/below the exclude bar must fall back (same rule as _thresholds_for), never merge the band."""
    r = run_node(["P", "M", "X"], path)
    for name, fb in thresholds("company", 0.5, 0.9).items():
        post = resume_manager(r.pre, fb)
        assert fz(r.ids, "P") not in groups(post), (
            f"{name}: inverted thresholds merged the 0.80 pair"
        )
        assert fz(r.ids, "M") in groups(post)
    for name, fb in thresholds("company", "high", None).items():
        post = resume_manager(r.pre, fb)  # must not raise
        assert fz(r.ids, "P") not in groups(post)


# ══ E. Resume trap (plan 2.6): payload from state, decision applied where the resume happens ═
def test_e1_payload_is_a_pure_function_of_state():
    r = run_node(["P", "M", "X"], "heuristic")
    base = payload(r.pre)
    poisoned = {**r.pre, "interrupt_data": {"data": {"pending_merge_candidates": []}}}
    assert payload(poisoned) == base, (
        "the payload leans on an interrupt_data stashed by a node body"
    )
    via_manager = WorkflowManager._build_interrupt_data("human_review_matches", r.pre)["data"]
    assert via_manager == base, "manager-synthesised payload differs from _build_matches_interrupt"
    assert band_counts(base) is not None
    assert json.dumps(base, default=str)  # JSON-serialisable for the poll response


def test_e2_resume_goes_through_update_state_as_the_gate_node():
    r = run_node(["P", "M", "X"], "heuristic")
    fb = SHAPES["pair_decisions"]("company", [(r.ids["P"], "merge")])
    post = resume_manager(r.pre, fb)  # asserts as_node == human_review_matches inside
    assert post["human_feedback"]["decision"] == "approve"


def test_e3_node_body_and_manager_agree():
    """Both code paths exist today (the body is reached by direct callers); they must not drift."""
    r = run_node(["P", "Q", "M"], "heuristic")
    pin = os.environ.get("P15_SHAPE")
    for name, build in SHAPES.items():
        if pin and name != pin:
            continue
        fb = build("company", [(r.ids["P"], "merge"), (r.ids["Q"], "keep")])
        via_mgr = resume_manager(r.pre, fb)
        if fz(r.ids, "P") not in groups(via_mgr):
            continue
        out = asyncio.run(hn.human_review_matches({**r.pre, "human_feedback": fb}))
        via_body = {**r.pre, **out}
        assert groups(via_body) == groups(via_mgr), f"{name}: body path != manager path"
        assert pending_pairs(via_body) == pending_pairs(via_mgr) == set()
        return
    pytest.fail("no shape merged P through the manager (see test_a2)")


def test_e4_per_pair_reject_is_not_a_run_level_reject():
    """The router ENDS the run on decision reject/abort.  Keeping a pair apart is a per-pair decision inside an
    approved gate; every feedback shape must still route 'forward' (to unify), never 'abort'."""
    from src.orchestrator.graph import _gate_router

    route = _gate_router("human_review_matches", "unify_entities")
    assert route({"human_feedback": {"decision": "approve"}}) == "forward"
    r = run_node(["P", "M"], "heuristic")
    for name, build in SHAPES.items():
        fb = build("company", [(r.ids["P"], "keep")])
        assert route({"human_feedback": fb}) == "forward", (
            f"{name}: a pair reject would abort the run"
        )


# ══ F. 2.4 / 2.5: no fitted constants, no corpus literals, mechanical guards ═════════════════

BASELINE = {
    "src/mcp_server/tools/resolve_entities.py": {
        "defs": frozenset(
            [
                "_assign_cluster_id",
                "_average_linkage",
                "_best_name_field",
                "_build_clusters",
                "_build_comparison_vector",
                "_cached_projection",
                "_company_native_id",
                "_comparison_level",
                "_comparisons",
                "_compute_heuristic_confidence",
                "_cross_name_similarity",
                "_deterministic_conflict",
                "_deterministic_id_fields",
                "_deterministic_match",
                "_deterministic_values",
                "_domain_of",
                "_evidence_for_members",
                "_fit_match_model",
                "_format_pairs_block",
                "_generate_candidate_pairs",
                "_has_conflicting_identity",
                "_has_discriminating_agreement",
                "_is_domain_field",
                "_judge_pairs_with_llm",
                "_keeps",
                "_min_internal_confidence",
                "_neighbours",
                "_normalize",
                "_only_partial_name_evidence",
                "_ordered",
                "_pair_field_evidence",
                "_partition_component",
                "_passes",
                "_pending_merge_candidates",
                "_project_to_canonical",
                "_record_id",
                "_relational_agreement",
                "_resolve_core",
                "_score_pair",
                "_seq_ratio",
                "_shared_neighbors_beyond_common_parent",
                "_split_weakest_link",
                "_string_similarity",
                "_string_similarity_cached",
                "_subject_stripped_normalize",
                "_subject_tokens",
                "_thresholds_for",
                "_token_keys",
                "_token_ratio",
                "_tokenize",
                "_tokens_match",
                "_unique_id_fields",
                "build_relationship_index",
                "find",
                "find",
                "judge_chunk",
                "normalise",
                "rarity",
                "resolve_entities_impl",
                "trigram_candidate_pairs",
                "union",
            ]
        ),
        "mod": frozenset(
            [
                "RecordCache",
                "_AUTO_EXCLUDE",
                "_AUTO_MATCH",
                "_CANONICAL_FIELD_HINTS",
                "_DETERMINISTIC_ID_HINTS",
                "_DOMAIN_FIELD_HINTS",
                "_FUZZY_NAME_CAP",
                "_LARGE_COMPONENT_THRESHOLD",
                "_LLM_CHUNK_SIZE",
                "_LLM_CONCURRENCY",
                "_MAX_CANDIDATE_PAIRS",
                "_MAX_DISCRIMINATING_FREQUENCY",
                "_MAX_LLM_PAIRS",
                "_MAX_PENDING_CANDIDATES",
                "_MIN_COHESION",
                "_MIN_PAIRS_FOR_MODEL",
                "_NAME_FIELD_HINTS",
                "_NAME_LIKE_CANONICAL_FIELDS",
                "_PARTIAL_NAME_FIELDS",
                "_PROCESS_POOL_MIN_RECORDS",
                "_REF_FIELD_HINTS",
                "_SIMILARITY_CACHE_SIZE",
                "_STRICTNESS",
                "_SURROGATE_KEY_HINTS",
                "_UNIQUE_IDENTITY_HINTS",
                "_WORD_RE",
                "logger",
            ]
        ),
        "nums": {
            "_thresholds_for": [],
            "_resolve_core": [4],
            "resolve_entities_impl": [0, 0.6, 1, 2, 4, 1000],
            "_pending_merge_candidates": [3],
            "_build_clusters": [0, 0.85, 1, 3, 49, 50],
        },
    },
    "src/orchestrator/nodes/resolve_entities.py": {
        "defs": frozenset(
            [
                "_inject_subject_destination_rows",
                "_load_entity_map",
                "_matching_connector_and_config",
                "_matching_rules_for",
                "_relationship_index_for_companies",
                "_without_metadata",
                "has_low_confidence_clusters",
                "resolve_entities",
            ]
        ),
        "mod": frozenset(["_DEST_ORIGIN", "_DEST_READ_LIMIT", "_MAX_POOL_WORKERS", "logger"]),
        "nums": {"resolve_entities": [0, 1, 2], "has_low_confidence_clusters": [0, 1]},
    },
    "src/orchestrator/nodes/human_nodes.py": {
        "defs": frozenset(
            [
                "_auto_match_stats",
                "_backfill_mapping_provenance",
                "_build_deps_interrupt",
                "_build_field_provenance",
                "_build_mapping_rows",
                "_build_matches_interrupt",
                "_build_origin_schemas",
                "_build_scope_interrupt",
                "build_error_interrupt",
                "handle_error",
                "human_final_approval",
                "human_review_deps",
                "human_review_mappings",
                "human_review_matches",
                "human_review_scope",
            ]
        ),
        "mod": frozenset(["logger"]),
        "nums": {"_build_matches_interrupt": [0], "human_review_matches": [1]},
    },
    "src/orchestrator/workflow_manager.py": {
        "defs": frozenset(
            [
                "__init__",
                "_auto_match_stats",
                "_bare_config",
                "_build_interrupt_data",
                "_build_interrupt_data_inner",
                "_capture_profile",
                "_clusters_by_entity",
                "_collect_events",
                "_collect_events",
                "_columns_for",
                "_continue_workflow",
                "_create_checkpointer",
                "_derive_skipped_gates",
                "_finalize_stream_status",
                "_get_workflow_summary",
                "_payload_version",
                "_run_workflow",
                "_safe_state_values",
                "_sanitise_column_dependencies",
                "_sanitise_entity_tables",
                "_traced_run_config",
                "_valid",
                "abort_workflow",
                "adelete_thread",
                "aget_tuple",
                "alist",
                "apply_mapping_feedback",
                "aput",
                "aput_writes",
                "cleanup_completed",
                "get_match_inspection",
                "get_workflow_status",
                "list_active_workflows",
                "rehydrate_workflow",
                "resume_workflow",
                "start_workflow",
            ]
        ),
        "mod": frozenset(["_CHECKPOINT_DB", "logger"]),
        "nums": {"resume_workflow": [0, 1]},
    },
    "src/orchestrator/utils/cluster_edits.py": {
        "defs": frozenset(["apply_cluster_decisions"]),
        "mod": frozenset(["_KEEP", "_SPLIT", "logger"]),
        "nums": {"apply_cluster_decisions": [0, 1]},
    },
}


FILES = {
    "src/mcp_server/tools/resolve_entities.py": [
        "_pending_merge_candidates",
        "_thresholds_for",
        "_resolve_core",
        "resolve_entities_impl",
        "_build_clusters",
    ],
    "src/orchestrator/nodes/resolve_entities.py": [
        "resolve_entities",
        "has_low_confidence_clusters",
    ],
    "src/orchestrator/nodes/human_nodes.py": ["_build_matches_interrupt", "human_review_matches"],
    "src/orchestrator/workflow_manager.py": ["resume_workflow"],
    "src/orchestrator/utils/cluster_edits.py": ["apply_cluster_decisions"],
}
# 2.4 corpus literals (plan) + the corporate-suffix words a "suffix rule" would hard-code.
CORPUS = {
    "france",
    "us",
    "sas",
    "berhad",
    "global",
    "group",
    "holdings",
    "companyname",
    "main_entity_id",
    "company_relation",
    "staff",
    "org_title",
    "testdiffbot",
    "companyexperience",
    "ltd",
    "inc",
    "gmbh",
    "corp",
    "llc",
    "plc",
    "limited",
    "sa",
    "co",
}
P15_NAME = re.compile(
    r"band|pending|threshold|hist|distribution|bins?|log_?m|decision|commit|review|gate|predict",
    re.I,
)
ALLOWED_INTS = {0, 1, 2, 3, 4}  # indices / rounding digits; anything else is a constant to justify


def _tree(rel):
    return ast.parse((ROOT / rel).read_text())


def _scanned(rel):
    """(function nodes to scan for strings, function nodes to scan for numbers, new module assignments)."""
    tree = _tree(rel)
    base = BASELINE[rel]
    owned = set(FILES[rel])
    strs, nums = [], []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            new = n.name not in base["defs"]
            if n.name in owned or new:
                strs.append(n)
            if n.name in owned or (new and P15_NAME.search(n.name)):
                nums.append(n)
    mod = []
    for n in tree.body:
        for t in (
            n.targets
            if isinstance(n, ast.Assign)
            else [n.target]
            if isinstance(n, ast.AnnAssign)
            else []
        ):
            if isinstance(t, ast.Name) and t.id not in base["mod"]:
                mod.append((t.id, n))
    return strs, nums, mod


def _docstring_ids(fn):
    first = fn.body[0] if fn.body else None
    ds = (
        first.value
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
        else None
    )
    return {id(ds)} if ds is not None else set()


@pytest.mark.parametrize("rel", sorted(FILES))
def test_f1_no_corpus_literal_in_changed_code(rel):
    strs, _, mod = _scanned(rel)
    hits = []
    for fn in strs:
        skip = _docstring_ids(fn)
        for c in ast.walk(fn):
            if isinstance(c, ast.Constant) and isinstance(c.value, str) and id(c) not in skip:
                words = set(re.findall(r"[A-Za-z_]+", c.value.lower()))
                if words & CORPUS or c.value.lower() in CORPUS:
                    hits.append((fn.name, c.value[:40]))
    for name, n in mod:
        for c in ast.walk(n):
            if (
                isinstance(c, ast.Constant)
                and isinstance(c.value, str)
                and set(re.findall(r"[A-Za-z_]+", c.value.lower())) & CORPUS
            ):
                hits.append((name, c.value[:40]))
    assert not hits, f"{rel}: corpus literals in changed code (plan 2.4): {hits}"


@pytest.mark.parametrize("rel", sorted(FILES))
def test_f2_no_new_numeric_constant(rel):
    """Plan 2.4: 'would a count over the corpus produce this?'  The band is the sanctioned 0.95/0.70 (module
    constants, unchanged).  A new bin count, floor, cap, margin or default in a changed function must be
    DERIVED from the data (e.g. Sturges: ceil(log2 n) + 1) -- or the lead whitelists it explicitly here."""
    _, nums, mod = _scanned(rel)
    base = BASELINE[rel]["nums"]
    bad = []
    for fn in nums:
        known = set(base.get(fn.name, []))
        skip = _docstring_ids(fn)
        for c in ast.walk(fn):
            if (
                isinstance(c, ast.Constant)
                and isinstance(c.value, (int, float))
                and not isinstance(c.value, bool)
                and id(c) not in skip
            ):
                v = c.value
                if v in known or (isinstance(v, int) and v in ALLOWED_INTS):
                    continue
                bad.append((fn.name, v))
    for name, n in mod:
        for c in ast.walk(n):
            if (
                isinstance(c, ast.Constant)
                and isinstance(c.value, (int, float))
                and not isinstance(c.value, bool)
            ):
                bad.append((name, c.value))
    assert not bad, (
        f"{rel}: numeric literals not in the baseline and not derivable (plan 2.4): {bad}"
    )


def test_f3_band_constants_are_referenced_not_retyped():
    """0.95 / 0.70 appear exactly as the two module constants; changed code must refer to them (or to the
    effective per-entity values), never re-type them."""
    for rel in FILES:
        _, nums, _ = _scanned(rel)
        for fn in nums:
            for c in ast.walk(fn):
                if (
                    isinstance(c, ast.Constant)
                    and isinstance(c.value, float)
                    and c.value in (0.95, 0.7, 0.70)
                ):
                    pytest.fail(f"{rel}:{fn.name} re-types the band constant {c.value}")
    src = (ROOT / "src/mcp_server/tools/resolve_entities.py").read_text()
    assert len(re.findall(r"^_AUTO_MATCH\s*=\s*0\.95\s*$", src, re.M)) == 1
    assert len(re.findall(r"^_AUTO_EXCLUDE\s*=\s*0\.70\s*$", src, re.M)) == 1


def test_f4_arch_lint_clean_and_no_column_added():
    r = subprocess.run(
        [sys.executable, "scripts/lint_arch.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    assert tre._AUTO_MATCH > tre._AUTO_EXCLUDE
