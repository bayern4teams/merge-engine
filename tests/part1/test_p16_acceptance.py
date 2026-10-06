"""P16 acceptance: label-free quality report (derived from the report's Evidence + Fix text, plan section 9 P16,
plan 2.4, and user decision (c) of 2026-10-06; NO P16 implementation seen -- only the existing
``cluster_quality.name_variation_rate`` signature, to know what to reuse).

Report P16: four signals that need no ground truth, printed in the run report:
  homonymy rate (rise = under-merging), name-variation rate (rise = over-merging), cluster-size distribution as Hill
  numbers (a giant component is visible at once), and B-cubed against the PREVIOUS run's clustering, which needs the
  previous clustering persisted: run one reports "not available", NOT a number.
User decision (c): name coherence is a FLAG, not a cut.  The existing ``name_variation_rate`` flags incoherent
clusters (a count + which ones) with a threshold derived from the run's own data (no hand-set literal), and the count
is a machine-readable key, because P10 will count it as a pre-load invariant.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p16_acceptance.py -q -p no:cacheprovider

MINIMAL CONTRACT (everything the tests call; each is an adaptation point below)
  Module (NEW, or ``cluster_quality`` itself; never ``resolve_entities``) under ``src.mcp_server.tools``, probed in
  order: label_free_quality, quality_report, quality_signals, cluster_quality (override: env P16_QUALITY_MODULE=dotted).
    quality_signals(clusters, names, history_path=None, entity="") -> dict
        clusters : list of dicts with ``cluster_id`` and ``record_ids`` (the engine's cluster dicts; singletons included)
        names    : {record_id: display name or None}; a missing id is a missing name (never an error)
        history_path : path-like.  Where the PREVIOUS run's clustering is read from and the CURRENT one is written
                   (after computing).  None = no persistence = B-cubed "not available".  One file serves many
                   entities: ``entity`` is the key inside it.
    hill_numbers(sizes) -> {0: D0, 1: D1, 2: D2}      (sizes = cluster sizes in records; see J2)
    bcubed(previous, current) -> float | None          (each = list of lists of record ids; F1 of B-cubed P and R,
                   over the records present in BOTH; None when there are none in common)
    homonymy_rate(clusters, names) -> float            (see J1)
  The dict returned by quality_signals has the keys named in K_* below (all JSON-serialisable, no NaN).
  ``build_reconciliation_report(state, load_results, errors)`` in orchestrator/nodes/load_data.py puts the same dict
  at ``report["entities"][entity][QUALITY_KEY]`` for every entity, reading the history path from the state key
  STATE_PATH_KEY (None/absent = no persistence); names come from the record's own best name-ish field the way the
  engine reads them (``company_name`` and ``org_title`` both count).

JUDGEMENT CALLS (flagged; each is waivable by the lead, see .handoff/P16.acceptance.md)
  J1 homonymy rate = share of the DISTINCT usable names (``cluster_quality.comparable_name``) that occur in two or more
     different clusters.  Tests pin the exact value only where this and the record-weighted variant agree (0.0 and 1.0).
  J2 Hill numbers are over cluster SIZE IN RECORDS (p_i = size_i / total), singletons included: D0 = number of
     clusters, D1 = exp(Shannon), D2 = 1 / sum(p^2).
  J3 name-variation rate (run level) = mean of the per-cluster rate over clusters of >= 2 records (pinned only where
     every variant agrees: one multi-record cluster -> its own rate; none -> 0.0).
  J4 the flag threshold is the implementer's design (any rule computed from the run's own names).  The tests accept
     only what a planted run makes unambiguous: exactly the planted incoherent clusters, none of the coherent ones,
     and the threshold exposed under K_THR.  A run with fewer than two multi-record clusters has no reference to
     compare against; whether it flags is NOT asserted.
"""

from __future__ import annotations

import ast
import importlib
import itertools
import json
import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.mcp_server.tools import cluster_quality as cq
from tests.part1 import fixtures_matching as fx
from tests.part1.test_p20_acceptance import world

ROOT = Path(__file__).resolve().parents[2]
BASE_REV = "fb167b9"  # HEAD when these checks were derived (before any P16 code)
LOAD_DATA_REL = "src/orchestrator/nodes/load_data.py"

# ── adaptation points ────────────────────────────────────────────────────────────────────────
MODULE_CANDIDATES = (
    [os.environ["P16_QUALITY_MODULE"]]
    if os.environ.get("P16_QUALITY_MODULE")
    else [
        f"src.mcp_server.tools.{m}"
        for m in ("label_free_quality", "quality_report", "quality_signals", "cluster_quality")
    ]
)
FN_SIGNALS = os.environ.get("P16_FN_SIGNALS", "quality_signals")
FN_HILL = os.environ.get("P16_FN_HILL", "hill_numbers")
FN_BCUBED = os.environ.get("P16_FN_BCUBED", "bcubed")
FN_HOMONYMY = os.environ.get("P16_FN_HOMONYMY", "homonymy_rate")
HISTORY_KW = os.environ.get("P16_HISTORY_KW", "history_path")
ENTITY_KW = os.environ.get("P16_ENTITY_KW", "entity")
K_HOM = "homonymy_rate"
K_VAR = "name_variation_rate"
K_HILL = "hill_numbers"
K_B3 = "bcubed"  # float, or None when not available
K_B3_NOTE = "bcubed_note"  # a string containing "not available" when K_B3 is None
K_FLAGGED = "flagged_clusters"  # list of cluster_id
K_COUNT = "flagged_count"  # int, == len(flagged)
K_THR = "name_variation_threshold"  # the run-derived threshold actually used
QUALITY_KEY = os.environ.get("P16_QUALITY_KEY", "quality")
STATE_PATH_KEY = os.environ.get("P16_STATE_PATH_KEY", "quality_history_path")
ALLOWED_NUMS = {0, 1, 2}  # indices, q in {0,1,2}, the unit interval; anything else must be computed
CORPUS = {
    "france", "us", "sas", "berhad", "global", "group", "holdings", "companyname", "main_entity_id",
    "company_relation", "staff", "org_title", "testdiffbot", "companyexperience", "ltd", "inc", "gmbh",
    "corp", "llc", "plc", "limited", "sa", "co", "pagila", "customer", "film", "actor",
}  # fmt: skip


# ══ harness ═══════════════════════════════════════════════════════════════════════════════════
def _mod():
    errs = []
    for dotted in MODULE_CANDIDATES:
        try:
            m = importlib.import_module(dotted)
        except ImportError as e:
            errs.append(f"{dotted}: not importable ({e})")
            continue
        if hasattr(m, FN_SIGNALS):
            assert m.__name__ != "src.mcp_server.tools.resolve_entities"
            return m
        errs.append(f"{dotted}: imports, but has no {FN_SIGNALS}()")
    pytest.fail("no P16 label-free quality module (plan section 9 P16): " + "; ".join(errs))


def signals(clusters, names, history=None, entity="company"):
    kw = {}
    if history is not None:
        kw[HISTORY_KW] = history
    if entity is not None:
        kw[ENTITY_KW] = entity
    return getattr(_mod(), FN_SIGNALS)(clusters, names, **kw)


def hill(sizes):
    return getattr(_mod(), FN_HILL)(list(sizes))


def hill_q(d, q):
    for key in (q, str(q), f"q{q}", f"D{q}", f"d{q}", f"hill_{q}", f"hill{q}"):
        if key in d:
            return float(d[key])
    raise AssertionError(f"Hill number q={q} not found in {d!r}")


def bcubed(prev, cur):
    return getattr(_mod(), FN_BCUBED)([list(c) for c in prev], [list(c) for c in cur])


def homonymy(clusters, names):
    return getattr(_mod(), FN_HOMONYMY)(clusters, names)


def mk(groups, prefix="c"):
    return [{"cluster_id": f"{prefix}{i}", "record_ids": sorted(g)} for i, g in enumerate(groups)]


def names_of(records, field="name"):
    return {fx.rid(r): r.get(field) for r in records}


def components(records, pairs):
    parent = {fx.rid(r): fx.rid(r) for r in records}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in pairs:
        parent[find(a)] = find(b)
    out: dict[str, list[str]] = {}
    for r in parent:
        out.setdefault(find(r), []).append(r)
    return sorted(sorted(g) for g in out.values())


SIZES = [3, 4, 5, 3, 6, 4, 3, 5, 4, 3, 5, 6, 4, 3]


def coherent_world(seed, k=2, sizes=SIZES):
    """Every entity is a clique of variants of one name (case, punctuation, a dropped letter, a legal-form suffix)."""
    return world(seed, list(sizes), ["clique"] * len(sizes), k=k)


def planted_run(seed, k=2, merge=((0, 1),), pair_of_singletons=False):
    """-> (names, clusters, bad_ids): the truth clustering of a coherent world, but each ``merge`` pair of entities is
    fused into ONE cluster (an incoherent clique: two wholly different names, e.g. an identifier collision)."""
    w = coherent_world(seed, k)
    truth = [list(g) for g in w["truth"]]
    used = {i for pair in merge for i in pair}
    groups = [g for i, g in enumerate(truth) if i not in used]
    bad = [sorted(truth[i] + truth[j]) for i, j in merge]
    if (
        pair_of_singletons
    ):  # a lone strong pair of two different names, one record from each of two entities
        a, b = truth[-1], truth[-2]
        groups = [g for g in groups if g not in (a, b)]
        bad.append(sorted([a[0], b[0]]))
        groups += [a[1:], b[1:]]
    clusters = mk(groups + bad)
    bad_ids = {c["cluster_id"] for c in clusters if c["record_ids"] in bad}
    return names_of(w["records"]), clusters, bad_ids, w


# ══ A. the contract exists and is machine-readable (fails today: no P16 code) ═════════════════════
def test_a_module_and_entry_points_exist():
    m = _mod()
    for fn in (FN_SIGNALS, FN_HILL, FN_BCUBED, FN_HOMONYMY):
        assert callable(getattr(m, fn, None)), f"{m.__name__}.{fn} missing"


def test_a_signals_has_every_key_and_is_json_clean():
    names, clusters, bad, _ = planted_run(30)
    out = signals(clusters, names)
    for key in (K_HOM, K_VAR, K_HILL, K_B3, K_B3_NOTE, K_FLAGGED, K_COUNT, K_THR):
        assert key in out, f"missing key {key!r}: {sorted(out)}"
    json.dumps(out, allow_nan=False)  # P10 and the report file read it: no NaN, no non-JSON types
    assert isinstance(out[K_COUNT], int) and not isinstance(out[K_COUNT], bool)
    assert isinstance(out[K_FLAGGED], list) and out[K_COUNT] == len(out[K_FLAGGED]) == len(
        set(out[K_FLAGGED])
    )
    assert set(out[K_FLAGGED]) <= {c["cluster_id"] for c in clusters}
    for key in (K_HOM, K_VAR):
        assert isinstance(out[key], float) and 0.0 <= out[key] <= 1.0, (key, out[key])
    for q in (0, 1, 2):
        assert hill_q(out[K_HILL], q) > 0


def test_a_inputs_are_not_mutated():
    names, clusters, _, _ = planted_run(30)
    import copy

    n0, c0 = copy.deepcopy(names), copy.deepcopy(clusters)
    signals(clusters, names)
    assert names == n0 and clusters == c0


# ══ B. Hill numbers on known distributions ════════════════════════════════════════════════════
def test_b_hill_known_values():
    d = hill([2, 2, 2, 2])
    assert [hill_q(d, q) for q in (0, 1, 2)] == pytest.approx([4, 4, 4])
    d = hill([6, 2])  # p = .75/.25: D0 2, D1 exp(H) 1.75488, D2 1/(.5625+.0625) 1.6
    assert hill_q(d, 0) == pytest.approx(2)
    assert hill_q(d, 1) == pytest.approx(math.exp(-(0.75 * math.log(0.75) + 0.25 * math.log(0.25))))
    assert hill_q(d, 2) == pytest.approx(1.6)
    assert [hill_q(hill([1] * 5), q) for q in (0, 1, 2)] == pytest.approx([5, 5, 5])
    assert [hill_q(hill([100]), q) for q in (0, 1, 2)] == pytest.approx([1, 1, 1])


def test_b_hill_order_free_ordered_in_q_and_empty_is_not_a_crash():
    sizes = [9, 1, 1, 4, 2, 2, 1]
    a, b = hill(sizes), hill(list(reversed(sizes)))
    d = [hill_q(a, q) for q in (0, 1, 2)]
    assert d == pytest.approx([hill_q(b, q) for q in (0, 1, 2)])
    assert d[0] >= d[1] >= d[2] > 0  # Hill numbers fall as q weights the big clusters more
    e = hill([])
    assert all(math.isfinite(hill_q(e, q)) for q in (0, 1, 2))
    z = hill([0, 3])  # an empty cluster is not an abundance
    assert hill_q(z, 0) == pytest.approx(1)


def test_b_giant_component_is_visible_in_the_distribution():
    """60 entities, 120 wrong edges (the P20 giant shape), clustered as connected components with NO pruning."""
    rng = random.Random(
        101
    )  # the shape of test_p20_acceptance.test_d_giant_component_is_taken_apart
    w = world(101, [rng.randrange(3, 7) for _ in range(60)], ["clique"] * 60, k=4)
    ids = [fx.rid(r) for r in w["records"]]
    wrong = [
        (*rng.sample(ids, 2), 0.99) for _ in range(120)
    ]  # 120 wrong edges between random records
    giant = components(w["records"], w["pairs"] + wrong)
    assert max(len(g) for g in giant) > len(w["records"]) // 2, (
        "fixture must contain a giant component"
    )
    names = names_of(w["records"])
    good, bad = signals(mk(w["truth"]), names), signals(mk(giant), names)
    for q in (0, 1, 2):
        assert hill_q(bad[K_HILL], q) < hill_q(good[K_HILL], q), q
    assert hill_q(good[K_HILL], 2) > 4 * hill_q(
        bad[K_HILL], 2
    )  # visible, not marginal (a ratio of the two runs)
    assert bad[K_VAR] > good[K_VAR]
    assert bad[K_VAR] > max(cq.name_variation_rate([names[i] for i in g]) for g in w["truth"])


def test_b_signals_hill_is_over_all_clusters_including_singletons():
    names = {"a": "x y", "b": "x y", "c": "p q", "d": "m n", "e": None}
    clusters = mk([["a", "b"], ["c"], ["d"], ["e"]])
    out = signals(clusters, names)
    assert [hill_q(out[K_HILL], q) for q in (0, 1, 2)] == pytest.approx(
        [hill_q(hill([2, 1, 1, 1]), q) for q in (0, 1, 2)]
    )


# ══ C. B-cubed ═══════════════════════════════════════════════════════════════════════════════
def test_c_self_is_one_and_perturbed_is_below_one():
    rng = random.Random(7)
    ids = [f"r{i}" for i in range(40)]
    for _ in range(6):
        rng.shuffle(ids)
        cuts = sorted(rng.sample(range(1, 40), 7))
        part = [ids[a:b] for a, b in zip([0, *cuts], [*cuts, 40], strict=True)]
        assert bcubed(part, part) == pytest.approx(1.0)
        moved = [list(c) for c in part]
        moved[1].append(moved[0].pop())  # one record changes cluster
        assert 0.0 <= bcubed(part, moved) < 1.0
    assert bcubed([ids], [ids]) == pytest.approx(1.0)
    assert bcubed([[i] for i in ids], [[i] for i in ids]) == pytest.approx(1.0)


def test_c_known_values_symmetric_and_order_free():
    prev, merged = [["a", "b"], ["c", "d"]], [["a", "b", "c", "d"]]
    assert bcubed(prev, merged) == pytest.approx(2 / 3)  # P 1/2, R 1 -> F1 2/3
    assert bcubed(merged, prev) == pytest.approx(2 / 3)  # P 1, R 1/2 -> F1 2/3
    assert bcubed([["a", "b", "c", "d"]], [["a"], ["b"], ["c"], ["d"]]) == pytest.approx(
        0.4
    )  # P 1, R 1/4
    assert bcubed(prev, [["d", "c"], ["b", "a"]]) == pytest.approx(
        1.0
    )  # order of clusters and members is not identity
    ids = [f"r{i}" for i in range(12)]
    p = [ids[:4], ids[4:8], ids[8:]]
    one, three = [list(c) for c in p], [list(c) for c in p]
    one[1].append(one[0].pop())
    for _ in range(3):
        three[1].append(three[0].pop())
    assert bcubed(p, three) < bcubed(p, one) < 1.0  # more records moved, lower score


def test_c_only_records_in_both_runs_are_compared():
    prev = [["a", "b"], ["c", "d"], ["gone"]]
    cur = [["a", "b"], ["c", "d"], ["new1", "new2"]]  # records appear and vanish between runs
    assert bcubed(prev, cur) == pytest.approx(1.0)
    assert (
        bcubed([["a", "b"]], [["x", "y"]]) is None
    )  # nothing in common: not 0 and not 1, unavailable
    assert bcubed([], [["a"]]) is None and bcubed([["a"]], []) is None


# ══ D. persistence across two calls; run one is "not available" ═══════════════════════════════
def _run(names, groups, path, entity="company", prefix="c"):
    return signals(mk(groups, prefix), names, history=path, entity=entity)


def test_d_first_run_says_not_available_and_second_run_gives_a_number(tmp_path):
    names, clusters, _, w = planted_run(31)
    path = tmp_path / "hist" / "prev.json"
    path.parent.mkdir()
    first = signals(clusters, names, history=path)
    assert first[K_B3] is None, f"run one must not print a number, got {first[K_B3]!r}"
    assert "not available" in first[K_B3_NOTE].lower()
    assert path.exists(), "the current clustering must be persisted for the next run"
    second = signals(clusters, names, history=path)
    assert isinstance(second[K_B3], float) and second[K_B3] == pytest.approx(1.0)
    assert second[K_B3_NOTE] is None or "not available" not in second[K_B3_NOTE].lower()


def test_d_previous_means_the_last_run_not_the_first(tmp_path):
    w = coherent_world(32)
    names = names_of(w["records"])
    a = [list(g) for g in w["truth"]]
    b = [a[0] + a[1], *a[2:]]  # run 2 merges two entities
    path = tmp_path / "p.json"
    assert _run(names, a, path)[K_B3] is None
    assert _run(names, b, path)[K_B3] < 1.0  # B vs A
    assert _run(names, b, path)[K_B3] == pytest.approx(1.0)  # B vs B, not B vs A
    assert _run(names, a, path)[K_B3] < 1.0  # A vs B


def test_d_membership_counts_not_cluster_ids(tmp_path):
    """Cluster ids are minted afresh each run: identical membership under new ids is 1.0; the same ids over
    different membership is below 1.0 (a comparison by cluster_id fails both)."""
    w = coherent_world(33)
    names = names_of(w["records"])
    a = [list(g) for g in w["truth"]]
    path = tmp_path / "p.json"
    _run(names, a, path, prefix="run1-")
    assert _run(names, list(reversed(a)), path, prefix="run2-")[K_B3] == pytest.approx(1.0)
    b = [a[0] + a[1], *a[2:]]
    _run(names, a, path, prefix="x")
    assert _run(names, b, path, prefix="x")[K_B3] < 1.0


def test_d_no_path_means_no_number_and_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    names, clusters, _, _ = planted_run(31)
    for _ in range(2):
        out = signals(clusters, names, history=None)
        assert out[K_B3] is None and "not available" in out[K_B3_NOTE].lower()
    assert list(tmp_path.iterdir()) == [], "nothing may be written when no history path is given"


def test_d_entities_do_not_share_a_history(tmp_path):
    w = coherent_world(34)
    names = names_of(w["records"])
    groups = [list(g) for g in w["truth"]]
    path = tmp_path / "p.json"
    assert _run(names, groups, path, entity="company")[K_B3] is None
    assert (
        _run(names, groups, path, entity="person")[K_B3] is None
    )  # the company run is not person's previous
    assert _run(names, groups, path, entity="company")[K_B3] == pytest.approx(1.0)
    assert _run(names, groups, path, entity="person")[K_B3] == pytest.approx(1.0)


def test_d_a_corrupt_or_foreign_history_file_degrades_to_not_available(tmp_path):
    w = coherent_world(35)
    names = names_of(w["records"])
    groups = [list(g) for g in w["truth"]]
    for junk in (
        "",
        "{not json",
        "[1, 2, 3]",
        '{"company": 5}',
        '{"company": [[1], 2]}',
        "null",
        "\x00\x01",
    ):
        path = tmp_path / "p.json"
        path.write_text(junk)
        out = _run(names, groups, path)
        assert out[K_B3] is None, junk
        assert _run(names, groups, path)[K_B3] == pytest.approx(1.0), (
            "the bad file must be replaced by a good one"
        )


def test_d_history_survives_a_fresh_process(tmp_path):
    """No module state: run 1 here, run 2 in a new interpreter (different PYTHONHASHSEED)."""
    w = coherent_world(36)
    names = names_of(w["records"])
    groups = [list(g) for g in w["truth"]]
    path = tmp_path / "p.json"
    assert _run(names, groups, path)[K_B3] is None
    code = (
        "import json,sys;from tests.part1.test_p16_acceptance import _run,K_B3,coherent_world,names_of;"
        "w=coherent_world(36);n=names_of(w['records']);g=[list(x) for x in w['truth']];"
        "print(json.dumps(_run(n,g,sys.argv[1])[K_B3]))"
    )
    env = {
        **os.environ,
        "PYTHONHASHSEED": "12345",
        "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
    }
    r = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == pytest.approx(1.0)


# ══ E. homonymy rate, defined without labels (J1) ═════════════════════════════════════════════
def test_e_homonymy_pinned_where_all_definitions_agree():
    nm = {"a": "Acme", "b": "Orchid", "c": "ACME.", "d": "orchid", "e": "Birch", "f": "Quill"}
    assert homonymy(mk([["a", "b"], ["c", "d"]]), nm) == pytest.approx(
        1.0
    )  # both names are in both clusters
    assert homonymy(mk([["a", "b"], ["e", "f"]]), nm) == pytest.approx(
        0.0
    )  # every name lives in one cluster
    assert homonymy(mk([["a", "c"], ["b", "d"]]), nm) == pytest.approx(
        0.0
    )  # same name twice in ONE cluster is fine
    assert homonymy([], nm) == 0.0 and homonymy(mk([["a"]]), nm) == 0.0


def test_e_homonymy_rises_with_the_number_of_split_names_and_ignores_missing_names():
    nm = {f"{k}{i}": f"name{k}" for k in range(6) for i in (1, 2)}
    rates = []
    for split in range(7):  # the first `split` names are each spread over two clusters
        c1 = [f"{k}1" for k in range(6)] + [f"{k}2" for k in range(split, 6)]
        c2 = [f"{k}2" for k in range(split)]
        rates.append(homonymy(mk([c1, c2] if c2 else [c1]), nm))
    assert rates[0] == 0.0 and all(x < y for x, y in itertools.pairwise(rates)), rates
    gaps = {"a": None, "b": "", "c": None, "d": "  "}  # nothing known: nothing to call a homonym
    assert homonymy(mk([["a", "b"], ["c", "d"]]), gaps) == 0.0
    assert homonymy(mk([["zz"], ["yy"]]), {}) == 0.0  # ids with no name at all


def test_e_under_merged_run_scores_above_the_truth_and_label_free():
    """An entity split into two clusters shows up as one name living in two clusters; no ground truth is passed."""
    w = coherent_world(37)
    nm = names_of(w["records"])
    truth = [list(g) for g in w["truth"]]
    assert (
        homonymy(mk(truth), nm) == 0.0
    )  # distinct entities never share a comparable name in this fixture
    for rid in truth[0]:
        nm[rid] = "orchid works"  # entity 0: one canonical name on every record ...
    split = [truth[0][:2], truth[0][2:], *truth[1:]]  # ... split across two clusters
    assert homonymy(mk(split), nm) > homonymy(mk([list(g) for g in w["truth"]]), nm)
    assert signals(mk(split), nm)[K_HOM] == pytest.approx(homonymy(mk(split), nm))


def test_e_label_free_by_construction():
    """No function of the quality module takes a truth/label/gold argument (the whole point of P16)."""
    tree = ast.parse(Path(_mod().__file__).read_text())
    params = {
        a.arg.lower()
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        for a in [*n.args.args, *n.args.kwonlyargs]
    }
    assert not params & {"truth", "labels", "label", "gold", "ground_truth", "reference", "gt"}, (
        params
    )


# ══ F. name-variation rate: reuse, aggregate, edge cases ═════════════════════════════════════
def test_f_single_multi_cluster_equals_the_existing_function():
    names = {"a": "bako dumi", "b": "BAKO DUMI Ltd", "c": "ruzo kela", "s": "tife nogu"}
    out = signals(mk([["a", "b", "c"], ["s"]]), names)
    assert out[K_VAR] == pytest.approx(cq.name_variation_rate([names[i] for i in "abc"]), abs=1e-12)
    assert out[K_VAR] > 0.0


def test_f_no_multi_record_cluster_means_zero_not_an_error():
    names = {"a": "x y", "b": "p q", "c": None}
    for clusters in ([], mk([["a"], ["b"], ["c"]])):
        out = signals(clusters, names)
        assert out[K_VAR] == 0.0 and out[K_COUNT] == 0 and out[K_FLAGGED] == []
        json.dumps(out, allow_nan=False)


def test_f_reuses_the_existing_function_and_defines_no_second_one():
    defs = []
    for p in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(p.read_text())
        defs += [
            (str(p.relative_to(ROOT)), n.name)
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "variation" in n.name.lower()
        ]
    assert defs == [("src/mcp_server/tools/cluster_quality.py", "name_variation_rate")], defs
    m = _mod()
    src = Path(m.__file__).read_text()
    assert "name_variation_rate" in src
    if (
        m.__name__ != cq.__name__
    ):  # a separate module: it must import the measure, not re-implement string similarity
        assert not re.search(
            r"^\s*(import|from)\s+(difflib|rapidfuzz|jellyfish|Levenshtein|thefuzz)", src, re.M
        )


# ══ G. the flag (user decision c): derived from the run's own data, a count + which clusters ═════
@pytest.mark.parametrize("seed,k", [(30, 2), (31, 2), (32, 3), (33, 3), (34, 4)])
def test_g_planted_incoherent_cluster_is_flagged_and_nothing_coherent_is(seed, k):
    names, clusters, bad, _ = planted_run(seed, k)
    out = signals(clusters, names)
    assert set(out[K_FLAGGED]) == bad and out[K_COUNT] == len(bad) == 1, (
        out[K_FLAGGED],
        bad,
        out[K_THR],
    )


@pytest.mark.parametrize("seed,k", [(30, 2), (31, 3)])
def test_g_two_incoherent_clusters_and_a_lone_incoherent_pair(seed, k):
    names, clusters, bad, _ = planted_run(seed, k, merge=((0, 1), (2, 3)), pair_of_singletons=True)
    assert len(bad) == 3
    out = signals(clusters, names)
    assert set(out[K_FLAGGED]) == bad and out[K_COUNT] == 3, (out[K_FLAGGED], bad)


@pytest.mark.parametrize("seed,k", [(30, 2), (31, 2), (32, 3), (33, 3), (34, 4), (35, 2)])
def test_g_a_run_of_coherent_clusters_flags_nothing(seed, k):
    """The false-positive negative: variants (case, punctuation, a typo, a legal-form suffix) are not incoherence."""
    w = coherent_world(seed, k)
    out = signals(mk(w["truth"]), names_of(w["records"]))
    assert out[K_COUNT] == 0 and out[K_FLAGGED] == [], (out[K_FLAGGED], out[K_THR])


def test_g_giant_cluster_among_coherent_ones_is_flagged():
    w = coherent_world(38)
    truth = [list(g) for g in w["truth"]]
    giant = [r for g in truth[:6] for r in g]  # six entities fused: the shape of a giant component
    clusters = mk([giant, *truth[6:]])
    out = signals(clusters, names_of(w["records"]))
    assert out[K_FLAGGED] == [clusters[0]["cluster_id"]] and out[K_COUNT] == 1


def test_g_singletons_and_missing_names_are_never_evidence_of_incoherence():
    w = coherent_world(30)
    names = names_of(w["records"])
    truth = [list(g) for g in w["truth"]]
    odd = truth[0][:1] + ["ghost1", "ghost2"]  # one real name plus ids with no name / None / ''
    names["ghost1"], names["ghost2"] = None, ""
    lone = [[r] for g in truth[1:3] for r in g]  # wholly different names, but each alone
    clusters = mk([odd, *lone, *truth[3:]])
    out = signals(clusters, names)
    assert out[K_COUNT] == 0 and out[K_FLAGGED] == [], out[K_FLAGGED]


def test_g_flag_is_independent_of_input_order():
    names, clusters, bad, _ = planted_run(30)
    base = signals(clusters, names)
    rng = random.Random(5)
    for _ in range(4):
        shuffled = [
            {**c, "record_ids": rng.sample(c["record_ids"], len(c["record_ids"]))} for c in clusters
        ]
        rng.shuffle(shuffled)
        out = signals(shuffled, dict(rng.sample(sorted(names.items()), len(names))))
        assert set(out[K_FLAGGED]) == bad and out[K_COUNT] == base[K_COUNT]
        assert out[K_THR] == pytest.approx(base[K_THR])


def test_g_threshold_is_exposed_and_comes_from_the_data():
    """A constant cannot follow the data: two corpora that differ in name length and alphabet must not share it."""
    thr = {}
    for label, seed, k in (("short", 30, 2), ("long", 30, 4)):
        names, clusters, bad, _ = planted_run(seed, k)
        out = signals(clusters, names)
        assert set(out[K_FLAGGED]) == bad
        assert isinstance(out[K_THR], float) and 0.0 <= out[K_THR] <= 1.0, out[K_THR]
        thr[label] = out[K_THR]
    assert thr["short"] != thr["long"], f"identical threshold on different corpora: {thr}"


def test_g_coherent_non_latin_names_are_not_incoherent():
    w = coherent_world(30)
    names = names_of(w["records"])
    kana = ["東京電力", "大阪ガス", "名古屋鉄道", "北海道電力"]
    clusters = mk(w["truth"])
    extra = {f"j{i}{k}": kana[i] for i in range(4) for k in range(2)}
    names.update(extra)
    clusters += mk([[f"j{i}0", f"j{i}1"] for i in range(4)], prefix="jp")
    out = signals(clusters, names)
    assert out[K_COUNT] == 0 and out[K_FLAGGED] == []


# ══ H. robustness: empty / singletons / None names ═══════════════════════════════════════════
@pytest.mark.parametrize(
    "clusters,names",
    [
        ([], {}),
        ([], {"a": "x"}),
        (mk([["a"]]), {}),
        (mk([["a"], ["b"]]), {"a": None, "b": None}),
        (mk([["a", "b"]]), {"a": None, "b": None}),
        (mk([["a", "b"]]), {"a": "", "b": "   "}),
        (mk([["a", "b", "c"]]), {"a": "x y", "b": None, "c": ""}),
        (mk([["a", "b"], ["c", "d"], ["e", "f"]]), {k: "same name" for k in "abcdef"}),
        (mk([["a", "b"], ["c"]]), {"a": "東京電力", "b": "Société Générale", "c": "x"}),
        (mk([[]]), {}),
    ],
)
def test_h_no_crash_json_clean_and_flagless_when_there_is_nothing_to_compare(
    clusters, names, tmp_path
):
    out = signals(clusters, names)
    json.dumps(out, allow_nan=False)
    assert out[K_HOM] == 0.0 or clusters  # nothing clustered, nothing homonymous
    assert out[K_VAR] == 0.0 or any(len(c["record_ids"]) > 1 for c in clusters)
    path = tmp_path / "h.json"
    for _ in range(
        2
    ):  # and persistence of a degenerate clustering does not crash the next run either
        json.dumps(signals(clusters, names, history=path), allow_nan=False)


def test_h_every_record_alone_every_name_identical_is_all_homonymy_no_variation():
    names = {f"r{i}": "same name" for i in range(6)}
    out = signals(mk([[r] for r in names]), names)
    assert out[K_HOM] == pytest.approx(1.0)  # one name, six clusters: the under-merge signature
    assert out[K_VAR] == 0.0 and out[K_COUNT] == 0
    assert hill_q(out[K_HILL], 0) == pytest.approx(6) and hill_q(out[K_HILL], 2) == pytest.approx(6)


# ══ I. the run report: build_reconciliation_report carries the signals (flag 5, ownership row P16) ═══
def _state(records, clusters, path=None, entity="company"):
    st = {
        "run_id": "p16",
        "extracted_data": {entity: records},
        "entity_clusters": {entity: clusters},
    }
    if path is not None:
        st[STATE_PATH_KEY] = str(path)
    return st


def _report(state):
    from src.orchestrator.nodes.load_data import build_reconciliation_report

    return build_reconciliation_report(state, {}, [])


def test_i_report_carries_the_signals_flag_count_and_run_one_unavailable(tmp_path):
    names, clusters, bad, w = planted_run(30)
    state = _state(w["records"], clusters, tmp_path / "q.json")
    q = _report(state)["entities"]["company"][QUALITY_KEY]
    assert q[K_COUNT] == 1 and set(q[K_FLAGGED]) == bad
    assert q[K_B3] is None and "not available" in q[K_B3_NOTE].lower()
    json.dumps(_report(state), allow_nan=False)  # the whole report stays serialisable (run 2 below)
    q2 = _report(state)["entities"]["company"][QUALITY_KEY]
    assert q2[K_B3] == pytest.approx(1.0)
    for key in (K_HOM, K_VAR, K_HILL, K_THR):
        assert key in q2


def test_i_report_reads_names_the_way_the_engine_does_and_keeps_existing_keys(tmp_path):
    names, clusters, bad, w = planted_run(31)
    recs = []
    for i, r in enumerate(w["records"]):  # two origins, two name columns
        r = {k: v for k, v in r.items() if k != "name"}
        r["company_name" if i % 2 == 0 else "org_title"] = names[fx.rid(w["records"][i])]
        recs.append(r)
    rep = _report(_state(recs, clusters, tmp_path / "q.json"))
    ent = rep["entities"]["company"]
    assert ent[QUALITY_KEY][K_COUNT] == 1 and set(ent[QUALITY_KEY][K_FLAGGED]) == bad
    for key in ("clusters_formed", "extracted_total", "auto_merged_clusters"):
        assert key in ent
    assert "data_quality" in rep and "grade" in rep["data_quality"]


def test_i_report_without_a_history_path_or_clusters_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    names, clusters, _, w = planted_run(30)
    q = _report(_state(w["records"], clusters))["entities"]["company"][QUALITY_KEY]
    assert q[K_B3] is None and "not available" in q[K_B3_NOTE].lower()
    st = _state(
        w["records"], [], tmp_path / "q.json"
    )  # an entity that was extracted but never clustered
    st["extracted_data"]["person"] = [
        {"_origin_id": "s", "_origin_table": "t", "_origin_id_row": "1"}
    ]
    rep = _report(st)
    assert rep["entities"]["person"][QUALITY_KEY][K_COUNT] == 0


def test_i_each_entity_has_its_own_history(tmp_path):
    names, clusters, bad, w = planted_run(30)
    state = _state(w["records"], clusters, tmp_path / "q.json")
    state["extracted_data"]["person"] = w["records"]
    state["entity_clusters"]["person"] = clusters
    ents = _report(state)["entities"]
    assert all(ents[e][QUALITY_KEY][K_B3] is None for e in ("company", "person"))
    ents = _report(state)["entities"]
    assert all(ents[e][QUALITY_KEY][K_B3] == pytest.approx(1.0) for e in ("company", "person"))


# ══ J. plan 2.4 / 2.5: no fitted constants, no corpus literals, mechanical guards ═════════════════
def _git_show(rev, rel):
    return subprocess.run(
        ["git", "show", f"{rev}:{rel}"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def _fns(tree):
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _doc_ids(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = n.body[0] if n.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _round_digit_ids(tree):
    """ids of the digits argument of ``round(x, n)``: presentation, not a corpus-derived constant."""
    return {
        id(n.args[1])
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "round" and len(n.args) > 1
    }


def _consts(node, skip):
    return [c for c in ast.walk(node) if isinstance(c, ast.Constant) and id(c) not in skip]


def _new_code():
    """[(label, ast node, doc/round skip ids, constants already present at BASE_REV in that function)] for the quality
    module (every function and module-level assignment) and for the functions of load_data.py new/changed since BASE_REV."""
    out = []
    m = _mod()
    mtree = ast.parse(Path(m.__file__).read_text())
    base_q = (
        ast.parse(_git_show(BASE_REV, str(Path(m.__file__).relative_to(ROOT))))
        if m.__name__ == cq.__name__
        else None
    )
    skip = _doc_ids(mtree) | _round_digit_ids(mtree)
    bfn = _fns(base_q) if base_q else {}
    for name, fn in _fns(mtree).items():
        known = (
            {c.value for c in _consts(bfn[name], set())}
            if name in bfn and ast.dump(bfn[name]) == ast.dump(fn)
            else set()
        )
        out.append((f"{m.__name__}.{name}", fn, skip, known))
    for n in mtree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            out.append(
                (
                    f"{m.__name__}:<module>",
                    n,
                    skip,
                    {c.value for c in _consts(base_q, set())} if base_q else set(),
                )
            )
    now = ast.parse((ROOT / LOAD_DATA_REL).read_text())
    base = ast.parse(_git_show(BASE_REV, LOAD_DATA_REL))
    bf, skip2 = _fns(base), _doc_ids(now) | _round_digit_ids(now)
    base_strs = {c.value for c in _consts(base, set()) if isinstance(c.value, str)}
    for name, fn in _fns(now).items():
        if name in bf and ast.dump(bf[name]) == ast.dump(fn):
            continue  # unchanged since BASE_REV: not P16's
        known = {c.value for c in _consts(bf[name], set())} if name in bf else set()
        out.append((f"load_data.{name}", fn, skip2, known | base_strs))
    return out


def test_j_no_corpus_literal_in_new_or_changed_code():
    hits = []
    for label, node, skip, known in _new_code():
        for c in _consts(node, skip):
            if isinstance(c.value, str) and c.value not in known:
                words = set(re.findall(r"[A-Za-z_]+", c.value.lower()))
                if words & CORPUS or c.value.lower() in CORPUS:
                    hits.append((label, c.value[:40]))
    assert not hits, f"corpus literals (plan 2.4): {hits}"


def test_j_no_hand_set_threshold_or_unexplained_constant():
    """Plan 2.4, 'would a count over the corpus produce this?'  The only numbers allowed in new code are the indices,
    the Hill orders q in {0,1,2} and the unit interval; a flag threshold, a confidence cut, a multiplier (3 sigma, 1.5 IQR)
    or an epsilon is a literal drawn from a corpus and must be computed from the run's names instead."""
    bad = []
    for label, node, skip, known in _new_code():
        for c in _consts(node, skip):
            v = c.value
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v in known:
                continue
            if v not in ALLOWED_NUMS:
                bad.append((label, v))
    assert not bad, f"numeric literals not derivable from data (plan 2.4): {bad}"


def test_j_no_default_valued_threshold_parameter():
    """A threshold smuggled in as ``def f(..., threshold=<number>)`` is caught by the literal scan above only when it
    is not 0/1; this closes the gap: a parameter whose name says threshold/cut/cutoff/limit never defaults to a number."""
    tree = ast.parse(Path(_mod().__file__).read_text())
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        a = fn.args
        pos = a.posonlyargs + a.args
        pairs = list(zip(pos[len(pos) - len(a.defaults) :], a.defaults, strict=True)) + [
            (k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None
        ]
        for arg, d in pairs:
            if re.search(r"thresh|cut|limit|bar|tol|eps", arg.arg.lower()):
                assert not (isinstance(d, ast.Constant) and isinstance(d.value, (int, float))), (
                    fn.name,
                    arg.arg,
                )


def test_j_arch_lint_clean():
    r = subprocess.run(
        [sys.executable, "scripts/lint_arch.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
