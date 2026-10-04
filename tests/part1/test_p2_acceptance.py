"""P2 acceptance (derived from the report's Evidence + Fix text only; no implementation seen).

Report: "The comparison budget is a silent truncation". Fix: replace the 250k cap
with purging -> filtering -> meta-blocking (WNP local mean); keep a budget only as
an out-of-memory guard that FAILS LOUDLY; loss measured as pair completeness.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p2_acceptance.py -q -s
The negative cases (1, 2, 3, 4, 5, order-invariance) FAIL on the old capped code.

ADAPTATION POINTS (one place each, so the lead can retarget without touching tests):
  GUARD_CANDIDATES / env P2_GUARD_NAME : module-level name of the OOM guard constant
  PRUNE_FAMILIES                       : stats-key regexes naming the pruning steps
  COMPLETENESS_KEY                     : regex for the stats key holding pair completeness
"""

from __future__ import annotations

import ast
import os
import random
import re
from pathlib import Path

import pytest

os.environ.setdefault("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", "1")

from src.mcp_server.tools import resolve_entities as re_
from tests.part1 import fixtures_matching as fx
from tests.part1.scorer import pairs_of

SRC = Path(re_.__file__)

# ── adaptation points ─────────────────────────────────────────────────────────
GUARD_CANDIDATES = (
    "_MAX_CANDIDATE_PAIRS",
    "_MAX_PAIRS_IN_MEMORY",
    "_OOM_GUARD_PAIRS",
    "_PAIR_MEMORY_GUARD",
    "_MAX_COMPARISONS",  # the OLD name; on old code it is a silent cap, so test 4 fails
)
PRUNE_FAMILIES = {  # computed-property steps from the report: stage 2 (x2) and stage 3
    "purging": re.compile(r"purg", re.I),
    "filtering": re.compile(r"filter", re.I),
    "meta_blocking": re.compile(r"prun|meta|wnp", re.I),
}
COMPLETENESS_KEY = re.compile(r"completeness", re.I)
# a stats key that names a fixed-cap truncation is forbidden
CAP_KEY = re.compile(r"(^|_)(cap|capped|trunc\w*|budget|limit\w*|overflow\w*)($|_)|max_comp", re.I)


def guard_name() -> str:
    forced = os.environ.get("P2_GUARD_NAME")
    names = (forced,) if forced else GUARD_CANDIDATES
    for n in names:
        if hasattr(re_, n):
            return n
    raise AssertionError(f"no OOM guard constant found among {names}")


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def run(records: list[dict]) -> tuple[list, dict, set[tuple[str, str]]]:
    stats: dict = {}
    pairs = re_._generate_candidate_pairs(records, stats=stats)
    return pairs, stats, {_pair(p[0], p[1]) for p in pairs}


@pytest.fixture(scope="module")
def big() -> dict:
    f = fx.p2_big_block()
    pairs, stats, got = run(f["records"])
    planted = pairs_of(f["truth"])
    n = len(f["records"])
    pc = len(planted & got) / len(planted)
    print(
        f"\n[P2] pairs_returned={len(pairs)} planted_found={len(planted & got)}/{len(planted)} "
        f"PC={pc:.3f} lost={sorted(planted - got)[:3]}... stats={stats}"
    )
    return {
        "f": f,
        "pairs": pairs,
        "stats": stats,
        "got": got,
        "planted": planted,
        "possible": n * (n - 1) // 2,
    }


# 1 ── no planted pair lost to a counter ───────────────────────────────────────
def test_1_all_planted_pairs_survive(big) -> None:
    lost = sorted(big["planted"] - big["got"])
    assert not lost, f"{len(lost)}/{len(big['planted'])} planted pairs lost (old code: 9): {lost}"


def test_1b_survival_holds_on_other_seeds_and_sizes() -> None:
    """A constant tuned to the Phase-0 fixture would not survive a different shape."""
    for n, d, seed in ((1000, 30, 7), (900, 60, 11)):
        f = fx.p2_big_block(n_records=n, n_dupes=d, seed=seed)
        _, _, got = run(f["records"])
        lost = sorted(pairs_of(f["truth"]) - got)
        assert not lost, f"n={n} seed={seed}: {len(lost)} planted pairs lost: {lost[:5]}"


def test_1c_result_does_not_depend_on_record_order(big) -> None:
    """A counter drops 'whatever order the block yields' (report). A computed
    property cannot depend on input order, so the candidate set must be invariant."""
    for seed in (1, 2):
        recs = list(big["f"]["records"])
        random.Random(seed).shuffle(recs)
        _, _, got = run(recs)
        diff = got ^ big["got"]
        assert not diff, f"order-dependent: {len(diff)} pairs differ under shuffle seed {seed}"


# 2 ── exclusions are by computed property, visible in stats ───────────────────
def test_2_stats_expose_pruning_steps_and_no_cap_key(big) -> None:
    stats = big["stats"]
    cap_keys = [k for k in stats if CAP_KEY.search(k)]
    assert not cap_keys, f"stats imply a fixed-cap truncation: {cap_keys}"
    fams = {
        fam
        for fam, rx in PRUNE_FAMILIES.items()
        if any(rx.search(k) and isinstance(v, int) and v >= 0 for k, v in stats.items())
    }
    assert len(fams) >= 2, (
        f"stats must expose a count per pruning step (purging, filtering, meta-blocking); "
        f"found only {sorted(fams)} in {sorted(stats)}"
    )


def test_2b_returned_count_matches_stats(big) -> None:
    """Independent check: the stat must equal what was actually returned."""
    assert big["stats"].get("candidate_pairs") == len(big["pairs"]) == len(big["got"])


# 3 ── uncompared_pairs is truthful ────────────────────────────────────────────
def test_3_uncompared_pairs_absent_or_zero_when_no_cap(big) -> None:
    v = big["stats"].get("uncompared_pairs", 0)
    assert v == 0, f"uncompared_pairs={v}: nothing may be discarded by a counter (old: 389200)"


def test_3b_uncompared_pairs_never_overcounts(big) -> None:
    """Old defect: block reached under two keys is counted twice (389,200 vs 69,600 true)."""
    true_discard = big["possible"] - len(big["got"])
    v = big["stats"].get("uncompared_pairs", 0)
    assert v <= true_discard, f"reported {v} > true unique discard {true_discard}"


# 4 ── OOM guard raises, never truncates ───────────────────────────────────────
def test_4_guard_raises_when_it_binds(monkeypatch, big) -> None:
    name = guard_name()
    monkeypatch.setattr(re_, name, 10)  # far below the 40 planted survivors: must bind
    try:
        pairs = re_._generate_candidate_pairs(big["f"]["records"], stats={})
    except Exception:  # any type: the implementation chooses it
        return  # failed loudly: accepted
    pytest.fail(f"guard {name}=10 bound silently: returned {len(pairs)} pairs")


def test_4b_guard_does_not_bind_at_default_scale(big) -> None:
    assert not hasattr(re_, guard_name()) or getattr(re_, guard_name()) >= 100_000


# 5 ── pair completeness is measured ───────────────────────────────────────────
def test_5_pair_completeness_reported(big) -> None:
    """Lead adaptation (P19 conflict): pair completeness needs ground truth, and
    P19 forbids guessing PC/PQ without it. So the engine reports the exact
    label-free loss per pruning step, and the harness measures PC on planted truth."""
    stats = big["stats"]
    assert not [k for k in stats if COMPLETENESS_KEY.search(k)], "engine must not guess PC"
    prune_counts = [v for k, v in stats.items() if PRUNE_FAMILIES["meta_blocking"].search(k)]
    assert prune_counts and all(isinstance(v, int) and v >= 0 for v in prune_counts)
    pc = len(big["planted"] & big["got"]) / len(big["planted"])
    print(f"[P2] harness PC vs planted truth = {pc:.3f}")
    assert pc == 1.0


# 6 ── §2.4 no fitted constants ────────────────────────────────────────────────
LITERALS = (
    "France",
    "US",
    "SAS",
    "Berhad",
    "Global",
    "Group",
    "Holdings",
    "companyname",
    "main_entity_id",
    "company_relation",
    "staff",
    "org_title",
)
# today's hits (docstring/comment text plus the `staff` synonym tuple), from the parent commit
BASELINE_HITS = {
    ("staff", '"employee_count": ("employee", "headcount", "staff", "size"),'),
    ("company_relation", "call by the caller (from the ``company_relation`` entity family's"),
    ("org_title", "differently (pg ``company_name`` vs ch ``org_title``) — a single global"),
    ("org_title", "concept differently (``company_name`` vs ``org_title``), and a key tied"),
    ("org_title", "``company_name`` and ``org_title`` both land in the ``name`` slot."),
    ("org_title", "# (e.g. ``company_name`` over ``org_title`` for ``name``)"),
    ("SAS", '("SE", "SAS", "Helicopters", ...) carry the discriminating weight instead.'),
    ("company_relation", "ids it asserts a relationship with, from the ``company_relation``"),
    ("company_relation", 'between a resolve_entities record and its company_relation rows."""'),
    ("org_title", "# `org_title` both mapped to the `name` slot) is a judgment call,"),
    ("org_title", "``org_title``), so common-field comparison alone can never match their"),
}


def _hits() -> set[tuple[str, str]]:
    out = set()
    for line in SRC.read_text(encoding="utf-8").splitlines():
        for w in LITERALS:
            if re.search(rf"(?<![A-Za-z0-9_]){w}(?![A-Za-z0-9_])", line):
                out.add((w, line.strip()))
    return out


def test_6_no_new_corpus_literals() -> None:
    new = sorted(_hits() - BASELINE_HITS)
    assert not new, f"new corpus literals in resolve_entities.py: {new}"


BASELINE_NUMERIC = {  # module-level numeric constants today (names only)
    "_MAX_COMPARISONS",
    "_AUTO_MATCH",
    "_AUTO_EXCLUDE",
    "_MIN_COHESION",
    "_MAX_REF_BLOCK",
    "_MAX_LLM_PAIRS",
    "_LLM_CHUNK_SIZE",
    "_LLM_CONCURRENCY",
    "_MIN_PAIRS_FOR_MODEL",
    "_MAX_PENDING_CANDIDATES",
    "_MAX_DISCRIMINATING_FREQUENCY",
    "_LARGE_COMPONENT_THRESHOLD",
    "_PROCESS_POOL_MIN_RECORDS",
    "_SIMILARITY_CACHE_SIZE",
    "_FUZZY_NAME_CAP",
}


def test_6b_new_numeric_constants_only_the_named_guard() -> None:
    """Every NEW module-level numeric constant must be the OOM guard (sanctioned by
    the report) -- anything else (a fixed purge ratio, filter ratio, WNP threshold)
    is a fitted constant. The attacker also asks of the guard: would a corpus count
    produce this value? (No: it is a memory bound, not a quality knob.)"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        v = node.value if isinstance(node, ast.Assign) else None
        if isinstance(v, ast.Constant) and type(v.value) in (int, float):
            found |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    new = sorted(found - BASELINE_NUMERIC - set(GUARD_CANDIDATES))
    assert not new, f"new numeric constants (justify each or derive from data): {new}"
