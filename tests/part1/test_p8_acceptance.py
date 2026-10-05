"""P8 acceptance (derived from the report's Evidence + Fix text only; no P8 code seen).

Report P8: the LLM budget is spent on the wrong pairs.  Fix: (1) ONE pair per call, returning a
calibrated PROBABILITY (not a free-form score); (2) target the UNCERTAIN BAND (P15's review band
auto_exclude <= score < auto_match, from `_thresholds_for`), not "closest to auto-match first, top-N";
(3) keep concurrency (bounded, more than one call in flight).  Why 40-per-prompt is wrong: one response
covers forty decisions and a single formatting failure damages all forty.  Ceiling: a model's number is
not calibrated merely because it is in [0,1] -- calibration is an open item (P13 / 2.17); NOT tested,
NOT invented here.  Live endpoint behaviour is `unverified-live`.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p8_acceptance.py -q -p no:cacheprovider

CONTRACT (the report fixes semantics, not interfaces; this is the minimal one -- see
.handoff/P8.acceptance.md):

    await tools.resolve_entities._judge_pairs_with_llm(entity_type, borderline, llm_config, evidence_sink=None)
        borderline = [(id1, id2, rec1, rec2, heuristic_score), ...]      (signature unchanged)
        -> [(id1, id2, score), ...]   exactly one tuple per input pair; a pair the LLM could not
                                      judge keeps its heuristic score; the pair ids are preserved
    await tools.resolve_entities.resolve_entities_impl("company", records, llm_config=..., matching_rules=...)
        end to end; its scoring core `_resolve_core` is replaced by a fake that returns a chosen
        band (so the selection rule is observed, not the scorer).
    LLM client: `src.llm.LLMClient(llm_config)`; the stub implements `call_json(system_prompt, user_prompt)`
        and `call(...)`.  A pair is identified in a prompt by a unique token in its records' name field.
    response, one pair per call:  {"probability": p}  (p = calibrated P(match), a float in [0, 1]).
        The report names no field; the stub sets every key in PROB_KEYS to the same p.  The stub ALSO
        sets decoy legacy fields (`match` inverted, `confidence` 0.5) so that an implementation reading
        the old free-form fields instead of the probability fails.
    malformed = anything that is not a finite float in [0, 1] under a probability key (raise, non-JSON,
        non-dict, missing key, None, NaN, inf, out of range, bool): that ONE pair keeps its heuristic
        score; every other pair is still judged.

ADAPTATION POINTS (one place each):
  PROB_KEYS            the response field name(s) for the probability.
  PRE_P8_REF           the pre-P8 commit the 2.4 baselines are read from (git show).
  installed()          how the stub client is injected (src.llm.LLMClient, and tre.LLMClient if it exists).
  judge()              how `_judge_pairs_with_llm` is called (add arguments here if P8 adds any).
  run_impl()           how the band is injected (fake `_resolve_core`) and the end-to-end call.
  SRC                  the source file the 2.4 greps read (env P8_SRC, for oracle checks only).
  JUDGEMENT tests      marked in their docstring; relax only that parametrization, nothing else.
"""

from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

import src.llm as llm_pkg
from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools import resolve_entities as tre

REPO = Path(__file__).resolve().parents[2]
SRC = Path(os.environ.get("P8_SRC", REPO / "src/mcp_server/tools/resolve_entities.py"))
PRE_P8_REF = "21cd31f"
PROB_KEYS = ("probability", "p_match", "match_probability")

AUTO_MATCH = tre._AUTO_MATCH
AUTO_EXCLUDE = tre._AUTO_EXCLUDE
OLD_CAP = 200  # the report's `_MAX_LLM_PAIRS`; only used to size the budget-binding fixture
TOKEN = re.compile(r"(ZQ|DC)(\d{4})")
HEUR = 0.8  # heuristic score of every pair in the direct tests (inside the default band)


# ── stub LLM ─────────────────────────────────────────────────────────────────────────────────
class Stub:
    """Records every call; answers per pair index.  spec[idx] = float | ("raise", exc) | ("raw", obj)."""

    def __init__(self, spec=None, default=0.99, delay=0.0, delays=None):
        self.spec = spec or {}
        self.default = default
        self.delay = delay
        self.delays = delays or {}
        self.calls: list[tuple[int, ...]] = []  # ZQ pair indices carried by each call
        self.decided_seen: list[int] = []  # DC (decided-pair) indices seen in any prompt
        self.prompts: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def _body(self, p: float, n: int = 1) -> dict:
        legacy = {"match": not (p >= 0.5), "confidence": 0.5, "reason": "stub"}
        flat = {k: p for k in PROB_KEYS}
        return {**flat, **legacy, "judgments": [{"pair_id": n, **flat, **legacy}]}

    async def _handle(self, system_prompt: str, user_prompt: str):
        found = [(m.group(1), int(m.group(2))) for m in TOKEN.finditer(user_prompt)]
        idxs = tuple(dict.fromkeys(i for k, i in found if k == "ZQ"))
        self.decided_seen += [i for k, i in found if k == "DC"]
        self.calls.append(idxs)
        self.prompts.append(user_prompt)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(max([self.delay, *(self.delays.get(i, 0.0) for i in idxs)]))
        finally:
            self.in_flight -= 1
        specs = [self.spec.get(i, self.default) for i in idxs]
        bad = [s for s in specs if not isinstance(s, float)]
        if bad:  # a failing response to a call that carries it
            kind, payload = bad[0]
            return kind, payload
        if len(idxs) == 1:
            return "ok", self._body(specs[0])
        # a batched prompt (pre-P8 shape): numbered judgments, one per pair, in prompt order
        legacy = [
            {"pair_id": n, **{k: p for k in PROB_KEYS}, "match": not (p >= 0.5), "confidence": 0.5}
            for n, p in enumerate(specs, 1)
        ]
        return "ok", {"judgments": legacy}

    async def call_json(self, system_prompt: str, user_prompt: str, **_kw):
        kind, payload = await self._handle(system_prompt, user_prompt)
        if kind == "raise":
            raise payload
        return payload

    async def call(self, system_prompt: str, user_prompt: str, **_kw):
        kind, payload = await self._handle(system_prompt, user_prompt)
        if kind == "raise":
            raise payload
        return payload if isinstance(payload, str) else json.dumps(payload)


@pytest.fixture
def installed(monkeypatch):
    def _install(stub: Stub) -> Stub:
        def factory(*_a, **_k):
            return stub

        monkeypatch.setattr(llm_pkg, "LLMClient", factory)
        if hasattr(tre, "LLMClient"):
            monkeypatch.setattr(tre, "LLMClient", factory)
        return stub

    return _install


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────
def _rec(tag: str, secret: bool = True) -> dict:
    r = {
        "_origin_id": "o",
        "_origin_table": "t",
        "_origin_id_row": tag,
        "name": f"{tag} acme",
        "city": "Lyon",
    }
    if secret:
        r["_secret"] = "PRIVATEVALUE"
    return r


def make_pairs(n: int, scores=None, prefix: str = "ZQ", start: int = 0):
    out = []
    for i in range(start, start + n):
        a, b = _rec(f"{prefix}{i:04d}A"), _rec(f"{prefix}{i:04d}B")
        s = HEUR if scores is None else scores[i - start]
        out.append((tre._record_id(a), tre._record_id(b), a, b, s))
    return out


def idx_of(pair_id: str) -> int:
    return int(TOKEN.search(pair_id).group(2))


def judge(pairs, llm_config=None, sink=None):
    return asyncio.run(
        tre._judge_pairs_with_llm(
            "company", pairs, llm_config or {"provider": "openai"}, evidence_sink=sink
        )
    )


def by_idx(results):
    out = {}
    for id1, id2, score in results:
        out[idx_of(id1)] = score
    return out


def run_impl(
    band_scores,
    stub,
    monkeypatch,
    decided_match=20,
    decided_excl=20,
    rules=None,
    llm_config="default",
    thresholds=(AUTO_MATCH, AUTO_EXCLUDE),
):
    """End to end through resolve_entities_impl; `_resolve_core` returns the chosen band."""
    band = make_pairs(len(band_scores), band_scores)
    dm = make_pairs(decided_match, [0.99] * decided_match, prefix="DC")
    de = make_pairs(decided_excl, [0.05] * decided_excl, prefix="DC", start=decided_match)
    records: list[dict] = []
    for _a, _b, ra, rb, _s in [*band, *dm, *de]:
        records += [ra, rb]
    scored = [(a, b, s) for a, b, _ra, _rb, s in [*dm, *de]]
    fake = {
        "auto_match": thresholds[0],
        "auto_exclude": thresholds[1],
        "candidate_pair_count": len(band) + len(scored),
        "blocking_stats": {},
        "scored_pairs": scored,
        "borderline": list(band),
        "pair_evidence": {},
        "model_fitted": False,
        "possibly_related": [],
        "blocking_seconds": 0.0,
        "fit_model_seconds": 0.0,
        "scoring_seconds": 0.0,
    }
    monkeypatch.setattr(
        tre,
        "_resolve_core",
        lambda *a, **k: dict(fake, borderline=list(band), scored_pairs=list(scored)),
    )
    monkeypatch.setattr(resolve_cache, "get", lambda *_a, **_k: None)
    monkeypatch.setattr(resolve_cache, "put", lambda *_a, **_k: None)
    cfg = {"provider": "openai"} if llm_config == "default" else llm_config
    res = asyncio.run(
        tre.resolve_entities_impl("company", records, llm_config=cfg, matching_rules=rules)
    )
    return res, band


def sent_set(stub: Stub) -> set[int]:
    return {i for call in stub.calls for i in call}


# ── a. one pair per call ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [10, 95])
def test_a1_one_pair_per_call_and_each_pair_exactly_once(installed, n):
    """Old code: ceil(n/40) calls carrying up to 40 pairs each -> FAILS."""
    stub = installed(Stub())
    res = judge(make_pairs(n))
    assert len(res) == n
    assert all(len(c) == 1 for c in stub.calls), (
        f"calls carrying >1 pair: {[len(c) for c in stub.calls]}"
    )
    assert sorted(i for c in stub.calls for i in c) == list(range(n)), (
        "each pair must be sent exactly once"
    )


def test_a2_single_pair_and_empty_input(installed):
    stub = installed(Stub())
    assert judge([]) == []
    assert stub.calls == []
    res = judge(make_pairs(1))
    assert len(res) == 1 and len(stub.calls) == 1 and stub.calls[0] == (0,)


def test_a3_chunk_size_constant_is_gone_or_one():
    """The report's `_LLM_CHUNK_SIZE = 40` 'defeats calibration'.  Old code: 40 -> FAILS."""
    assert getattr(tre, "_LLM_CHUNK_SIZE", 1) == 1


# ── b. calibrated probability is what is read ────────────────────────────────────────────────
GRID = [0.01, 0.2, 0.4, 0.6, 0.8, 0.99]


def test_b1_probability_field_drives_the_score(installed):
    """Decoy legacy fields point the other way; the score must follow the probability.  Old code -> FAILS."""
    stub = installed(Stub(spec={i: p for i, p in enumerate(GRID)}))
    got = by_idx(judge(make_pairs(len(GRID))))
    scores = [got[i] for i in range(len(GRID))]
    assert all(0.0 <= s <= 1.0 and math.isfinite(s) for s in scores)
    assert scores == sorted(scores), f"score must not decrease as the probability rises: {scores}"
    assert scores[-1] >= AUTO_MATCH, f"p=0.99 must reach the match bar, got {scores[-1]}"
    assert scores[0] < AUTO_EXCLUDE, f"p=0.01 must fall below the exclude bar, got {scores[0]}"
    assert len({round(s, 6) for s in scores}) > 2, (
        "scores must carry the probability, not collapse to 2 verdicts"
    )
    assert sent_set(stub) == set(range(len(GRID)))


def test_b2_judged_pairs_get_llm_evidence(installed):
    sink: dict = {}
    installed(Stub(spec={0: 0.99, 1: 0.01}))
    judge(make_pairs(2), sink=sink)
    assert len(sink) == 2 and all(e.get("method") == "llm" for e in sink.values())


# ── c. one malformed response damages exactly one pair ───────────────────────────────────────
NAN = float("nan")
MALFORMED = {
    "raises": ("raise", RuntimeError("endpoint 500")),
    "bad_json": ("raise", json.JSONDecodeError("Response is not valid JSON after retry", "x", 0)),
    "plain_text": ("raw", "I think these are the same company."),
    "none": ("raw", None),
    "list": ("raw", [0.9]),
    "empty_dict": ("raw", {}),
    "missing_key_legacy_shape": (
        "raw",
        {"judgments": [{"pair_id": 1, "match": True, "confidence": 0.99}]},
    ),
    "probability_none": ("raw", {k: None for k in PROB_KEYS}),
    "probability_text": ("raw", {k: "high" for k in PROB_KEYS}),
    "probability_nan": ("raw", {k: NAN for k in PROB_KEYS}),
    "probability_inf": ("raw", {k: float("inf") for k in PROB_KEYS}),
    "probability_above_1": ("raw", {k: 7.3 for k in PROB_KEYS}),
    "probability_negative": ("raw", {k: -0.2 for k in PROB_KEYS}),
    "probability_bool": ("raw", {k: True for k in PROB_KEYS}),
}
# JUDGEMENT: the last three (out of range / bool) are "not a probability" here; relax only those if the lead
# prefers clamping.  NaN is NOT judgement: float('nan') passes max(0, min(1, nan)) as 1.0 -> a destructive merge.


@pytest.mark.parametrize("kind", sorted(MALFORMED))
def test_c1_one_malformed_response_damages_exactly_one_pair(installed, kind):
    """Old code: the victim sits in a 12-pair chunk, so the whole chunk (or all 12) is lost -> FAILS."""
    n, victim = 12, 5
    spec = {i: (0.99 if i % 2 == 0 else 0.01) for i in range(n)}
    spec[victim] = MALFORMED[kind]
    installed(Stub(spec=spec))
    sink: dict = {}
    res = judge(make_pairs(n), sink=sink)
    assert len(res) == n, "one result per input pair"
    got = by_idx(res)
    assert got[victim] == pytest.approx(HEUR), (
        f"{kind}: the damaged pair must keep its heuristic score, got {got[victim]}"
    )
    for i in range(n):
        if i == victim:
            continue
        if i % 2 == 0:
            assert got[i] >= AUTO_MATCH, (
                f"{kind}: pair {i} (p=0.99) must still be judged, got {got[i]}"
            )
        else:
            assert got[i] < AUTO_EXCLUDE, (
                f"{kind}: pair {i} (p=0.01) must still be judged, got {got[i]}"
            )
    assert len(sink) == n - 1, "no LLM evidence for the pair that was not judged"
    assert victim not in {idx_of(e["a"]) for e in sink.values()}


@pytest.mark.parametrize("victim", [0, 11])
def test_c2_position_of_the_failure_does_not_matter(installed, victim):
    n = 12
    spec = {i: 0.99 for i in range(n)}
    spec[victim] = ("raise", RuntimeError("boom"))
    installed(Stub(spec=spec))
    got = by_idx(judge(make_pairs(n)))
    assert got[victim] == pytest.approx(HEUR)
    assert all(got[i] >= AUTO_MATCH for i in range(n) if i != victim)


def test_c3_two_failures_damage_exactly_two_and_all_fail_damages_only_heuristic(installed):
    n = 10
    spec = {i: 0.99 for i in range(n)}
    spec[2] = ("raw", {})
    spec[7] = ("raise", RuntimeError("boom"))
    installed(Stub(spec=spec))
    got = by_idx(judge(make_pairs(n)))
    assert sorted(i for i, s in got.items() if s == pytest.approx(HEUR)) == [2, 7]
    assert all(got[i] >= AUTO_MATCH for i in got if i not in (2, 7))
    installed(Stub(spec={i: ("raise", RuntimeError("down")) for i in range(n)}))
    got = by_idx(judge(make_pairs(n)))
    assert len(got) == n and all(s == pytest.approx(HEUR) for s in got.values()), (
        "endpoint down: pipeline never blocks"
    )


# ── d. concurrency kept: bounded, more than one in flight ────────────────────────────────────
def test_d1_concurrency_is_bounded_and_greater_than_one(installed):
    stub = installed(Stub(delay=0.02))
    n = 60
    res = judge(make_pairs(n))
    assert len(res) == n
    bound = getattr(tre, "_LLM_CONCURRENCY", None)
    assert stub.max_in_flight >= 2, (
        "calls must overlap (serial dispatch loses the one thing the report kept)"
    )
    if isinstance(bound, int):
        assert bound >= 2 and stub.max_in_flight <= bound, (
            f"in flight {stub.max_in_flight} > bound {bound}"
        )
    else:
        assert stub.max_in_flight < n, "unbounded gather"


def test_d2_results_stay_aligned_when_calls_finish_out_of_order(installed):
    n = 20
    probs = [0.02 + 0.05 * i for i in range(n)]  # strictly increasing, all in (0, 1)
    delays = {i: 0.002 * (n - i) for i in range(n)}  # later pairs answer first
    installed(Stub(spec={i: p for i, p in enumerate(probs)}, delays=delays))
    pairs = make_pairs(n)
    res = judge(pairs)
    assert sorted(idx_of(a) for a, _b, _s in res) == list(range(n))
    got = by_idx(res)
    scores = [got[i] for i in range(n)]
    assert scores == sorted(scores), f"a score landed on the wrong pair: {scores}"
    assert scores[0] < AUTO_EXCLUDE and scores[-1] >= AUTO_MATCH
    for a, b, _s in res:  # both ids of a result belong to one pair
        assert idx_of(a) == idx_of(b)


# ── e. prompts keep their old guarantees ─────────────────────────────────────────────────────
def test_e1_prompt_carries_both_records_public_fields_only(installed):
    stub = installed(Stub())
    judge(make_pairs(3))
    assert stub.prompts
    for p in stub.prompts:
        for i in {int(m.group(2)) for m in TOKEN.finditer(p)}:
            assert f"ZQ{i:04d}A" in p and f"ZQ{i:04d}B" in p, "both records of the pair"
        assert "Lyon" in p
        assert "PRIVATEVALUE" not in p and "_origin" not in p, (
            "internal fields must not reach the model"
        )
        assert "{pairs_block}" not in p and "{pair_count}" not in p and "{entity_type}" not in p


def test_e2_retry_hint_reaches_every_call(installed):
    stub = installed(Stub())
    judge(make_pairs(4), llm_config={"provider": "openai", "retry_hint": "HINT-MARKER-7"})
    assert stub.prompts and all("HINT-MARKER-7" in p for p in stub.prompts)


# ── f. targeting: the uncertain band, end to end ─────────────────────────────────────────────
def _band(n, lo=AUTO_EXCLUDE, hi=AUTO_MATCH):
    return [lo + (hi - lo) * i / n for i in range(n)]  # lo inclusive, hi exclusive, distinct


def test_f1_exactly_the_band_pairs_are_sent_decided_pairs_never(installed, monkeypatch):
    """Control on old code (it already sends `borderline`): every band pair, no decided pair, and fewer
    pairs than were scored."""
    stub = installed(Stub())
    res, band = run_impl(_band(30), stub, monkeypatch)
    assert sent_set(stub) == set(range(30)), "all and only the band pairs"
    assert stub.decided_seen == [], (
        "a decided pair (>= auto_match or < auto_exclude) was sent to the model"
    )
    assert len(sent_set(stub)) < 30 + 40
    bs = res["band_summary"]["predicted_counts"]
    assert bs["match"] >= 20 and bs["exclude"] >= 20, "decided pairs keep their side"
    assert sum(bs.values()) == 30 + 40, "no pair lost"


def test_f2_budget_binding_selection_is_not_nearest_to_auto_match(installed, monkeypatch):
    """The report's defect.  With more band pairs than the old budget, old code sends the top-200 by score
    (closest to auto-match) -> FAILS.  Either the whole band is sent, or a budgeted subset that is not an
    extreme slice of the band (not top-N, not bottom-N); never a pair outside the band; unsent pairs are not
    dropped, they stay in the review band."""
    n = OLD_CAP + 60
    scores = _band(n)
    stub = installed(Stub())
    res, band = run_impl(scores, stub, monkeypatch)
    sent = sent_set(stub)
    assert sent <= set(range(n)), "a pair outside the band was sent"
    assert stub.decided_seen == []
    k = len(sent)
    order = sorted(range(n), key=lambda i: scores[i])
    if k < n:
        assert k <= OLD_CAP, "a budgeted selection must not exceed the old budget"
        assert sent != set(order[-k:]), (
            "selection is 'closest to auto-match first' (top-N by score): the old defect"
        )
        assert sent != set(order[:k]), (
            "selection is an extreme slice of the band (bottom-N by score)"
        )
    pc = res["band_summary"]["predicted_counts"]
    assert pc["possible"] == n - k, (
        f"unsent band pairs must stay in the review band: {pc['possible']} != {n - k}"
    )
    assert pc["match"] == 20 + k and pc["exclude"] == 20


def test_f8_one_pair_per_call_also_end_to_end(installed, monkeypatch):
    """Old code: ceil(band/40) calls of up to 40 pairs -> FAILS."""
    stub = installed(Stub())
    run_impl(_band(30), stub, monkeypatch)
    assert stub.calls and all(len(c) == 1 for c in stub.calls), [len(c) for c in stub.calls]


def test_f3_number_sent_not_above_the_old_selection_unless_it_covers_the_band(
    installed, monkeypatch
):
    """'the number sent falls versus the old selection': the old selection sends min(band, 200).  With the P15
    band already applied the old code sent exactly the band when band <= 200 (no fall is possible there, see
    .handoff/P8.acceptance.md); above 200 the new selection may send the whole band or at most the old number."""
    for n in (OLD_CAP - 50, OLD_CAP + 60, OLD_CAP * 3):
        stub = installed(Stub())
        run_impl(_band(n), stub, monkeypatch, decided_match=5, decided_excl=5)
        k = len(sent_set(stub))
        assert k == n or k <= min(n, OLD_CAP), f"band {n}: sent {k}"
        if n <= OLD_CAP:
            assert k == n, "budget not binding: every band pair must be examined"


def test_f4_band_edges_come_from_the_entity_thresholds_not_a_literal(installed, monkeypatch):
    """Rules move the band to [0.60, 0.90): a pair at 0.62 (below the default exclude bar) must be sent, one at
    0.60 is the inclusive edge; nothing is re-filtered against 0.70/0.95."""
    rules = {"auto_match": 0.90, "auto_exclude": 0.60}
    scores = _band(30, 0.60, 0.90)
    stub = installed(Stub())
    res, _band_pairs = run_impl(scores, stub, monkeypatch, rules=rules, thresholds=(0.90, 0.60))
    assert sent_set(stub) == set(range(30))
    assert res["band_summary"]["thresholds"] == {"auto_match": 0.90, "auto_exclude": 0.60}


def test_f5_probability_decides_the_merge_end_to_end(installed, monkeypatch):
    """Old code: the free-form fields are the decoys -> p=0.99 does not merge, FAILS."""
    spec = {0: 0.99, 1: 0.01, 2: 0.99, 3: 0.01}
    stub = installed(Stub(spec=spec))
    res, band = run_impl(
        [0.80, 0.80, 0.80, 0.80], stub, monkeypatch, decided_match=0, decided_excl=0
    )
    merged = {frozenset(c["record_ids"]) for c in res["clusters"] if len(c["record_ids"]) > 1}
    want = {frozenset((band[i][0], band[i][1])) for i in (0, 2)}
    assert merged == want, f"merged {sorted(map(sorted, merged))}"
    assert res["llm_scored"] == 4


def test_f6_llm_off_or_unconfigured_sends_nothing_and_keeps_band(installed, monkeypatch):
    for cfg in ({"provider": "openai", "entity_resolution_enabled": False}, None):
        stub = installed(Stub())
        res, _ = run_impl(_band(12), stub, monkeypatch, llm_config=cfg)
        assert stub.calls == []
        assert res["band_summary"]["predicted_counts"]["possible"] == 12
        assert res["llm_scored"] == 0


def test_f7_a_malformed_response_end_to_end_leaves_that_pair_in_the_band(installed, monkeypatch):
    spec = {i: 0.99 for i in range(6)}
    spec[3] = ("raw", {k: NAN for k in PROB_KEYS})
    stub = installed(Stub(spec=spec))
    res, band = run_impl([0.80] * 6, stub, monkeypatch, decided_match=0, decided_excl=0)
    pc = res["band_summary"]["predicted_counts"]
    assert (pc["match"], pc["possible"], pc["exclude"]) == (5, 1, 0)
    merged = {frozenset(c["record_ids"]) for c in res["clusters"] if len(c["record_ids"]) > 1}
    assert frozenset((band[3][0], band[3][1])) not in merged, "NaN must never become a merge"


# ── g. 2.4 / 2.5 greps ───────────────────────────────────────────────────────────────────────
CORPUS = re.compile(
    r"(?i)\b(france|us|sas|berhad|global|group|holdings?|ltd|staff|companyname|main_entity_id|"
    r"company_relation|companyid|testdiffbot|companyexperience)\b"
)
SMALL = {
    0,
    1,
    2,
    3,
    4,
    120,
}  # (0.0/1.0 hash equal to 0/1) indices, rounding digits, log truncation: not a corpus count
CHANGED = ("_judge_pairs_with_llm", "_format_pairs_block", "resolve_entities_impl")


def _fns(text: str) -> dict[str, ast.AST]:
    tree = ast.parse(text)
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _consts(fn: ast.AST):
    doc = ast.get_docstring(fn, clean=False)
    strs, nums = Counter(), Counter()
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant):
            if isinstance(n.value, str) and n.value != doc:
                strs[n.value] += 1
            elif isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                nums[n.value] += 1
    return strs, nums


def _baseline() -> str:
    try:
        out = subprocess.run(
            ["git", "show", f"{PRE_P8_REF}:src/mcp_server/tools/resolve_entities.py"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"pre-P8 ref {PRE_P8_REF} not available for the 2.4 baseline")
    return out.stdout


def _owned_and_new():
    cur, base = _fns(SRC.read_text()), _fns(_baseline())
    return cur, base, [n for n in cur if n not in base]


def test_g1_no_corpus_literal_in_owned_or_new_code():
    cur, base, new = _owned_and_new()
    for name in [*CHANGED, *new]:
        if name not in cur:
            continue
        strs, _ = _consts(cur[name])
        bstrs, _ = _consts(base[name]) if name in base else (Counter(), Counter())
        hits = [s for s in strs - bstrs if CORPUS.search(s)]
        assert not hits, f"{name}: corpus literal(s) {hits}"


def test_g2_no_new_numeric_constant_in_owned_or_new_code():
    """Old `_judge_pairs_with_llm` invents 0.5 twice (a missing confidence defaults to a made-up probability):
    FAILS on old code.  New numbers must be indices/rounding/log-truncation only; 0.95/0.70 are never re-typed."""
    cur, base, new = _owned_and_new()
    for name in [*CHANGED, *new]:
        if name not in cur:
            continue
        _, nums = _consts(cur[name])
        if name == "resolve_entities_impl":  # a large shared function: only what P8 ADDS is judged
            nums = nums - _consts(base[name])[1]
        bad = {v: c for v, c in nums.items() if v not in SMALL}
        assert not bad, f"{name}: numeric literal(s) not derivable from data or the band: {bad}"


def test_g3_no_new_module_level_numeric_constant():
    def numeric_assigns(text: str) -> dict[str, float]:
        out = {}
        for n in ast.parse(text).body:
            tgt = n.targets[0] if isinstance(n, ast.Assign) else getattr(n, "target", None)
            if isinstance(tgt, ast.Name) and getattr(n, "value", None) is not None:
                try:
                    v = ast.literal_eval(n.value)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[tgt.id] = v
        return out

    cur, base = numeric_assigns(SRC.read_text()), numeric_assigns(_baseline())
    added = {k: v for k, v in cur.items() if k not in base}
    assert not added, (
        f"new module constant(s): {added} (budget, margin, chunk, floor must be derived or sanctioned)"
    )
    assert (
        base["_AUTO_MATCH"] == cur["_AUTO_MATCH"] == 0.95
        and base["_AUTO_EXCLUDE"] == cur["_AUTO_EXCLUDE"] == 0.7
    )


def test_g4_prompt_substitution_is_not_str_format():
    cur, _base, new = _owned_and_new()
    for name in [*CHANGED, *new]:
        if name not in cur:
            continue
        for n in ast.walk(cur[name]):
            is_fmt = (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "format"
            )
            if is_fmt and isinstance(
                n.func.value, (ast.Name, ast.Attribute)
            ):  # a template, not a literal str
                pytest.fail(f"{name}: .format() on a template (prompts use str.replace)")


def test_g5_arch_lint_clean():
    r = subprocess.run(
        [str(REPO / ".venv/bin/python"), "scripts/lint_arch.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-400:]
