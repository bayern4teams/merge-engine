"""Pre-LLM pair scores for P13/P14/P15/P6, scored against the dataset truth.

Usage: python -m scripts.verify_local.score_probe <scenario> <before|after> [--tag r1]

Runs the version's own ``_resolve_core`` (blocking + model fit + scoring; no LLM, no
clustering; code root pinned by PYTHONPATH in a child process) on the exact records the
run fed to resolution (spool ``rows_<entity>``), then counts per stratum
(within/cross-source), from the truth, never from the engine's stats:
- recall@t = true pairs scored >= t / ALL true pairs (unscored true pairs count as misses),
  t in 0.70, 0.90, 0.95;
- FP@t = non-true scored pairs >= t; band = pairs in [0.70, 0.95) (and how many are true).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS = (0.70, 0.90, 0.95)

CHILD = r"""
import json, sys, duckdb
import src.mcp_server.tools.resolve_entities as re_
spool, table, entity, out = sys.argv[1:5]
c = duckdb.connect(spool, read_only=True)
recs = [json.loads(p) if isinstance(p, str) else p
        for (p,) in c.execute(f'select payload from "rows_{table}" order by seq').fetchall()]
core = re_._resolve_core(entity, recs, None, None, None)
scored = [[a, b, float(s)] for a, b, s in core["scored_pairs"]]
scored += [[a, b, float(s)] for a, b, _r1, _r2, s in core.get("borderline") or []]
json.dump({"scored": scored, "borderline": len(core.get("borderline") or []),
           "model_fitted": core.get("model_fitted")}, open(out, "w"))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("version", choices=["before", "after"])
    ap.add_argument("--tag", default="r1")
    a = ap.parse_args()
    run = ROOT / ".tmp" / "verify_local" / "runs" / f"{a.scenario}_{a.version}_{a.tag}"
    meta = json.loads((ROOT / ".tmp" / "benchmarks" / "truth" / f"{a.scenario}.json").read_text())
    (spool,) = list((run / "logs" / "spool").glob("wf_*.duckdb"))
    code = ROOT / ".tmp" / "wt_main" if a.version == "before" else ROOT
    out = run / "score_probe_raw.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(code),
        "MERGE_ENGINE_DISABLE_RESOLVE_CACHE": "1",
        "TMPDIR": str(ROOT / ".tmp"),
    }
    subprocess.run(
        [sys.executable, "-c", CHILD, str(spool), meta["table"], meta["table"], str(out)],
        env=env,
        cwd=run,
        check=True,
    )
    raw = json.loads(out.read_text())

    groups: dict[str, list[str]] = {}
    source = {}
    for r in meta["records"]:
        rid = f"{r['origin']}:public.{r['table']}:{r['id']}"
        groups.setdefault(r["cluster"], []).append(rid)
        source[rid] = r["origin"]
    truth = {tuple(sorted(p)) for g in groups.values() for p in combinations(g, 2)}
    best: dict[tuple[str, str], float] = {}
    for x, y, s in raw["scored"]:
        k = tuple(sorted((x, y)))
        best[k] = max(s, best.get(k, s))

    def stratum(keep) -> dict:
        t = {p for p in truth if keep(p)}
        sc = {p: s for p, s in best.items() if keep(p)}
        row = {"true_pairs": len(t), "true_scored": len(t & set(sc))}
        for th in THRESHOLDS:
            hit = sum(1 for p in t if sc.get(p, -1) >= th)
            fp = sum(1 for p, s in sc.items() if s >= th and p not in t)
            row[f"recall@{th}"] = round(hit / len(t), 4) if t else None
            row[f"tp@{th}"], row[f"fp@{th}"] = hit, fp
        band = [p for p, s in sc.items() if 0.70 <= s < 0.95]
        row["band_pairs"] = len(band)
        row["band_true"] = sum(1 for p in band if p in t)
        return row

    def cross(p: tuple[str, str]) -> bool:
        return source.get(p[0]) != source.get(p[1])

    res = {
        "scenario": a.scenario,
        "version": a.version,
        "model_fitted": raw["model_fitted"],
        "scored_pairs": len(best),
        "engine_borderline": raw["borderline"],
        "all": stratum(lambda p: True),
        "within_source": stratum(lambda p: not cross(p)),
        "cross_source": stratum(cross),
    }
    (run / "score_probe.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
