"""P8 attack (adversarial, written after the implementation, verifier).  Strict xfail = a defect found
(each reproduced against the parent 2f1e305 where it is a regression, or stated as pre-existing); the reason names it.
Controls (no xfail) pin behaviour that must keep holding.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p8_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import src.llm as llm_pkg
from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools import resolve_entities as tre
from tests.part1 import fixtures_matching as fx
from tests.part1 import test_p8_acceptance as acc

REPO = Path(__file__).resolve().parents[2]
HEUR = acc.HEUR


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(resolve_cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(resolve_cache, "put", lambda *a, **k: None)


@pytest.fixture
def installed(monkeypatch):
    def _install(stub):
        monkeypatch.setattr(llm_pkg, "LLMClient", lambda *a, **k: stub)
        return stub

    return _install


class Raw:
    """Client whose call_json returns whatever `fn(prompt)` says; records prompts."""

    def __init__(self, fn, delay=0.0):
        self.fn, self.delay, self.prompts = fn, delay, []
        self.in_flight = self.max_in_flight = 0

    async def call_json(self, system_prompt, user_prompt, **_k):
        self.prompts.append(user_prompt)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        return self.fn(user_prompt)


def _idx(prompt: str) -> int:
    return int(acc.TOKEN.search(prompt).group(2))


# ── 1. budget selection: determinism / ties / shifted band / NaN ───────────────────────────────
def _sent_for(scores, stub, monkeypatch, thresholds=(acc.AUTO_MATCH, acc.AUTO_EXCLUDE), order=None):
    scores = list(scores)
    res, band = acc.run_impl(scores, stub, monkeypatch, thresholds=thresholds)
    return acc.sent_set(stub), res, band


def _run_shuffled(scores, seed, installed, monkeypatch, thresholds):
    """Same band, presented to the resolver in a shuffled order; returns the set of sent pair NAMES."""
    stub = installed(acc.Stub())
    band = acc.make_pairs(len(scores), scores)
    order = list(range(len(band)))
    random.Random(seed).shuffle(order)
    shuffled = [band[i] for i in order]
    fake = {
        "auto_match": thresholds[0],
        "auto_exclude": thresholds[1],
        "candidate_pair_count": len(band),
        "blocking_stats": {},
        "scored_pairs": [],
        "borderline": shuffled,
        "pair_evidence": {},
        "model_fitted": False,
        "possibly_related": [],
        "blocking_seconds": 0.0,
        "fit_model_seconds": 0.0,
        "scoring_seconds": 0.0,
    }
    monkeypatch.setattr(tre, "_resolve_core", lambda *a, **k: dict(fake, borderline=list(shuffled)))
    recs = [r for _a, _b, ra, rb, _s in band for r in (ra, rb)]
    asyncio.run(tre.resolve_entities_impl("company", recs, llm_config={"provider": "x"}))
    return acc.sent_set(stub)


def test_control_ties_in_distance_are_order_independent(installed, monkeypatch):
    """All 260 pairs tie on distance from the middle: the sent set must not depend on input order."""
    scores = [0.8] * 260
    ref = _run_shuffled(scores, 0, installed, monkeypatch, (acc.AUTO_MATCH, acc.AUTO_EXCLUDE))
    assert len(ref) == tre._MAX_LLM_PAIRS
    for seed in (1, 2, 3):
        assert (
            _run_shuffled(scores, seed, installed, monkeypatch, (acc.AUTO_MATCH, acc.AUTO_EXCLUDE))
            == ref
        )


def test_control_mirror_ties_order_independent(installed, monkeypatch):
    """Scores mirrored around the middle (0.80 / 0.85): same set whatever the input order."""
    scores = [0.80, 0.85] * 130
    ref = _run_shuffled(scores, 0, installed, monkeypatch, (acc.AUTO_MATCH, acc.AUTO_EXCLUDE))
    assert all(
        _run_shuffled(scores, s, installed, monkeypatch, (acc.AUTO_MATCH, acc.AUTO_EXCLUDE)) == ref
        for s in (7, 8)
    )


def test_control_budget_follows_operator_band_not_defaults(installed, monkeypatch):
    """Operator rules move the band to [0.60, 0.90): the 200 sent are those nearest 0.75 (the new middle),
    not nearest 0.825 (the default middle)."""
    rng = random.Random(3)
    scores = [round(rng.uniform(0.61, 0.89), 6) for _ in range(300)]
    stub = installed(acc.Stub())
    sent, _res, _band = _sent_for(scores, stub, monkeypatch, thresholds=(0.90, 0.60))
    assert len(sent) == tre._MAX_LLM_PAIRS
    far_in = {i for i in range(300)} - sent
    assert (
        max(abs(scores[i] - 0.75) for i in sent)
        <= min(abs(scores[i] - 0.75) for i in far_in) + 1e-12
    )
    # and it is NOT the default-middle selection
    dflt = set(sorted(range(300), key=lambda i: abs(scores[i] - 0.825))[:200])
    assert sent != dflt


def test_control_nan_heuristic_unreachable_via_real_core():
    """The band filter `auto_exclude <= conf < auto_match` is False for NaN, so NaN never reaches the LLM sort
    in a real run (the sort key is therefore safe)."""
    nan = float("nan")
    assert not (tre._AUTO_EXCLUDE <= nan < tre._AUTO_MATCH)


@pytest.mark.xfail(
    strict=True,
    reason="latent (unreachable via the real core): a NaN heuristic score in an over-budget band makes the "
    "sort key NaN, so WHICH 200 pairs are sent depends on input order. Low severity.",
)
def test_nan_in_overbudget_band_is_order_independent(installed, monkeypatch):
    scores = [0.8 + 0.0001 * (i % 50) for i in range(259)] + [float("nan")]
    sets = [
        _run_shuffled(scores, s, installed, monkeypatch, (acc.AUTO_MATCH, acc.AUTO_EXCLUDE))
        for s in range(6)
    ]
    assert all(s == sets[0] for s in sets)


# ── 2. reply shapes the stub of the acceptance file does not cover ─────────────────────────────
def _judge(pairs, stub, installed, sink=None):
    installed(stub)
    return acc.judge(pairs, sink=sink)


def test_control_call_json_returning_a_json_string_damages_only_that_pair(installed):
    def fn(prompt):
        i = _idx(prompt)
        return json.dumps({"probability": 0.99}) if i == 3 else {"probability": 0.99, "reason": "r"}

    sink: dict = {}
    res = acc.by_idx(_judge(acc.make_pairs(8), Raw(fn), installed, sink))
    assert res[3] == pytest.approx(HEUR)
    assert all(res[i] == pytest.approx(0.99) for i in range(8) if i != 3)
    assert len(sink) == 7


@pytest.mark.parametrize("reason", [None, 7, {"x": 1}, ["a"], "", "ok"])
def test_control_reason_any_type_never_breaks_the_pair(installed, reason):
    sink: dict = {}
    res = _judge(
        acc.make_pairs(2), Raw(lambda p: {"probability": 0.9, "reason": reason}), installed, sink
    )
    assert len(res) == 2 and all(
        e["method"] == "llm" and isinstance(e["reason"], str) for e in sink.values()
    )
    assert all(s == pytest.approx(0.9) for *_x, s in res)


def test_control_reason_missing_is_empty_string(installed):
    sink: dict = {}
    _judge(acc.make_pairs(1), Raw(lambda p: {"probability": 0.9}), installed, sink)
    assert next(iter(sink.values()))["reason"] == ""


def test_reason_null_is_not_the_string_none(installed):
    sink: dict = {}
    _judge(acc.make_pairs(1), Raw(lambda p: {"probability": 0.9, "reason": None}), installed, sink)
    assert next(iter(sink.values()))["reason"] != "None"


@pytest.mark.parametrize("p", [0, 1, 0.0, 1.0, 0.95, 0.7, 0.6999, 0.9499])
def test_control_boundary_probabilities_follow_the_committed_band(installed, p):
    (res,) = _judge(acc.make_pairs(1), Raw(lambda _p: {"probability": p}), installed)
    s = res[2]
    assert s == pytest.approx(float(p))
    verdict = "merge" if s >= acc.AUTO_MATCH else ("apart" if s < acc.AUTO_EXCLUDE else "review")
    want = "merge" if p >= 0.95 else ("apart" if p < 0.7 else "review")
    assert verdict == want


def test_control_numeric_string_probability_is_not_trusted(installed):
    (res,) = _judge(acc.make_pairs(1), Raw(lambda _p: {"probability": "0.99"}), installed)
    assert res[2] == pytest.approx(HEUR)


def test_control_evidence_probability_in_unit_interval_and_matches_score(installed):
    sink: dict = {}
    res = _judge(
        acc.make_pairs(5), Raw(lambda p: {"probability": 0.1 + 0.2 * _idx(p)}), installed, sink
    )
    scores = {(a, b): s for a, b, s in res}
    for (a, b), ev in sink.items():
        assert 0 <= ev["confidence"] <= 1 and ev["llm_probability"] == ev["confidence"]
        assert any(abs(s - ev["confidence"]) < 1e-12 for s in scores.values())


# ── 3. prompts / harness / consumers ──────────────────────────────────────────────────────────
def test_control_no_batch_vocabulary_in_prompt_files_or_sent_prompts(installed):
    for f in (REPO / "src/llm/prompts").glob("entity_resolution.*.txt"):
        t = f.read_text()
        for bad in (
            "judgments",
            "pair_id",
            "{pair_count}",
            "numbered",
            "Pair 1",
            "each pair",
            "in the same order",
        ):
            assert bad not in t, f"{f.name} still mentions {bad!r}"
    stub = Raw(lambda p: {"probability": 0.9})
    _judge(acc.make_pairs(3), stub, installed)
    for p in stub.prompts:
        assert not re.search(r"Pair \d|\{pair_count\}|\{pairs_block\}|\{entity_type\}", p)
        assert p.count("Record A:") == 1 and p.count("Record B:") == 1


def test_control_prompt_harness_offline_passes():
    r = subprocess.run(
        [sys.executable, "-m", "tests.prompts.harness"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0 and "entity_resolution" in r.stdout, r.stdout + r.stderr


def test_control_no_consumer_of_removed_llm_match_field():
    hits = []
    for root in ("src", "scripts", "docs"):
        for p in (REPO / root).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".ts", ".tsx", ".js", ".md", ".json", ".txt"}:
                if "node_modules" in p.parts or "dist" in p.parts:
                    continue
                if "llm_match" in p.read_text(errors="ignore"):
                    hits.append(str(p.relative_to(REPO)))
    assert hits == [], hits


def test_control_golden_split_preserves_every_pair_and_verdict():
    old = subprocess.run(
        ["git", "show", "2f1e305:tests/prompts/golden/entity_resolution.json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    o = json.loads(old)["cases"]
    n = json.loads((REPO / "tests/prompts/golden/entity_resolution.json").read_text())["cases"]
    flat_o = [
        (json.dumps(p, sort_keys=True), m)
        for c in o
        for p, m in zip(c["input"]["pairs"], c["expected"]["matches"])
    ]
    flat_n = [
        (json.dumps(c["input"]["pairs"][0], sort_keys=True), c["expected"]["matches"][0]) for c in n
    ]
    assert all(len(c["input"]["pairs"]) == 1 for c in n) and flat_o == flat_n


# ── 4. throughput ─────────────────────────────────────────────────────────────────────────────
def test_control_1000_pairs_concurrency_four_throughput(installed):
    stub = installed(Raw(lambda p: {"probability": 0.9}, delay=0.01))
    pairs = acc.make_pairs(1000)
    t0 = time.perf_counter()
    res = acc.judge(pairs)
    dt = time.perf_counter() - t0
    assert len(res) == 1000 and len(stub.prompts) == 1000
    assert stub.max_in_flight == tre._LLM_CONCURRENCY == 4
    assert dt < 4.5, f"{dt:.2f}s for 1000 pairs at 10ms/4-wide (ideal 2.5s)"


def test_control_overbudget_band_sends_exactly_budget_and_keeps_rest_in_review(
    installed, monkeypatch
):
    stub = installed(acc.Stub(default=0.99))
    res, band = acc.run_impl([0.8] * 1000, stub, monkeypatch)
    assert sum(len(c) for c in stub.calls) == tre._MAX_LLM_PAIRS == len(stub.calls)
    assert res["llm_scored"] == tre._MAX_LLM_PAIRS
    assert len(res["pending_merge_candidates"]) == min(
        tre._MAX_PENDING_CANDIDATES, 1000 - tre._MAX_LLM_PAIRS
    )
    assert res["band_summary"]["predicted_counts"]["possible"] == 1000 - tre._MAX_LLM_PAIRS


# ── 5. semantics vs parent 2f1e305 (see .handoff/P8.verdict.md) ──────────────────────────────
def _band_run(p, case="b_same_address_diff_tax_id"):
    recs = fx.p6_cases()[case]["records"] + fx.p6_fillers()
    res = asyncio.run(tre.resolve_entities_impl("company", recs, llm_config={"provider": "x"}))
    merged = [
        c
        for c in res["clusters"]
        if len(c["record_ids"]) > 1 and any(r.endswith(("2", "4")) for r in c["record_ids"])
    ]
    return res, merged


def _patch_p(monkeypatch, p):
    monkeypatch.setattr(
        llm_pkg, "LLMClient", lambda *a, **k: Raw(lambda _u: {"probability": p, "reason": "r"})
    )


def test_control_high_probability_merges_like_parent_and_low_stays_apart(monkeypatch):
    """Parent (match=true, conf=0.99 -> merge; match=false -> apart) gave the same outcomes on the P6 fixture."""
    _patch_p(monkeypatch, 0.99)
    res, _ = _band_run(0.99)
    assert res["pending_merge_candidates"] == [] and res["llm_scored"] == 1
    _patch_p(monkeypatch, 0.01)
    res, _ = _band_run(0.01)
    assert res["pending_merge_candidates"] == [] and res["llm_scored"] == 1


def test_control_probability_in_band_goes_to_review_on_p6_fixture(monkeypatch):
    _patch_p(monkeypatch, 0.8)
    res, _ = _band_run(0.8)
    assert len(res["pending_merge_candidates"]) == 1


def test_unsure_llm_answer_does_not_remove_pair_from_human_review(monkeypatch):
    _patch_p(monkeypatch, 0.5)
    res, _ = _band_run(0.5)
    assert len(res["pending_merge_candidates"]) == 1
