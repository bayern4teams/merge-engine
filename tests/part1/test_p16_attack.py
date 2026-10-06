"""P16 attack (round r1): defects found against commit ee6b00f, written as strict xfails.

Each xfail has a control that passes today (the same fixture on the case the code does handle),
so the xfail fails for the stated reason and a fix flips it to XPASS(strict) -> remove the marker.
Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p16_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import time

import pytest

from src.mcp_server.tools.cluster_quality import quality_signals
from src.orchestrator.nodes.load_data import build_reconciliation_report

# 60 real-looking company names (no generated syllables): short names are the point.
BASES = [
    "Barclays",
    "Tesco",
    "Vodafone",
    "Unilever",
    "Diageo",
    "Rolls-Royce",
    "AstraZeneca",
    "BP",
    "Shell",
    "National Grid",
    "Burberry",
    "Aviva",
    "Prudential",
    "Rio Tinto",
    "Anglo American",
    "Compass Group",
    "Experian",
    "Pearson",
    "Sage",
    "Intertek",
    "Halma",
    "Whitbread",
    "Next",
    "Kingfisher",
    "Ocado",
    "Wise",
    "Rightmove",
    "Admiral",
    "Berkeley",
    "Persimmon",
    "Barratt",
    "Bellway",
    "Hiscox",
    "Beazley",
    "Schroders",
    "Abrdn",
    "Ashtead",
    "Bunzl",
    "DCC",
    "Mondi",
    "Glencore",
    "Greggs",
    "Frasers",
    "Currys",
    "Dunelm",
    "Rotork",
    "Weir",
    "Smiths",
    "Renishaw",
    "Spectris",
    "Victrex",
    "Senior",
    "Cobham",
    "Serco",
    "Capita",
    "Mitie",
    "Royal Mail",
    "Stagecoach",
    "FirstGroup",
    "easyJet",
]
PEOPLE = [
    (f, last)
    for f in ("John", "Mary", "Robert", "Linda", "James", "Karen")
    for last in ("Smith", "Jones", "Brown", "Davis", "Wilson")
]


def _run(groups):
    clusters, names = [], {}
    for i, g in enumerate(groups):
        ids = []
        for j, v in enumerate(g):
            names[f"{i}_{j}"] = v
            ids.append(f"{i}_{j}")
        clusters.append({"cluster_id": f"c{i}", "record_ids": ids})
    return quality_signals(clusters, names)


# ── D2: legal-suffix variants of one company are false-flagged ───────────────────────────────
def test_control_case_and_typo_variants_are_not_flagged():
    groups = [[b, b.upper(), b.lower()] for b in BASES]
    assert _run(groups)["flagged_count"] == 0


def test_legal_suffix_variants_of_one_company_are_not_flagged():
    groups = [[b, f"{b} Limited", f"THE {b} PLC"] for b in BASES]
    out = _run(groups)
    # a healthy corpus: P10 would count every flag, so a handful at most
    assert out["flagged_count"] <= len(groups) // 20, (out["flagged_count"], len(groups))


# ── D3: reordered / initialled person names are false-flagged ────────────────────────────────
def test_control_identical_person_names_are_not_flagged():
    assert _run([[f"{f} {last}", f"{f} {last}"] for f, last in PEOPLE])["flagged_count"] == 0


def test_reordered_and_initialled_person_names_are_not_flagged():
    groups = [[f"{f} {last}", f"{last}, {f}", f"{f[0]}. {last}"] for f, last in PEOPLE]
    out = _run(groups)
    assert out["flagged_count"] <= len(groups) // 20, (out["flagged_count"], len(groups))


# ── D1: the gate preview and the load node both build the report; the second call compares a run with itself
def _state(clusters, path, run_id):
    recs = [
        {"_origin_id": "o", "_origin_table": "t", "_origin_id_row": i, "company_name": f"Name {i}"}
        for i in range(1, 7)
    ]
    return {
        "run_id": run_id,
        "extracted_data": {"company": recs},
        "entity_clusters": {"company": clusters},
        "quality_history_path": str(path),
    }


def _cl(*groups):
    return [
        {"cluster_id": f"c{i}", "record_ids": [f"o:t:{r}" for r in g]} for i, g in enumerate(groups)
    ]


def _bcubed(state):
    return build_reconciliation_report(state, {}, [])["entities"]["company"]["quality"]["bcubed"]


def test_control_run_two_differs_from_run_one(tmp_path):
    p = tmp_path / "h.json"
    assert _bcubed(_state(_cl([1, 2, 3], [4, 5, 6]), p, "run1")) is None
    assert _bcubed(_state(_cl([1], [2], [3], [4, 5, 6]), p, "run2")) < 1.0


def test_rebuilding_the_report_within_one_run_does_not_compare_the_run_to_itself(tmp_path):
    p = tmp_path / "h.json"
    _bcubed(_state(_cl([1, 2, 3], [4, 5, 6]), p, "run1"))
    run2 = _cl([1], [2], [3], [4, 5, 6])
    preview = _bcubed(_state(run2, p, "run2"))  # final-approval gate (workflow_manager)
    final = _bcubed(_state(run2, p, "run2"))  # load_data_node's report
    assert preview is not None and preview < 1.0
    assert final == pytest.approx(preview)


# ── D4: the report goes quadratic on exactly the failure it exists to detect (one giant cluster) ──
def _giant_seconds(n):
    import random

    rng = random.Random(0)
    syl = ["ka", "ri", "to", "men", "sol", "vex", "dar", "lin", "mor", "pel", "qui", "zan"]
    names = {f"r{i}": "".join(rng.choice(syl) for _ in range(5)) for i in range(n)}
    clusters = [
        {"cluster_id": "g", "record_ids": list(names)},
        {"cluster_id": "s", "record_ids": ["x"]},
    ]
    names["x"] = "Zed"
    t = time.perf_counter()
    quality_signals(clusters, names)
    return time.perf_counter() - t


def test_control_small_cluster_is_fast():
    assert _giant_seconds(50) < 2.0


def test_cost_of_one_giant_cluster_is_not_quadratic():
    small, big = _giant_seconds(300), _giant_seconds(1200)  # 4x the names
    assert big < 8 * small + 0.5, (small, big)  # linear ~4x, quadratic ~16x
