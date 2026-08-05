"""Profile + byte-identical-output harness for resolve_entities (Part 1.5 A.0).

Part 1.5's scoring-waste removal (A.1-A.5) must not change a single matcher
decision. "Byte-identical" is the acceptance test, and this script is the
instrument: it runs ``resolve_entities_impl`` over a FIXED, deterministic
record set with the LLM disabled (heuristic-only, so the number is pure CPU
with no network variance), dumps the resulting cluster structure to JSON, and
compares it against a saved baseline. If the cluster structure differs at all,
the optimisation was not behaviour-preserving and the harness fails loudly.

It also captures a cProfile of the run so the actual hot functions are visible
(the whole point of profiling before optimising, per D.1's finding that the
earlier PERFORMANCE_BOTTLENECKS.md guess was wrong).

Determinism: records are pulled ``ORDER BY`` a stable key and snapshotted to
the scratchpad, so the same input is reused across runs; even if the snapshot
is deleted, the ``ORDER BY`` makes the re-fetch reproduce the same rows.

Usage:
    # 1. Record the baseline BEFORE any A.1-A.6 edit:
    python -m scripts.diagnostics.profile_resolve --save-baseline

    # 2. After each edit, assert nothing changed:
    python -m scripts.diagnostics.profile_resolve --check

    # Profile a single entity, more records:
    python -m scripts.diagnostics.profile_resolve --entity technology --limit 1503
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json
import os
import pstats
import sys
import time
from pathlib import Path
from typing import Any

# Part 1.5 D.7: this script's entire job is proving resolve_entities_impl
# itself is byte-identical after an edit — a resolve_cache hit would return a
# PRE-edit cached result and report it as "verified" without the new code
# ever running. Must be set before resolve_entities is imported anywhere
# down the call chain.
os.environ["MERGE_ENGINE_DISABLE_RESOLVE_CACHE"] = "1"

import clickhouse_connect

ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_DIR = ROOT / "docs" / "verification"
SNAPSHOT_DIR = (
    ROOT.parent.parent
    / "AppData"
    / "Local"
    / "Temp"
    / "claude"
    / "D--transfer-merging"
    / "_profile_snapshots"
)

# Each case: entity_type -> (origin db, table, order-by column, default limit).
# These are the real Boeing-scale hot spots from the speed report: technology
# (1,503 records, pure origin1) and a fixed experience sample.
CASES: dict[str, tuple[str, str, str, int]] = {
    "technology": ("origin1", "CompanyTechnographics", "techID", 1503),
    "experience": ("origin2", "Experience", "experienceID", 5000),
}


def _fetch_records(entity: str, limit: int) -> list[dict[str, Any]]:
    """Deterministically fetch + tag records; snapshot to disk for reuse."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snap = SNAPSHOT_DIR / f"{entity}_{limit}.json"
    if snap.exists():
        return json.loads(snap.read_text(encoding="utf-8"))

    db, table, order_col, _ = CASES[entity]
    origin_tag = "o1" if db == "origin1" else "o2"
    client = clickhouse_connect.get_client(
        host="localhost", port=8190, username="default", password="pass", database=db
    )
    result = client.query(f"SELECT * FROM {db}.{table} ORDER BY {order_col} LIMIT {limit}")
    cols = result.column_names
    records: list[dict[str, Any]] = []
    for row in result.result_rows:
        rec = {c: (str(v) if v is not None else None) for c, v in zip(cols, row)}
        rid = rec.get(order_col) or str(len(records))
        rec["_origin_id"] = origin_tag
        rec["_origin_table"] = table
        rec["_origin_id_row"] = str(rid)
        records.append(rec)
    snap.write_text(json.dumps(records), encoding="utf-8")
    return records


def _cluster_signature(result: dict[str, Any]) -> dict[str, Any]:
    """Order-independent fingerprint of the cluster structure.

    Two runs are 'byte-identical' when this fingerprint is equal: the same
    set of clusters, each with the same sorted member set and the same
    match_confidence. Cluster IDs themselves are deterministic
    (deterministic_uuid over sorted member ids) so they are comparable too,
    but membership is the load-bearing invariant.
    """
    clusters = []
    for c in result.get("clusters", []):
        clusters.append(
            {
                "members": sorted(c.get("record_ids", [])),
                "confidence": round(float(c.get("match_confidence", 0.0)), 6),
                "review": bool(c.get("required_human_review", False)),
            }
        )
    clusters.sort(key=lambda c: (c["members"], c["confidence"]))
    return {
        "entity": result.get("entity_type"),
        "total_records": result.get("total_records"),
        "auto_matched": result.get("auto_matched"),
        "auto_excluded": result.get("auto_excluded"),
        "n_clusters": len(clusters),
        "clusters": clusters,
    }


async def _run_one(entity: str, limit: int) -> tuple[dict[str, Any], float]:
    from src.mcp_server.tools.resolve_entities import resolve_entities_impl

    records = _fetch_records(entity, limit)
    start = time.perf_counter()
    result = await resolve_entities_impl(entity_type=entity, records=records, llm_config=None)
    elapsed = time.perf_counter() - start
    return result, elapsed


def _profile_run(entity: str, limit: int) -> tuple[dict[str, Any], float, str]:
    holder: dict[str, Any] = {}

    def _target() -> None:
        holder["result"], holder["elapsed"] = asyncio.run(_run_one(entity, limit))

    pr = cProfile.Profile()
    pr.enable()
    _target()
    pr.disable()

    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(25)
    return holder["result"], holder["elapsed"], s.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entity", choices=list(CASES), default=None, help="One entity; default all.")
    ap.add_argument("--limit", type=int, default=None, help="Record count (default per-case).")
    ap.add_argument(
        "--save-baseline", action="store_true", help="Write the byte-identical baseline."
    )
    ap.add_argument(
        "--check", action="store_true", help="Assert output matches the saved baseline."
    )
    ap.add_argument("--profile", action="store_true", help="Write cProfile to SCORING_PROFILE.md.")
    args = ap.parse_args()

    entities = [args.entity] if args.entity else list(CASES)
    signatures: dict[str, Any] = {}
    profile_md: list[str] = ["# Scoring profile — Part 1.5 A.0\n"]
    overall_ok = True

    for entity in entities:
        limit = args.limit if args.limit is not None else CASES[entity][3]
        if args.profile:
            result, elapsed, prof = _profile_run(entity, limit)
            profile_md.append(
                f"\n## {entity} ({limit} records) — {elapsed:.2f}s\n\n```\n{prof}\n```\n"
            )
        else:
            result, elapsed = asyncio.run(_run_one(entity, limit))
        sig = _cluster_signature(result)
        signatures[entity] = sig
        print(
            f"{entity:12s} records={limit:6d}  clusters={sig['n_clusters']:6d}  "
            f"auto_matched={sig['auto_matched']:6d}  time={elapsed:7.2f}s"
        )

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = BASELINE_DIR / "resolve_baseline.json"

    if args.profile:
        (BASELINE_DIR / "SCORING_PROFILE.md").write_text("".join(profile_md), encoding="utf-8")
        print(f"\nProfile written to {BASELINE_DIR / 'SCORING_PROFILE.md'}")

    if args.save_baseline:
        # Merge into any existing baseline rather than overwrite — a
        # single-entity re-save (e.g. --entity experience) must not drop
        # the other entities' already-recorded signatures.
        existing: dict[str, Any] = {}
        if baseline_path.exists():
            existing = json.loads(baseline_path.read_text(encoding="utf-8"))
        existing.update(signatures)
        baseline_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print(f"\nBaseline saved to {baseline_path} (entities now: {sorted(existing)})")
        return 0

    if args.check:
        if not baseline_path.exists():
            print(
                "\nERROR: no baseline to check against — run --save-baseline first.",
                file=sys.stderr,
            )
            return 2
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        for entity in entities:
            if baseline.get(entity) != signatures.get(entity):
                overall_ok = False
                print(
                    f"\nMISMATCH for {entity}: cluster structure changed vs baseline!",
                    file=sys.stderr,
                )
                b = baseline.get(entity, {})
                s = signatures.get(entity, {})
                for k in ("n_clusters", "auto_matched", "auto_excluded", "total_records"):
                    if b.get(k) != s.get(k):
                        print(f"   {k}: baseline={b.get(k)} now={s.get(k)}", file=sys.stderr)
        if overall_ok:
            print("\nOK: cluster structure byte-identical to baseline.")
            return 0
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
