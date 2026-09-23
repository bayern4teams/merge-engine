"""Turn `pip-audit --format json` into a signal worth reacting to.

pip-audit exits non-zero on *any* advisory, including ones with no released
fix. A weekly job that goes red for something nobody can act on is the
OpenWiki failure again -- that workflow failed every night for months and
taught everyone to ignore a red badge, which is the one thing CI must never
do. So:

    fixable advisory      -> exit 1. Someone can bump a pin today.
    no fix available yet  -> exit 0, reported loudly in the job summary.

Both are always printed. The difference is only whether the run goes red.

Usage:
    pip-audit --requirement requirements.txt --format json > audit.json
    python scripts/audit_deps.py audit.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def evaluate(payload: dict | list) -> tuple[list[str], list[str]]:
    """Split advisories into (fixable, unfixable) human-readable lines."""
    deps = payload.get("dependencies", payload) if isinstance(payload, dict) else payload
    fixable: list[str] = []
    unfixable: list[str] = []
    for dep in deps:
        for vuln in dep.get("vulns") or []:
            fixes = vuln.get("fix_versions") or []
            line = (
                f"{dep.get('name')} {dep.get('version')} — {vuln.get('id')}"
                f"{' → fix in ' + ', '.join(fixes) if fixes else ' (no fix released)'}"
            )
            (fixable if fixes else unfixable).append(line)
    return fixable, unfixable


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python scripts/audit_deps.py <pip-audit-json>")
        return 2
    payload = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    fixable, unfixable = evaluate(payload)

    if not fixable and not unfixable:
        print("No known vulnerabilities.")
        return 0

    if unfixable:
        print(f"\n{len(unfixable)} advisory/advisories with NO released fix (not failing):")
        for line in unfixable:
            print(f"  - {line}")

    if fixable:
        print(f"\n{len(fixable)} FIXABLE advisory/advisories — bump these:")
        for line in fixable:
            print(f"  - {line}")
        print("\nFix in pyproject.toml, then: python scripts/generate_requirements.py")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
