"""Continuous debt mitigation scanner (B5.5).

Scans for:
1. Architecture-lint findings via scripts/lint_arch.py & shrinkable arch-lint-baseline.txt entries.
2. Stale file:line citations and broken links via scripts/lint_docs.py.
3. Shrinkable entries in mypy-baseline.txt that no longer trigger errors in `mypy src`.

Usage:
    python scripts/lint_debt.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import lint_arch  # noqa: E402
import lint_docs  # noqa: E402

_LINE_NUM_RE = re.compile(r"^(?P<file>[^:]+):\d+(?::\d+)?: (?P<rest>.*)$")


def scan_arch_debt() -> list[str]:
    findings: list[str] = []
    errors: list[str] = []
    lint_arch.check_layering(errors)
    lint_arch.check_no_new_module_mutable_state(errors)
    lint_arch.check_no_module_scope_compile(errors)
    lint_arch.check_node_enter_exit_logging(errors)

    baseline_path = ROOT / "arch-lint-baseline.txt"
    if baseline_path.exists():
        baseline_lines = {
            line.strip()
            for line in baseline_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        current_errors = set(errors)
        shrinkable = baseline_lines - current_errors
        for item in sorted(shrinkable):
            findings.append(
                f"[arch-baseline-shrinkable] Can remove from arch-lint-baseline.txt: {item}"
            )

    return findings


def scan_doc_debt() -> list[str]:
    errors: list[str] = []
    lint_docs.check_links(errors)
    lint_docs.check_citations(errors)
    return [f"[doc-rot] {e}" for e in sorted(errors)]


def scan_mypy_debt() -> list[str]:
    findings: list[str] = []
    baseline_path = ROOT / "mypy-baseline.txt"
    if not baseline_path.exists():
        return findings

    baseline_lines = {
        line.strip()
        for line in baseline_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    if not baseline_lines:
        return findings

    py_bin = sys.executable
    result = subprocess.run(
        [py_bin, "-m", "mypy", "src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    active_errors = set()
    for line in result.stdout.splitlines():
        if ": error:" in line:
            m = _LINE_NUM_RE.match(line)
            if m:
                active_errors.add(f"{m.group('file')}: {m.group('rest')}")

    shrinkable = baseline_lines - active_errors
    for item in sorted(shrinkable):
        findings.append(f"[mypy-baseline-shrinkable] Can remove from mypy-baseline.txt: {item}")

    return findings


def main() -> int:
    arch_findings = scan_arch_debt()
    doc_findings = scan_doc_debt()
    mypy_findings = scan_mypy_debt()

    all_findings = arch_findings + doc_findings + mypy_findings
    if not all_findings:
        print("Debt scan complete — zero debt items or baseline regressions detected.")
        return 0

    print(f"=== Debt Mitigation Scanner: {len(all_findings)} finding(s) ===")
    for item in all_findings:
        print(f"  - {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
