"""Pair-level scorer for matching fixtures with PLANTED ground truth.

Counts, not rates (report 0.4): TP / FP / FN, plus the exact named pairs for FP
and FN, split cross-source vs within-source. A pair is cross-source when its two
record ids carry different sources (``source_of[id]``).

Run ``python -m tests.part1.scorer`` to execute the both-ways self-proof.
"""

from __future__ import annotations

from itertools import combinations

Pair = tuple[str, str]


def pairs_of(clusters: list[list[str]]) -> set[Pair]:
    """Every unordered pair inside every cluster, as sorted tuples."""
    return {tuple(sorted(p)) for c in clusters for p in combinations(c, 2)}  # type: ignore[misc]


def score_pairs(predicted: set[Pair], truth: set[Pair], source_of: dict[str, str]) -> dict:
    def stratum(keep) -> dict:
        tp = {p for p in predicted & truth if keep(p)}
        fp = sorted(p for p in predicted - truth if keep(p))
        fn = sorted(p for p in truth - predicted if keep(p))
        return {"tp": len(tp), "fp": len(fp), "fn": len(fn), "fp_pairs": fp, "fn_pairs": fn}

    def cross(p: Pair) -> bool:
        return source_of[p[0]] != source_of[p[1]]

    return {
        "all": stratum(lambda p: True),
        "cross_source": stratum(cross),
        "within_source": stratum(lambda p: not cross(p)),
    }


def score_clusters(
    predicted: list[list[str]], truth: list[list[str]], source_of: dict[str, str]
) -> dict:
    return score_pairs(pairs_of(predicted), pairs_of(truth), source_of)


# ── self-proof: identity -> 0/0; one injected FP -> named; one injected FN -> named ──
_SRC = {"a1": "s1", "a2": "s2", "b1": "s1", "b2": "s1", "c1": "s2", "d1": "s1"}
_TRUTH = [["a1", "a2"], ["b1", "b2"], ["c1"], ["d1"]]  # a: cross-source, b: within-source


def test_identity() -> None:
    r = score_clusters(_TRUTH, _TRUTH, _SRC)
    assert (r["all"]["tp"], r["all"]["fp"], r["all"]["fn"]) == (2, 0, 0)
    assert r["all"]["fp_pairs"] == [] and r["all"]["fn_pairs"] == []


def test_injected_fp_is_named() -> None:
    pred = [["a1", "a2"], ["b1", "b2"], ["c1", "d1"]]  # c1+d1 wrongly merged (cross-source)
    r = score_clusters(pred, _TRUTH, _SRC)
    assert (r["all"]["fp"], r["all"]["fn"]) == (1, 0)
    assert r["all"]["fp_pairs"] == [("c1", "d1")]
    assert r["cross_source"]["fp_pairs"] == [("c1", "d1")] and r["within_source"]["fp"] == 0


def test_injected_fn_is_named() -> None:
    pred = [["a1", "a2"], ["b1"], ["b2"], ["c1"], ["d1"]]  # b1/b2 wrongly split (within-source)
    r = score_clusters(pred, _TRUTH, _SRC)
    assert (r["all"]["fp"], r["all"]["fn"]) == (0, 1)
    assert r["all"]["fn_pairs"] == [("b1", "b2")]
    assert r["within_source"]["fn_pairs"] == [("b1", "b2")] and r["cross_source"]["fn"] == 0


if __name__ == "__main__":
    import json

    for t in (test_identity, test_injected_fp_is_named, test_injected_fn_is_named):
        t()
        print("PASS", t.__name__)
    pred = [["a1", "a2"], ["b1", "b2"], ["c1", "d1"]]
    print("sample FP result:", json.dumps(score_clusters(pred, _TRUTH, _SRC)["all"]))
