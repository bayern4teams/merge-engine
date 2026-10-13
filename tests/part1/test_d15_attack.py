"""D15 attack: the matches gate's "N of M" must hold on inputs the acceptance suite does not build.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d15_attack.py -q -p no:cacheprovider

Payloads are built from hand-made ``resolution_stats`` (the shape ``node.resolve_entities`` stores), so every
odd ``band_summary`` can be planted directly.  The ground truth is the planted listed list and ``possible``.
"""

from __future__ import annotations

import importlib
import json
import re

import pytest

from src.orchestrator.nodes import human_nodes as hn

node = importlib.import_module("src.orchestrator.nodes.resolve_entities")

BAND_TAIL = "looked similar but not similar enough to merge automatically"


def cands(n, prefix="r"):
    return [
        {"record_ids": [f"{prefix}{i}a", f"{prefix}{i}b"], "confidence": 0.9 - i / 1000}
        for i in range(n)
    ]


def stats(listed, possible="absent"):
    s = {"pending_merge_candidates": cands(listed)}
    if possible != "absent":
        s["band_summary"] = {"predicted_counts": {"possible": possible}}
    return s


def payload(res_stats):
    return hn._build_matches_interrupt({"resolution_stats": res_stats})["data"]


# ── malformed / odd `possible`: never a crash, never an N > M, never a negative ──────────────
@pytest.mark.parametrize(
    "possible", [None, "80", 80.0, -5, 0, True, False, [], {}, float("nan"), float("inf")]
)
def test_odd_possible_never_crashes_and_never_shows_n_of_smaller_m(possible):
    d = payload({"company": stats(12, possible)})
    sh = d["pending_merge_shown"]
    e = sh["per_entity"]["company"]
    assert e["listed"] == 12
    assert isinstance(e["total"], int) and e["total"] >= e["listed"]
    assert sh["listed"] == 12 and sh["total"] >= 12
    json.dumps(d)


@pytest.mark.xfail(
    strict=True, reason="MINOR, unreachable: isinstance(True, int); possible is always a sum() int"
)
def test_bool_possible_is_not_counted_as_the_int_one():
    # isinstance(True, int) is True; True > 0 listed would make total == 1. With 0 listed this must stay 0.
    d = payload(
        {
            "company": {
                "pending_merge_candidates": [],
                "band_summary": {"predicted_counts": {"possible": True}},
            }
        }
    )
    assert d["pending_merge_shown"]["total"] == 0, "True was read as a band of 1 pair"


def test_float_possible_is_ignored_not_truncated():
    # 80.0 comes back from a JSON round-trip only if someone wrote a float; it must not crash. Either answer is
    # acceptable, but the total may not be below the listed count.
    d = payload({"company": stats(12, 80.0)})
    assert d["pending_merge_shown"]["total"] in (12, 80)


@pytest.mark.parametrize(
    "summary",
    [
        None,
        [],
        pytest.param("x", marks=pytest.mark.xfail(strict=True, reason="MINOR, unreachable")),
        {"predicted_counts": None},
        {"predicted_counts": []},
    ],
)
def test_malformed_band_summary_is_tolerated(summary):
    s = {"pending_merge_candidates": cands(3), "band_summary": summary}
    d = payload({"company": s})
    assert d["pending_merge_shown"]["listed"] == 3


# ── the arithmetic across entities ──────────────────────────────────────────────────────────
def test_per_entity_cap_and_total_sum_independently():
    d = payload({"company": stats(50, 800), "person": stats(7, 7), "product": stats(50, 130)})
    sh = d["pending_merge_shown"]
    assert sh["per_entity"] == {
        "company": {"listed": 50, "total": 800},
        "person": {"listed": 7, "total": 7},
        "product": {"listed": 50, "total": 130},
    }
    assert (sh["listed"], sh["total"]) == (107, 937)
    assert d["pending_merge_note"].startswith(
        "107 of 937 pair(s) in the review band are listed here"
    )


def test_no_hidden_pairs_keeps_the_old_wording_exactly():
    d = payload({"company": stats(4, 4)})
    assert d["pending_merge_note"].startswith("4 pair(s) looked similar")
    assert "of" not in d["pending_merge_note"].split("pair(s)")[0]
    # band_summary total below listed (stale or hand-made): still the old wording, never "4 of 2"
    d = payload({"company": stats(4, 2)})
    assert d["pending_merge_shown"]["total"] == 4
    assert d["pending_merge_note"].startswith("4 pair(s) looked similar")


def test_note_and_shown_agree_for_any_sizes():
    for listed, band in [(1, 2), (49, 50), (50, 51), (50, 10_000), (3, 3), (0, 0)]:
        d = payload({"company": stats(listed, band)})
        sh = d["pending_merge_shown"]
        if listed == 0:
            assert d["pending_merge_note"] == "" and sh["total"] == 0
            continue
        m = re.match(r"(\d+) of (\d+) pair", d["pending_merge_note"])
        if band > listed:
            assert m and (int(m[1]), int(m[2])) == (sh["listed"], sh["total"]) == (listed, band)
        else:
            assert not m and sh["total"] == sh["listed"] == listed


# ── the empty-list case: band pairs exist, the list is empty ────────────────────────────────
def test_band_pairs_but_empty_list_is_reported_machine_readably_but_note_stays_empty():
    """All band pairs resolved by decision memory (or the report's retry), none left to review.

    Pinned as the observed behaviour: shown says 0 of M, the note is "" and no pending section renders.
    Whether "0 of M" is true is the stale-band question below.
    """
    d = payload({"company": stats(0, 80)})
    assert d["pending_merge_candidates"] == []
    assert d["pending_merge_shown"]["per_entity"]["company"] == {"listed": 0, "total": 80}
    assert d["pending_merge_note"] == ""


@pytest.mark.xfail(
    strict=True, reason="MINOR: M is the run's band size; pairs answered by memory stay in M"
)
def test_empty_list_with_band_pairs_does_not_claim_pairs_are_hidden():
    """If memory resolved every band pair, `total: 80` with `listed: 0` reads as 80 hidden pairs.

    ``resolve_entities`` drops memory-decided pairs from the LIST but ``band_summary.predicted_counts.possible``
    still counts them, so M overstates what is pending.  A consumer of pending_merge_shown (the UI shows only
    the note, which is "" here) must not be told 80 are unlisted when 0 are.
    """
    d = payload(
        {"company": {**stats(0, 80), "band_resolution": {"accepted": 0, "kept_separate": 80}}}
    )
    assert d["pending_merge_shown"]["total"] == 0, (
        "pending_merge_shown reports 80 band pairs 'unlisted' for an entity whose band was fully resolved"
    )


# ── stale band_summary after apply_band_decisions ───────────────────────────────────────────
def _resolved_state(n_band, listed_n, memory_decided):
    pend = cands(listed_n)
    st = {
        "run_id": "r",
        "resolution_stats": {
            "company": {
                "pending_merge_candidates": pend,
                "band_summary": {
                    "thresholds": {"auto_match": 0.95, "auto_exclude": 0.7},
                    "predicted_counts": {"possible": n_band},
                },
                "retained_pairs": [
                    [p["record_ids"][0], p["record_ids"][1], p["confidence"]] for p in pend
                ],
            }
        },
    }
    fb = {
        "pair_decisions": [
            {
                "entity_type": "company",
                "record_ids": pend[i]["record_ids"],
                "action": "keep_separate",
            }
            for i in range(memory_decided)
        ]
    }
    return st, fb


@pytest.mark.xfail(
    strict=True,
    reason="MINOR: M is the run's band size, so N-k of M overstates the unlisted by the k memory-answered pairs",
)
def test_after_decisions_applied_the_gate_total_does_not_exceed_what_is_still_pending():
    st, fb = _resolved_state(n_band=80, listed_n=50, memory_decided=3)
    upd = node.apply_band_decisions(st, fb, keep_undecided=True)
    st["resolution_stats"] = upd["resolution_stats"]
    d = payload(st["resolution_stats"])
    listed = d["pending_merge_shown"]["listed"]
    assert listed == 47
    # 30 band pairs were never listed; 3 were answered. "47 of 80" would say 33 are unlisted, truth is 30.
    assert d["pending_merge_shown"]["total"] == 77 or d["pending_merge_shown"]["total"] == 80
    assert d["pending_merge_shown"]["total"] == 77, (
        "total is the run's original band size; 3 answered pairs are neither listed nor hidden"
    )


@pytest.mark.xfail(
    strict=True, reason="MINOR: stale band_summary; note is empty so the UI shows nothing wrong"
)
def test_after_decisions_all_listed_answered_and_no_hidden_pairs_leaves_nothing_to_show():
    st, fb = _resolved_state(n_band=5, listed_n=5, memory_decided=5)
    upd = node.apply_band_decisions(st, fb, keep_undecided=True)
    d = payload(upd["resolution_stats"])
    assert d["pending_merge_candidates"] == []
    assert d["pending_merge_note"] == ""
    assert d["pending_merge_shown"]["total"] == 0, (
        "5 answered pairs reported as 5 band pairs still to be seen"
    )


# ── Splink path: same band fields, no special shape ─────────────────────────────────────────
def test_splink_path_band_fields_have_the_same_shape():
    from src.mcp_server.tools import resolve_entities as tre

    pairs = [(f"a{i}", f"b{i}", 0.72 + i / 1000) for i in range(60)]
    fields = tre._band_fields(pairs, 0.95, 0.70, True)
    capped = tre._pending_merge_candidates(pairs, 0.95, 0.70)
    d = payload(
        {"company": {"pending_merge_candidates": capped, "band_summary": fields["band_summary"]}}
    )
    assert fields["band_summary"]["predicted_counts"]["possible"] == 60
    assert d["pending_merge_shown"]["per_entity"]["company"] == {"listed": 50, "total": 60}
    assert d["pending_merge_note"].startswith("50 of 60 pair(s)")


def test_splink_path_returns_a_dict_whose_possible_is_a_plain_int():
    from src.mcp_server.tools import resolve_entities as tre

    f = tre._band_fields([("a", "b", 0.8), ("c", "d", float("nan"))], 0.95, 0.70, True)
    v = f["band_summary"]["predicted_counts"]["possible"]
    assert type(v) is int and v == 1


# ── serialisation and resume path ───────────────────────────────────────────────────────────
def test_payload_is_json_serialisable_and_round_trips_equal():
    d = payload({"company": stats(50, 1330), "person": stats(0, 0)})
    assert json.loads(json.dumps(d)) == d


def test_payload_is_built_from_state_not_the_gate_body():
    """With interrupt_before the gate body never runs on resume; the payload must come from a pure
    state -> dict builder that WorkflowManager calls (docs/architecture/orchestration_and_ui.md)."""
    import inspect

    from src.orchestrator import workflow_manager as wm

    assert "_build_matches_interrupt" in inspect.getsource(wm)
    assert "pending_merge_shown" not in inspect.getsource(hn.human_review_matches)


def test_gate_with_no_resolution_stats_or_none_stats():
    for rs in (None, {}, {"company": {}}):
        d = hn._build_matches_interrupt({"resolution_stats": rs})["data"]
        assert d["pending_merge_shown"] == {"per_entity": {}, "listed": 0, "total": 0}
        assert d["pending_merge_note"] == ""
