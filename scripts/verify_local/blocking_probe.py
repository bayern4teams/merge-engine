"""Blocking evidence for P2/P5, scored against the dataset truth (not the engine's stats).

Usage (from the repo root):
  python -m scripts.verify_local.blocking_probe <scenario> <before|after> [--tag r1]

Re-runs the version's own candidate generation (code root pinned by PYTHONPATH in a
child process) on the exact records that run fed to resolution (its spool
``rows_<entity>``), then counts:
- C = distinct candidate pairs returned for scoring; G = distinct pairs over every
  block the version's own key function builds, before any purge/budget/meta-blocking;
- true discard = |G - C| (report P2: ``uncompared_pairs`` can over-count);
- pair completeness PC(C), PC(G) = share of TRUE pairs inside C / G; reduction ratio.
G is materialised only up to 20M pairs; above that |G| is reported as "not counted".
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
G_LIMIT = 20_000_000

CHILD = r"""
import json, sys, duckdb
from itertools import combinations
import src.mcp_server.tools.resolve_entities as re_
spool, table, out, limit = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
c = duckdb.connect(spool, read_only=True)
recs = [json.loads(p) if isinstance(p, str) else p
        for (p,) in c.execute(f'select payload from "rows_{table}" order by seq').fetchall()]
stats = {}
cand = re_._generate_candidate_pairs(recs, stats=stats)
rid = lambda x: x if isinstance(x, str) else re_._record_id(x)
C = sorted({tuple(sorted((rid(a), rid(b)))) for a, b, *_ in cand})
blocks = {}
if hasattr(re_, "_token_keys"):
    for r in recs:
        for k in re_._token_keys(r, set()) or {"__nokey__"}:
            blocks.setdefault(k, set()).add(re_._record_id(r))
    keyfn = "_token_keys"
else:
    fields = re_._blockable_fields(recs)
    for r in recs:
        for k in re_._blocking_keys_for(r, fields) or {"__nokey__"}:
            blocks.setdefault(k, set()).add(re_._record_id(r))
    keyfn = "_blocking_keys_for"
blocks = [sorted(v) for v in {frozenset(v) for v in blocks.values()} if len(v) > 1]
raw = sum(len(b) * (len(b) - 1) // 2 for b in blocks)
G = None
if raw <= limit:
    G = set()
    for b in blocks:
        G.update(combinations(b, 2))
idx = {}
for i, b in enumerate(blocks):
    for r in b:
        idx.setdefault(r, []).append(i)
json.dump({"records": len(recs), "stats": stats, "keyfn": keyfn, "C": C, "idx": idx,
           "G_count": None if G is None else len(G), "raw_block_pairs": raw,
           "G_minus_C": None if G is None else len(G - set(C))}, open(out, "w"))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("version", choices=["before", "after"])
    ap.add_argument("--tag", default="r1")
    a = ap.parse_args()
    run = ROOT / ".tmp" / "verify_local" / "runs" / f"{a.scenario}_{a.version}_{a.tag}"
    meta = json.loads((ROOT / ".tmp" / "benchmarks" / "truth" / f"{a.scenario}.json").read_text())
    (spool,) = [p for p in (run / "logs" / "spool").glob("wf_*.duckdb")]
    code = ROOT / ".tmp" / "wt_main" if a.version == "before" else ROOT
    out = run / "blocking_probe_raw.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(code),
        "MERGE_ENGINE_DISABLE_RESOLVE_CACHE": "1",
        "TMPDIR": str(ROOT / ".tmp"),
    }
    subprocess.run(
        [sys.executable, "-c", CHILD, str(spool), meta["table"], str(out), str(G_LIMIT)],
        env=env,
        cwd=run,
        check=True,
    )
    raw = json.loads(out.read_text())

    groups: dict[str, list[str]] = {}
    for r in meta["records"]:
        groups.setdefault(r["cluster"], []).append(f"{r['origin']}:public.{r['table']}:{r['id']}")
    truth = {tuple(sorted(p)) for g in groups.values() for p in combinations(g, 2)}
    C = {tuple(p) for p in raw["C"]}
    n = raw["records"]
    tp_c = len(truth & C)
    idx = {k: set(v) for k, v in raw["idx"].items()}
    g_true = sum(1 for x, y in truth if idx.get(x, set()) & idx.get(y, set()))
    res = {
        "scenario": a.scenario,
        "version": a.version,
        "records": n,
        "keyfn": raw["keyfn"],
        "engine_stats": raw["stats"],
        "C_pairs": len(C),
        "G_pairs": raw["G_count"],
        "raw_block_pairs": raw["raw_block_pairs"],
        "true_discard_G_minus_C": raw["G_minus_C"],
        "true_pairs": len(truth),
        "true_pairs_in_C": tp_c,
        "PC_C": tp_c / len(truth) if truth else None,
        "PQ_C": tp_c / len(C) if C else None,
        "RR_C": 1 - len(C) / (n * (n - 1) / 2) if n > 1 else None,
        "true_pairs_in_G": g_true,
    }
    (run / "blocking_probe.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
