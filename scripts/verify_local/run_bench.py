"""Run one benchmark scenario through the full app (REST) and score it independently.

Usage: python -m scripts.verify_local.run_bench <scenario> <before|after> [--tag T]
           [--port P] [--memory FILE] [--quality-history FILE] [--keep-dst]

- before = detached worktree .tmp/wt_main (507f9d4), after = this checkout.
- Each run gets its own cwd ``.tmp/verify_local/runs/<scenario>_<version>_<tag>/`` so
  config/profiles, the LLM cache, logs/spool (resolve cache) and data/checkpoints never
  carry over; the copied config turns the persistent LLM cache off (user decision).
  The editable install points at this checkout, so the code root is pinned by
  PYTHONPATH and checked before launch.
- Fresh destination database ``bm_dst_<scenario>_<version>_<tag>`` on the bench server
  (127.0.0.1:5439 only), one table per entity, fixed columns (no new columns are ever
  expected; the run's DDL is checked afterwards).
- Every gate is approved with a plain {"decision": "approve"} (``skip`` at handle_error),
  every gate payload is written to gates.jsonl.
- Scoring reads ``merge_engine_entity_map`` by SQL (never the app's status) and runs
  scripts/verify_local/scorer.py against the published truth.
- LLM calls = "LLM response received" lines in the run's own DEBUG log (one per real
  endpoint call; cache hits log differently).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

from scripts.verify_local import scorer
from scripts.verify_local.load_bench import BM, connect, recreate_db

ROOT = Path(__file__).resolve().parents[2]
CODE = {"before": ROOT / ".tmp" / "wt_main", "after": ROOT}
PY = ROOT / ".venv311" / "bin" / "python"

DEST_COLUMNS = {
    "person": [
        "given_name",
        "surname",
        "street_number",
        "address_1",
        "address_2",
        "suburb",
        "postcode",
        "state",
        "date_of_birth",
        "soc_sec_id",
    ],
    "organisation": ["name"],
    "product": ["name", "description", "manufacturer", "price"],
    "publication": ["title", "authors", "venue", "year"],
    "settlement": ["label", "lat", "lon", "type", "elevation"],
    "recording": ["number", "title", "length", "artist", "album", "year", "language"],
}
REQUEST = "merge all {entity} records from every origin into the destination"


def _env_from_dotenv() -> dict[str, str]:
    """.env values go into the child's environment only, never into a file."""
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def make_destination(name: str, table: str) -> None:
    recreate_db(name)
    c = connect(name)
    with c.cursor() as cur:
        cols = ", ".join(f'"{k}" TEXT' for k in DEST_COLUMNS[table])
        cur.execute(f'CREATE TABLE public."{table}" ("id" TEXT PRIMARY KEY, {cols})')
    c.close()


def dest_shape(name: str) -> dict[str, list[str]]:
    c = connect(name)
    with c.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY table_name, ordinal_position"
        )
        out: dict[str, list[str]] = {}
        for t, col in cur.fetchall():
            out.setdefault(t, []).append(col)
    c.close()
    return out


def dest_counts(name: str) -> dict[str, int]:
    c = connect(name)
    out = {}
    with c.cursor() as cur:
        for t in dest_shape(name):
            cur.execute(f'SELECT count(*) FROM public."{t}"')
            out[t] = cur.fetchone()[0]
    c.close()
    return out


def read_entity_map(name: str) -> list[tuple[str, str, str]]:
    c = connect(name)
    with c.cursor() as cur:
        cur.execute("SELECT to_regclass('public.merge_engine_entity_map')")
        if cur.fetchone()[0] is None:
            c.close()
            return []
        cur.execute("SELECT entity_type, record_id, cluster_id FROM public.merge_engine_entity_map")
        rows = cur.fetchall()
    c.close()
    return rows


def start_app(rundir: Path, version: str, port: int, extra_env: dict[str, str]):
    code = CODE[version]
    env = {
        **os.environ,
        **_env_from_dotenv(),
        **extra_env,
        "PYTHONPATH": str(code),
        "TMPDIR": str(ROOT / ".tmp"),
        "MERGE_ENGINE_DISABLE_RESOLVE_CACHE": "1",
        "MERGE_ENGINE_HISTORY_PATH": str(rundir / "config" / "recent_connections.json"),
    }
    where = subprocess.run(
        [str(PY), "-c", "import src;print(src.__file__)"],
        cwd=rundir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(where).resolve().is_relative_to(code.resolve()), (where, code)
    if version == "before":
        assert not Path(where).resolve().is_relative_to((ROOT / "src").resolve())
    log = (rundir / "app.out").open("w")
    proc = subprocess.Popen(
        [
            str(PY),
            "-m",
            "src.main",
            "--mode",
            "gradio",
            "--port",
            str(port),
            "--mcp-port",
            str(port + 1000),
            "--log-file",
            str(rundir / "logs" / "debug.log"),
        ],
        cwd=rundir,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(120):
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/workflow/active", timeout=2).ok:
                return proc, where
        except requests.RequestException:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"app exited early, see {rundir / 'app.out'}")
        time.sleep(1)
    raise RuntimeError("app did not come up")


def drive(
    base: str, body: dict, gates_path: Path, max_minutes: float, oracle: dict | None = None
) -> tuple[dict, list]:
    """Approve every gate; with ``oracle`` (record id -> true cluster) also answer each pair
    listed at the matches gate the way a perfect human would (P9/P15)."""
    r = requests.post(f"{base}/api/workflow/start", json=body, timeout=120)
    r.raise_for_status()
    tid = r.json()["thread_id"]
    gates, st, deadline = [], {}, time.monotonic() + max_minutes * 60
    with gates_path.open("w") as g:
        while time.monotonic() < deadline:
            time.sleep(3)
            st = requests.get(f"{base}/api/workflow/{tid}/status", timeout=60).json()
            status, node = st.get("status"), str(st.get("current_interrupt_node"))
            if status == "interrupted":
                decision = "skip" if "handle_error" in node else "approve"
                g.write(
                    json.dumps(
                        {"node": node, "decision": decision, "t": time.time(), "status": st},
                        default=str,
                    )
                    + "\n"
                )
                g.flush()
                gates.append((node, decision))
                feedback = {"decision": decision, "reason": "local verify"}
                data = (st.get("interrupt_data") or {}).get("data") or {}
                if oracle and node == "human_review_matches":
                    feedback["pair_decisions"] = [
                        {
                            "entity_type": p.get("entity_type") or body["request"].split()[2],
                            "record_ids": p["record_ids"],
                            "action": "merge"
                            if oracle.get(p["record_ids"][0]) == oracle.get(p["record_ids"][1])
                            else "keep_separate",
                        }
                        for p in data.get("pending_merge_candidates") or []
                    ]
                    (gates_path.parent / "oracle_decisions.json").write_text(
                        json.dumps(feedback["pair_decisions"])
                    )
                fb = requests.post(
                    f"{base}/api/workflow/{tid}/feedback", json=feedback, timeout=120
                )
                if fb.status_code != 200 or len(gates) > 15:
                    st["driver_stop"] = f"feedback {fb.status_code} {fb.text[:300]}"
                    break
                continue
            if status in ("completed", "error", "failed", "aborted", "rejected", "not_found"):
                break
        else:
            st["driver_stop"] = "timeout"
    st["thread_id"] = tid
    return st, gates


def llm_counts(log: Path) -> dict[str, int]:
    text = log.read_text(errors="replace") if log.exists() else ""
    return {
        "llm_calls": text.count("LLM response received"),
        "llm_cache_hits": text.count("LLM cache hit"),
        "llm_ainvoke": text.count("Calling model.ainvoke()"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("version", choices=["before", "after"])
    ap.add_argument("--tag", default="r1")
    ap.add_argument("--port", type=int, default=7871)
    ap.add_argument("--memory", help="MERGE_ENGINE_DECISION_MEMORY file (P9)")
    ap.add_argument("--quality-history", help="MERGE_ENGINE_QUALITY_HISTORY file (P16)")
    ap.add_argument("--keep-dst", action="store_true", help="reuse the destination (2nd run)")
    ap.add_argument("--max-minutes", type=float, default=90)
    ap.add_argument("--score-only", action="store_true", help="re-score a finished run")
    ap.add_argument("--oracle", action="store_true", help="answer gate pairs from the truth")
    a = ap.parse_args()

    meta = json.loads((BM / "truth" / f"{a.scenario}.json").read_text())
    table = meta["table"]
    run = f"{a.scenario}_{a.version}_{a.tag}"
    rundir = ROOT / ".tmp" / "verify_local" / "runs" / run
    dst = f"bm_dst_{a.scenario}_{a.version}_{a.tag}".lower()
    if a.score_only:
        old = json.loads((rundir / "result.json").read_text())
        old.pop("score", None)
        return report(rundir, meta, dst, old)
    if rundir.exists():
        shutil.rmtree(rundir)
    (rundir / "config").mkdir(parents=True)
    (rundir / "logs").mkdir()
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text())
    cfg["llm"]["persistent_cache_enabled"] = False
    (rundir / "config" / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    shutil.copy(ROOT / "config" / "consistency_rules.yaml", rundir / "config")

    if not a.keep_dst:
        make_destination(dst, table)
    shape_before, counts_before = dest_shape(dst), dest_counts(dst)

    extra = {"MERGE_ENGINE_QUALITY_HISTORY": a.quality_history or str(rundir / "quality.json")}
    if a.memory:
        extra["MERGE_ENGINE_DECISION_MEMORY"] = a.memory
    conn = {"host": "127.0.0.1", "port": 5439, "user": "postgres", "password": ""}
    body = {
        "request": REQUEST.replace("{entity}", table),
        "origins": [
            {
                "id": o["source"],
                "type": "postgres",
                "priority": i + 1,
                "connection": {**conn, "database": o["database"]},
            }
            for i, o in enumerate(meta["origins"])
        ],
        "destination": {"type": "postgres", **conn, "database": dst, "schema": "public"},
        "full_remerge": True,
    }
    proc, code_file = start_app(rundir, a.version, a.port, extra)
    t0 = time.monotonic()
    try:
        oracle = (
            {f"{r['origin']}:public.{r['table']}:{r['id']}": r["cluster"] for r in meta["records"]}
            if a.oracle
            else None
        )
        st, gates = drive(
            f"http://127.0.0.1:{a.port}", body, rundir / "gates.jsonl", a.max_minutes, oracle
        )
    finally:
        wall = time.monotonic() - t0
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
    (rundir / "final_status.json").write_text(json.dumps(st, indent=1, default=str))
    base = {
        "run": run,
        "version": a.version,
        "code_file": code_file,
        "destination_db": dst,
        "status": st.get("status"),
        "error": st.get("error"),
        "driver_stop": st.get("driver_stop"),
        "gates": gates,
        "wall_seconds": round(wall, 1),
        **llm_counts(rundir / "logs" / "debug.log"),
        "dest_shape_before": shape_before,
        "dest_counts_before": counts_before,
    }
    return report(rundir, meta, dst, base)


def report(rundir: Path, meta: dict, dst: str, base: dict) -> int:
    """Score the destination by SQL and write result.json."""
    shape_before = base["dest_shape_before"]
    rows = read_entity_map(dst)
    # record_id = "<origin_id>:<schema>.<table>:<row id>" (seen in the entity map)
    truth, source_of = {}, {}
    for r in meta["records"]:
        rid = f"{r['origin']}:public.{r['table']}:{r['id']}"
        truth[rid], source_of[rid] = r["cluster"], r["origin"]
    pred = {rid: cid for _e, rid, cid in rows}
    entity_types = sorted({e for e, _r, _c in rows})
    result = {
        **base,
        "entity_map_rows": len(rows),
        "entity_types": entity_types,
        "dest_shape_after": dest_shape(dst),
        "dest_counts_after": dest_counts(dst),
        "score": scorer.score(truth, pred, source_of) if rows else None,
    }
    result["new_columns"] = {
        t: sorted(set(c) - set(shape_before.get(t, [])))
        for t, c in result["dest_shape_after"].items()
        if t in shape_before and set(c) - set(shape_before[t])
    }
    (rundir / "result.json").write_text(json.dumps(result, indent=1, default=str))
    s = result["score"] or {}
    pw = (s.get("pairwise") or {}).get("all", {})
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "run",
                    "status",
                    "error",
                    "driver_stop",
                    "wall_seconds",
                    "llm_calls",
                    "entity_map_rows",
                    "dest_counts_after",
                    "new_columns",
                )
            },
            default=str,
        )
    )
    print(
        "pairwise",
        {k: pw.get(k) for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
        "bcubed",
        s.get("bcubed"),
        "largest",
        s.get("largest_cluster"),
        "missing",
        s.get("missing_records"),
        "unknown",
        s.get("unknown_records"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
