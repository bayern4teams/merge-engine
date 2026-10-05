"""P9 gate hook (lead): matches-gate band decisions remembered across runs.

Placement obeys the resume trap (plan 2.6): remembered decisions are applied by the
resolve_entities node, BEFORE the gate (so a fully remembered band never pauses), and a
human's answers are recorded by apply_band_decisions, which the manager runs on resume.
"""

from __future__ import annotations

import importlib
import json
import os

import pytest

from src.orchestrator.decision_memory import question_key

node = importlib.import_module("src.orchestrator.nodes.resolve_entities")

from tests.part1.test_p15_acceptance import (  # noqa: E402
    _approve,
    _pd,
    fz,
    groups,
    pending_pairs,
    resume_manager,
    run_node,
)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    path = tmp_path / "decisions.json"
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(path))
    return path


def _fb(**kw):
    return _approve({"node": "human_review_matches", **kw})


def _decide(r, items):
    return resume_manager(
        r.pre, _fb(pair_decisions=_pd("company", [(r.ids[k], a) for k, a in items]))
    )


def test_run2_with_identical_questions_asks_zero_and_reports_the_count(memory):
    r1 = run_node(["P", "Q", "M"])
    assert {fz(r1.ids, "P"), fz(r1.ids, "Q")} <= pending_pairs(r1.pre)
    post1 = _decide(r1, [("P", "merge"), ("Q", "keep_separate")])
    assert fz(r1.ids, "P") in groups(post1)
    assert memory.exists()

    r2 = run_node(["P", "Q", "M"])  # same data, a new run
    stats = r2.pre["resolution_stats"]["company"]
    assert stats["decisions_from_memory"] == 2
    assert pending_pairs(r2.pre) == set()
    assert not node.has_low_confidence_clusters(r2.pre), "the gate would still pause"
    assert fz(r2.ids, "P") in groups(r2.pre)
    assert fz(r2.ids, "Q") not in groups(r2.pre)
    assert groups(r2.pre) == groups(post1)


def test_partial_hit_leaves_only_the_new_question_for_the_gate(memory):
    r1 = run_node(["P", "Q"])
    _decide(r1, [("P", "merge")])  # Q undecided: approving keeps it apart, not remembered
    r2 = run_node(["P", "Q"])
    assert r2.pre["resolution_stats"]["company"]["decisions_from_memory"] == 1
    assert pending_pairs(r2.pre) == {fz(r2.ids, "Q")}
    assert fz(r2.ids, "P") in groups(r2.pre)
    post2 = _decide(r2, [("Q", "merge")])  # answered at the gate, then remembered too
    assert fz(r2.ids, "Q") in groups(post2)
    r3 = run_node(["P", "Q"])
    assert pending_pairs(r3.pre) == set()


def test_a_changed_attribute_is_a_new_question(memory):
    r = run_node(["P"])
    pair = fz(r.ids, "P")
    before = question_key(node._band_question(r.pre, "company", pair))
    changed = {
        **r.pre,
        "extracted_data": {
            "company": [
                {**rec, "name": rec["name"] + " Revised"} if rec.get("_origin_id_row") else rec
                for rec in r.pre["extracted_data"]["company"]
            ]
        },
    }
    assert question_key(node._band_question(changed, "company", pair)) != before
    assert question_key(node._band_question(r.pre, "company", pair)) == before


def test_corrupt_file_degrades_to_asking_again(memory):
    memory.write_text("{not json")
    r = run_node(["P"])
    assert pending_pairs(r.pre) == {fz(r.ids, "P")}


def test_unwritable_memory_never_fails_the_gate(tmp_path, monkeypatch):
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(blocker / "decisions.json"))
    r = run_node(["P"])
    post = _decide(r, [("P", "merge")])
    assert fz(r.ids, "P") in groups(post)


def test_off_by_default_nothing_written_nothing_replayed(tmp_path, monkeypatch):
    monkeypatch.delenv("MERGE_ENGINE_DECISION_MEMORY", raising=False)
    monkeypatch.chdir(tmp_path)
    r = run_node(["P"])
    _decide(r, [("P", "merge")])
    assert list(tmp_path.iterdir()) == [] or not any(
        p.suffix == ".json" for p in tmp_path.rglob("*")
    )
    r2 = run_node(["P"])
    assert pending_pairs(r2.pre) == {fz(r2.ids, "P")}
    assert os.environ.get("MERGE_ENGINE_DECISION_MEMORY") is None


def test_key_does_not_depend_on_the_run(memory):
    r1 = run_node(["P"])
    _decide(r1, [("P", "merge")])
    q = next(iter(json.loads(memory.read_text()).values()))["question"]
    assert set(q) == {"gate", "entity_type", "record_ids", "records"}
    assert "p15run" not in json.dumps(q)
