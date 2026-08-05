"""Regenerate mypy-baseline.txt from the current state of `mypy src`.

Phase 0 Sec 0.2's ratchet: mypy strict=true over a codebase that has never
been checked produces hundreds of errors on day one. Rather than disable
strict mode or silence errors with # type: ignore, the baseline records
every currently-tolerated error; scripts/check.py types then fails only on
errors NOT in the baseline (this file), so no new untyped code can land
while the existing backlog gets fixed incrementally.

The baseline must only shrink. Run this script after fixing errors (to
record the shrinkage) - never run it to silently accept new ones without
reviewing what changed first.

Usage: python scripts/update_mypy_baseline.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "mypy-baseline.txt"

# Drop the line number - it drifts on every unrelated edit above the error,
# which would make the baseline spuriously stale. What's tracked is "this
# file has N occurrences of this exact message", not "line 42 specifically".
_LINE_NUM_RE = re.compile(r"^(?P<file>[^:]+):\d+(?::\d+)?: (?P<rest>.*)$")


def normalize(mypy_line: str) -> str | None:
    m = _LINE_NUM_RE.match(mypy_line)
    if not m:
        return None
    return f"{m.group('file')}: {m.group('rest')}"


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    lines = [normalize(line) for line in result.stdout.splitlines() if ": error:" in line]
    entries = sorted(line for line in lines if line)

    BASELINE_PATH.write_text(
        "# GENERATED FILE - regenerate with: python scripts/update_mypy_baseline.py\n"
        "# This baseline may only shrink. A PR that grows it needs a reason in\n"
        "# its description, not a silent bump.\n" + "\n".join(entries) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(entries)} baseline entries to {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
