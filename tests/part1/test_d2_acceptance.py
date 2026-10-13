"""D2 acceptance: band answers (P15) and decision memory (P9) must survive the REST API.

Derived from docs/plans/part1/LOCAL_VERIFY_REPORT.md section D2 (Evidence + Fix) only; the fix was not read.

Defect: ``Feedback`` (src/api/main.py) has no ``pair_decisions`` / ``band_thresholds`` field, pydantic drops them,
the route forwards only the fields it lists, so ``WorkflowManager.resume_workflow`` -> ``apply_band_decisions``
never sees them; the React matches gate never sends them either.  Live: 3 oracle "merge" answers through
POST /api/workflow/{id}/feedback -> 3 pairs still apart, no memory file.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d2_acceptance.py -q -p no:cacheprovider

Groups
  A  API contract (manager mocked, narrowest seam = ``get_manager``): both fields forwarded in the exact shapes
     ``apply_band_decisions`` documents; omitted fields are NOT injected; wrong types -> 422, never dropped.
  B  End to end through the real route + real WorkflowManager.resume_workflow + real apply_band_decisions.
     MOCKED: only the LangGraph object (``build_graph`` -> MagicMock: ``get_state`` returns the pre-gate state the
     real ``resolve_entities`` node produced; ``update_state`` is read back) and ``_continue_workflow`` (the
     background stream).  The independent check is ``clusters_for(state)`` on the state handed to
     ``update_state(as_node="human_review_matches")`` and the decision-memory file on disk, not any HTTP body.
  C  Frontend (GREP-LEVEL ONLY, no browser): the matches gate sends ``pair_decisions`` in its feedback body.
  D  Plan 2.4 "no fitted constants": added lines since the pinned base carry no new numeric/corpus literal.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.orchestrator.spool as spool_mod
from src.api.main import create_api
from src.orchestrator.workflow_manager import WorkflowManager
from tests.part1.test_p15_acceptance import fz, groups, pending_pairs, run_node

ROOT = Path(__file__).resolve().parents[2]
BASE = "82e209b"  # HEAD when D2 was handed to the worker; the diff below is "the D2 fix"
node = importlib.import_module("src.orchestrator.nodes.resolve_entities")
GATE = "human_review_matches"
TID = "d2thread"

PLAIN_APPROVE_SENT = {"approved": True, "decision": "approve", "node": GATE}


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    monkeypatch.delenv("MERGE_ENGINE_DECISION_MEMORY", raising=False)
    yield


# ── A. API contract ──────────────────────────────────────────────────────────────────────────
def _post_mock(body, node_name=GATE):
    """POST /feedback with a MagicMock manager; returns (response, feedback dict handed to resume_workflow)."""
    mgr = MagicMock()
    mgr.get_workflow_status.return_value = {
        "status": "interrupted",
        "current_interrupt_node": node_name,
    }
    with patch("src.api.main.get_manager", return_value=mgr):
        r = TestClient(create_api()).post(f"/api/workflow/{TID}/feedback", json=body)
    sent = mgr.resume_workflow.call_args.args[1] if mgr.resume_workflow.called else None
    return r, sent, mgr


PAIRS = [
    # one extra key per item on purpose: the route must not re-shape what apply_band_decisions reads
    {"entity_type": "person", "record_ids": ["fa:t:1", "fb:t:1"], "action": "merge", "note": "x"},
    {"entity_type": "person", "record_ids": ["fa:t:2", "fb:t:2"], "action": "keep_separate"},
]
THRESH = {"person": {"auto_match": 0.9, "auto_exclude": 0.6}}


def test_a1_pair_decisions_forwarded_unchanged():
    r, sent, _ = _post_mock({"decision": "approve", "pair_decisions": PAIRS})
    assert r.status_code == 200
    assert sent is not None and sent.get("pair_decisions") == PAIRS


def test_a2_band_thresholds_forwarded_unchanged():
    r, sent, _ = _post_mock({"decision": "approve", "band_thresholds": THRESH})
    assert r.status_code == 200
    assert sent is not None and sent.get("band_thresholds") == THRESH


def test_a3_both_together_with_the_ordinary_keys_intact():
    r, sent, _ = _post_mock(
        {"decision": "approve", "pair_decisions": PAIRS, "band_thresholds": THRESH}
    )
    assert r.status_code == 200
    assert sent is not None
    assert sent["pair_decisions"] == PAIRS and sent["band_thresholds"] == THRESH
    assert (sent["approved"], sent["decision"], sent["node"]) == (True, "approve", GATE)


def test_a4_plain_approve_is_byte_identical_to_before():
    """GUARD: omitted fields must not be injected (an empty list/dict would still be falsy for
    ``resolves_band`` today, but a future ``is not None`` check would turn it into 'keep every pair apart')."""
    r, sent, _ = _post_mock({"decision": "approve"})
    assert r.status_code == 200
    assert sent == PLAIN_APPROVE_SENT


def test_a5_explicit_nulls_are_not_injected_either():
    """GUARD: the React client sends ``null`` for everything it has nothing for (api.ts)."""
    r, sent, _ = _post_mock(
        {"decision": "approve", "pair_decisions": None, "band_thresholds": None, "thresholds": None}
    )
    assert r.status_code == 200
    assert sent == PLAIN_APPROVE_SENT


def test_a6_other_gate_fields_still_forwarded_when_band_fields_present():
    """GUARD: adding the two fields must not displace cluster_decisions / reason."""
    cd = [{"entity_type": "person", "record_ids": ["a", "b"], "action": "split"}]
    r, sent, _ = _post_mock(
        {"decision": "approve", "reason": "ok", "cluster_decisions": cd, "pair_decisions": PAIRS}
    )
    assert r.status_code == 200 and sent is not None
    assert sent["cluster_decisions"] == cd and sent["reason"] == "ok"
    assert sent.get("pair_decisions") == PAIRS


@pytest.mark.parametrize(
    "bad",
    [
        {"pair_decisions": "merge everything"},
        {"pair_decisions": {"record_ids": ["a", "b"], "action": "merge"}},  # one dict, not a list
        {"pair_decisions": [1, 2, 3]},
        {"pair_decisions": ["a|b"]},
        {"band_thresholds": "0.9"},
        {"band_thresholds": [0.9, 0.7]},
    ],
    ids=["str", "bare-dict", "list-of-int", "list-of-str", "thresholds-str", "thresholds-list"],
)
def test_a7_wrong_type_is_422_and_never_resumes(bad):
    """Today pydantic drops the unknown key, so these come back 200 and resume as a silent plain approve --
    exactly the 'accepted and ignored' failure the report describes."""
    r, sent, mgr = _post_mock({"decision": "approve", **bad})
    assert r.status_code == 422, f"{bad} -> {r.status_code}; resumed with {sent}"
    assert not mgr.resume_workflow.called


# ── B. end to end: real route -> real resume_workflow -> real apply_band_decisions ───────────
def _api_resume(pre, body, node_name=GATE):
    """POST ``body`` through the real route into a REAL WorkflowManager over a mocked graph.
    Returns (response, state as handed to graph.update_state(as_node=gate))."""
    with patch("src.orchestrator.workflow_manager.build_graph") as mb:
        mb.return_value = MagicMock()
        mgr = WorkflowManager()
    mgr.graph.get_state.return_value = MagicMock(values=dict(pre), next=())
    mgr._active_workflows[TID] = {
        "config": {"configurable": {"thread_id": TID}},
        "current_interrupt_node": node_name,
        "status": "interrupted",
        "interrupt_data": None,
    }
    mgr._continue_workflow = lambda *a, **k: None  # the background stream is not under test
    with patch("src.api.main.get_manager", return_value=mgr):
        r = TestClient(create_api()).post(f"/api/workflow/{TID}/feedback", json=body)
    call = mgr.graph.update_state.call_args
    post = None
    if call is not None:
        assert call.kwargs.get("as_node") == node_name
        post = {**pre, **call.args[1]}
    return r, post


def _pd(ids, action):
    return {"entity_type": "company", "record_ids": list(ids), "action": action}


@pytest.fixture
def band_run():
    run = run_node(["P", "Q", "M", "X"], "heuristic")
    # precondition (independent of the API): P and Q are band pairs, apart; M is a clear match
    assert {fz(run.ids, "P"), fz(run.ids, "Q")} <= pending_pairs(run.pre)
    assert fz(run.ids, "P") not in groups(run.pre) and fz(run.ids, "M") in groups(run.pre)
    return run


def test_b1_merge_answer_via_api_puts_the_pair_in_one_cluster(band_run):
    r, post = _api_resume(
        band_run.pre,
        {"decision": "approve", "pair_decisions": [_pd(band_run.ids["P"], "merge")]},
    )
    assert r.status_code == 200 and post is not None
    assert fz(band_run.ids, "P") in groups(post), "merge answered over REST, pair still apart"
    assert fz(band_run.ids, "M") in groups(post)
    assert fz(band_run.ids, "X") not in groups(post)
    assert not pending_pairs(post)


def test_b2_per_pair_answers_via_api_merge_one_keep_the_other(band_run):
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "pair_decisions": [
                _pd(band_run.ids["P"], "merge"),
                _pd(band_run.ids["Q"], "keep_separate"),
            ],
        },
    )
    assert r.status_code == 200 and post is not None
    assert fz(band_run.ids, "P") in groups(post)
    assert fz(band_run.ids, "Q") not in groups(post)
    assert not pending_pairs(post), "answered pairs must not be asked again"


def test_b3_plain_approve_via_api_still_merges_nothing_extra(band_run):
    """GUARD: an approve without band answers leaves the band pairs apart (unintended merges are unrecoverable)."""
    r, post = _api_resume(band_run.pre, {"decision": "approve"})
    assert r.status_code == 200 and post is not None
    assert fz(band_run.ids, "P") not in groups(post)
    assert fz(band_run.ids, "Q") not in groups(post)
    assert fz(band_run.ids, "M") in groups(post)


def test_b4_empty_answers_do_not_merge_anything(band_run):
    """The repro sends ``band_thresholds: {}``: empty must mean 'no answer', never 'merge' or 'merge all'."""
    r, post = _api_resume(
        band_run.pre,
        {"decision": "approve", "pair_decisions": [], "band_thresholds": {}},
    )
    assert r.status_code == 200 and post is not None
    assert fz(band_run.ids, "P") not in groups(post)
    assert fz(band_run.ids, "Q") not in groups(post)


def test_b5_committed_thresholds_via_api_move_the_band(band_run):
    """Lower the match bar below P's planted 0.80 (score read from the run's own ledger, not typed here)."""
    p_score = band_run.ledger[fz(band_run.ids, "P")]
    q_score = band_run.ledger[fz(band_run.ids, "Q")]
    bar = min(p_score, q_score) - 0.01
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "band_thresholds": {"company": {"auto_match": bar, "auto_exclude": bar / 2}},
        },
    )
    assert r.status_code == 200 and post is not None
    assert fz(band_run.ids, "P") in groups(post), (
        "committed bar below the pair's score had no effect"
    )
    assert fz(band_run.ids, "Q") in groups(post)
    assert not pending_pairs(post)


def test_b6_nonsense_thresholds_via_api_do_not_merge_the_band(band_run):
    """GUARD-ish: exclude >= match is refused by apply_band_decisions; the route must not turn it into a merge."""
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "band_thresholds": {"company": {"auto_match": 0.5, "auto_exclude": 0.9}},
        },
    )
    assert r.status_code == 200
    if post is not None:
        assert fz(band_run.ids, "P") not in groups(post)


def test_b7_decision_memory_written_via_api_and_replayed(band_run, tmp_path, monkeypatch):
    """P9 half of D2: 'the memory file was never created'.  Answer P=merge, Q=keep over REST with a memory
    configured; the file must hold exactly those 2 entries; a fresh run asks neither again."""
    mem = tmp_path / "mem" / "decisions.json"
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(mem))
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "pair_decisions": [
                _pd(band_run.ids["P"], "merge"),
                _pd(band_run.ids["Q"], "keep_separate"),
            ],
        },
    )
    assert r.status_code == 200 and post is not None
    assert mem.exists(), "no decision-memory file after band answers over REST"
    entries = json.loads(mem.read_text(encoding="utf-8"))
    assert len(entries) == 2
    assert sorted(e["decision"] for e in entries.values()) == ["keep_separate", "merge"]

    # run 2: same data, same memory, nothing answered -> both resolved without asking
    run2 = run_node(["P", "Q", "M", "X"], "heuristic")
    update = node.apply_remembered_decisions(run2.pre)
    post2 = {**run2.pre, **update}
    assert fz(run2.ids, "P") in groups(post2) and fz(run2.ids, "Q") not in groups(post2)
    assert not pending_pairs(post2), (
        "second run asks again although the answers are in the memory file"
    )
    assert post2["resolution_stats"]["company"]["decisions_from_memory"] == 2


def test_b8_no_memory_configured_means_no_file_and_no_error(band_run, tmp_path):
    """GUARD: memory is off by default; answering over REST must not create files in cwd/tmp."""
    before = set(tmp_path.rglob("*.json"))
    r, post = _api_resume(
        band_run.pre,
        {"decision": "approve", "pair_decisions": [_pd(band_run.ids["P"], "keep_separate")]},
    )
    assert r.status_code == 200
    assert set(tmp_path.rglob("*.json")) == before


# ── C. frontend (grep-level) ─────────────────────────────────────────────────────────────────
FE = ROOT / "src" / "ui" / "frontend" / "src"


def _fe(name):
    return (FE / name).read_text(encoding="utf-8")


def _match_body(gates):
    m = re.search(r"function MatchBody\b(.*?)(?=\n// ──|\nfunction [A-Z])", gates, re.S)
    assert m, "MatchBody not found in Gates.tsx"
    return m.group(1)


def test_c1_api_ts_sends_pair_decisions_in_the_feedback_body():
    """GREP-LEVEL: the request body literal of sendFeedback carries the snake_case key the API reads."""
    api = _fe("api.ts")
    send = re.search(r"export function sendFeedback\b.*?\n}\n", api, re.S)
    assert send, "sendFeedback not found"
    assert "pair_decisions" in send.group(0), "sendFeedback never puts pair_decisions in the body"


def test_c2_matches_gate_offers_merge_and_keep_per_pair():
    """GREP-LEVEL: MatchBody must render per-pair merge / keep controls and hand the answers to onApprove."""
    body = _match_body(_fe("components/Gates.tsx"))
    assert "pending_merge_candidates" in body, "the gate never lists the band pairs to decide"
    assert "keep_separate" in body and re.search(r"\bmerge\b", body)
    assert "onApprove" in body, "MatchBody cannot send anything: onApprove is not in its props"


def test_c3_pipeline_screen_passes_the_answers_on_to_sendfeedback():
    """GREP-LEVEL: PipelineScreen.onApprove must carry the matches gate's answers into sendFeedback (the way it
    already does for mapping edits / deps / scope / thresholds), not drop them between Gates.tsx and api.ts."""
    ps = _fe("components/PipelineScreen.tsx")
    on_approve = re.search(r"async function onApprove\b.*?\n  }\n", ps, re.S)
    assert on_approve, "onApprove not found"
    assert re.search(r"pair_?decisions", on_approve.group(0), re.I), (
        "onApprove never mentions the pair decisions: they cannot reach sendFeedback"
    )


def test_c4_existing_feedback_keys_still_sent():
    """GUARD: the other gates' payload keys survive the edit."""
    api = _fe("api.ts")
    for key in ("mapping_edits", "column_dependencies", "scope_plan", "thresholds"):
        assert key in api


# ── D. plan 2.4: no fitted constants in the D2 diff ──────────────────────────────────────────
def _added_lines():
    out = subprocess.run(
        ["git", "diff", "-U0", BASE, "--", "src/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ln[1:] for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def _code(line):
    line = re.sub(r"#.*$|//.*$", "", line)
    return re.sub(r"(\"[^\"]*\"|'[^']*')", '""', line)  # numbers inside strings/comments are prose


def test_d1_no_new_numeric_literal_in_the_diff():
    """The fix is plumbing: a new field, a forward, UI controls.  Any number other than 0/1/2 (and an HTTP 422 the
    route already uses) would be a threshold/cap/default a corpus count would not produce."""
    bad = []
    for ln in _added_lines():
        for tok in re.findall(r"(?<![\w.])(\d+\.\d+|\d+)(?![\w.])", _code(ln)):
            if tok not in {"0", "1", "2", "422"}:
                bad.append((tok, ln.strip()[:80]))
    assert not bad, f"new numeric literals in the D2 diff (plan 2.4): {bad}"


def test_d2_no_band_constants_retyped_and_no_corpus_names():
    txt = "\n".join(_code(ln) for ln in _added_lines())
    assert not re.search(r"0?\.(95|70|85)\b", txt), "band bar re-typed in the diff"
    corpus = re.compile(r"febrl|abt|buy|dblp|amazon|musicbrainz|pagila|leipzig", re.I)
    assert not corpus.search("\n".join(_added_lines())), "benchmark/corpus name in the diff"
