"""Apply the user-confirmed pass rule (LOCAL_VERIFY_REPORT.md S2) to a before/after pair.

Usage: python -m scripts.verify_local.compare <scenario> [<scenario> ...] [--tag r1]
Reads .tmp/verify_local/runs/<scenario>_{before,after}_<tag>/result.json (+ gates.jsonl
for the review count) and prints one verdict line per scenario plus the failing rules.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RUNS = Path(__file__).resolve().parents[2] / ".tmp" / "verify_local" / "runs"
TOL = 0.01


def _metrics(s: dict) -> dict[str, float | None]:
    out = {}
    for k in ("all", "within_source", "cross_source"):
        for m in ("precision", "recall", "f1"):
            out[f"pair_{k}_{m}"] = s["pairwise"][k][m]
    for m in ("precision", "recall", "f1"):
        out[f"bcubed_{m}"] = s["bcubed"][m]
    out["cluster_f1"] = s["cluster"]["f1"]
    return out


def review_pairs(run: Path) -> int | None:
    """Pairs listed for a human at the matches gate (None if the gate never showed)."""
    gates = run / "gates.jsonl"
    if not gates.exists():
        return None
    n = None
    for line in gates.open():
        g = json.loads(line)
        if g["node"] == "human_review_matches":
            d = (g["status"].get("interrupt_data") or {}).get("data") or {}
            n = (n or 0) + len(d.get("pending_merge_candidates") or [])
    return n


def verdict(scenario: str, tag: str) -> dict:
    b_dir, a_dir = RUNS / f"{scenario}_before_{tag}", RUNS / f"{scenario}_after_{tag}"
    b = json.loads((b_dir / "result.json").read_text())
    a = json.loads((a_dir / "result.json").read_text())
    fails, notes = [], []
    sa, sb = a.get("score"), b.get("score")
    if not sa or a["status"] != "completed":
        return {
            "scenario": scenario,
            "verdict": "FAIL",
            "fails": [
                f"rule 5: after status={a['status']} error={a.get('error')} "
                f"entity_map_rows={a.get('entity_map_rows')}"
            ],
        }
    true_pairs = sa["pairwise"]["all"]["true_pairs"]
    # rule 2: giant cluster
    if sa["largest_cluster"] > 2 * sa["largest_true_cluster"]:
        fails.append(
            f"rule 2: largest cluster {sa['largest_cluster']} > 2 x true "
            f"{sa['largest_true_cluster']}"
        )
    # rule 4: new destination columns
    if a.get("new_columns"):
        fails.append(f"rule 4: new columns {a['new_columns']}")
    baseline = bool(sb) and b["status"] == "completed"
    if baseline:
        mb, ma = _metrics(sb), _metrics(sa)
        for k, vb in mb.items():
            va = ma[k]
            if vb is None and va is None:
                continue
            if va is None or (vb is not None and va < vb - TOL):
                fails.append(
                    f"rule 1: {k} {vb if vb is None else round(vb, 4)} -> "
                    f"{va if va is None else round(va, 4)}"
                )
        multi = sa["cluster"]["true_multi_clusters"]
        allowed = sb["over_merged_clusters"] + max(2, round(0.01 * multi))
        if sa["over_merged_clusters"] > allowed:
            fails.append(
                f"rule 3: over-merged {sb['over_merged_clusters']} -> "
                f"{sa['over_merged_clusters']} (allowed {allowed})"
            )
    else:
        notes.append(
            f"rule 7: no baseline (before status={b['status']}, "
            f"error={b.get('error')}) -> at most PARTIAL"
        )
    rv = review_pairs(a_dir)
    rv_share = (rv or 0) / true_pairs if true_pairs else 0
    if rv_share > 0.10:
        notes.append(f"rule 6: review pairs {rv} = {rv_share:.1%} of true pairs > 10%")
    if fails:
        v = "FAIL"
    elif not baseline or rv_share > 0.10:
        v = "PARTIAL"
    else:
        v = "PASS"
    return {
        "scenario": scenario,
        "verdict": v,
        "fails": fails,
        "notes": notes,
        "review_pairs_after": rv,
        "review_share": round(rv_share, 4),
        "before": _summary(b),
        "after": _summary(a),
    }


def _summary(r: dict) -> dict:
    s = r.get("score") or {}
    pw = s.get("pairwise") or {}
    row = {
        "status": r["status"],
        "wall_s": r["wall_seconds"],
        "llm_calls": r["llm_calls"],
        "rows": r.get("dest_counts_after"),
    }
    for k in ("all", "within_source", "cross_source"):
        if k in pw:
            row[k] = {
                m: (round(pw[k][m], 4) if isinstance(pw[k][m], float) else pw[k][m])
                for m in ("tp", "fp", "fn", "precision", "recall", "f1")
            }
    if s:
        row["bcubed_f1"] = s["bcubed"]["f1"] and round(s["bcubed"]["f1"], 4)
        row["cluster_f1"] = s["cluster"]["f1"] and round(s["cluster"]["f1"], 4)
        row["over_merged"] = s["over_merged_clusters"]
        row["largest"] = s["largest_cluster"]
        row["largest_true"] = s["largest_true_cluster"]
        row["missing"] = s["missing_records"]
    return row


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--tag", default="r1")
    a = ap.parse_args()
    for sc in a.scenarios:
        print(json.dumps(verdict(sc, a.tag), default=str))
