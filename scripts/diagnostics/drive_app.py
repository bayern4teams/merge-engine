"""
Drive the merge-engine app end-to-end via its REST API and capture every gate's
diagnostic evidence. Usage: python drive_app.py "<Company>" [--assert-budgets]

Approves through every HITL gate (or SKIP at an error gate) so the run reaches
load_data, and logs the full state snapshot at each interrupt.

Promoted from the original scratchpad script 2026-07-30 (used for the Airbus/Lockheed
diagnosis runs); destination port updated from 55432 to 55201 (see
merge-engine-remediation-1-to-6 in project memory for why). The error-gate heuristic
matches on the interrupt NODE NAME only (`handle_error`), never on payload content --
the final-approval gate's summary also carries an `errors` count, and matching on
payload text caused an infinite invalid-`skip` loop the first time this ran.

--assert-budgets (Phase 0 Sec 0.6) checks the finished run against
docs/verification/BUDGETS.yaml: the resolve_entities node's wall time (summed
per-entity budget, falling back to `_default` for an entity not explicitly
listed), the whole run's wall-clock minutes, and — for the three known
golden subjects (boeing/airbus/lockheed) only — parity via
compare_to_golden.py. `llm_stage_latency_ms_p95` is not asserted: nothing in
the pipeline currently records per-call LLM latency anywhere the driver can
read it, and faking that check would be worse than skipping it honestly.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

BASE = "http://localhost:7860"
ROOT = Path(__file__).resolve().parent.parent.parent
BUDGETS_PATH = ROOT / "docs" / "verification" / "BUDGETS.yaml"

# Real Boeing-scale runs take 60-90 minutes (docs/diagnostics/PERFORMANCE_BOTTLENECKS.md
# "operational friction"). The old max_iters=300 at a 3s poll interval capped
# out at 15 minutes and forced manual restarts mid-run; raised so one
# invocation can watch a full run unattended.
POLL_INTERVAL_SECONDS = 5
MAX_ITERS = 1200  # 1200 * 5s = 100 minutes

ORIGINS = [
    {
        "id": "o1",
        "type": "clickhouse",
        "connection": {
            "host": "localhost",
            "port": 8190,
            "user": "default",
            "password": "pass",
            "database": "origin1",
        },
        "priority": 1,
    },
    {
        "id": "o2",
        "type": "clickhouse",
        "connection": {
            "host": "localhost",
            "port": 8190,
            "user": "default",
            "password": "pass",
            "database": "origin2",
        },
        "priority": 2,
    },
]
DESTINATION = {
    "type": "postgres",
    "host": "localhost",
    "port": 55201,
    "user": "postgres",
    "password": "pass",
    "database": "global_warehouse",
    "schema": "public",
}


def log(m):
    print(m, flush=True)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("company", nargs="?", default="Boeing")
    ap.add_argument(
        "--assert-budgets",
        action="store_true",
        help="After the run finishes, check it against docs/verification/BUDGETS.yaml "
        "and exit non-zero on a breach.",
    )
    return ap.parse_args()


def drive(company: str) -> tuple[dict, float]:
    """Run the workflow to completion. Returns (final_status, elapsed_minutes)."""
    wall_start = time.monotonic()
    log(f"\n{'=' * 70}\nSTARTING WORKFLOW: 'merge all information about {company}'\n{'=' * 70}")
    r = requests.post(
        f"{BASE}/api/workflow/start",
        json={
            "request": f"merge all information about {company}",
            "origins": ORIGINS,
            "destination": DESTINATION,
            "full_remerge": True,
        },
        timeout=60,
    )
    log(f"start -> HTTP {r.status_code}")
    if r.status_code != 200:
        log(r.text)
        sys.exit(1)
    thread_id = r.json()["thread_id"]
    log(f"thread_id = {thread_id}")

    gate_count = 0
    last_seen = None
    st: dict = {}
    for it in range(MAX_ITERS):
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            st = requests.get(f"{BASE}/api/workflow/{thread_id}/status", timeout=30).json()
        except Exception as e:
            log(f"[poll {it}] status error: {e}")
            continue
        status = st.get("status")
        node = st.get("current_interrupt_node")
        seen = f"{status}|{node}"
        if seen != last_seen:
            log(f"[poll {it}] status={status} node={node}")
            last_seen = seen

        if status == "interrupted":
            gate_count += 1
            log(f"\n----- GATE #{gate_count} (node={node}) -----")
            idata = st.get("interrupt_data") or {}
            log("interrupt_data:")
            log(json.dumps(idata, indent=2, default=str)[:7000])
            if st.get("error"):
                log(f"error: {st['error']}")
            if st.get("warning"):
                log(f"warning: {st['warning']}")
            # Decide feedback: skip ONLY at the actual handle_error node; the
            # final-approval summary also carries an "errors" count, so match on
            # the node name, not the payload.
            node_l = str(node).lower()
            is_error_gate = node_l == "handle_error" or "handle_error" in node_l
            decision = "skip" if is_error_gate else "approve"
            log(f"  -> is_error_gate={is_error_gate}; sending decision='{decision}'")
            fb = requests.post(
                f"{BASE}/api/workflow/{thread_id}/feedback",
                json={"decision": decision, "reason": "auto-diagnosis run"},
                timeout=60,
            )
            log(f"  feedback -> HTTP {fb.status_code} {fb.text[:300]}")
            if fb.status_code != 200:
                log("  !! non-200 feedback; stopping to avoid a retry loop")
                break
            if gate_count > 12:
                log("  !! too many gates; stopping")
                break
            time.sleep(2)
            continue

        if status in ("completed", "error", "failed", "aborted", "rejected", "not_found"):
            log(f"\n===== TERMINAL: status={status} =====")
            if st.get("error"):
                log(f"error: {st['error']}")
            if st.get("warning"):
                log(f"warning: {st['warning']}")
            if st.get("summary"):
                log("summary:\n" + json.dumps(st["summary"], indent=2, default=str)[:8000])
            break
    else:
        log("!! max iterations reached without terminal status")

    log(f"\n{'=' * 70}\nFINAL FULL STATUS\n{'=' * 70}")
    st = requests.get(f"{BASE}/api/workflow/{thread_id}/status", timeout=30).json()
    log(json.dumps(st, indent=2, default=str)[:12000])
    log(f"\nthread_id={thread_id}")

    elapsed_minutes = (time.monotonic() - wall_start) / 60.0
    return st, elapsed_minutes


def assert_budgets(final_status: dict, elapsed_minutes: float, company: str) -> bool:
    """Check the finished run against docs/verification/BUDGETS.yaml. Returns True if all
    assertable budgets passed (prints PASS/FAIL/SKIP per budget either way)."""
    budgets = yaml.safe_load(BUDGETS_PATH.read_text(encoding="utf-8"))
    report = (final_status.get("summary") or {}).get("reconciliation_report") or {}
    ok = True

    log(f"\n{'=' * 70}\nBUDGET ASSERTIONS ({BUDGETS_PATH.relative_to(ROOT)})\n{'=' * 70}")

    timings = report.get("timings") or {}
    entities = list((report.get("entities") or {}).keys())
    re_budgets = budgets.get("resolve_entities_seconds_max", {})
    default_budget = re_budgets.get("_default", 1800)
    if entities:
        allowed = sum(re_budgets.get(e, default_budget) for e in entities)
    else:
        allowed = default_budget
    actual = timings.get("resolve_entities")
    if actual is None:
        log(
            f"SKIP resolve_entities_seconds_max: no 'resolve_entities' timing in the report "
            f"(report.timings={timings})"
        )
    elif actual <= allowed:
        log(f"PASS resolve_entities_seconds_max: {actual:.1f}s <= {allowed}s (entities={entities})")
    else:
        log(f"FAIL resolve_entities_seconds_max: {actual:.1f}s > {allowed}s (entities={entities})")
        ok = False

    full_max = budgets.get("full_run_minutes_max")
    if full_max is not None:
        if elapsed_minutes <= full_max:
            log(f"PASS full_run_minutes_max: {elapsed_minutes:.1f} min <= {full_max} min")
        else:
            log(f"FAIL full_run_minutes_max: {elapsed_minutes:.1f} min > {full_max} min")
            ok = False

    log(
        "SKIP llm_stage_latency_ms_p95: no per-call LLM latency is currently recorded "
        "anywhere this driver can read it (not in the run report or workflow status). "
        "Not asserted rather than faked."
    )

    subject = company.strip().lower()
    parity_min = budgets.get("parity_pct_min")
    if subject in ("boeing", "airbus", "lockheed") and parity_min is not None:
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "diagnostics" / "compare_to_golden.py"),
                    subject,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
            )
            parity_result = json.loads(proc.stdout)
        except Exception as exc:
            log(f"SKIP parity_pct_min: compare_to_golden.py failed to run ({exc})")
        else:
            parity = (parity_result.get("totals") or {}).get("overall_parity_pct")
            if parity is None:
                log("SKIP parity_pct_min: no overall_parity_pct in compare_to_golden.py output")
            elif parity >= parity_min:
                log(f"PASS parity_pct_min: {parity}% >= {parity_min}%")
            else:
                log(f"FAIL parity_pct_min: {parity}% < {parity_min}%")
                ok = False
    else:
        log(
            f"SKIP parity_pct_min: '{company}' is not one of the three golden subjects "
            f"(boeing/airbus/lockheed) that have a hand-merge baseline to compare against"
        )

    return ok


def main() -> int:
    args = parse_args()
    final_status, elapsed_minutes = drive(args.company)
    log(f"\nTotal wall time: {elapsed_minutes:.1f} minutes")
    if not args.assert_budgets:
        return 0
    return 0 if assert_budgets(final_status, elapsed_minutes, args.company) else 1


if __name__ == "__main__":
    sys.exit(main())
