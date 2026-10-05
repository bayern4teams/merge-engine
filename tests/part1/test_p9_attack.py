"""P9 attack (decision memory + matches-gate hook): written by the attack verifier.

Drives the REAL resolve_entities node, the REAL graph router (``_resolve_entities_router``) and the REAL
``WorkflowManager.resume_workflow`` (mocked graph; the dict handed to ``update_state(as_node=...)`` is the
resume).  Reuses the P15 harness (planted pair scores) so band pairs are controlled, not guessed.

``xfail(strict=True)`` marks a defect the attack found: it flips to a failure the day it is fixed.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p9_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from src.orchestrator.decision_memory import DecisionMemory, question_key
from src.orchestrator.graph import _resolve_entities_router
from src.orchestrator.spool import RunSpool, make_handle
from tests.part1 import p15_fixtures as fx
from tests.part1.test_p15_acceptance import (  # noqa: F401  (_spool_isolation is an autouse fixture)
    _pd,
    _spool_isolation,
    fz,
    groups,
    node,
    pending_pairs,
    resume_manager,
    run_node,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mem(tmp_path, monkeypatch):
    p = tmp_path / "mem" / "decisions.json"
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(p))
    return p


def decide(r, items, **extra):
    fb = {
        "decision": "approve",
        "approved": True,
        "pair_decisions": _pd("company", [(r.ids[k], a) for k, a in items]),
        **extra,
    }
    return resume_manager(r.pre, fb)


def stored(p):
    return json.loads(p.read_text())


def decisions_in(p):
    return sorted(v["decision"] for v in stored(p).values())


# ── 1. run 2 asks ZERO, end to end through node + router ─────────────────────────────────────
@pytest.mark.parametrize("path", ["heuristic", "fitted"])
def test_run2_identical_questions_zero_pause_through_router(mem, path):
    r1 = run_node(["P", "Q", "B", "M"], path=path)
    assert _resolve_entities_router(r1.pre) == "review"  # old behaviour: the gate pauses
    post1 = decide(r1, [("P", "merge"), ("Q", "keep_separate"), ("B", "merge")])
    r2 = run_node(["P", "Q", "B", "M"], path=path)
    assert pending_pairs(r2.pre) == set()
    assert _resolve_entities_router(r2.pre) == "continue", "run 2 still pauses at the gate"
    assert r2.pre["resolution_stats"]["company"]["decisions_from_memory"] == 3
    assert groups(r2.pre) == groups(post1)
    # the resumed state also routes on (what the graph evaluates after the gate)
    assert _resolve_entities_router(post1) == "continue"


def test_memory_hit_counts_only_hits_and_is_absent_without_hits(mem):
    r = run_node(["P"])
    assert "decisions_from_memory" not in r.pre["resolution_stats"]["company"]
    decide(r, [("P", "merge")])
    r2 = run_node(["P", "Q"])  # Q is new
    assert r2.pre["resolution_stats"]["company"]["decisions_from_memory"] == 1
    assert pending_pairs(r2.pre) == {fz(r2.ids, "Q")}
    assert _resolve_entities_router(r2.pre) == "review"


# ── 2. partial memory: remembered ones applied consistently with human ones ──────────────────
@pytest.mark.parametrize("last", ["merge", "keep_separate"])
def test_partial_memory_equals_all_human_outcome(tmp_path, monkeypatch, last):
    # reference: memory OFF, a human decides all three
    monkeypatch.delenv("MERGE_ENGINE_DECISION_MEMORY", raising=False)
    ref = run_node(["P", "Q", "B"])
    ref_post = decide(ref, [("P", "merge"), ("Q", "keep_separate"), ("B", last)])
    expect = {frozenset(map(str, g)) for g in groups(ref_post)}
    # memory run 1: only P and Q answered (B left undecided -> kept apart, NOT remembered)
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(tmp_path / "m.json"))
    r1 = run_node(["P", "Q", "B"])
    decide(r1, [("P", "merge"), ("Q", "keep_separate")])
    assert len(stored(tmp_path / "m.json")) == 2, "an unanswered pair must not be remembered"
    # run 2: only B is asked; P and Q applied from memory
    r2 = run_node(["P", "Q", "B"])
    assert pending_pairs(r2.pre) == {fz(r2.ids, "B")}
    assert _resolve_entities_router(r2.pre) == "review"
    assert fz(r2.ids, "P") in groups(r2.pre)
    assert fz(r2.ids, "Q") not in groups(r2.pre)
    post2 = decide(r2, [("B", last)])
    got = {frozenset(map(str, g)) for g in groups(post2)}
    assert got == expect
    # remembered keep-apart survives the resume that happens after memory applied it
    assert fz(r2.ids, "Q") not in groups(post2)
    r3 = run_node(["P", "Q", "B"])
    assert pending_pairs(r3.pre) == set()
    assert groups(r3.pre) == groups(post2)


def test_remembered_keep_separate_is_a_cannot_link_for_the_rest_of_the_run(mem):
    r1 = run_node(["P", "Q"])
    decide(r1, [("P", "keep_separate"), ("Q", "merge")])
    r2 = run_node(["P", "Q"])
    assert fz(r2.ids, "P") not in groups(r2.pre) and fz(r2.ids, "Q") in groups(r2.pre)
    stats = r2.pre["resolution_stats"]["company"]
    assert sorted(fz(r2.ids, "P")) in [sorted(p) for p in stats["band_kept_apart"]]


# ── 3. resume path: the gate body does not run; answers are recorded anyway ──────────────────
def test_resume_records_exactly_the_answered_pairs(mem):
    r = run_node(["P", "Q", "B"])
    decide(r, [("P", "merge"), ("Q", "no")])  # B unanswered
    assert decisions_in(mem) == ["keep_separate", "merge"]
    for v in stored(mem).values():
        assert set(v["question"]) == {"gate", "entity_type", "record_ids", "records"}


@pytest.mark.parametrize("action", ["skip", "", "later", "unsure"])
def test_non_answer_is_not_remembered(mem, action):
    r = run_node(["P"])
    decide(r, [("P", action)])
    assert not mem.exists() or stored(mem) == {}


@pytest.mark.parametrize("verb", ["abort", "reject"])
def test_abandoned_gate_records_nothing(mem, verb):
    r = run_node(["P"])
    decide(r, [("P", "merge")], decision=verb, approved=False)
    assert not mem.exists() or stored(mem) == {}


@pytest.mark.parametrize("order", [("merge", "keep_separate"), ("keep_separate", "merge")])
def test_recorded_answer_equals_what_the_run_applied_on_conflicting_submission(mem, order):
    r = run_node(["P"])
    post = decide(r, [("P", order[0]), ("P", order[1])])
    merged = fz(r.ids, "P") in groups(post)
    assert decisions_in(mem) == (["merge"] if merged else ["keep_separate"])


def test_decision_for_a_pair_the_gate_never_showed_is_not_applied_wrongly(mem):
    """Pair not in the band: the decision must not corrupt the run (probe; recording is tolerated)."""
    r = run_node(["P", "X"])
    post = decide(r, [("X", "merge")])
    assert fz(r.ids, "P") not in groups(post)


# ── 4. a changed attribute re-asks (end to end) ──────────────────────────────────────────────
def _with_city(monkeypatch, city_of):
    orig = fx.planted

    def planted(keys):
        recs, ids = orig(keys)
        return [{**x, "city": city_of(x)} for x in recs], ids

    monkeypatch.setattr(fx, "planted", planted)


def test_changed_attribute_reasks_only_that_pair(mem, monkeypatch):
    _with_city(monkeypatch, lambda x: "Paris")
    r1 = run_node(["P", "Q"])
    decide(r1, [("P", "merge"), ("Q", "merge")])
    _with_city(monkeypatch, lambda x: "Lyon" if x["name"].startswith("Birch") else "Paris")
    r2 = run_node(["P", "Q"])
    assert pending_pairs(r2.pre) == {fz(r2.ids, "P")}, "changed record must be asked again"
    assert r2.pre["resolution_stats"]["company"]["decisions_from_memory"] == 1
    assert fz(r2.ids, "Q") in groups(r2.pre)
    assert fz(r2.ids, "P") not in groups(r2.pre)  # no stale merge applied to changed data


def test_a_cleared_attribute_also_reasks(mem, monkeypatch):
    _with_city(monkeypatch, lambda x: "Paris")
    r1 = run_node(["P"])
    decide(r1, [("P", "merge")])
    _with_city(monkeypatch, lambda x: None)
    r2 = run_node(["P"])
    assert pending_pairs(r2.pre) == {fz(r2.ids, "P")}


# ── 5. key stability: record order, pair order, hash seed, handle vs list ────────────────────
def test_record_order_and_pair_order_do_not_change_the_key(mem, monkeypatch):
    r = run_node(["P"])
    pair = fz(r.ids, "P")
    a, b = sorted(pair)
    k = question_key(node._band_question(r.pre, "company", pair))
    rev = {
        **r.pre,
        "extracted_data": {"company": list(reversed(r.pre["extracted_data"]["company"]))},
    }
    assert question_key(node._band_question(rev, "company", pair)) == k
    assert question_key(node._band_question(r.pre, "company", frozenset([b, a]))) == k


def test_run2_with_reversed_record_order_still_asks_zero(mem, monkeypatch):
    r1 = run_node(["P", "Q"])
    decide(r1, [("P", "merge"), ("Q", "keep_separate")])
    orig = fx.planted
    monkeypatch.setattr(
        fx,
        "planted",
        lambda keys: (lambda rc_ids: (list(reversed(rc_ids[0])), rc_ids[1]))(orig(keys)),
    )
    r2 = run_node(["P", "Q"])
    assert pending_pairs(r2.pre) == set()
    assert r2.pre["resolution_stats"]["company"]["decisions_from_memory"] == 2


def test_key_same_for_list_records_and_spooled_handle_records(mem):
    r = run_node(["P"])
    pair = fz(r.ids, "P")
    k = question_key(node._band_question(r.pre, "company", pair))
    recs = r.pre["extracted_data"]["company"]
    RunSpool("p15run").append("company", recs)
    handled = {**r.pre, "extracted_data": {"company": make_handle("p15run", "company", len(recs))}}
    assert question_key(node._band_question(handled, "company", pair)) == k


_CHILD = textwrap.dedent(
    """
    import os, sys, json, tempfile
    from pathlib import Path
    sys.path.insert(0, {root!r})
    import src.orchestrator.spool as sp
    sp.SPOOL_DIR = Path(tempfile.mkdtemp()) / "spool"
    from tests.part1.test_p15_acceptance import run_node, resume_manager, _pd
    r = run_node(["P", "Q"])
    resume_manager(r.pre, {{"decision": "approve", "approved": True,
        "pair_decisions": _pd("company", [(r.ids["P"], "merge"), (r.ids["Q"], "keep_separate")])}})
    """
)


def test_recorded_file_is_byte_identical_across_hash_seeds(tmp_path):
    outs = []
    for seed in ("0", "1", "4242", "random"):
        p = tmp_path / f"m_{seed}.json"
        env = {
            **os.environ,
            "PYTHONHASHSEED": seed,
            "MERGE_ENGINE_DECISION_MEMORY": str(p),
            "TMPDIR": str(tmp_path),
        }
        res = subprocess.run(
            [sys.executable, "-c", _CHILD.format(root=str(ROOT))],
            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=300,
        )  # fmt: skip
        assert res.returncode == 0, res.stderr[-2000:]
        outs.append(p.read_text())
    assert len(set(outs)) == 1, "keys/serialisation depend on the process hash seed"
    assert len(json.loads(outs[0])) == 2


# ── 6. concurrency and atomic replace ────────────────────────────────────────────────────────
def test_readers_never_see_a_torn_file_while_a_writer_replaces(tmp_path):
    p = tmp_path / "m.json"
    DecisionMemory(p).record({"q": 0}, "merge")
    stop, bad = threading.Event(), []

    def reader():
        while not stop.is_set():
            try:
                json.loads(p.read_text())
            except FileNotFoundError:
                bad.append("missing mid-replace")
            except ValueError as exc:
                bad.append(f"torn: {exc}")

    t = threading.Thread(target=reader)
    t.start()
    w = DecisionMemory(p)
    for i in range(300):
        w.record({"q": i}, "merge" if i % 2 else "keep_separate")
    stop.set()
    t.join()
    assert not bad, bad[:3]
    assert not [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]


def test_concurrent_writers_never_corrupt_the_file_or_leave_tmp_files(tmp_path):
    p = tmp_path / "m.json"
    errs = []

    def worker(i):
        try:
            m = DecisionMemory(p)
            for j in range(25):
                m.record({"w": i, "j": j}, "merge")
        except Exception as exc:
            errs.append(repr(exc))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs[:3]
    assert isinstance(json.loads(p.read_text()), dict)
    assert not [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
    assert len(DecisionMemory(p)._entries) >= 25  # at least one writer's full history


@pytest.mark.xfail(
    strict=True,
    reason="DOCUMENTED CEILING (report P9 / module ponytail): two live instances are last-writer-wins; an interleaved record is lost. Non-blocking, Part 3 trigger.",
)
def test_two_instances_do_not_lose_each_others_decisions(tmp_path):
    p = tmp_path / "m.json"
    a, b = DecisionMemory(p), DecisionMemory(p)
    a.record({"q": "x"}, "merge")
    b.record({"q": "y"}, "merge")
    fresh = DecisionMemory(p)
    assert fresh.lookup({"q": "x"}) == "merge" and fresh.lookup({"q": "y"}) == "merge"


# ── 7. corrupt / truncated / wrong-type files -> ask again, no crash, self-heal ──────────────
BAD = {
    "empty": "",
    "ws": "  \n ",
    "half": '{"abc": {"question": {"a": 1}, "decis',
    "garbage": "}{ not json",
    "list": "[]",
    "number": "7",
    "string": '"merge"',
    "null": "null",
    "entry_not_dict": '{"k": 1}',
    "entry_no_decision": '{"k": {"question": {}}}',
    "nul_bytes": "\x00" * 64,
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_bad_file_asks_again_then_self_heals(mem, name):
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text(BAD[name])
    r = run_node(["P"])
    assert pending_pairs(r.pre) == {fz(r.ids, "P")}
    assert _resolve_entities_router(r.pre) == "review"
    decide(r, [("P", "merge")])
    assert decisions_in(mem) == ["merge"]
    assert pending_pairs(run_node(["P"]).pre) == set()


def test_binary_file_asks_again(mem):
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_bytes(bytes(range(256)) * 4)
    r = run_node(["P"])
    assert pending_pairs(r.pre) == {fz(r.ids, "P")}


@pytest.mark.parametrize("bad", ["MERGE", 5, ["merge"], None, {"a": 1}, "yes", "approve", ""])
def test_wrong_typed_or_unknown_decision_value_is_asked_again_not_applied(mem, bad):
    r = run_node(["P"])
    decide(r, [("P", "merge")])
    data = stored(mem)
    for v in data.values():
        v["decision"] = bad
    mem.write_text(json.dumps(data))
    r2 = run_node(["P"])
    assert pending_pairs(r2.pre) == {fz(r2.ids, "P")}
    assert fz(r2.ids, "P") not in groups(r2.pre)


def test_path_is_a_directory_gate_still_works(tmp_path, monkeypatch):
    d = tmp_path / "isdir"
    d.mkdir()
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(d))
    r = run_node(["P"])
    assert pending_pairs(r.pre) == {fz(r.ids, "P")}
    post = decide(r, [("P", "merge")])
    assert fz(r.ids, "P") in groups(post)
    assert not [f for f in tmp_path.rglob("*") if f.suffix == ".tmp"]


def test_unwritable_parent_gate_applies_the_human_answer_and_leaves_no_tmp(tmp_path, monkeypatch):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(blocker / "sub" / "d.json"))
    r = run_node(["P", "Q"])
    post = decide(r, [("P", "merge"), ("Q", "keep_separate")])
    assert fz(r.ids, "P") in groups(post) and fz(r.ids, "Q") not in groups(post)
    assert blocker.read_text() == "x"


def test_write_failure_midway_keeps_old_file_and_gate_works(mem, monkeypatch):
    r = run_node(["P", "Q"])
    decide(r, [("P", "merge")])
    before = mem.read_text()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    r2 = run_node(["P", "Q"])  # P remembered, Q asked
    post = decide(r2, [("Q", "merge")])
    assert fz(r2.ids, "Q") in groups(post)
    assert mem.read_text() == before


def test_deeply_nested_garbage_file_does_not_crash_the_node(mem):
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text("[" * 200000)
    r = run_node(["P"])
    assert pending_pairs(r.pre) == {fz(r.ids, "P")}


# ── 8. off by default; precedence; no side effects ───────────────────────────────────────────
@pytest.mark.parametrize("env", [None, ""])
def test_off_by_default_creates_no_file_and_replays_nothing(tmp_path, monkeypatch, env):
    if env is None:
        monkeypatch.delenv("MERGE_ENGINE_DECISION_MEMORY", raising=False)
    else:
        monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", env)
    monkeypatch.chdir(tmp_path)
    r = run_node(["P", "Q"])
    decide(r, [("P", "merge"), ("Q", "merge")])
    r2 = run_node(["P", "Q"])
    assert pending_pairs(r2.pre) == {fz(r2.ids, "P"), fz(r2.ids, "Q")}
    assert "decisions_from_memory" not in r2.pre["resolution_stats"]["company"]
    leftovers = [
        f for f in tmp_path.rglob("*") if f.is_file() and "spool" not in f.parts and f.suffix in {".json", ".tmp"}
    ]  # fmt: skip
    assert leftovers == []


def test_state_key_enables_memory_and_wins_over_env(tmp_path, monkeypatch):
    env_p, st_p = tmp_path / "env.json", tmp_path / "state.json"
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(env_p))
    r = run_node(["P"])
    r.pre["decision_memory_path"] = str(st_p)
    decide(r, [("P", "merge")])
    assert st_p.exists() and not env_p.exists()
    r2 = run_node(["P"])
    assert pending_pairs(r2.pre) == {fz(r2.ids, "P")}  # env path is empty: state key not in run 2
    out = node.apply_remembered_decisions({**r2.pre, "decision_memory_path": str(st_p)})
    assert out["resolution_stats"]["company"]["decisions_from_memory"] == 1


def test_memory_is_per_entity_type_and_gate(mem):
    r = run_node(["P"])
    decide(r, [("P", "merge")])
    entry = next(iter(stored(mem).values()))
    assert entry["question"]["entity_type"] == "company"
    assert entry["question"]["gate"] == "human_review_matches"
    # same records under another entity type are a different question
    other = node._band_question(r.pre, "person", fz(r.ids, "P"))
    assert question_key(other) not in stored(mem)


# ── 9. design limitation (reported, not a hook-correctness bug) ──────────────────────────────
@pytest.mark.xfail(
    strict=True,
    reason="LIMITATION: only band pairs are looked up; a remembered keep_separate is not honoured when run 2 scores the same records >= auto_match, so the human's 'apart' is silently overridden",
)
def test_remembered_keep_separate_survives_a_score_shift_above_the_match_bar(mem):
    r1 = run_node(["P"])
    decide(r1, [("P", "keep_separate")])
    r2 = run_node(["P"], scores={"P": 0.99})
    assert fz(r2.ids, "P") not in groups(r2.pre)
