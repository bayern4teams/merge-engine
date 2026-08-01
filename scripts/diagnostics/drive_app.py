"""
Drive the merge-engine app end-to-end via its REST API and capture every gate's
diagnostic evidence. Usage: python drive_app.py "<Company>"

Approves through every HITL gate (or SKIP at an error gate) so the run reaches
load_data, and logs the full state snapshot at each interrupt.

Promoted from the original scratchpad script 2026-07-30 (used for the Airbus/Lockheed
diagnosis runs); destination port updated from 55432 to 55201 (see
merge-engine-remediation-1-to-6 in project memory for why). The error-gate heuristic
matches on the interrupt NODE NAME only (`handle_error`), never on payload content --
the final-approval gate's summary also carries an `errors` count, and matching on
payload text caused an infinite invalid-`skip` loop the first time this ran.
"""
import json
import sys
import time

import requests

BASE = "http://localhost:7860"
COMPANY = sys.argv[1] if len(sys.argv) > 1 else "Boeing"

ORIGINS = [
    {"id": "o1", "type": "clickhouse",
     "connection": {"host": "localhost", "port": 8190, "user": "default",
                    "password": "pass", "database": "origin1"},
     "priority": 1},
    {"id": "o2", "type": "clickhouse",
     "connection": {"host": "localhost", "port": 8190, "user": "default",
                    "password": "pass", "database": "origin2"},
     "priority": 2},
]
DESTINATION = {
    "type": "postgres", "host": "localhost", "port": 55201,
    "user": "postgres", "password": "pass", "database": "global_warehouse",
    "schema": "public",
}


def log(m):
    print(m, flush=True)


# 1. START
log(f"\n{'=' * 70}\nSTARTING WORKFLOW: 'merge all information about {COMPANY}'\n{'=' * 70}")
r = requests.post(f"{BASE}/api/workflow/start", json={
    "request": f"merge all information about {COMPANY}",
    "origins": ORIGINS,
    "destination": DESTINATION,
    "full_remerge": True,
}, timeout=60)
log(f"start -> HTTP {r.status_code}")
if r.status_code != 200:
    log(r.text)
    sys.exit(1)
thread_id = r.json()["thread_id"]
log(f"thread_id = {thread_id}")

# 2. POLL + DRIVE GATES
gate_count = 0
last_seen = None
max_iters = 300
for it in range(max_iters):
    time.sleep(3)
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
        fb = requests.post(f"{BASE}/api/workflow/{thread_id}/feedback",
                            json={"decision": decision, "reason": "auto-diagnosis run"},
                            timeout=60)
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

# 3. FINAL STATUS DUMP
log(f"\n{'=' * 70}\nFINAL FULL STATUS\n{'=' * 70}")
st = requests.get(f"{BASE}/api/workflow/{thread_id}/status", timeout=30).json()
log(json.dumps(st, indent=2, default=str)[:12000])
log(f"\nthread_id={thread_id}")
