"""Drive a live e2e merge for any company through the real app -- ground-truth
sweep (2026-09-15): Boeing/Airbus (existing baselines) plus three new
domain-verified ground-truth companies (Thuraya, OHB SE, Capella Space),
each run against both the default (builtin) engine and Splink-forced, to
cross-check the app's own completeness/family reporting against manually
derived expected values (see TODO.md / the audit artifact for the manual
ground truth).

Usage: python drive_company_2026-09-15.py "<Company Name>" [splink]
"""

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


def drive(company: str, splink: bool):
    payload = {
        "request": f"merge all information about {company}",
        "subject": company,
        "origins": ORIGINS,
        "destination": DESTINATION,
        "full_remerge": True,
    }
    if splink:
        payload["matching_rules"] = {"company": {"engine": "splink"}}

    wall_start = time.monotonic()
    tag = "SPLINK-FORCED" if splink else "DEFAULT-ENGINE"
    log(f"\n{'=' * 70}\nSTARTING WORKFLOW ({tag}): 'merge all information about {company}'\n{'=' * 70}")
    r = requests.post(f"{BASE}/api/workflow/start", json=payload, timeout=60)
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
                json={"decision": decision, "reason": f"ground-truth sweep: {company} ({tag})"},
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
    company_name = sys.argv[1]
    use_splink = len(sys.argv) > 2 and sys.argv[2].lower() == "splink"
    st, thread_id = drive(company_name, use_splink)
    print("\nTHREAD_ID:", thread_id)
