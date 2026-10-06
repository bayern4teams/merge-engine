"""P20 attack (verifier, adversarial): the checks the derived acceptance does not make.

Controls pass; every REAL defect is a strict xfail (it flips to XPASS = failure the day it is fixed, so the
marker must then be removed).  Parent for the comparisons quoted in .handoff/P20.verdict.md is 23234c0.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p20_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.mcp_server.tools import resolve_entities as tre
from src.mcp_server.tools.cluster_quality import name_variation_rate
from tests.part1 import fixtures_matching as fx
from tests.part1 import test_p20_acceptance as acc

ROOT = Path(__file__).resolve().parents[2]


# ── harness ──────────────────────────────────────────────────────────────────────────────────
def _rec(i, name, **extra):
    return {
        "_origin_id": f"s{i % 2}",
        "_origin_table": "t",
        "_origin_id_row": str(i),
        "name": name,
        **extra,
    }


def _rid(i):
    return f"s{i % 2}:t:{i}"


def named(names, edges, w=0.97, **kw):
    """Cluster ``names`` (record i = names[i]) with scored ``edges`` [(i, j)] or [(i, j, weight)];
    -> sorted list of sorted record-index lists."""
    recs = [_rec(i, n) for i, n in enumerate(names)]
    pairs = [(_rid(e[0]), _rid(e[1]), e[2] if len(e) > 2 else w) for e in edges]
    cl, _m, _x = asyncio.run(tre._build_clusters(recs, pairs, "company", stats={}, **kw))
    return sorted(sorted(int(x.rsplit(":", 1)[1]) for x in c["record_ids"]) for c in cl)


def clique(n):
    return list(itertools.combinations(range(n), 2))


def path(n):
    return [(i, i + 1) for i in range(n - 1)]


def jitter_clique_world(seed, n, amp=0.01):
    rng = random.Random(seed)
    w = acc.world(seed, [n], ["clique"], 0)
    w["pairs"] = [(a, b, 0.97 + rng.uniform(-amp, amp)) for a, b, _ in w["pairs"]]
    return w


# ══ 1. recall: WNP + triangle support on realistic (non-tied) weights ══════════════════════════
def test_control_tied_weight_worlds_still_exact():
    """The acceptance's own regime (one weight per entity) works; this is the control for the xfails below."""
    for seed in range(10):
        w = acc.world(seed, [5, 4, 3], None, 0)
        pred, _c, _s = acc.run(w)
        acc.assert_exact(w, pred, f"seed {seed}")


def test_four_clique_with_natural_jitter_keeps_every_member():
    ids = ["a", "b", "c", "d"]
    names = ["Zeluko Bamira"] * 4
    w = {
        (0, 1): 0.9725, (0, 2): 0.9748, (0, 3): 0.9759,
        (1, 2): 0.9788, (1, 3): 0.9748, (2, 3): 0.9784,
    }  # fmt: skip
    got = named(names, [(i, j, c) for (i, j), c in w.items()])
    assert got == [[0, 1, 2, 3]], f"{ids}: {got}"


def test_jittered_5cliques_exact_recovery():
    fn = 0
    for seed in range(20):
        w = jitter_clique_world(seed, 5)
        pred, _c, _s = acc.run(w)
        fn += acc.measure(w, pred)["fn"]
    assert fn == 0, f"FN={fn} of 200 true pairs"


@pytest.mark.xfail(
    strict=True,
    reason="CEILING (lead r2: the report's accepted WNP recall trade). DEFECT (high): the report's own chain shape (ii) with a 0.4% weight wobble: an interior edge below "
    "the mean at BOTH ends is pruned, the chain splits (acceptance passes only because its chain is tied).",
)
def test_sparse_chain_with_natural_jitter_stays_whole():
    got = named(
        ["Zeluko Bamira"] * 5, [(0, 1, 0.972), (1, 2, 0.968), (2, 3, 0.971), (3, 4, 0.9695)]
    )
    assert got == [[0, 1, 2, 3, 4]]


def test_control_jitter_never_creates_false_positives():
    """Whatever recall is lost, a wobble must not MERGE entities (precision side of the trade)."""
    for seed in range(15):
        rng = random.Random(seed)
        w = acc.world(seed, [5, 5, 4], ["clique", "path", "tree"], 0)
        w["pairs"] = [(a, b, 0.97 + rng.uniform(-0.01, 0.01)) for a, b, _ in w["pairs"]]
        pred, _c, _s = acc.run(w)
        assert acc.measure(w, pred)["fp"] == 0


def test_control_determinism_on_jittered_worlds():
    """Order-free: records / pairs / orientation shuffles cannot change the partition."""
    for seed in range(12):
        w = jitter_clique_world(seed, 6)
        base = acc.run(w)[0]
        for s in (1, 2, 3):
            assert acc.run(acc.shuffled(w, s))[0] == base, f"seed {seed} shuffle {s}"


@pytest.mark.xfail(
    strict=True,
    reason="CEILING (P20 narrowed, user decision c): every member has a stronger wrong edge, so WNP prunes every "
    "true edge (below the mean at both ends) and keeps the five bridges as pairs. Before the narrowing a name cut "
    "removed the bridges first; names are no longer a cut (P16 flags such clusters).",
)
def test_double_bridged_true_edges_precision():
    """Every member of clique 0 has a stronger wrong edge into clique 1: no merge may survive."""
    w = acc.world(7, [5, 5], ["clique", "clique"], 0)
    a, b = w["truth"]
    w["pairs"] = [(x, y, 0.96) for x, y, _ in w["pairs"]] + [(a[i], b[i], 0.99) for i in range(5)]
    pred, _c, _s = acc.run(w)
    assert acc.measure(w, pred)["fp"] == 0


# ══ 2. name coherence ═════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "names",
    [
        ["Intl Business Machines", "International Business Machines"],
        ["Smith, John", "John Smith"],
        ["J. Smith", "John Smith"],
        ["John A. Smith", "John Smith"],
        ["Газпром", "ПАО Газпром"],
        ["Acme Holdings Ltd", "Acme Holdings Limited"],
        ["Acme", "Acme Ltd"],
        ["Microsoft", "Microsoft Corporation"],
        ["Mc Donalds", "McDonald's"],
        ["Johnson & Johnson", "Johnson and Johnson"],
    ],
)
def test_control_lone_edge_variants_stay_together(names):
    assert named(names, [(0, 1)]) == [[0, 1]]


@pytest.mark.parametrize("a,b", [(None, "Acme"), ("", "Acme"), (None, None), ("  ", "Acme Ltd")])
def test_control_missing_names_are_not_evidence(a, b):
    assert named([a, b], [(0, 1)]) == [[0, 1]]


@pytest.mark.parametrize(
    "names",
    [["Acme Ltd", "Acme Limited", "ACME LTD"], ["Smith John", "John Smith", "Smith, John"]],
)
def test_control_clique_of_variants_stays_whole(names):
    assert named(names, clique(len(names))) == [list(range(len(names)))]


@pytest.mark.parametrize(
    "names",
    [
        ["Microsoft", "Microsoft Corporation", "Microsoft Corp"],
        ["Acme", "Acme Ltd", "Acme Limited"],
    ],
)
def test_chain_of_legal_suffix_variants_stays_whole(names):
    assert named(names, path(3)) == [[0, 1, 2]]


def test_chain_of_person_initial_variants_stays_whole():
    names = ["John A Smith", "J. A. Smith", "John Smith", "Smith, John A."]
    assert named(names, path(4)) == [[0, 1, 2, 3]]


@pytest.mark.parametrize(
    "a,b", [("Société Générale", "Societe Generale SA"), ("Nestle SA", "Nestlé")]
)
def test_lone_edge_accent_variants_are_not_split(a, b):
    assert named([a, b], [(0, 1)]) == [[0, 1]]


# ══ 3. name_variation_rate contract (P16 reuses it) ═══════════════════════════════════════════
def test_control_variation_contract():
    assert name_variation_rate([]) == 0.0
    assert name_variation_rate(None) == 0.0  # type: ignore[arg-type]
    assert name_variation_rate(["Acme"]) == 0.0
    assert name_variation_rate([None, "", "  ", "Acme"]) == 0.0
    assert name_variation_rate(["Acme", "ACME."]) == 0.0
    v = name_variation_rate(["Acme Ltd", "Acme Limited", "Zenith Steel"])
    assert 0.0 < v <= 1.0
    names = ["Acme Ltd", "Acme Limited", "Zenith Steel", "Omega"]
    for perm in itertools.permutations(names):
        assert name_variation_rate(list(perm)) == pytest.approx(name_variation_rate(names))
    assert name_variation_rate(["Société", "Societe"]) == 0.0  # accent-fold
    assert name_variation_rate(["Газпром", "Лукойл"]) > 0.0  # non-Latin does not collapse
    assert name_variation_rate(["株式会社トヨタ", "株式会社ホンダ"]) > 0.0


def test_japanese_voiced_kana_are_not_folded_away():
    assert name_variation_rate(["ガス", "カス"]) > 0.0


def test_control_variation_numeric_names_do_not_crash():
    assert 0.0 <= name_variation_rate([123, "123 "]) <= 1.0  # type: ignore[list-item]


# ══ 4. one-to-one LP ══════════════════════════════════════════════════════════════════════════
def _brute_best(left, right, edges):
    best = 0.0
    n = min(len(left), len(right))
    for k in range(n + 1):
        for ls in itertools.combinations(left, k):
            for rs in itertools.permutations(right, k):
                tot = 0.0
                for a, b in zip(ls, rs, strict=True):
                    w = edges.get((a, b) if a < b else (b, a))
                    if w is None:
                        break
                    tot += w
                else:
                    best = max(best, tot)
    return best


def _lp_case(rng, nl, nr, density):
    recs, left, right = {}, [], []
    for i in range(nl):
        r = {"_origin_id": "L", "_origin_table": "t", "_origin_id_row": f"l{i}", "name": "Acme"}
        recs[fx.rid(r)] = r
        left.append(fx.rid(r))
    for j in range(nr):
        r = {"_origin_id": "R", "_origin_table": "t", "_origin_id_row": f"r{j}", "name": "Acme"}
        recs[fx.rid(r)] = r
        right.append(fx.rid(r))
    edges = {}
    for a in left:
        for b in right:
            if rng.random() < density:
                edges[(a, b) if a < b else (b, a)] = round(rng.uniform(0.95, 1.0), 4)
    return recs, left, right, edges


def test_one_to_one_parts_equals_brute_force_optimum_unequal_sizes():
    rng = random.Random(5)
    checked = 0
    for _ in range(150):
        nl, nr = rng.randrange(1, 5), rng.randrange(1, 5)
        recs, left, right, edges = _lp_case(rng, nl, nr, 0.8)
        members = sorted(left + right)
        if not edges:
            continue
        parts = tre._one_to_one_parts(members, edges, recs)
        got = 0.0
        for p in parts:
            ls = [m for m in p if m in left]
            rs = [m for m in p if m in right]
            assert len(ls) <= 1 and len(rs) <= 1, "one record per source per cluster"
            if ls and rs:
                got += edges[(ls[0], rs[0]) if ls[0] < rs[0] else (rs[0], ls[0])]
        assert got == pytest.approx(_brute_best(left, right, edges)), (nl, nr)
        assert sorted(m for p in parts for m in p) == members  # nobody lost
        checked += 1
    assert checked > 100


def test_control_one_to_one_default_off_and_noop_when_already_one_to_one():
    names = ["Acme"] * 4
    assert named(names, [(0, 1), (2, 3)], one_to_one=True) == [[0, 1], [2, 3]]
    assert named(names, [(0, 1), (2, 3)]) == [[0, 1], [2, 3]]


def test_control_one_to_one_end_to_end_optimum_when_edges_survive_pruning():
    # 2x2: optimum 0.99+0.99 (diag) over greedy-first-pick trap 0.995 + 0.95; every edge survives WNP at its row/col max
    names = ["Acme"] * 4  # records 0,2 = source s0 ; 1,3 = source s1
    got = named(names, [(0, 1, 0.995), (0, 3, 0.99), (2, 1, 0.99), (2, 3, 0.95)], one_to_one=True)
    assert got == [[0, 3], [1, 2]]


def test_one_to_one_end_to_end_reaches_the_inputs_optimum_on_random_blocks():
    rng = random.Random(11)
    worse = 0
    for _ in range(60):
        n = 4
        names = ["Acme"] * (2 * n)  # even index = s0, odd = s1
        s0 = [2 * i for i in range(n)]
        s1 = [2 * i + 1 for i in range(n)]
        w = {(a, b): round(rng.uniform(0.95, 1.0), 4) for a in s0 for b in s1}
        got = named(names, [(a, b, c) for (a, b), c in w.items()], one_to_one=True)
        tot = sum(w[(p[0], p[1])] if p[0] % 2 == 0 else w[(p[1], p[0])] for p in got if len(p) == 2)
        left = [_rid(a) for a in s0]
        right = [_rid(b) for b in s1]
        edges = {(_rid(a), _rid(b)): c for (a, b), c in w.items()}
        edges = {(k if k[0] < k[1] else (k[1], k[0])): v for k, v in edges.items()}
        best = _brute_best(left, right, edges)
        worse += tot < best - 1e-9
    assert worse == 0, f"{worse}/60 blocks below the optimum"


def test_control_scipy_absent_degrades_to_unreduced_with_a_warning(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "scipy", None)
    monkeypatch.setitem(sys.modules, "scipy.optimize", None)
    with caplog.at_level(logging.WARNING):
        got = named(
            ["Acme"] * 4,
            [(0, 1, 0.995), (0, 3, 0.995), (2, 1, 0.995), (2, 3, 0.995)],
            one_to_one=True,
        )
    assert got == [[0, 1, 2, 3]]  # no crash; the 1:1 request is silently NOT met (only a log line)
    assert "scipy is not installed" in caplog.text


def test_control_three_sources_are_not_reduced_but_do_not_crash(caplog):
    recs = [
        {"_origin_id": s, "_origin_table": "t", "_origin_id_row": s, "name": "Acme"}
        for s in ("A", "B", "C")
    ]
    pairs = [(fx.rid(recs[0]), fx.rid(recs[1]), 0.97), (fx.rid(recs[1]), fx.rid(recs[2]), 0.97)]
    with caplog.at_level(logging.WARNING):
        cl, _m, _x = asyncio.run(
            tre._build_clusters(recs, pairs, "company", one_to_one=True, stats={})
        )
    assert sorted(len(c["record_ids"]) for c in cl) == [3]


def test_one_to_one_is_unreachable_from_the_pipeline():
    """Report only (J1): no caller in src/ passes one_to_one, so the step cannot be turned on in a run."""
    out = subprocess.run(
        ["grep", "-rn", "one_to_one\\s*=", "src", "--include=*.py"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout  # fmt: skip
    callers = [
        ln for ln in out.splitlines() if "def " not in ln and "relationship_scoring" not in ln
    ]
    assert callers == [] or pytest.fail("a caller now exists: drop this test")


# ══ 5. P15 gate rebuild shares _split_component ═══════════════════════════════════════════════
def _gate_case():
    names = ["Zeluko Bamira"] * 3 + ["Quorvan Delisa"] * 3
    recs = [_rec(i, n) for i, n in enumerate(names)]
    by = {_rid(i): r for i, r in enumerate(recs)}
    return recs, by


def test_control_gate_accepted_pair_between_different_names_is_not_split():
    recs, by = _gate_case()
    accepted = {frozenset((_rid(0), _rid(3)))}
    reb = tre.rebuild_band_clusters(
        "company",
        [{"cluster_id": f"c{i}", "record_ids": [_rid(i)]} for i in range(6)],
        [[_rid(0), _rid(3), 0.80]],
        accepted,
        set(),
        0.95,
        by,
    )
    assert [c["record_ids"] for c in reb if len(c["record_ids"]) > 1] == [
        sorted((_rid(0), _rid(3)))
    ]


def test_control_gate_keep_apart_is_never_remerged_by_the_split():
    recs, by = _gate_case()
    pairs = [[_rid(a), _rid(b), 0.97] for a, b in [(0, 1), (1, 2), (0, 2)]]
    rejected = {frozenset((_rid(0), _rid(2)))}
    reb = tre.rebuild_band_clusters(
        "company",
        [{"cluster_id": "c", "record_ids": [_rid(0), _rid(1), _rid(2)]}],
        pairs,
        set(),
        rejected,
        0.95,
        by,
    )
    for c in reb:
        assert not ({_rid(0), _rid(2)} <= set(c["record_ids"]))


def test_control_gate_no_decision_rebuild_equals_build_on_full_corpus():
    rng = random.Random(3)
    words = ["acme", "zenith", "omega", "alpha", "global", "bank", "steel", "holdings"]
    for seed in range(40):
        rng = random.Random(seed)
        n = rng.randrange(4, 14)
        names = [" ".join(rng.sample(words, rng.randrange(1, 3))).title() for _ in range(n)]
        recs = [_rec(i, nm) for i, nm in enumerate(names)]
        pairs = [
            (_rid(a), _rid(b), rng.choice([0.96, 0.97, 0.98]))
            for a, b in itertools.combinations(range(n), 2)
            if rng.random() < 0.25
        ]
        cl, _m, _x = asyncio.run(tre._build_clusters(recs, pairs, "company", stats={}))
        reb = tre.rebuild_band_clusters(
            "company",
            cl,
            [list(p) for p in pairs],
            set(),
            set(),
            0.95,
            {_rid(i): r for i, r in enumerate(recs)},
        )
        assert sorted(sorted(c["record_ids"]) for c in cl) == sorted(
            sorted(c["record_ids"]) for c in reb
        )


# ══ 6. P6 repair, caller bars, degenerate input, runtime ══════════════════════════════════════
def test_control_p6_identity_conflict_still_separates_every_member():
    recs = [_rec(i, "Acme Ltd", tax_id=("111" if i < 2 else "222")) for i in range(4)]
    pairs = [(_rid(a), _rid(b), 0.97) for a, b in clique(4)]
    cl, _m, _x = asyncio.run(tre._build_clusters(recs, pairs, "company", stats={}))
    assert all(len(c["record_ids"]) == 1 for c in cl)


def test_control_conflict_free_clique_with_tax_id_stays_whole():
    recs = [_rec(i, "Acme Ltd", tax_id="111") for i in range(4)]
    pairs = [(_rid(a), _rid(b), 0.97) for a, b in clique(4)]
    cl, _m, _x = asyncio.run(tre._build_clusters(recs, pairs, "company", stats={}))
    assert [len(c["record_ids"]) for c in cl] == [4]


def test_control_callers_bars_are_honoured_and_degenerate_inputs():
    names = ["Zeluko Bamira"] * 2
    assert named(names, [(0, 1, 0.80)]) == [[0], [1]]  # review band default
    assert named(names, [(0, 1, 0.80)], auto_match=0.75) == [[0, 1]]
    assert named(names, [(0, 1, 0.80)], auto_match=0.75, auto_exclude=0.1) == [[0, 1]]
    assert named(names, []) == [[0], [1]]
    assert named([], []) == []
    # unknown ids and self-pairs are ignored; NaN is not an edge
    recs = [_rec(0, "Acme"), _rec(1, "Acme")]
    pairs = [
        (_rid(0), "ghost:t:9", 0.99),
        (_rid(0), _rid(0), 0.99),
        (_rid(0), _rid(1), float("nan")),
    ]
    cl, _m, _x = asyncio.run(tre._build_clusters(recs, pairs, "company", stats={}))
    assert sorted(len(c["record_ids"]) for c in cl) == [1, 1]


def test_control_known_cluster_ids_survive_the_split():
    recs = [_rec(i, "Zeluko Bamira") for i in range(3)]
    pairs = [(_rid(0), _rid(1), 0.97), (_rid(1), _rid(2), 0.97)]
    cl1, _m, _x = asyncio.run(tre._build_clusters(recs, pairs, "company", stats={}))
    cl2, _m, _x = asyncio.run(
        tre._build_clusters(
            recs, pairs, "company", {c["cluster_id"]: c["record_ids"] for c in cl1}, stats={}
        )
    )
    assert [c["cluster_id"] for c in cl1] == [c["cluster_id"] for c in cl2]


def test_runtime_5k_record_component_is_not_quadratic_in_the_conflict_loop():
    """Report: a 1000-record connected component takes ~5 s (P6's pairwise conflict loop, unchanged by P20;
    parent equal), a 5000-record one ~130 s.  Here only the cheap size is asserted (so the suite stays fast)
    -- the 5k figure is in the verdict.  This asserts 1000 records < 30 s."""
    import time

    n = 1000
    recs = [_rec(i, "Company Holdings") for i in range(n)]
    rng = random.Random(0)
    pairs = [(_rid(i), _rid(i + 1), 0.97) for i in range(n - 1)]
    pairs += [(_rid(rng.randrange(n)), _rid(rng.randrange(n)), 0.97) for _ in range(n)]
    pairs = [p for p in pairs if p[0] != p[1]]
    t = time.time()
    asyncio.run(tre._build_clusters(recs, pairs, "company", stats={}))
    assert time.time() - t < 30


# ══ 7. static: §2.4 and the dead cohesion cut ═════════════════════════════════════════════════
def _diff_added():
    out = subprocess.run(
        ["git", "diff", "23234c0", "6706b49", "--", "src/"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [ln[1:] for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def _numeric_constants(rev, path):
    import ast
    from collections import Counter

    src = subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return Counter(
        n.value
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    )


def test_control_no_new_numeric_literal_or_corpus_word_in_the_diff():
    """AST, not text: numeric constants added by 6706b49 to resolve_entities.py and cluster_quality.py
    (docstrings excluded by construction) may only be 0/1/2/0.0 -- no fitted number."""
    added = _numeric_constants("6706b49", acc.RE_REL) - _numeric_constants("23234c0", acc.RE_REL)
    quality = _numeric_constants("6706b49", "src/mcp_server/tools/cluster_quality.py")
    nums = set(added) | set(quality)
    assert nums <= {0, 1, 2}, nums
    text = "\n".join(_diff_added()).lower()
    for word in acc.CORPUS:
        assert not re.search(rf'["\']{re.escape(word)}["\']', text), word


def test_dead_min_cohesion_report():
    """Report only: after P20 `min_cohesion` is dead in build and rebuild; callers that remain are the
    orchestrator gate node (imports and passes it, ignored) and the unit test calling _partition_component."""
    out = subprocess.run(
        ["grep", "-rn", "_MIN_COHESION", "src", "--include=*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert (
        "src/orchestrator/nodes/resolve_entities.py" in out
    )  # a caller still passes it -> argument is ignored
    body = Path(ROOT / acc.RE_REL).read_text()
    i = body.index("def rebuild_band_clusters")
    j = body.index("\ndef ", i + 10)
    assert "min_cohesion" not in body[i:j].split('"""')[2]  # parameter unused in the function body
    assert math.isfinite(tre._MIN_COHESION)


# ══ R2 (commit 4171c64): attacks on the new rules ═════════════════════════════════════════════
_FILLER = [
    f"{w} Corp" for w in ["Qux", "Wib", "Zed", "Fon", "Gra", "Hul", "Ipa", "Jor", "Kel", "Lum"]
]


@pytest.mark.xfail(
    strict=True,
    reason="CEILING (two wrong edges into one node form a real triangle; names are not a cut since P20 was narrowed -- see _split_component). DEFECT r2 (medium, regression vs r1 and parent): support counted over CONFIDENT (pre-WNP) edges "
    "re-admits a strong double bridge: two strong wrong edges into one node of the other clique make the "
    "WNP-pruned true edge a 'triangle' that corroborates both bridges (names uninformative).",
)
def test_r2_double_bridge_between_two_cliques_is_not_corroborated_by_a_pruned_edge():
    names = ["Acme"] * 10
    edges = [
        (i, j, 0.96) for g in (range(0, 5), range(5, 10)) for i, j in itertools.combinations(g, 2)
    ]
    got = named(names, edges + [(0, 5, 0.99), (1, 5, 0.99)])
    assert got == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]]


def test_control_r2_single_bridge_between_two_cliques_still_cut():
    names = ["Acme"] * 10
    edges = [
        (i, j, 0.96) for g in (range(0, 5), range(5, 10)) for i, j in itertools.combinations(g, 2)
    ]
    assert named(names, edges + [(0, 5, 0.99)]) == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]]


@pytest.mark.parametrize(
    "a,b",
    [
        ("Сбербанк", "ПАО Сбербанк России"),
        ("Яндекс", "ООО Яндекс Технологии"),  # noqa: RUF001
        ("トヨタ", "トヨタ自動車株式会社"),
        ("삼성", "삼성전자 주식회사"),
    ],
)
def test_r2_non_latin_lone_edge_variants_are_not_split(a, b):
    assert named([a, b], [(0, 1)]) == [[0, 1]]


def test_control_r2_latin_equivalents_are_kept():
    for a, b in [
        ("Sberbank", "PJSC Sberbank of Russia"),
        ("Samsung", "Samsung Electronics Co Ltd"),
    ]:
        assert named([a, b], [(0, 1)]) == [[0, 1]]


def test_control_r2_determinism_with_names_and_three_sources():
    words = ["acme", "zenith", "omega", "alpha", "bank", "steel", "holdings", "qorvan", "tata"]
    for seed in range(40):
        rng = random.Random(seed)
        n = rng.randrange(6, 24)
        names = [
            " ".join(rng.sample(words, rng.randrange(1, 3))).title() + rng.choice(["", " Ltd"])
            for _ in range(n)
        ]
        edges = [(a, b, rng.uniform(0.95, 0.99)) for a, b in clique(n) if rng.random() < 0.15]

        def go(s, n=n, names=names, edges=edges):
            r = random.Random(s)
            order = list(range(n))
            r.shuffle(order)
            recs = [_rec(i, names[i]) for i in order]
            ps = [
                (_rid(a), _rid(b), w) if r.random() < 0.5 else (_rid(b), _rid(a), w)
                for a, b, w in edges
            ]
            r.shuffle(ps)
            cl, _m, _x = asyncio.run(tre._build_clusters(recs, ps, "company", stats={}))
            return sorted(sorted(c["record_ids"]) for c in cl)

        assert go(1) == go(2) == go(3)


# ══ R3 (commit 1d14d28): attacks on the token-coverage name rule ══════════════════════════════
def _apart(got, i, j):
    where = {m: k for k, c in enumerate(got) for m in c}
    return where[i] != where[j]


def test_control_r3_legit_variants_of_real_names_are_rarely_split():
    """Operators: legal suffix/abbreviation/initials/dropped word/reordering on 20 real-style names, all variant
    pairs as lone edges (267 pairs): at most 3 splits (measured 2: 'Lim' vs 'ltd', both abbreviations)."""
    ents = ["Bank of America Corporation", "General Electric Company", "International Business Machines Corp",
            "Procter and Gamble Company", "Deutsche Telekom AG", "John Michael Smith", "Maria Elena Rodriguez Garcia",
            "Siemens Healthineers AG", "Tata Consultancy Services Limited", "Banco Santander Central Hispano"]  # fmt: skip
    suf = {
        "corporation": "corp",
        "company": "co",
        "limited": "ltd",
        "international": "intl",
        "and": "&",
        "services": "svcs",
    }
    splits = total = 0
    for e in ents:
        t = e.split()
        vs = {e, " ".join(suf.get(x.lower(), x) for x in t), " ".join(t[:-1]) if len(t) > 2 else e,
              " ".join(x[0] + "." if 0 < i < len(t) - 1 else x for i, x in enumerate(t)), ", ".join([t[-1], " ".join(t[:-1])])}  # fmt: skip
        for a, b in itertools.combinations(sorted(vs), 2):
            total += 1
            splits += len(named([a, b], [(0, 1)])) == 2
    assert total > 30 and splits <= 1, (splits, total)


def test_control_gate_noop_with_band_records_only_equals_build():
    """The gate rebuild sees only the records its pairs name; with no name cut there is nothing it could re-join."""
    differs = 0
    for seed in range(40):
        rng = random.Random(seed)
        w = acc.world(seed, [4, 4, 3, 3, 2, 2, 2], None, 0)
        ids = [i for t in w["truth"] for i in t]
        grp = {i: g for g, t in enumerate(w["truth"]) for i in t}
        for _ in range(5):
            x, y = rng.sample(ids, 2)
            if grp[x] != grp[y]:
                w["pairs"].append((x, y, rng.choice([0.96, 0.97, 0.98])))
        cl, _m, _x = asyncio.run(tre._build_clusters(w["records"], w["pairs"], "company", stats={}))
        ret = [list(p) for p in w["pairs"]]
        need = {x for p in ret for x in p[:2]}
        reb = tre.rebuild_band_clusters(
            "company",
            cl,
            ret,
            set(),
            set(),
            0.95,
            {fx.rid(r): r for r in w["records"] if fx.rid(r) in need},
        )
        differs += sorted(sorted(c["record_ids"]) for c in cl) != sorted(
            sorted(c["record_ids"]) for c in reb
        )
    assert differs == 0
