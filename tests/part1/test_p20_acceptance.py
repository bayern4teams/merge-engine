"""P20 acceptance: clustering that does not amplify matching error (derived from the report's Evidence + Fix text;
NO P20 implementation seen -- only today's signatures, to know what to call).

Report P20 / plan section 9 P20 / HANDOFF flag 5.  Four steps: (1) prune weak edges with meta-blocking's own
threshold (WNP local mean, NOT a hand-set confidence cut); (2) connected components on the survivors; (3) coherence
split using the within-cluster NAME-VARIATION rate; (4) LP 1:1 reduction where strictly one-to-one is required.
Phase 0 (PHASE0_REPORT) found the report's own shape (weak bridge) already passes, so it is a REGRESSION GUARD; the
real failures are (i) the bridge is the strongest edge -> both clusters shatter (FN 20) and (ii) a true cluster seen only
as a chain -> 5 singletons (FN 10).

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p20_acceptance.py -q -p no:cacheprovider

MINIMAL CONTRACT (everything the tests call; each is an adaptation point below)
  1. ``tre._build_clusters(records, scored_pairs, entity_type, known_cluster_ids=None, auto_match=, auto_exclude=,
     stats=None, pair_evidence=None, one_to_one=False)`` -- the SAME async signature as today plus ONE new optional
     keyword ``one_to_one: bool = False`` (name: ONE_TO_ONE_KW).  Returns ``(clusters, n_auto_matched,
     n_auto_excluded)``; each cluster is a dict with ``record_ids`` (sorted list) and the keys it has today.  Every
     record is in exactly one cluster; singletons are clusters.  Records carry the engine's id fields; the display name
     is ``rec["name"]`` (NAME_FIELD; "name" is already in ``_NAME_FIELD_HINTS``).  Edge weight = the scored confidence.
  2. The name-variation function, in a NEW module that is NOT ``resolve_entities.py`` (HANDOFF flag 5: P16 reuses it):
         ``<QUALITY_MODULE>.<NAME_VARIATION_FN>(names: Sequence[str | None]) -> float``
     names = the display names of the members of ONE cluster (None/empty = missing, ignored).  Returns a variation
     rate in [0, 1]: 0.0 for identical names, 0.0 for fewer than two usable names, and strictly larger when members'
     names are unrelated than when they are variants (case/punctuation/typo/legal-suffix) of one name.  It must not
     depend on a list of legal-form words (plan 2.4); non-Latin names must not collapse to empty.
     Module default probed in order: cluster_quality, quality, quality_report, quality_signals, label_free_quality
     under ``src.mcp_server.tools`` (override: env P20_QUALITY_MODULE=dotted.path, P20_NAME_VARIATION_FN=name).
     Static check: ``resolve_entities.py`` imports it from that module and defines no ``*variation*`` function.
  3. WNP convention for the pruning step = the repo's own (``_generate_candidate_pairs``): an edge survives if its
     weight >= the mean weight of its node's incident edges at EITHER endpoint.  The 1:1 cases are built so their
     optimum edges survive that rule (each is the row or column maximum).

HOW EACH SHAPE IS MEASURED: the planted truth is a list of clusters; the scorer is tests/part1/scorer.py (pairwise
TP/FP/FN, already proven both ways in Phase 0).  A test passes only on FP == 0 and FN == 0 for planted worlds whose
names are unambiguous (generator asserts within-entity similarity >= 0.70 and cross-entity < 0.45, so ANY sane
name-similarity separates them).  The raw Phase 0 fixtures (names "a0".."b4" carry no name signal) are required to be
exactly right too: structure alone must fix them.

JUDGEMENT CALLS (flagged; each is waivable by the lead, see the .handoff/P20.acceptance.md):
  J1 "strictly one-to-one is required" has no destination constraint to read.  Contract: the caller says so with
     ``one_to_one=True`` (default False = today's behaviour, no 1:1).  Waive: P20_ONE_TO_ONE=skip (records why).
  J2 low-band scale test (test_j*): a data-derived design must not depend on an absolute 0.90 cohesion constant.
  J3 an alias with no lexical overlap (IBM / International Business Machines) is out of scope: name variation is a
     heuristic with a stated ceiling; not tested.
  J4 ``name_variation_rate`` argument shape (list of names) is mine; a different shape is one change in ``_variation``.
"""

from __future__ import annotations

import ast
import asyncio
import difflib
import importlib
import itertools
import math
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.mcp_server.tools import resolve_entities as tre
from tests.part1 import fixtures_matching as fx
from tests.part1.scorer import score_clusters

ROOT = Path(__file__).resolve().parents[2]
RE_REL = "src/mcp_server/tools/resolve_entities.py"
BASE_REV = "1231afe"  # HEAD when these checks were derived (before any P20 code)

# ── adaptation points ────────────────────────────────────────────────────────────────────────
NAME_FIELD = "name"
ONE_TO_ONE_KW = os.environ.get("P20_ONE_TO_ONE_KW", "one_to_one")
ONE_TO_ONE_WAIVED = os.environ.get("P20_ONE_TO_ONE", "") == "skip"
QUALITY_CANDIDATES = [
    os.environ["P20_QUALITY_MODULE"]
    if os.environ.get("P20_QUALITY_MODULE")
    else f"src.mcp_server.tools.{m}"
    for m in (
        "cluster_quality",
        "quality",
        "quality_report",
        "quality_signals",
        "label_free_quality",
    )
]
NAME_VARIATION_FN = os.environ.get("P20_NAME_VARIATION_FN", "name_variation_rate")
OWNED = {"_build_clusters", "_partition_component", "_split_weakest_link"}
ALLOWED_INTS = {
    0,
    1,
    2,
}  # indices / rounding; anything else is a constant the lead must whitelist here
CORPUS = {
    "france", "us", "sas", "berhad", "global", "group", "holdings", "companyname", "main_entity_id",
    "company_relation", "staff", "org_title", "testdiffbot", "companyexperience", "ltd", "inc", "gmbh",
    "corp", "llc", "plc", "limited", "sa", "co",
}  # fmt: skip


# ══ harness ═══════════════════════════════════════════════════════════════════════════════════
def _quality_module():
    errs = []
    for dotted in QUALITY_CANDIDATES:
        try:
            return importlib.import_module(dotted)
        except ImportError as e:
            errs.append(f"{dotted}: {e}")
    pytest.fail("no name-variation module found (HANDOFF flag 5): " + "; ".join(errs))


def _variation(names):
    """Adaptation point J4: how the name-variation function is called."""
    mod = _quality_module()
    assert mod.__name__ != tre.__name__, "name variation must live outside resolve_entities.py"
    return getattr(mod, NAME_VARIATION_FN)(list(names))


def run(case, **kw):
    """-> (predicted clusters as sorted lists, raw clusters, stats)."""
    stats: dict = {}
    clusters, _m, _x = asyncio.run(
        tre._build_clusters(case["records"], case["pairs"], "company", stats=stats, **kw)
    )
    pred = sorted(sorted(c["record_ids"]) for c in clusters)
    return pred, clusters, stats


def measure(case, pred):
    return score_clusters(pred, case["truth"], fx.source_of(case["records"]))["all"]


def assert_exact(case, pred, what=""):
    s = measure(case, pred)
    assert (s["fp"], s["fn"]) == (0, 0), (
        f"{what} FP={s['fp']} FN={s['fn']} sizes={sorted((len(c) for c in pred), reverse=True)} "
        f"fp_pairs={s['fp_pairs'][:3]} fn_pairs={s['fn_pairs'][:3]}"
    )


def assert_partition(case, clusters):
    got = sorted(r for c in clusters for r in c["record_ids"])
    assert got == sorted(fx.rid(r) for r in case["records"]), "every record in exactly one cluster"
    for c in clusters:
        assert c["record_ids"] == sorted(c["record_ids"]) and c["record_ids"]


def shuffled(case, seed):
    rng = random.Random(seed)
    recs = list(case["records"])
    rng.shuffle(recs)
    pairs = [(b, a, w) if rng.random() < 0.5 else (a, b, w) for a, b, w in case["pairs"]]
    rng.shuffle(pairs)
    return {**case, "records": recs, "pairs": pairs}


# ── planted worlds with realistic names ─────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower())


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _syl(rng, k=2):
    return "".join(rng.choice("bdfgklmnprtvz") + rng.choice("aeiou") for _ in range(k))


def _variant(rng, canon: str) -> str:
    """One mild variation of a canonical 'tok tok' name: case, punctuation, a dropped letter, a legal-form suffix."""
    first, second = canon.split(" ")
    kind = rng.randrange(6)
    if kind == 0:
        return canon
    if kind == 1:
        return canon.upper() + "."
    if kind == 2:
        return f"{first.title()} {second.title()}"
    if kind == 3:
        return f"{canon} {rng.choice(['ltd', 'inc', 'co'])}"
    if kind == 4:
        i = rng.randrange(1, len(first))
        return f"{first[:i] + first[i + 1 :]} {second}"
    return f"{canon.title()} {rng.choice(['Ltd', 'Inc'])}"


def make_entity(rng, taken: list[str], n: int, k: int = 2) -> list[str]:
    """n variant names of ONE new entity; unambiguously apart from every name in ``taken``."""
    for _ in range(500):
        canon = f"{_syl(rng, k)} {_syl(rng, k)}"
        names = [_variant(rng, canon) for _ in range(n)]
        within = all(_sim(a, b) >= 0.70 for a, b in itertools.combinations(names, 2))
        apart = all(_sim(a, b) < 0.45 for a in names for b in taken)
        if within and apart:
            return names
    raise AssertionError("fixture generator could not draw an unambiguous entity")


def _rec(src, row, name):
    return {"_origin_id": src, "_origin_table": "t", "_origin_id_row": row, NAME_FIELD: name}


def _clique(ids, rng, lo, hi):
    return [(a, b, rng.uniform(lo, hi)) for a, b in itertools.combinations(ids, 2)]


def _path(ids, rng, lo, hi):
    return [(ids[i], ids[i + 1], rng.uniform(lo, hi)) for i in range(len(ids) - 1)]


def _tree(ids, rng, lo, hi):
    return [(ids[i], ids[rng.randrange(i)], rng.uniform(lo, hi)) for i in range(1, len(ids))]


def world(seed, sizes, kinds=None, n_bridges=0, internal=(0.955, 0.985), bridge=(0.97, 0.999), k=2):
    """Entities of the given sizes; each is a clique, path or tree of strong edges (never a clique with a pendant:
    that shape is structurally ambiguous, names would have to decide).  ``n_bridges`` WRONG edges join random
    members of different entities, with weights drawn from ``bridge`` (frequently stronger than every internal edge).

    WNP NOTE fixing the generator (fairness to the report's own design): WNP prunes an edge that is below the local mean
    at BOTH its endpoints, and "meta-blocking is lossy by design".  So (a) each entity's internal edges share ONE weight
    (ties survive: a node whose edges all tie keeps all of them), and (b) no wrong edge touches both ends of a true
    edge or the same node twice, so no true edge is below the mean at both ends.  With jittered weights or doubly
    bridged true edges a correct WNP-first pipeline legitimately loses true edges, which is a recall trade, not a bug."""
    rng = random.Random(seed)
    taken: list[str] = []
    records, truth, pairs, groups = [], [], [], []
    row = 0
    for i, n in enumerate(sizes):
        names = make_entity(rng, taken, n, k)
        taken += names
        ids = []
        for nm in names:
            r = _rec("src_a" if row % 2 == 0 else "src_b", f"r{row}", nm)
            row += 1
            records.append(r)
            ids.append(fx.rid(r))
        truth.append(ids)
        groups.append(ids)
        kind = (kinds[i] if kinds else rng.choice(["clique", "path", "tree"])) if n > 2 else "path"
        build = {"clique": _clique, "path": _path, "tree": _tree}[kind]
        w_e = rng.uniform(
            *internal
        )  # ONE weight per entity: see the WNP note in world()'s docstring
        pairs += build(ids, rng, w_e, w_e)
    true_edges = {frozenset((a, b)) for a, b, _ in pairs}
    used: set[tuple[str, str]] = set()
    touched: set[str] = set()
    for _ in range(n_bridges * 20):
        if len(used) >= n_bridges:
            break
        g1, g2 = rng.sample(range(len(groups)), 2)
        a, b = rng.choice(groups[g1]), rng.choice(groups[g2])
        if (a, b) in used or (b, a) in used or a in touched or b in touched:
            continue
        if any(frozenset((x, t)) in true_edges for x in (a, b) for t in touched):
            continue
        used.add((a, b))
        touched |= {a, b}
        pairs.append((a, b, rng.uniform(*bridge)))
    return {"records": records, "truth": truth, "pairs": pairs}


def named_cases():
    """Hand-shaped, named versions of the Phase 0 shapes, plus the incoherent-clique shapes."""
    c = {}
    # (i) the wrong edge is the strongest: two 0.96 cliques, one 0.99 bridge
    w = world(11, [5, 5], ["clique", "clique"])
    a, b = w["truth"]
    w["pairs"] = [(x, y, 0.96) for x, y, _ in w["pairs"]] + [(a[0], b[0], 0.99)]
    c["bridge_strongest"] = w
    # control: the report's own weak-bridge shape
    w = world(12, [5, 5], ["clique", "clique"])
    a, b = w["truth"]
    w["pairs"] = [(x, y, 0.98) for x, y, _ in w["pairs"]] + [(a[0], b[0], 0.96)]
    c["bridge_weak"] = w
    # (ii) a true cluster seen only as a chain
    w = world(13, [5], ["path"])
    w["pairs"] = [(x, y, 0.97) for x, y, _ in w["pairs"]]
    c["sparse_chain"] = w
    # chain through ANOTHER entity: path A - x - path B, every edge 0.96, no triangle anywhere (only names can tell)
    w = world(14, [5, 1, 5], ["path", "path", "path"])
    a, x, b = w["truth"]
    w["pairs"] = [(p, q, 0.96) for p, q, _ in w["pairs"]] + [(a[2], x[0], 0.96), (x[0], b[2], 0.96)]
    c["chain_through_other_entity"] = w
    # incoherent clique: 6 records, every pair 0.97 (an identifier collision), but two different names
    w = world(15, [3, 3], ["clique", "clique"])
    ids = [i for t in w["truth"] for i in t]
    w["pairs"] = [(p, q, 0.97) for p, q in itertools.combinations(ids, 2)]
    c["incoherent_clique"] = w
    # a lone strong edge between two different names
    w = world(16, [1, 1])
    ids = [i for t in w["truth"] for i in t]
    w["pairs"] = [(ids[0], ids[1], 0.97)]
    c["lone_incoherent_pair"] = w
    # controls: coherent clique with every kind of variation, and a lone coherent pair
    w = world(17, [6], ["clique"])
    w["pairs"] = [(x, y, 0.97) for x, y, _ in w["pairs"]]
    c["coherent_clique"] = w
    w = world(18, [2])
    w["pairs"] = [(x, y, 0.97) for x, y, _ in w["pairs"]]
    c["lone_coherent_pair"] = w
    return c


NAMED = named_cases()


# ══ A. the Phase 0 shapes, exactly as fixtured (structure alone must fix them) ════════════════
RAW = fx.p20_cases()


@pytest.mark.parametrize(
    "name",
    [
        "bridge_stronger_than_internal",
        "sparse_true_cluster_chain",
        "bridge_weak",
        "chain_via_singleton",
    ],
)
def test_a_phase0_shapes_exact(name):
    """bridge_stronger_than_internal (FN 20 today) and sparse_true_cluster_chain (FN 10 today) are the NEGATIVES;
    bridge_weak and chain_via_singleton already pass and must not regress."""
    case = RAW[name]
    pred, clusters, _ = run(case)
    assert_partition(case, clusters)
    assert_exact(case, pred, name)


# ══ B. named shapes ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name", sorted(NAMED))
def test_b_named_shapes_exact(name):
    """Negatives today: bridge_strongest, sparse_chain, chain_through_other_entity, incoherent_clique,
    lone_incoherent_pair.  Controls: bridge_weak, coherent_clique, lone_coherent_pair."""
    case = NAMED[name]
    pred, clusters, _ = run(case)
    assert_partition(case, clusters)
    assert_exact(case, pred, name)


def test_b_missing_names_are_not_evidence_of_incoherence():
    """A clique of 4 where one name is None, one is '' and one key is absent, one present: nothing to split on."""
    recs = [
        _rec("src_a", "0", "orchid works"),
        _rec("src_b", "1", None),
        _rec("src_a", "2", ""),
        _rec("src_b", "3", "Orchid Works Ltd"),
    ]
    del recs[2][NAME_FIELD]
    ids = [fx.rid(r) for r in recs]
    case = {
        "records": recs,
        "truth": [ids],
        "pairs": [(a, b, 0.97) for a, b in itertools.combinations(ids, 2)],
    }
    pred, clusters, _ = run(case)
    assert_partition(case, clusters)
    assert_exact(case, pred, "missing names")


# ══ C. no fitted constant: the answer depends on the RELATIVE weights, not on absolute cut values ══
STRONG_BRIDGE = [(0.96, 0.99), (0.951, 0.952), (0.990, 0.999), (0.975, 0.9999)]
WEAK_BRIDGE = [(0.99, 0.96), (0.952, 0.951), (0.999, 0.99), (0.9999, 0.975)]


def _bridge_case(internal, bridge_w, n=5, sparse=False):
    A, B = fx._members("a", n), fx._members("b", n)
    a, b = [fx.rid(r) for r in A], [fx.rid(r) for r in B]
    edges = (
        _path(a, random.Random(0), internal, internal)
        + _path(b, random.Random(0), internal, internal)
        if sparse
        else fx._clique(a, internal) + fx._clique(b, internal)
    )
    return {"records": A + B, "truth": [a, b], "pairs": edges + [(a[0], b[0], bridge_w)]}


@pytest.mark.parametrize("internal,bridge_w", STRONG_BRIDGE)
def test_c_strongest_bridge_any_weight_scale(internal, bridge_w):
    """Same topology, weights remapped order-preservingly inside [auto_match, 1): the partition must not change.  A
    literal confidence cut (e.g. 'prune below 0.97') passes one scale and fails another.  Negative today (all four)."""
    case = _bridge_case(internal, bridge_w)
    pred, _, _ = run(case)
    assert_exact(case, pred, f"internal={internal} bridge={bridge_w}")


@pytest.mark.parametrize("internal,bridge_w", WEAK_BRIDGE)
def test_c_weak_bridge_any_weight_scale(internal, bridge_w):
    case = _bridge_case(internal, bridge_w)
    pred, _, _ = run(case)
    assert_exact(case, pred, f"internal={internal} bridge={bridge_w}")


@pytest.mark.parametrize("internal,bridge_w,auto_match", [(0.60, 0.80, 0.50), (0.20, 0.30, 0.10)])
def test_j_low_band_strongest_bridge(internal, bridge_w, auto_match):
    """J2.  The caller's own match bar is lower than today's 0.90 cohesion constant; the shape is the same.
    Negative today (a 0.60 clique is cut by the absolute _MIN_COHESION)."""
    case = _bridge_case(internal, bridge_w)
    pred, _, _ = run(case, auto_match=auto_match, auto_exclude=auto_match / 4)
    assert_exact(case, pred, f"band {auto_match}")


# ══ D. giant components and random planted worlds ═════════════════════════════════════════════
@pytest.mark.parametrize("seed", [101, 102])
def test_d_giant_component_is_taken_apart(seed):
    """60 planted entities (3..6 members) joined into one giant component by 120 wrong edges, most stronger than any
    internal edge.  Exact recovery, and within a time bound (the split must not be cubic in the component)."""
    rng = random.Random(seed)
    w = world(seed, [rng.randrange(3, 7) for _ in range(60)], n_bridges=120, k=4)
    t0 = time.monotonic()
    pred, clusters, stats = run(w)
    elapsed = time.monotonic() - t0
    assert_partition(w, clusters)
    assert_exact(w, pred, f"giant seed={seed}")
    assert elapsed < 60, f"giant component took {elapsed:.0f}s"


@pytest.mark.parametrize("seed", range(8))
def test_d_random_worlds_exact_and_order_invariant(seed):
    """Random planted worlds (cliques, paths, trees; wrong bridges incl. strongest).  Exact recovery, and the same
    partition however the records and pairs are ordered/oriented (union-find style order dependence)."""
    rng = random.Random(seed)
    w = world(1000 + seed, [rng.randrange(1, 8) for _ in range(8)], n_bridges=6)
    for k in range(3):
        case = w if k == 0 else shuffled(w, 50 * seed + k)
        pred, clusters, _ = run(case)
        assert_partition(case, clusters)
        assert_exact(w, pred, f"seed={seed} shuffle={k}")


# ══ E. contract controls (all pass today; P20 must not break them) ═════════════════════════════
def test_e_borderline_pairs_never_merge():
    """Pairs in the review band (0.70 <= s < 0.95) are not merged here (P15 owns the gate)."""
    w = world(21, [2])
    ids = w["truth"][0]
    case = {"records": w["records"], "truth": [[i] for i in ids], "pairs": [(ids[0], ids[1], 0.94)]}
    pred, clusters, _ = run(case)
    assert_partition(case, clusters)
    assert_exact(case, pred, "borderline")


def test_e_caller_match_bar_is_honoured():
    """auto_match is an argument; a 0.92 pair merges under auto_match=0.90 and not under the default."""
    w = world(22, [2])
    ids = w["truth"][0]
    case = {"records": w["records"], "truth": [ids], "pairs": [(ids[0], ids[1], 0.92)]}
    pred, _, _ = run(case, auto_match=0.90)
    assert pred == [sorted(ids)]
    pred, _, _ = run(case)
    assert pred == sorted([[i] for i in ids])


def test_e_empty_and_pairless_inputs():
    clusters, m, x = asyncio.run(tre._build_clusters([], [], "company"))
    assert clusters == [] and (m, x) == (0, 0)
    w = world(23, [1, 1, 1])
    pred, clusters, _ = run({**w, "pairs": []})
    assert_partition(w, clusters)
    assert [len(c) for c in pred] == [1, 1, 1]


def test_e_nan_and_reversed_duplicate_pairs():
    w = world(24, [2, 1])
    (a, b), (x,) = w["truth"]
    pairs = [
        (a, x, float("nan")),
        (b, a, 0.40),
        (a, b, 0.98),
    ]  # duplicate pair: the strongest score counts
    case = {**w, "pairs": pairs}
    pred, clusters, _ = run(case)
    assert_partition(case, clusters)
    assert_exact(case, pred, "nan/duplicate")


def test_e_known_cluster_ids_are_reused_and_output_is_deterministic():
    w = NAMED["coherent_clique"]
    _, first, _ = run(w)
    known = {rid: c["cluster_id"] for c in first for rid in c["record_ids"]}
    _, second, _ = run(w, known_cluster_ids=known)
    assert {c["cluster_id"] for c in second} == {c["cluster_id"] for c in first}
    _, third, _ = run(shuffled(w, 5))
    assert sorted((c["cluster_id"], tuple(c["record_ids"])) for c in third) == sorted(
        (c["cluster_id"], tuple(c["record_ids"])) for c in first
    )


def test_e_profile_stats_still_reported():
    _, _, stats = run(NAMED["bridge_weak"])
    for key in (
        "component_count",
        "largest_component_size",
        "large_components",
        "clustering_seconds",
    ):
        assert key in stats, f"stats lost {key!r}"


def test_h_pair_naming_an_unknown_record_does_not_crash():
    """HARDENING, not report-derived (waive with P20_SKIP_HARDENING=1): today a >= auto_match pair that names a record
    the caller did not pass raises KeyError inside union-find."""
    if os.environ.get("P20_SKIP_HARDENING"):
        pytest.skip("waived by the lead")
    w = NAMED["lone_coherent_pair"]
    case = {**w, "pairs": w["pairs"] + [(w["truth"][0][0], "ghost:t:999", 0.99)]}
    pred, clusters, _ = run(case)
    known = {r for c in clusters for r in c["record_ids"]} - {"ghost:t:999"}
    assert known == {fx.rid(r) for r in w["records"]}
    assert_exact(w, [[r for r in c if r != "ghost:t:999"] for c in pred], "unknown id")


# ══ F. LP 1:1 reduction (J1) ══════════════════════════════════════════════════════════════════
def _two_source(n, weights, name="orchid works"):
    X = [_rec("src_a", f"x{i}", name) for i in range(n)]
    Y = [_rec("src_b", f"y{i}", name) for i in range(n)]
    x, y = [fx.rid(r) for r in X], [fx.rid(r) for r in Y]
    pairs = [(x[i], y[j], w) for (i, j), w in weights.items()]
    return X + Y, pairs, x, y


def _optimum(n, weights):
    best = 0.0
    for k in range(n + 1):
        for rows in itertools.combinations(range(n), k):
            for cols in itertools.permutations(range(n), k):
                if all((r, c) in weights for r, c in zip(rows, cols, strict=True)):
                    best = max(best, sum(weights[(r, c)] for r, c in zip(rows, cols, strict=True)))
    return best


def _one_to_one_kw():
    if ONE_TO_ONE_WAIVED:
        pytest.skip("J1 waived by the lead (P20_ONE_TO_ONE=skip)")
    return {ONE_TO_ONE_KW: True}


def _checked_one_to_one(n, weights):
    records, pairs, x, y = _two_source(n, weights)
    case = {"records": records, "pairs": pairs, "truth": []}
    _, clusters, _ = run(case, **_one_to_one_kw())
    assert_partition(case, clusters)
    src = {fx.rid(r): r["_origin_id"] for r in records}
    for c in clusters:
        sources = [src[r] for r in c["record_ids"]]
        assert len(sources) == len(set(sources)), f"one-to-one violated: {c['record_ids']}"
    w_of = {tuple(sorted((a, b))): w for a, b, w in pairs}
    got = sum(
        w_of.get(tuple(sorted(p)), 0.0)
        for c in clusters
        for p in itertools.combinations(c["record_ids"], 2)
    )
    return got, _optimum(n, weights)


def test_f_decoy_edge_greedy_is_not_optimal():
    """x1-y1 0.995 is the strongest edge but taking it forfeits two 0.99 edges (sum 1.98 vs 0.995).  Greedy fails;
    the LP (maximise summed weight) passes.  Negative today (no such option)."""
    weights = {(0, 0): 0.995, (0, 1): 0.99, (1, 0): 0.99}
    got, best = _checked_one_to_one(2, weights)
    assert math.isclose(got, best, abs_tol=1e-9) and math.isclose(best, 1.98), (got, best)


def _decoy_matrix(seed, n=4):
    """Seeded search for a complete n x n weight matrix whose optimum is NOT the greedy matching, with every optimum
    edge a row or column maximum (so it survives WNP's either-end local mean)."""
    rng = random.Random(seed)
    while True:
        w = {(i, j): rng.uniform(0.951, 0.999) for i in range(n) for j in range(n)}
        opt = max(
            itertools.permutations(range(n)), key=lambda p: sum(w[(i, p[i])] for i in range(n))
        )
        if not all(
            w[(i, opt[i])] >= max(w[(i, j)] for j in range(n))
            or w[(i, opt[i])] >= max(w[(k, opt[i])] for k in range(n))
            for i in range(n)
        ):
            continue
        greedy, used_r, used_c = 0.0, set(), set()
        for (i, j), v in sorted(w.items(), key=lambda kv: -kv[1]):
            if i not in used_r and j not in used_c:
                used_r.add(i)
                used_c.add(j)
                greedy += v
        if _optimum(n, w) - greedy > 1e-6:
            return w


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_f_random_complete_matrices_reach_the_optimum(seed):
    weights = _decoy_matrix(seed)
    got, best = _checked_one_to_one(4, weights)
    assert math.isclose(got, best, abs_tol=1e-9), f"summed weight {got} != optimum {best}"


def test_f_default_does_not_impose_one_to_one():
    """Without the flag (no destination constraint known) same-source members stay together: x1-y1-x2 chain."""
    weights = {(0, 0): 0.97, (1, 0): 0.97}
    records, pairs, x, y = _two_source(2, weights)
    case = {"records": records, "pairs": pairs, "truth": [[x[0], x[1], y[0]], [y[1]]]}
    pred, clusters, _ = run(case)
    assert_partition(case, clusters)
    assert_exact(case, pred, "default is not 1:1")


# ══ G. the name-variation function (HANDOFF flag 5) ════════════════════════════════════════════
def test_g_function_lives_outside_resolve_entities_and_is_reused():
    mod = _quality_module()
    assert hasattr(mod, NAME_VARIATION_FN), f"{mod.__name__}.{NAME_VARIATION_FN} missing"
    tree = ast.parse((ROOT / RE_REL).read_text())
    defs = [
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert not [d for d in defs if "variation" in d.lower()], (
        "variation function defined in resolve_entities.py"
    )
    imports = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and (n.module or "").endswith(mod.__name__.split(".")[-1])
    ]
    assert any(NAME_VARIATION_FN in {a.name for a in n.names} for n in imports), (
        "resolve_entities.py must import the SAME function (P16 reuses it)"
    )
    mtree = ast.parse(Path(mod.__file__).read_text())
    assert not [
        n
        for n in ast.walk(mtree)
        if isinstance(n, (ast.Import, ast.ImportFrom)) and "resolve_entities" in ast.dump(n)
    ], "quality module must not import resolve_entities (layering)"


def test_g_zero_for_identical_and_for_too_few_names():
    for names in ([], ["orchid works"], [None], ["orchid works", None, ""], ["x y", "x y", "x y"]):
        r = _variation(names)
        assert isinstance(r, float) and r == 0.0, (names, r)


def test_g_variants_below_unrelated_and_in_unit_interval():
    rng = random.Random(3)
    unrelated = make_entity(rng, [], 1) + make_entity(rng, make_entity(rng, [], 1), 1)
    base = make_entity(rng, unrelated, 1)[0]
    variants = [base, base.upper() + ".", base.title(), base + " ltd", base[:1] + base[2:]]
    low, high = _variation(variants), _variation([base] + unrelated + [base[::-1]])
    assert 0.0 <= low < high <= 1.0, (low, high)
    case_only = _variation([base, base.upper(), base.title() + "."])
    assert 0.0 <= case_only <= low, (case_only, low)


def test_g_monotone_in_the_number_of_outliers_and_order_free():
    a = "bako dumi"
    others = ["ruzo kela", "tife nogu", "mape rizo", "dulo gefa"]
    rates = [_variation([a] * 5 + others[:k]) for k in range(len(others) + 1)]
    assert rates[0] == 0.0 and all(x < y for x, y in itertools.pairwise(rates)), rates
    seq = [a, a, others[0], a, others[1]]
    assert math.isclose(_variation(seq), _variation(list(reversed(seq))), abs_tol=1e-12)


def test_g_no_legal_form_wordlist_and_non_latin_does_not_collapse():
    """Names that differ only in the entity token but share a frequent trailing token are still unrelated, whether
    the trailing token is a real legal form or an invented one; and non-Latin names must not normalise to ''."""
    for tail in ("ltd", "zzyx"):
        same = _variation([f"bako dumi {tail}", f"bako dumi {tail}.", f"BAKO DUMI {tail.upper()}"])
        diff = _variation([f"bako dumi {tail}", f"ruzo kela {tail}", f"tife nogu {tail}"])
        assert same < diff, (tail, same, diff)
    assert _variation(["東京電力", "東京電力"]) == 0.0
    assert _variation(["東京電力", "大阪ガス"]) > 0.0
    assert _variation(["Société Générale", "Societe Generale"]) < _variation(
        ["Société Générale", "Kestrel Supply"]
    )


# ══ H. plan 2.4 / 2.5: no fitted constants, no corpus literals, mechanical guards ═══════════════
def _git_show(rev, rel):
    return subprocess.run(
        ["git", "show", f"{rev}:{rel}"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def _fn_nodes(tree):
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_names(fn):
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def _docstring_ids(fn):
    first = fn.body[0] if fn.body else None
    ok = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
    return {id(first.value)} if ok else set()


def _numbers(fn):
    skip = _docstring_ids(fn)
    return [
        c.value
        for c in ast.walk(fn)
        if isinstance(c, ast.Constant)
        and isinstance(c.value, (int, float))
        and not isinstance(c.value, bool)
        and id(c) not in skip
    ]


def _scan_targets():
    """(resolve_entities nodes to scan, quality-module nodes to scan, quality-module module-level assigns).
    Scanned in resolve_entities.py: the three owned functions, and any function NEW since BASE_REV that they call,
    transitively (so unrelated later edits by other items are not blamed on P20)."""
    now = _fn_nodes(ast.parse((ROOT / RE_REL).read_text()))
    base = _fn_nodes(ast.parse(_git_show(BASE_REV, RE_REL)))
    scan = {n: now[n] for n in OWNED if n in now}
    frontier = list(scan)
    while frontier:
        for callee in _called_names(scan[frontier.pop()]):
            if callee in now and callee not in base and callee not in scan:
                scan[callee] = now[callee]
                frontier.append(callee)
    mod = _quality_module()
    mtree = ast.parse(Path(mod.__file__).read_text())
    mod_assigns = [n for n in mtree.body if isinstance(n, (ast.Assign, ast.AnnAssign))]
    return scan, base, _fn_nodes(mtree), mod_assigns


def test_h_no_corpus_literal_in_new_or_changed_code():
    scan, _, qfns, qassigns = _scan_targets()
    hits = []
    for fn in [*scan.values(), *qfns.values()]:
        skip = _docstring_ids(fn)
        for c in ast.walk(fn):
            if (
                isinstance(c, ast.Constant)
                and isinstance(c.value, str)
                and id(c) not in skip
                and (
                    set(re.findall(r"[A-Za-z_]+", c.value.lower())) & CORPUS
                    or c.value.lower() in CORPUS
                )
            ):
                hits.append((fn.name, c.value[:40]))
    for n in qassigns:
        for c in ast.walk(n):
            if (
                isinstance(c, ast.Constant)
                and isinstance(c.value, str)
                and set(re.findall(r"[A-Za-z_]+", c.value.lower())) & CORPUS
            ):
                hits.append(("<module>", c.value[:40]))
    assert not hits, f"corpus literals (plan 2.4): {hits}"


def test_h_no_hand_set_confidence_cut_and_no_unexplained_constant():
    """Plan 2.4: 'would a count over the corpus produce this?'  Any literal in new/changed clustering code that was
    not already in that function at BASE_REV must be an index/rounding int in ALLOWED_INTS; a float strictly between 0
    and 1 is a confidence cut or a fitted weight and must be COMPUTED (WNP local mean, a corpus-derived quantity)."""
    scan, base, qfns, qassigns = _scan_targets()
    bad = []
    for name, fn in scan.items():
        known = set(_numbers(base[name])) if name in base else set()
        for v in _numbers(fn):
            if v in known or (isinstance(v, int) and v in ALLOWED_INTS):
                continue
            bad.append((name, v))
    for name, fn in qfns.items():
        bad += [
            (name, v)
            for v in _numbers(fn)
            if not (isinstance(v, int) and v in ALLOWED_INTS) and v not in (0.0, 1.0)
        ]
    for n in qassigns:
        bad += [
            ("<module>", c.value)
            for c in ast.walk(n)
            if isinstance(c, ast.Constant)
            and isinstance(c.value, (int, float))
            and not isinstance(c.value, bool)
        ]
    assert not bad, f"numeric literals not derivable from data (plan 2.4): {bad}"


def test_h_band_constants_are_referenced_not_retyped():
    scan, *_ = _scan_targets()
    for name, fn in scan.items():
        for v in _numbers(fn):
            assert not (isinstance(v, float) and v in (0.95, 0.7, 0.9)), (
                f"{name} types the constant {v}"
            )
    src = (ROOT / RE_REL).read_text()
    assert len(re.findall(r"^_AUTO_MATCH\s*=\s*0\.95\s*$", src, re.M)) == 1
    assert len(re.findall(r"^_AUTO_EXCLUDE\s*=\s*0\.70\s*$", src, re.M)) == 1


def test_h_arch_lint_clean():
    r = subprocess.run(
        [sys.executable, "scripts/lint_arch.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
