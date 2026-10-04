"""BEFORE numbers for the matching fixtures, on the current code.

    MERGE_ENGINE_DISABLE_RESOLVE_CACHE=1 .venv311/bin/python -m tests.part1.baseline_matching

Writes tests/part1/before_matching.json (deterministic: no timings). Re-run after
a fix and diff against the committed file; the fixtures and scorer do not change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

os.environ["MERGE_ENGINE_DISABLE_RESOLVE_CACHE"] = "1"

from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools import resolve_entities as re_
from tests.part1 import fixtures_matching as fx
from tests.part1.scorer import pairs_of, score_clusters

resolve_cache.put = lambda *a, **k: None  # type: ignore[assignment]  # keep logs/spool clean

OUT = Path(__file__).with_name("before_matching.json")


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def p2() -> dict:
    f = fx.p2_big_block()
    recs, n = f["records"], len(f["records"])
    logger = logging.getLogger(re_.logger.name)
    seen: list[str] = []

    class H(logging.Handler):
        def emit(self, r: logging.LogRecord) -> None:
            seen.append(f"{r.levelname}")

    h = H(level=logging.DEBUG)
    logger.addHandler(h)
    old = logger.level
    logger.setLevel(logging.DEBUG)
    stats: dict = {}
    raised = None
    try:
        pairs = re_._generate_candidate_pairs(recs, stats=stats)
    except Exception as exc:
        raised, pairs = repr(exc), []
    finally:
        logger.removeHandler(h)
        logger.setLevel(old)
    got = {_pair(p[0], p[1]) for p in pairs}
    planted = pairs_of(f["truth"])
    lost = sorted(planted - got)
    possible = n * (n - 1) // 2
    return {
        "shape": "one block (every name shares token 'widget') larger than _MAX_COMPARISONS",
        "records": n,
        "max_comparisons": getattr(re_, "_MAX_COMPARISONS", None),
        "pairs_in_block": possible,
        "planted_true_pairs": len(planted),
        "stats": stats,
        "candidate_pairs_returned": len(pairs),
        "pairs_truly_discarded": possible - len(got),
        "reported_uncompared_pairs": stats.get("uncompared_pairs"),
        "planted_pairs_found": len(planted & got),
        "planted_pairs_lost_to_cap": len(lost),
        "lost_pairs": [list(p) for p in lost],
        "pair_completeness_count": f"{len(planted & got)}/{len(planted)}",
        "exception_raised": raised,
        "log_levels_emitted": sorted(set(seen)),
        "warning_count": seen.count("WARNING"),
    }


def _p6_run(recs: list[dict], a: dict, b: dict) -> dict:
    ia, ib = fx.rid(a), fx.rid(b)
    key = _pair(ia, ib)
    core = re_._resolve_core("company", recs, None, None, None)
    am, ae = core["auto_match"], core["auto_exclude"]
    conf = None
    for x, y, c in core["scored_pairs"]:
        if _pair(x, y) == key:
            conf = c
    for x, y, _r1, _r2, c in core["borderline"]:
        if _pair(x, y) == key:
            conf = c
    res = asyncio.run(re_.resolve_entities_impl("company", recs))
    merged = any(ia in c["record_ids"] and ib in c["record_ids"] for c in res["clusters"])
    pending = any(_pair(*p["record_ids"]) == key for p in res["pending_merge_candidates"])
    if merged:
        outcome = "auto-merged"
    elif pending:
        outcome = "review band"
    elif conf is None:
        outcome = "excluded (never compared: not co-blocked)"
    else:
        outcome = "excluded (scored at or below auto_exclude)"
    return {
        "candidate_pair": conf is not None,
        "score": conf,
        "auto_match": am,
        "auto_exclude": ae,
        "model_fit": len(core["pair_evidence"]) > 0,
        "outcome": outcome,
    }


def p6() -> dict:
    out: dict = {}
    fillers = fx.p6_fillers()
    for name, case in fx.p6_cases().items():
        a, b = case["records"]
        iso = _p6_run(case["records"], a, b)
        cmb = _p6_run(case["records"] + fillers, a, b)
        exp = case["expected"]
        out[name] = {
            "truth": case["truth"],
            "expected": exp,
            "isolated": iso,
            "with_fillers": cmb,
            "isolated_meets_expected": iso["outcome"].startswith(exp),
            "with_fillers_meets_expected": cmb["outcome"].startswith(exp),
        }
    return out


def p20() -> dict:
    out: dict = {}
    for name, c in fx.p20_cases().items():
        stats: dict = {}
        clusters, _m, _x = asyncio.run(
            re_._build_clusters(c["records"], c["pairs"], "company", stats=stats)
        )
        pred = sorted(cl["record_ids"] for cl in clusters)
        s = score_clusters(pred, c["truth"], fx.source_of(c["records"]))
        out[name] = {
            "records": len(c["records"]),
            "scored_edges": len(c["pairs"]),
            "predicted_clusters": pred,
            "cluster_sizes": sorted((len(x) for x in pred), reverse=True),
            "largest_cluster": max(len(x) for x in pred),
            "largest_component_pre_split": stats["largest_component_size"],
            "score": s,
        }
    return out


def main() -> None:
    result = {"p2_big_block": p2(), "p6_three_way": p6(), "p20_bridge": p20()}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
