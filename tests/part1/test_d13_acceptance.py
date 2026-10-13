"""D13 acceptance (derived from LOCAL_VERIFY_REPORT D13 + HANDOFF decision 6 only; no fix code seen).

Defect: P8 sends ONE LLM call per review-band pair, and `_judge_pairs_with_llm` builds its OWN
`Semaphore(_LLM_CONCURRENCY)`. The resolve NODE runs every entity's `resolve_entities_impl` under one
`asyncio.gather`, so the endpoint sees up to `_LLM_CONCURRENCY x (number of entities)` calls at once
(measured: a serial LAN endpoint starved a second app's preflight).  Decision: bound the P8 pair calls
with the same `Semaphore(_LLM_CONCURRENCY)` P11 uses, i.e. ONE bound for the whole node run, same answers.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d13_acceptance.py -q -p no:cacheprovider

Harness (black box, no interface assumed for the fix): the NODE (`nodes.resolve_entities.resolve_entities`)
runs over N entities; `tre._resolve_core` is replaced (as test_p8_acceptance does) by a fake returning a
chosen review band per entity; `src.llm.LLMClient` is replaced by a stub whose `call_json` records the
number of calls in flight across ALL entities.  The bound is read from `tre._LLM_CONCURRENCY` and the tests
re-point it (2, 7) in every module that holds it, so a hard-coded 4 in the fix cannot pass.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

import src.llm as llm_pkg
import src.orchestrator.spool as spool_mod
from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools import resolve_entities as tre
from src.orchestrator.spool import clusters_for

re_node = importlib.import_module("src.orchestrator.nodes.resolve_entities")
REPO = Path(__file__).resolve().parents[2]
TOOLS_SRC = REPO / "src/mcp_server/tools/resolve_entities.py"
NODE_SRC = REPO / "src/orchestrator/nodes/resolve_entities.py"
BASE_REF = "722b12c"  # HEAD the defect and the golden outputs were measured on
TOKEN = re.compile(r"ZQ([a-e])(\d{3})")
ENTITIES = ["person", "product", "order", "invoice", "ticket"]
LETTER = dict(zip(ENTITIES, "abcde", strict=True))
DELAY = 0.02


# ── stub LLM: counts in-flight calls across everything that shares it ────────────────────────
def reply(e: str, i: int):
    """Deterministic reply per pair (entity letter, index): decisive merge, decisive apart, unsure,
    just-under/over the auto-match bar, malformed, missing probability, raise."""
    k = (i + "abcde".index(e)) % 9
    return {
        0: {"probability": 0.99, "reason": "same"},
        1: {"probability": 0.02, "reason": "different"},
        2: {"probability": 0.5, "reason": "unsure"},
        3: {"probability": 0.97, "reason": "very likely"},
        4: ("raise", RuntimeError("endpoint timeout")),
        5: {"probability": 1.7, "reason": "out of range"},
        6: {"reason": "no probability"},
        7: {"probability": 0.951, "reason": "just over"},
        8: {"probability": 0.949, "reason": "just under"},
    }[k]


class Stub:
    def __init__(self, fn=reply, delay=DELAY):
        self.fn, self.delay = fn, delay
        self.keys: list[tuple[str, int]] = []  # one (entity letter, index) per call
        self.bad_prompts: list[str] = []  # calls that did not carry exactly one pair
        self.in_flight = 0
        self.max_in_flight = 0
        self.per_entity: Counter = Counter()
        self.max_per_entity: Counter = Counter()

    async def call_json(self, system_prompt: str, user_prompt: str, **_kw):
        found = {(m.group(1), int(m.group(2))) for m in TOKEN.finditer(user_prompt)}
        if len(found) != 1:
            self.bad_prompts.append(user_prompt[:200])
            raise AssertionError("a call must carry exactly one pair")
        (key,) = found
        self.keys.append(key)
        self.in_flight += 1
        self.per_entity[key[0]] += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.max_per_entity[key[0]] = max(self.max_per_entity[key[0]], self.per_entity[key[0]])
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
            self.per_entity[key[0]] -= 1
        out = self.fn(*key)
        if isinstance(out, tuple) and out[0] == "raise":
            raise out[1]
        return out

    call = call_json


@pytest.fixture
def install(monkeypatch, tmp_path):
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    monkeypatch.setattr(resolve_cache, "get", lambda *_a, **_k: None)
    monkeypatch.setattr(resolve_cache, "put", lambda *_a, **_k: None)

    def _install(stub: Stub) -> Stub:
        monkeypatch.setattr(llm_pkg, "LLMClient", lambda *_a, **_k: stub)
        if hasattr(tre, "LLMClient"):
            monkeypatch.setattr(tre, "LLMClient", lambda *_a, **_k: stub)
        return stub

    return _install


def set_bound(monkeypatch, n: int) -> None:
    """Re-point the bound everywhere it may be held (tools module, and any module that imported it by name)."""
    for mod in (tre, re_node):
        if hasattr(mod, "_LLM_CONCURRENCY"):
            monkeypatch.setattr(mod, "_LLM_CONCURRENCY", n)


# ── fixtures: review-band pairs ──────────────────────────────────────────────────────────────
def _rec(tag: str) -> dict:
    return {
        "_origin_id": "o",
        "_origin_table": "t",
        "_origin_id_row": tag,
        "name": f"{tag} acme",
        "city": "Lyon",
    }


def band_for(e: str, n: int):
    out = []
    for i in range(n):
        a, b = _rec(f"ZQ{e}{i:03d}A"), _rec(f"ZQ{e}{i:03d}B")
        heur = round(
            0.72 + 0.0017 * ((i * 7 + "abcde".index(e)) % 100), 4
        )  # inside the (0.70, 0.95) band
        out.append((tre._record_id(a), tre._record_id(b), a, b, heur))
    return out


def run_node(sizes: dict[str, int], monkeypatch, llm_config="default", rules=None):
    """The resolve NODE over several entities; `_resolve_core` returns each entity's band."""
    bands = {et: band_for(LETTER[et], n) for et, n in sizes.items()}
    records = {
        et: [r for _a, _b, ra, rb, _s in b for r in (ra, rb)]
        or [_rec(f"ZQ{LETTER[et]}999A"), _rec(f"ZQ{LETTER[et]}999B")]
        for et, b in bands.items()
    }

    def fake_core(entity_type, *_a, **_k):
        return {
            "auto_match": tre._AUTO_MATCH,
            "auto_exclude": tre._AUTO_EXCLUDE,
            "candidate_pair_count": len(bands[entity_type]),
            "blocking_stats": {},
            "scored_pairs": [],
            "borderline": list(bands[entity_type]),
            "pair_evidence": {},
            "model_fitted": False,
            "possibly_related": [],
            "blocking_seconds": 0.0,
            "fit_model_seconds": 0.0,
            "scoring_seconds": 0.0,
        }

    monkeypatch.setattr(tre, "_resolve_core", fake_core)
    cfg = {"provider": "openai"} if llm_config == "default" else llm_config
    state = {
        "run_id": "d13",
        "target_entities": list(sizes),
        "extracted_data": records,
        "llm_config": cfg,
        "matching_rules": rules,
        "entity_clusters": {},
        "errors": [],
    }

    async def go():
        return await asyncio.wait_for(re_node.resolve_entities(state), timeout=30)

    out = asyncio.run(go())
    return {**state, **out}, bands


def judge(pairs, cfg=None, sink=None):
    async def go():
        return await asyncio.wait_for(
            tre._judge_pairs_with_llm(
                "company", pairs, cfg or {"provider": "openai"}, evidence_sink=sink
            ),
            timeout=30,
        )

    return asyncio.run(go())


def assert_each_pair_once(stub: Stub, sizes: dict[str, int]) -> None:
    got = Counter(stub.keys)
    want = Counter((LETTER[et], i) for et, n in sizes.items() for i in range(n))
    assert not stub.bad_prompts, stub.bad_prompts
    assert got == want, (
        f"calls lost: {sorted((want - got).items())[:5]}  duplicated/extra: {sorted((got - want).items())[:5]}"
    )
    assert sum(got.values()) == sum(sizes.values())


# ── (a) one bound across ALL entities of a node run ──────────────────────────────────────────
@pytest.mark.parametrize(
    "sizes",
    [
        {et: 30 for et in ENTITIES},  # equal entities
        {
            "person": 55,
            "product": 1,
            "order": 0,
            "invoice": 7,
            "ticket": 30,
        },  # uneven, one empty, one single
    ],
    ids=["5x30", "uneven"],
)
def test_a1_node_in_flight_never_exceeds_the_bound_across_entities(install, monkeypatch, sizes):
    """Old code: one Semaphore per entity -> ~4 x entities in flight (20 here) -> FAILS."""
    stub = install(Stub(lambda e, i: {"probability": 0.99, "reason": "ok"}))
    run_node(sizes, monkeypatch)
    assert tre._LLM_CONCURRENCY == 4  # the existing constant, unchanged
    assert stub.max_in_flight <= tre._LLM_CONCURRENCY, (
        f"{stub.max_in_flight} calls in flight at once across {len(sizes)} entities, bound is {tre._LLM_CONCURRENCY}"
    )


@pytest.mark.parametrize("bound", [2, 7])
def test_a2_bound_is_the_constant_not_a_literal_and_is_reached(install, monkeypatch, bound):
    """Re-pointing `_LLM_CONCURRENCY` must move the bound (a hard-coded 4 fails). The bound is also REACHED
    (== bound, not 1): concurrency is kept, only capped. Old code: 5 entities x bound in flight -> FAILS."""
    set_bound(monkeypatch, bound)
    stub = install(Stub(lambda e, i: {"probability": 0.99, "reason": "ok"}))
    sizes = {et: 30 for et in ENTITIES}
    run_node(sizes, monkeypatch)
    assert stub.max_in_flight <= bound, f"{stub.max_in_flight} in flight, bound {bound}"
    assert stub.max_in_flight == bound, (
        f"concurrency not kept: peak {stub.max_in_flight}, bound {bound}"
    )


def test_a3_bound_is_reached_at_default_and_no_entity_exceeds_it(install, monkeypatch):
    stub = install(Stub(lambda e, i: {"probability": 0.99, "reason": "ok"}))
    run_node({et: 30 for et in ENTITIES}, monkeypatch)
    assert stub.max_in_flight == tre._LLM_CONCURRENCY
    assert max(stub.max_per_entity.values()) <= tre._LLM_CONCURRENCY


def test_a4_failing_calls_release_their_permit(install, monkeypatch):
    """Every call raises: 150 > bound failures. A leaked permit would deadlock (wait_for -> TimeoutError)
    or let the count exceed the bound. Heuristic scores are kept; every pair was still tried once."""
    stub = install(Stub(lambda e, i: ("raise", RuntimeError("down"))))
    sizes = {et: 30 for et in ENTITIES}
    out, _bands = run_node(sizes, monkeypatch)
    assert_each_pair_once(stub, sizes)
    assert stub.max_in_flight <= tre._LLM_CONCURRENCY
    assert not out["errors"]


def test_a5_llm_disabled_or_no_config_makes_no_calls(install, monkeypatch):
    stub = install(Stub())
    run_node(
        {et: 10 for et in ENTITIES},
        monkeypatch,
        llm_config={"provider": "openai", "entity_resolution_enabled": False},
    )
    run_node({et: 10 for et in ENTITIES}, monkeypatch, llm_config=None)
    assert stub.keys == []


def test_a6_node_can_run_twice_in_separate_event_loops(install, monkeypatch):
    """A semaphore created once at import / cached across runs is bound to the first loop and breaks (or
    deadlocks) the second run.  Two node runs, two loops, same stub: both complete, same answers, bound holds."""
    stub = install(Stub())
    sizes = {et: 20 for et in ENTITIES}
    first, _ = run_node(sizes, monkeypatch)
    second, _ = run_node(sizes, monkeypatch)
    assert stub.max_in_flight <= tre._LLM_CONCURRENCY
    assert len(stub.keys) == 2 * sum(sizes.values())
    assert summary(first) == summary(second)


# ── (b) a single _judge_pairs_with_llm call keeps its bound ──────────────────────────────────
def test_b1_single_judge_call_is_bounded_and_concurrent(install):
    stub = install(Stub(lambda e, i: {"probability": 0.99, "reason": "ok"}))
    pairs = band_for("a", 40)
    res = judge(pairs)
    assert len(res) == 40
    assert stub.max_in_flight == tre._LLM_CONCURRENCY  # reached, not exceeded
    assert_each_pair_once(stub, {"person": 40})


@pytest.mark.parametrize("bound", [1, 3])
def test_b2_single_judge_call_follows_the_constant(install, monkeypatch, bound):
    set_bound(monkeypatch, bound)
    stub = install(Stub(lambda e, i: {"probability": 0.99, "reason": "ok"}))
    judge(band_for("a", 12))
    assert stub.max_in_flight == bound


def test_b3_empty_and_single_pair(install):
    stub = install(Stub())
    assert judge([]) == []
    assert stub.keys == []
    assert len(judge(band_for("a", 1))) == 1
    assert stub.keys == [("a", 0)]


# ── (c) same answers: golden outputs frozen from BASE_REF ────────────────────────────────────
def summary(state) -> dict:
    out = {}
    for et in state["target_entities"]:
        stats = state["resolution_stats"][et]
        groups = sorted(
            sorted(c["record_ids"]) for c in clusters_for(state, et) if len(c["record_ids"]) > 1
        )
        out[et] = {
            "llm_scored": stats["llm_scored"],
            "clusters_formed": stats["clusters_formed"],
            "merged_groups": groups,
            "pending": sorted(
                (sorted(TOKEN.search(r).group(0) for r in p["record_ids"]), p["confidence"])
                for p in stats["pending_merge_candidates"]
            ),
            "band": stats["band_summary"].get("predicted_counts"),
        }
    return out


def direct_scenario() -> dict:
    sink: dict = {}
    pairs = band_for("a", 40)
    res = judge(pairs, sink=sink)
    return {
        "results": [[idx_of(r[0]), idx_of(r[1]), round(r[2], 6)] for r in res],
        "sink": sorted(
            [
                idx_of(k[0]),
                idx_of(k[1]),
                v["method"],
                v["reason"],
                v["llm_probability"],
                round(v["confidence"], 6),
            ]
            for k, v in sink.items()
        ),
    }


def idx_of(record_id: str) -> int:
    return int(TOKEN.search(record_id).group(2))


def node_scenario(monkeypatch) -> dict:
    sizes = {"person": 40, "product": 25, "order": 0, "invoice": 9, "ticket": 33}
    state, _ = run_node(sizes, monkeypatch)
    return json.loads(
        json.dumps(
            {
                et: {
                    "llm_scored": v["llm_scored"],
                    "clusters_formed": v["clusters_formed"],
                    "merged": [[TOKEN.search(r).group(0) for r in g] for g in v["merged_groups"]],
                    "pending": v["pending"],
                    "band": v["band"],
                }
                for et, v in summary(state).items()
            }
        )
    )


# Frozen from the UNFIXED code at 722b12c (deterministic stub `reply`); see .handoff/D13.acceptance.md.
GOLDEN_DIRECT: dict = {
    "results": [
        [0, 0, 0.99],
        [1, 1, 0.02],
        [2, 2, 0.7438],
        [3, 3, 0.97],
        [4, 4, 0.7676],
        [5, 5, 0.7795],
        [6, 6, 0.7914],
        [7, 7, 0.951],
        [8, 8, 0.8152],
        [9, 9, 0.99],
        [10, 10, 0.02],
        [11, 11, 0.8509],
        [12, 12, 0.97],
        [13, 13, 0.8747],
        [14, 14, 0.8866],
        [15, 15, 0.7285],
        [16, 16, 0.951],
        [17, 17, 0.7523],
        [18, 18, 0.99],
        [19, 19, 0.02],
        [20, 20, 0.788],
        [21, 21, 0.97],
        [22, 22, 0.8118],
        [23, 23, 0.8237],
        [24, 24, 0.8356],
        [25, 25, 0.951],
        [26, 26, 0.8594],
        [27, 27, 0.99],
        [28, 28, 0.02],
        [29, 29, 0.7251],
        [30, 30, 0.97],
        [31, 31, 0.7489],
        [32, 32, 0.7608],
        [33, 33, 0.7727],
        [34, 34, 0.951],
        [35, 35, 0.7965],
        [36, 36, 0.99],
        [37, 37, 0.02],
        [38, 38, 0.8322],
        [39, 39, 0.97],
    ],
    "sink": [
        [0, 0, "llm", "same", 0.99, 0.99],
        [1, 1, "llm", "different", 0.02, 0.02],
        [2, 2, "llm", "unsure", 0.5, 0.7438],
        [3, 3, "llm", "very likely", 0.97, 0.97],
        [7, 7, "llm", "just over", 0.951, 0.951],
        [8, 8, "llm", "just under", 0.949, 0.8152],
        [9, 9, "llm", "same", 0.99, 0.99],
        [10, 10, "llm", "different", 0.02, 0.02],
        [11, 11, "llm", "unsure", 0.5, 0.8509],
        [12, 12, "llm", "very likely", 0.97, 0.97],
        [16, 16, "llm", "just over", 0.951, 0.951],
        [17, 17, "llm", "just under", 0.949, 0.7523],
        [18, 18, "llm", "same", 0.99, 0.99],
        [19, 19, "llm", "different", 0.02, 0.02],
        [20, 20, "llm", "unsure", 0.5, 0.788],
        [21, 21, "llm", "very likely", 0.97, 0.97],
        [25, 25, "llm", "just over", 0.951, 0.951],
        [26, 26, "llm", "just under", 0.949, 0.8594],
        [27, 27, "llm", "same", 0.99, 0.99],
        [28, 28, "llm", "different", 0.02, 0.02],
        [29, 29, "llm", "unsure", 0.5, 0.7251],
        [30, 30, "llm", "very likely", 0.97, 0.97],
        [34, 34, "llm", "just over", 0.951, 0.951],
        [35, 35, "llm", "just under", 0.949, 0.7965],
        [36, 36, "llm", "same", 0.99, 0.99],
        [37, 37, "llm", "different", 0.02, 0.02],
        [38, 38, "llm", "unsure", 0.5, 0.8322],
        [39, 39, "llm", "very likely", 0.97, 0.97],
    ],
}
GOLDEN_NODE: dict = {
    "invoice": {
        "band": {"exclude": 1, "match": 3, "possible": 5},
        "clusters_formed": 15,
        "llm_scored": 9,
        "merged": [["ZQd000", "ZQd000"], ["ZQd004", "ZQd004"], ["ZQd006", "ZQd006"]],
        "pending": [
            [["ZQd001", "ZQd001"], 0.737],
            [["ZQd002", "ZQd002"], 0.749],
            [["ZQd003", "ZQd003"], 0.761],
            [["ZQd005", "ZQd005"], 0.785],
            [["ZQd008", "ZQd008"], 0.82],
        ],
    },
    "order": {
        "band": {"exclude": 0, "match": 0, "possible": 0},
        "clusters_formed": 2,
        "llm_scored": 0,
        "merged": [],
        "pending": [],
    },
    "person": {
        "band": {"exclude": 5, "match": 14, "possible": 21},
        "clusters_formed": 66,
        "llm_scored": 40,
        "merged": [
            ["ZQa000", "ZQa000"],
            ["ZQa003", "ZQa003"],
            ["ZQa007", "ZQa007"],
            ["ZQa009", "ZQa009"],
            ["ZQa012", "ZQa012"],
            ["ZQa016", "ZQa016"],
            ["ZQa018", "ZQa018"],
            ["ZQa021", "ZQa021"],
            ["ZQa025", "ZQa025"],
            ["ZQa027", "ZQa027"],
            ["ZQa030", "ZQa030"],
            ["ZQa034", "ZQa034"],
            ["ZQa036", "ZQa036"],
            ["ZQa039", "ZQa039"],
        ],
        "pending": [
            [["ZQa002", "ZQa002"], 0.744],
            [["ZQa004", "ZQa004"], 0.768],
            [["ZQa005", "ZQa005"], 0.779],
            [["ZQa006", "ZQa006"], 0.791],
            [["ZQa008", "ZQa008"], 0.815],
            [["ZQa011", "ZQa011"], 0.851],
            [["ZQa013", "ZQa013"], 0.875],
            [["ZQa014", "ZQa014"], 0.887],
            [["ZQa015", "ZQa015"], 0.729],
            [["ZQa017", "ZQa017"], 0.752],
            [["ZQa020", "ZQa020"], 0.788],
            [["ZQa022", "ZQa022"], 0.812],
            [["ZQa023", "ZQa023"], 0.824],
            [["ZQa024", "ZQa024"], 0.836],
            [["ZQa026", "ZQa026"], 0.859],
            [["ZQa029", "ZQa029"], 0.725],
            [["ZQa031", "ZQa031"], 0.749],
            [["ZQa032", "ZQa032"], 0.761],
            [["ZQa033", "ZQa033"], 0.773],
            [["ZQa035", "ZQa035"], 0.796],
            [["ZQa038", "ZQa038"], 0.832],
        ],
    },
    "product": {
        "band": {"exclude": 3, "match": 8, "possible": 14},
        "clusters_formed": 42,
        "llm_scored": 25,
        "merged": [
            ["ZQb002", "ZQb002"],
            ["ZQb006", "ZQb006"],
            ["ZQb008", "ZQb008"],
            ["ZQb011", "ZQb011"],
            ["ZQb015", "ZQb015"],
            ["ZQb017", "ZQb017"],
            ["ZQb020", "ZQb020"],
            ["ZQb024", "ZQb024"],
        ],
        "pending": [
            [["ZQb001", "ZQb001"], 0.734],
            [["ZQb003", "ZQb003"], 0.757],
            [["ZQb004", "ZQb004"], 0.769],
            [["ZQb005", "ZQb005"], 0.781],
            [["ZQb007", "ZQb007"], 0.805],
            [["ZQb010", "ZQb010"], 0.841],
            [["ZQb012", "ZQb012"], 0.865],
            [["ZQb013", "ZQb013"], 0.876],
            [["ZQb014", "ZQb014"], 0.888],
            [["ZQb016", "ZQb016"], 0.742],
            [["ZQb019", "ZQb019"], 0.778],
            [["ZQb021", "ZQb021"], 0.802],
            [["ZQb022", "ZQb022"], 0.814],
            [["ZQb023", "ZQb023"], 0.825],
        ],
    },
    "ticket": {
        "band": {"exclude": 3, "match": 11, "possible": 19},
        "clusters_formed": 55,
        "llm_scored": 33,
        "merged": [
            ["ZQe003", "ZQe003"],
            ["ZQe005", "ZQe005"],
            ["ZQe008", "ZQe008"],
            ["ZQe012", "ZQe012"],
            ["ZQe014", "ZQe014"],
            ["ZQe017", "ZQe017"],
            ["ZQe021", "ZQe021"],
            ["ZQe023", "ZQe023"],
            ["ZQe026", "ZQe026"],
            ["ZQe030", "ZQe030"],
            ["ZQe032", "ZQe032"],
        ],
        "pending": [
            [["ZQe000", "ZQe000"], 0.727],
            [["ZQe001", "ZQe001"], 0.739],
            [["ZQe002", "ZQe002"], 0.751],
            [["ZQe004", "ZQe004"], 0.774],
            [["ZQe007", "ZQe007"], 0.81],
            [["ZQe009", "ZQe009"], 0.834],
            [["ZQe010", "ZQe010"], 0.846],
            [["ZQe011", "ZQe011"], 0.858],
            [["ZQe013", "ZQe013"], 0.881],
            [["ZQe016", "ZQe016"], 0.747],
            [["ZQe018", "ZQe018"], 0.771],
            [["ZQe019", "ZQe019"], 0.783],
            [["ZQe020", "ZQe020"], 0.795],
            [["ZQe022", "ZQe022"], 0.819],
            [["ZQe025", "ZQe025"], 0.854],
            [["ZQe027", "ZQe027"], 0.878],
            [["ZQe028", "ZQe028"], 0.72],
            [["ZQe029", "ZQe029"], 0.732],
            [["ZQe031", "ZQe031"], 0.756],
        ],
    },
}


def test_c1_direct_answers_identical_to_base(install):
    install(Stub())
    assert direct_scenario() == GOLDEN_DIRECT


def test_c2_node_answers_identical_to_base(install, monkeypatch):
    install(Stub())
    assert node_scenario(monkeypatch) == GOLDEN_NODE


def test_c3_node_answers_do_not_depend_on_scheduling(install, monkeypatch):
    """Same answers whatever the call timing (zero latency -> different completion order)."""
    install(Stub(delay=0.0))
    assert node_scenario(monkeypatch) == GOLDEN_NODE


# ── (e) no call lost or duplicated ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "sizes",
    [
        {"person": 40, "product": 25, "order": 0, "invoice": 9, "ticket": 33},
        {et: 30 for et in ENTITIES},
    ],
)
def test_e1_node_calls_equal_pairs_and_each_pair_once(install, monkeypatch, sizes):
    stub = install(Stub())
    state, _ = run_node(sizes, monkeypatch)
    assert_each_pair_once(stub, sizes)
    for et, n in sizes.items():
        assert state["resolution_stats"][et]["llm_scored"] == n


def test_e2_direct_calls_equal_pairs_with_mixed_replies(install):
    stub = install(Stub())
    res = judge(band_for("a", 53))
    assert len(res) == 53
    assert_each_pair_once(stub, {"person": 53})


def test_e3_per_entity_budget_still_applies(install, monkeypatch):
    """The per-entity cap (`_MAX_LLM_PAIRS`) is untouched: 230 pairs -> exactly the cap is sent, per entity."""
    stub = install(Stub(lambda e, i: {"probability": 0.99, "reason": "ok"}, delay=0.0))
    run_node({"person": tre._MAX_LLM_PAIRS + 30, "product": 5}, monkeypatch)
    assert Counter(k[0] for k in stub.keys) == Counter({"a": tre._MAX_LLM_PAIRS, "b": 5})


# ── (d) no module-level mutable state, no new numeric literal, lint clean ────────────────────
def _base_source(path: Path) -> str:
    rel = path.relative_to(REPO).as_posix()
    return subprocess.run(
        ["git", "show", f"{BASE_REF}:{rel}"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def _numeric_constants(src: str) -> Counter:
    c: Counter = Counter()
    for n in ast.walk(ast.parse(src)):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)
        ):
            c[n.value] += 1
    return c


@pytest.mark.parametrize("path", [TOOLS_SRC, NODE_SRC], ids=["tools", "node"])
def test_d1_no_new_numeric_literal(path):
    """The bound is the existing `_LLM_CONCURRENCY`. Any numeric constant (other than 0/1) that the diff adds
    to the two files is a fitted/invented number -> fail. (Vacuously true until a fix exists.)"""
    new = _numeric_constants(path.read_text()) - _numeric_constants(_base_source(path))
    added = {k: v for k, v in new.items() if k not in (0, 1)}
    assert not added, f"new numeric literals in {path.name}: {added}"


def test_d2_no_module_level_mutable_state_in_tools():
    """Plan 2.5 / CLAUDE.md: no module-level mutable state in src/mcp_server/tools/. A module-level
    Semaphore/Lock/Queue/ContextVar/dict/list/set in resolve_entities.py is new state (and a Semaphore bound
    at import is also bound to one event loop)."""
    base = {ast.dump(n) for n in ast.parse(_base_source(TOOLS_SRC)).body}
    flagged = []
    for n in ast.parse(TOOLS_SRC.read_text()).body:
        if ast.dump(n) in base or not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        value = n.value
        calls = [c for c in ast.walk(value) if isinstance(c, ast.Call)] if value is not None else []
        names = {ast.unparse(c.func).split(".")[-1] for c in calls}
        mutable = isinstance(value, (ast.Dict, ast.List, ast.Set)) or names & {
            "Semaphore",
            "BoundedSemaphore",
            "Lock",
            "Queue",
            "ContextVar",
            "Event",
            "dict",
            "list",
            "set",
            "defaultdict",
            "Counter",
        }
        if mutable:
            flagged.append(ast.unparse(n)[:100])
    assert not flagged, flagged


def test_d3_lint_arch_clean():
    r = subprocess.run(
        [sys.executable, "scripts/lint_arch.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]


def test_d4_no_semaphore_or_gather_of_calls_without_the_constant():
    """Every `Semaphore(` constructed in the two files takes `_LLM_CONCURRENCY` (or a parameter), never a literal."""
    for path in (TOOLS_SRC, NODE_SRC):
        for n in ast.walk(ast.parse(path.read_text())):
            if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("Semaphore"):
                assert not (n.args and isinstance(n.args[0], ast.Constant)), (
                    f"{path.name}: {ast.unparse(n)}"
                )
