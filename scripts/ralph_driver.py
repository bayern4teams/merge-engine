"""Ralph-loop driver for long-running verification cycles (B5.4).

A full verification run (e.g., Boeing) can take 30-90 minutes across multiple
HITL gates. If drive_app.py exits unexpectedly or encounters a transient poll
timeout, this driver re-injects the task loop into a fresh subprocess execution
while the underlying app and database state persist on the filesystem.

Usage:
    python scripts/ralph_driver.py "Boeing" --assert-budgets
    python scripts/ralph_driver.py --max-restarts 5
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVE_APP_SCRIPT = ROOT / "scripts" / "diagnostics" / "drive_app.py"


def log(msg: str) -> None:
    print(f"[ralph-driver] {msg}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "company", nargs="?", default="Boeing", help="Company subject (default: Boeing)"
    )
    parser.add_argument(
        "--assert-budgets",
        action="store_true",
        help="Assert docs/verification/BUDGETS.yaml contracts on completion.",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=5,
        help="Maximum restart attempts on transient failure (default: 5).",
    )
    return parser.parse_args()


def run_ralph_loop(company: str, assert_budgets: bool, max_restarts: int) -> int:
    cmd = [sys.executable, str(DRIVE_APP_SCRIPT), company]
    if assert_budgets:
        cmd.append("--assert-budgets")

    attempt = 0
    start_time = time.monotonic()

    while attempt <= max_restarts:
        attempt += 1
        log(f"Starting execution pass {attempt}/{max_restarts + 1} for '{company}'...")

        proc = subprocess.run(cmd, cwd=ROOT)
        if proc.returncode == 0:
            elapsed_min = (time.monotonic() - start_time) / 60.0
            log(
                f"Workflow completed successfully on pass {attempt} (total time: {elapsed_min:.1f} min)."
            )
            return 0

        log(f"Pass {attempt} exited with code {proc.returncode}.")
        if attempt <= max_restarts:
            log(
                "State is preserved on filesystem/DB. Re-injecting task into fresh driver context in 5 seconds..."
            )
            time.sleep(5)

    log(f"FAILED: Exceeded maximum restart count ({max_restarts}).")
    return 1


def main() -> int:
    args = parse_args()
    return run_ralph_loop(args.company, args.assert_budgets, args.max_restarts)


if __name__ == "__main__":
    sys.exit(main())
