"""Precision of merged edges per decision method (P8: LLM-decided pairs), from the truth.

Usage: python -m scripts.verify_local.audit_probe <scenario> <before|after> [--tag r1]
Reads ``merge_engine_merge_audit`` (record_a, record_b, method) by SQL from the run's
destination and counts, per method, edges whose two records share a true cluster.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from scripts.verify_local.load_bench import BM, connect


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("version", choices=["before", "after"])
    ap.add_argument("--tag", default="r1")
    a = ap.parse_args()
    meta = json.loads((BM / "truth" / f"{a.scenario}.json").read_text())
    truth = {f"{r['origin']}:public.{r['table']}:{r['id']}": r["cluster"] for r in meta["records"]}
    c = connect(f"bm_dst_{a.scenario}_{a.version}_{a.tag}".lower())
    with c.cursor() as cur:
        cur.execute("SELECT method, record_a, record_b FROM merge_engine_merge_audit")
        rows = cur.fetchall()
    c.close()
    total, good = Counter(), Counter()
    for method, x, y in rows:
        total[method] += 1
        good[method] += truth.get(x) is not None and truth.get(x) == truth.get(y)
    out = {
        m: {"edges": total[m], "true": good[m], "precision": round(good[m] / total[m], 4)}
        for m in total
    }
    print(json.dumps({"scenario": a.scenario, "version": a.version, "tag": a.tag, **out}))


if __name__ == "__main__":
    main()
