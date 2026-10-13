"""Defects found by the local live verification (docs/plans/part1/LOCAL_VERIFY_REPORT.md).

Each test reproduces one defect on 10-60 real records taken from a public benchmark run
(source named in the fixture). Every test is ``xfail(strict=True)``: it fails today, and
a fix flips it to XPASS, which fails the suite until the marker is removed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.mcp_server.tools.cluster_quality import quality_signals
from src.mcp_server.tools.resolve_entities import _best_name_field, _record_id

HERE = Path(__file__).parent


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text())


@pytest.mark.xfail(
    strict=True, reason="LOCAL_VERIFY_REPORT P16-D1: person name read from ONE column"
)
def test_p16_true_person_pairs_are_not_flagged():
    """Same path as load_data.py: names via _best_name_field -> quality_signals.

    Live (Febrl4, 0 over-merged clusters): 888 of 4,989 multi-member clusters flagged.
    """
    fx = _load("p16_person_name_flags.json")
    names = {_record_id(r): r.get(_best_name_field(r) or "") for r in fx["records"]}
    q = quality_signals(fx["clusters"], names)
    assert q["flagged_count"] == 0, sorted(q["flagged_clusters"])


@pytest.mark.xfail(strict=True, reason="LOCAL_VERIFY_REPORT P10-D1: own slack defeats the 1/2 cap")
def test_p10_single_entity_giant_cluster_is_refused():
    """One entity, one cluster absorbing 21 of 30 distinct people (Febrl4 records).

    Live: on every single-entity benchmark the proposals are 2x the run's own values
    and the largest-cluster slack is computed from the entity itself, so the gate shows
    measured 0.69 > threshold 0.50 with violated=False and would_fail=False.
    """
    from src.orchestrator.nodes.load_data import pre_load_gate

    recs = _load("p16_person_name_flags.json")["records"][:30]
    ids = [_record_id(r) for r in recs]
    clusters = [{"cluster_id": "giant", "record_ids": ids[:21]}] + [
        {"cluster_id": f"s{i}", "record_ids": [x]} for i, x in enumerate(ids[21:])
    ]
    state = {
        "extracted_data": {"person": recs},
        "entity_clusters": {"person": clusters},
        "field_mappings": {"person": {k: k for k in ("given_name", "surname", "suburb")}},
        "unmapped_fields": {"person": []},
    }
    g = pre_load_gate(state)
    inv = g["invariants"]["largest_cluster_share"]
    assert inv["measured"] > inv["threshold"]  # the defect's precondition holds
    assert inv["violated"] and g["would_fail"]


def test_api_feedback_forwards_band_decisions():
    """POST /api/workflow/{id}/feedback must hand pair_decisions / band_thresholds to
    resume_workflow (P15 applies them, P9 remembers them).

    Live (febrl4 run p9r1, 3 oracle 'merge' answers sent through the REST API): all 3
    pairs stayed apart in the destination and no decision-memory file was written; the
    ``Feedback`` model has neither field and pydantic drops them silently.
    """
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    from src.api.main import create_api

    mgr = MagicMock()
    mgr.get_workflow_status.return_value = {
        "status": "interrupted",
        "current_interrupt_node": "human_review_matches",
    }
    pair = {"entity_type": "person", "record_ids": ["fa:t:1", "fb:t:1"], "action": "merge"}
    with patch("src.api.main.get_manager", return_value=mgr):
        r = TestClient(create_api()).post(
            "/api/workflow/t1/feedback",
            json={"decision": "approve", "pair_decisions": [pair], "band_thresholds": {}},
        )
    assert r.status_code == 200
    sent = mgr.resume_workflow.call_args.args[1]
    assert sent.get("pair_decisions") == [pair]


@pytest.mark.xfail(
    strict=True, reason="LOCAL_VERIFY_REPORT P14-D1: soft Monge-Elkan is |A|x|B| on long text"
)
def test_p14_text_similarity_cost_is_not_quadratic_in_tokens():
    """idf_soft_similarity calls token_sim once per token PAIR in each direction.

    Live: Amazon descriptions reach 2,877 tokens; resolve_entities on 4,589 product
    records did not finish in 140 min (60 records: 62 s). Bound used here: 20 calls per
    token, i.e. linear; today it is 2 x |A| x |B|.
    """
    import re

    from src.mcp_server.tools.fellegi_sunter import idf_soft_similarity

    a, b = (
        set(re.findall(r"[a-z0-9]+", r["description"].lower()))
        for r in _load("p14_long_text_pair.json")["records"]
    )
    calls = 0

    def sim(x: str, y: str) -> float:
        nonlocal calls
        calls += 1
        return 1.0 if x == y else 0.0

    idf_soft_similarity(a, b, {}, 1000, sim)
    assert calls <= 20 * (len(a) + len(b)), (calls, len(a), len(b))


def test_p14_addendum_is_not_an_auto_match():
    """DBLP-ACM, 40 records, LLM off. 'Automatic Generation of Production Rules ...' (1994)
    vs 'Addendum to Automatic Generation of Production Rules ...' (1995): main scores
    0.917 (review band), the branch 0.968 (auto-merge; 0.988 on 63 records). Live this
    shape took DBLP-ACM over-merged clusters from 65 to 107.
    """
    from src.mcp_server.tools.resolve_entities import _resolve_core

    recs = _load("p14_dblp_addendum.json")["records"]
    paper, addendum = _record_id(recs[1]), _record_id(recs[2])
    core = _resolve_core("publication", recs, None, None, None)
    scores = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    scores |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    assert scores[frozenset((paper, addendum))] < core["auto_match"]


@pytest.mark.xfail(
    strict=True, reason="LOCAL_VERIFY_REPORT PRE-D1 (also on main): name-only entity never merges"
)
def test_identical_names_in_a_name_only_entity_reach_the_band():
    """Affiliations: one data column (``name``). Two records with the identical name
    score far below the band (live max over 30,593 scored pairs: 0.109; identical
    'Oracle' 0.012), so the run merges nothing on main and on the branch.
    """
    from src.mcp_server.tools.resolve_entities import _resolve_core

    recs = _load("single_field_identical_names.json")["records"]
    a, b = _record_id(recs[0]), _record_id(recs[1])
    core = _resolve_core("organisation", recs, None, None, None)
    scores = {frozenset((x, y)): s for x, y, s in core["scored_pairs"]}
    scores |= {frozenset((x, y)): s for x, y, _r1, _r2, s in core["borderline"]}
    assert scores.get(frozenset((a, b)), 0.0) >= core["auto_exclude"]
