"""Benchmark scorer for the Part 1 local verification (LOCAL_VERIFY_REPORT.md).

Written before the first run. Input is two maps from record id to cluster id:
``truth`` (from the dataset's published perfect mapping) and ``pred`` (read by SQL from
the destination's ``merge_engine_entity_map``). ``source_of`` maps a record id to its
origin, so every pair count is split within-source / cross-source (report 0.4).

Pair counts come from the contingency table (sum of C(n, 2) per cell), never from
listing pairs, so a giant cluster cannot exhaust memory. Named FP/FN pairs are a
bounded sample for the report.

A record in ``truth`` but missing from ``pred`` is scored as a singleton and counted
in ``missing_records`` (the run dropped it). A record in ``pred`` but not in ``truth``
is counted in ``unknown_records`` and ignored for the metrics.

Self-proof: ``python -m scripts.verify_local.scorer`` (identity = perfect; one
injected FP and one injected FN are each caught and named, in the right stratum).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Any


def _c2(n: int) -> int:
    return n * (n - 1) // 2


def _prf(tp: int, pred: int, true: int) -> dict[str, float | None]:
    p = tp / pred if pred else None
    r = tp / true if true else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"precision": p, "recall": r, "f1": f}


def _sample_pairs(
    groups: dict[Any, list[str]], other: dict[str, Any], source_of: dict[str, str], limit: int
) -> dict[str, list[tuple[str, str]]]:
    """Pairs inside one ``groups`` cluster whose members differ in ``other``."""
    out: dict[str, list[tuple[str, str]]] = {"cross_source": [], "within_source": []}
    for members in groups.values():
        if len(members) < 2:
            continue
        for a, b in combinations(sorted(members), 2):
            if other[a] == other[b]:
                continue
            key = "cross_source" if source_of[a] != source_of[b] else "within_source"
            if len(out[key]) < limit:
                out[key].append((a, b))
        if all(len(v) >= limit for v in out.values()):
            break
    return out


def score(
    truth: dict[str, str],
    pred: dict[str, str],
    source_of: dict[str, str],
    sample: int = 20,
) -> dict[str, Any]:
    missing = sorted(set(truth) - set(pred))
    unknown = sorted(set(pred) - set(truth))
    ids = sorted(truth)
    # A missing record is its own singleton, under a key no real cluster id can take.
    p_of = {r: pred.get(r, ("\0missing", r)) for r in ids}
    t_of = truth
    n = len(ids)

    cell = Counter((p_of[r], t_of[r]) for r in ids)
    p_size = Counter(p_of[r] for r in ids)
    t_size = Counter(t_of[r] for r in ids)
    cell_s = Counter((p_of[r], t_of[r], source_of[r]) for r in ids)
    p_s = Counter((p_of[r], source_of[r]) for r in ids)
    t_s = Counter((t_of[r], source_of[r]) for r in ids)

    tp_all = sum(_c2(v) for v in cell.values())
    pred_all = sum(_c2(v) for v in p_size.values())
    true_all = sum(_c2(v) for v in t_size.values())
    tp_w = sum(_c2(v) for v in cell_s.values())
    pred_w = sum(_c2(v) for v in p_s.values())
    true_w = sum(_c2(v) for v in t_s.values())

    def stratum(tp: int, pr: int, tr: int) -> dict[str, Any]:
        return {
            "tp": tp,
            "fp": pr - tp,
            "fn": tr - tp,
            "pred_pairs": pr,
            "true_pairs": tr,
            **_prf(tp, pr, tr),
        }

    pairwise = {
        "all": stratum(tp_all, pred_all, true_all),
        "within_source": stratum(tp_w, pred_w, true_w),
        "cross_source": stratum(tp_all - tp_w, pred_all - pred_w, true_all - true_w),
    }

    # B-cubed (per-record, averaged).
    bp = sum(v * v / p_size[c] for (c, _t), v in cell.items()) / n if n else None
    br = sum(v * v / t_size[t] for (_c, t), v in cell.items()) / n if n else None
    bf = 2 * bp * br / (bp + br) if bp and br else None

    # Cluster-level: a predicted cluster is correct when it equals a true cluster exactly.
    p_members: dict[Any, list[str]] = defaultdict(list)
    t_members: dict[Any, list[str]] = defaultdict(list)
    for r in ids:
        p_members[p_of[r]].append(r)
        t_members[t_of[r]].append(r)
    t_sets = {frozenset(m) for m in t_members.values()}
    exact = sum(1 for m in p_members.values() if frozenset(m) in t_sets)
    multi_true = [frozenset(m) for m in t_members.values() if len(m) > 1]
    p_sets = {frozenset(m) for m in p_members.values()}
    exact_multi = sum(1 for m in multi_true if m in p_sets)

    true_per_pred = Counter(c for (c, _t) in cell)
    pred_per_true = Counter(t for (_c, t) in cell)
    over_merged = sorted((c for c, k in true_per_pred.items() if k > 1), key=lambda c: -p_size[c])
    largest = max(p_size.values()) if p_size else 0

    return {
        "records": n,
        "missing_records": len(missing),
        "missing_sample": missing[:sample],
        "unknown_records": len(unknown),
        "pairwise": pairwise,
        "bcubed": {"precision": bp, "recall": br, "f1": bf},
        "cluster": {
            "pred_clusters": len(p_members),
            "true_clusters": len(t_members),
            "exact": exact,
            **_prf(exact, len(p_members), len(t_members)),
            "true_multi_clusters": len(multi_true),
            "true_multi_exact": exact_multi,
        },
        "over_merged_clusters": len(over_merged),
        "over_merged_top": [
            {"cluster": str(c), "size": p_size[c], "true_clusters": true_per_pred[c]}
            for c in over_merged[:sample]
        ],
        "split_true_clusters": sum(1 for k in pred_per_true.values() if k > 1),
        "largest_cluster": largest,
        "largest_true_cluster": max(t_size.values()) if t_size else 0,
        "largest_cluster_share": largest / n if n else None,
        "fp_sample": _sample_pairs(p_members, t_of, source_of, sample),
        "fn_sample": _sample_pairs(t_members, p_of, source_of, sample),
    }


# ── self-proof ───────────────────────────────────────────────────────────────
_SRC = {
    "a1": "s1",
    "a2": "s2",
    "b1": "s1",
    "b2": "s1",
    "c1": "s2",
    "d1": "s1",
    "e1": "s1",
    "e2": "s2",
    "e3": "s2",
}
_TRUTH = {
    "a1": "A",
    "a2": "A",
    "b1": "B",
    "b2": "B",
    "c1": "C",
    "d1": "D",
    "e1": "E",
    "e2": "E",
    "e3": "E",
}
# true pairs: a1-a2 (cross), b1-b2 (within), e1-e2, e1-e3 (cross), e2-e3 (within) = 5


def _check() -> None:
    r = score(_TRUTH, dict(_TRUTH), _SRC)
    a = r["pairwise"]["all"]
    assert (a["tp"], a["fp"], a["fn"]) == (5, 0, 0), a
    assert a["precision"] == a["recall"] == a["f1"] == 1.0
    assert r["bcubed"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert r["cluster"]["exact"] == 5 and r["cluster"]["f1"] == 1.0
    assert r["over_merged_clusters"] == 0 and r["split_true_clusters"] == 0
    assert r["fp_sample"] == {"cross_source": [], "within_source": []}
    assert r["fn_sample"] == {"cross_source": [], "within_source": []}

    fp = dict(_TRUTH, d1="C")  # c1 (s2) + d1 (s1) wrongly merged: one cross-source FP
    r = score(_TRUTH, fp, _SRC)
    a = r["pairwise"]["all"]
    assert (a["tp"], a["fp"], a["fn"]) == (5, 1, 0), a
    assert r["pairwise"]["cross_source"]["fp"] == 1 and r["pairwise"]["within_source"]["fp"] == 0
    assert r["fp_sample"]["cross_source"] == [("c1", "d1")]
    assert r["over_merged_clusters"] == 1 and r["bcubed"]["precision"] < 1.0

    fn = dict(_TRUTH, b2="B2")  # b1/b2 (both s1) wrongly split: one within-source FN
    r = score(_TRUTH, fn, _SRC)
    a = r["pairwise"]["all"]
    assert (a["tp"], a["fp"], a["fn"]) == (4, 0, 1), a
    assert r["pairwise"]["within_source"]["fn"] == 1 and r["pairwise"]["cross_source"]["fn"] == 0
    assert r["fn_sample"]["within_source"] == [("b1", "b2")]
    assert r["split_true_clusters"] == 1 and r["bcubed"]["recall"] < 1.0

    dropped = {k: v for k, v in _TRUTH.items() if k != "e3"}  # run lost e3: 2 FNs, 1 missing
    r = score(_TRUTH, dropped, _SRC)
    assert r["missing_records"] == 1 and r["missing_sample"] == ["e3"]
    assert r["pairwise"]["all"]["fn"] == 2

    giant = dict.fromkeys(_TRUTH, "G")  # everything in one cluster
    r = score(_TRUTH, giant, _SRC)
    assert r["largest_cluster"] == 9 and r["largest_cluster_share"] == 1.0
    assert r["pairwise"]["all"]["fp"] == 36 - 5 and r["over_merged_clusters"] == 1


if __name__ == "__main__":
    _check()
    print(
        "scorer self-proof: identity perfect; injected FP, FN, dropped record, giant cluster "
        "each caught and named"
    )
