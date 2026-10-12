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
