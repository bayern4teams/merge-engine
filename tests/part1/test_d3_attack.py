"""D3 attack: try to break the leave-one-out / no-slack-alone / capped-at-1/2 slack in ``pre_load_gate``.

Independent of the worker: the oracle is recomputed here from cluster sizes. Reuses the acceptance
builder only. Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d3_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import copy
import random

import pytest

from src.orchestrator.nodes.load_data import _pre_load_failure, pre_load_gate
from tests.part1.test_d3_acceptance import SHARE, build, gate, share

NULL = next(k for k in gate(build({"e": [1, 1]}))["invariants"] if "null" in k)
BIG = [30] + [1] * 970  # 1000 records, a cluster of 30 (largest 30)


def viol(g, inv=SHARE):
    return g["entities_violating"][inv]


# ── two-entity runs: each other's only sibling ───────────────────────────────────────────────────
def test_t1_big_sibling_lifts_a_small_healthy_lookup_but_only_up_to_the_plain_cap():
    # 20 records: cluster 10 -> share 9/19 = .474 (<= 1/2) passes beside a big entity; 11 -> .526 refused.
    assert viol(gate(build({"A": BIG, "B": [10] + [1] * 10}))) == []
    assert viol(gate(build({"A": BIG, "B": [11] + [1] * 9}))) == ["B"]


def test_t2_a_big_entity_is_not_lifted_by_a_small_sibling():
    # A: 1000 records, cluster 600 (.6). Sibling B is tiny; its tiny largest gives A ~no slack.
    g = gate(build({"A": [600] + [1] * 400, "B": [2, 1, 1]}))
    assert viol(g) == ["A"]


def test_t3_two_symmetric_overmerged_entities_are_both_refused():
    g = gate(build({"A": [60] + [1] * 40, "B": [60] + [1] * 40}))
    assert viol(g) == ["A", "B"]


def test_t4_two_entity_run_the_small_one_is_not_refused_for_being_small_when_below_the_cap():
    g = gate(build({"A": [2] * 500, "B": [6] + [1] * 14}))  # B share .263, A pairs
    assert viol(g) == [] and not g["would_fail"]


def test_t5_two_tiny_merged_entity_beside_a_big_one_is_refused_documented_change():
    # DOCUMENTED BEHAVIOUR CHANGE: a 2-record entity merged into 1 cluster has share 1.0 > 1/2; it was
    # passed by unbounded slack before (BASE), now refused beside a big sibling too (user rule: never a majority).
    assert viol(gate(build({"A": [2] * 50, "B": [2]}))) == ["B"]


def test_t6_swapping_sizes_changes_only_whoever_is_over_the_cap():
    for a, b in [([40] + [1] * 60, [2, 1]), ([2, 1], [40] + [1] * 60)]:
        g = gate(build({"A": a, "B": b}))
        want = [e for e, s in (("A", a), ("B", b)) if share(s) > 0.5]
        assert viol(g) == want


# ── degenerate entities ──────────────────────────────────────────────────────────────────────────
def test_t7_entity_without_clusters_beside_a_giant_does_not_lend_slack_or_crash():
    st = build({"A": [21] + [1] * 9, "B": [1]})
    st["entity_clusters"]["B"] = []  # no clusters at all
    g = gate(st)
    assert viol(g) == ["A"]


def test_t8_single_record_entity_clustered_equals_one_never_violates_and_lends_largest_one():
    g = gate(build({"A": [4] + [1] * 6, "B": [1]}))
    assert "B" not in viol(g)
    # B's largest is 1 -> A's slack (2*1-1)/(10-1) is tiny; A share 3/9 = .33 vs 2*median([.33,0]) = .33
    assert not g["would_fail"]


def test_t9_empty_state_and_empty_entity_do_not_crash():
    g = pre_load_gate({})
    assert not g["would_fail"]
    st = build({"A": [1, 1]})
    st["entity_clusters"]["A"] = []
    assert pre_load_gate(st)["entities_violating"][SHARE] == []


# ── randomized: no entity over a majority ever passes, payload self-consistent for one entity ────
def _rand_sizes(rng):
    n = rng.randint(2, 60)
    out, left = [], n
    while left:
        s = min(left, rng.choice([1, 1, 1, 2, 3, rng.randint(1, left)]))
        out.append(s)
        left -= s
    return out


def test_t10_random_multi_entity_worlds_majority_cluster_never_passes_and_input_not_mutated():
    rng = random.Random(20260926)
    refused = 0
    for _ in range(600):
        spec = {f"e{i}": _rand_sizes(rng) for i in range(rng.randint(2, 5))}
        st = build(spec)
        before = copy.deepcopy(st)
        g = pre_load_gate(st)
        assert st == before
        for e, s in spec.items():
            if share(s) > 0.5:
                assert e in viol(g), (spec, e)
                refused += 1
        assert g["invariants"][SHARE]["threshold"] <= 0.5
    assert refused > 100


def test_t11_random_single_entity_payload_never_contradicts_itself_clusters_and_nulls():
    rng = random.Random(7)
    for _ in range(600):
        sizes = _rand_sizes(rng)
        singles = sizes.count(1)
        k = rng.randint(0, singles)
        g = gate(build({"e": sizes}, null_names={"e": k}))
        for name, inv in g["invariants"].items():
            if "cover" in name:
                continue
            assert (inv["measured"] > inv["threshold"]) == inv["violated"], (sizes, k, name, inv)
        assert bool(viol(g)) == (share(sizes) > 0.5)


def test_t12_more_sibling_cluster_never_makes_a_small_entity_stricter():
    ok_before = None
    for big in [1, 2, 5, 20, 100, 500]:
        g = gate(build({"A": [big] + [1] * 600, "B": [9] + [1] * 11}))  # B share 8/19 = .42
        ok = viol(g) == []
        if ok_before:
            assert ok
        ok_before = ok or ok_before
    assert ok_before


# ── operator edits and the resume trap ───────────────────────────────────────────────────────────
def test_t13_operator_edit_is_used_as_given_even_when_slack_would_differ():
    st = build({"e": [21] + [1] * 9})  # share .69 single entity
    assert viol(gate(st)) == ["e"]
    g = gate(st, {SHARE: 0.9})
    assert viol(g) == [] and g["invariants"][SHARE]["confirmed"] == 0.9
    # tightening a small lookup that relied on slack: the operator's number wins, no slack
    st2 = build({"A": BIG, "B": [6] + [1] * 14})
    assert viol(gate(st2)) == []
    assert "B" in viol(gate(st2, {SHARE: 0.1}))


def test_t14_ui_echoing_every_proposal_back_keeps_slack_but_cannot_exceed_the_cap():
    st = build({"A": BIG, "B": [11] + [1] * 9})  # B .526
    g0 = gate(st)
    echo = {k: v["threshold"] for k, v in g0["invariants"].items()}
    g1 = gate(st, echo)
    assert viol(g1) == viol(g0) == ["B"]
    assert _pre_load_failure(g1)["disposition"] == "fail"


def test_t15_garbage_edit_fails_closed_and_nan_does_not_disable():
    st = build({"e": [21] + [1] * 9})
    for bad in ("0.9", float("nan"), True, None, [1]):
        g = gate(st, {SHARE: bad})
        assert g["would_fail"]


# ── NULL identity (single entity of 1-3 records) ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "sizes,k,refused",
    [([1], 1, True), ([1, 1], 1, False), ([1, 1, 1], 1, False), ([1, 1, 1], 2, True)],
)
def test_t16_single_entity_null_identity_small_runs(sizes, k, refused):
    # Behaviour change: with no sibling there is no NULL slack, so the ONLY newly refused case is
    # 1 NULL of 1 record (100% NULL). 1-of-2 is exactly 1/2 (not a majority), 1-of-3 is under it.
    g = gate(build({"e": sizes}, null_names={"e": k}))
    assert bool(viol(g, NULL)) is refused


@pytest.mark.xfail(
    strict=True,
    reason="F1 LOW (lead may waive): NULL slack 1/identity_records is uncapped and counts a data-less "
    "sibling as 'others', so a 100%-NULL 1-record entity passes beside a ghost entity / any sibling",
)
def test_t17_ghost_sibling_must_not_lend_null_slack_and_null_slack_is_capped_at_a_half():
    st = build({"e": [1]}, null_names={"e": 1})
    st["field_mappings"]["ghost"] = {}
    st["unified_entities"]["ghost"] = []
    assert viol(gate(st), NULL) == ["e"]


def test_t18_flagged_and_coverage_invariants_unaffected_by_the_slack_change():
    g = gate(build({"A": [2] * 10, "B": [2] * 10}))
    assert not g["would_fail"]
    assert set(g["invariants"]) >= {SHARE, NULL}
