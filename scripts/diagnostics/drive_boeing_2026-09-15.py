"""Drive a live Boeing merge through the real app -- B.9.2 final-gate e2e run
(2026-09-15), default engine (no matching_rules override), adapted verbatim
from scripts/diagnostics/drive_airbus_2026-09-13.py with only the subject and
log message changed, to re-measure against the audit's own Boeing baseline
(wf_67044cd3: 92.9% completeness, 13-of-14 families loaded)."""

import json
import sys
import time

import requests

BASE = "http://localhost:7860"
POLL_INTERVAL_SECONDS = 5
MAX_ITERS = 1200

ORIGINS = [
    {
        "id": "testdiffbot",
        "type": "postgres",
        "connection": {
            "host": "localhost",
            "port": 5433,
            "user": "postgres",
            "password": "supersecurepassword",
            "database": "testdiffbot",
        },
        "priority": 1,
    },
    {
        "id": "testzoominfo",
        "type": "clickhouse",
        "connection": {
            "host": "localhost",
            "port": 8125,
            "user": "postgres",
            "password": "supersecurepassword",
            "database": "testzoominfo",
        },
        "priority": 2,
    },
]
DESTINATION = {
    "type": "postgres",
    "host": "localhost",
    "port": 5434,
    "user": "postgres",
    "password": "supersecurepassword",
    "database": "destination",
    "schema": "kg",
}


def log(m):
    print(m, flush=True)


def drive(company: str):
    wall_start = time.monotonic()
    log(f"\n{'=' * 70}\nSTARTING WORKFLOW: 'merge all information about {company}'\n{'=' * 70}")
    r = requests.post(
        f"{BASE}/api/workflow/start",
        json={
            "request": f"merge all information about {company}",
            "subject": company,
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
    st = {}
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
            log(json.dumps(idata, indent=2, default=str)[:4000])
            if st.get("error"):
                log(f"error: {st['error']}")
            if st.get("warning"):
                log(f"warning: {st['warning']}")
            node_l = str(node).lower()
            is_error_gate = "handle_error" in node_l
            decision = "skip" if is_error_gate else "approve"
            log(f"  -> is_error_gate={is_error_gate}; sending decision='{decision}'")
            fb = requests.post(
                f"{BASE}/api/workflow/{thread_id}/feedback",
                json={"decision": decision, "reason": "B.9.2 final-gate Boeing e2e run"},
                timeout=60,
            )
            log(f"  feedback -> HTTP {fb.status_code} {fb.text[:300]}")
            if fb.status_code != 200:
                log("  !! non-200 feedback; stopping to avoid a retry loop")
                break
            if gate_count > 15:
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

    elapsed = (time.monotonic() - wall_start) / 60
    log(f"\nElapsed: {elapsed:.1f} minutes")
    return st, thread_id


if __name__ == "__main__":
    st, thread_id = drive("Boeing")
    print("\nTHREAD_ID:", thread_id)
