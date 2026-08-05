"""Mechanical cross-platform portability guard (Part 1.6.C).

Two checks, both purely mechanical, mirroring lint_arch.py/lint_docs.py's
"a lint rule, not a suggestion" design:

1. Hardcoded-path guard. No *instructional* file (one a fresh clone or a new
   session actually reads to know how to run this project) may hardcode this
   machine's conda env name (`na180`) or an absolute path into it
   (`.../miniconda3/...`). Before Part 1.6.C, `.claude/launch.json` and
   `CLAUDE.md` both did this, so a clean checkout on another machine had no
   correct way to start the app without already knowing this developer's
   local paths.

   Deliberately NOT scanned: TODO.md, docs/plans/, docs/verification/ — these
   are append-only historical records (profiling output, dated checklists)
   that describe what a past command actually was, not an instruction for a
   future one. Rewriting them to hide the real historical path would be
   revisionist, not portability. See CLAUDE.md's "no adjectives, report
   numbers" ethos extended to history: a profiling report's captured
   `C:\\...\\na180\\...\\difflib.py` frame is real, measured data.

2. Portability-surface completeness. The files Part 1.6.C's from-scratch
   path depends on must all exist: .env.example, .python-version,
   scripts/bootstrap.py, Dockerfile, .dockerignore.

Usage: python scripts/lint_portability.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Instructional surfaces only — see the module docstring for what's
# deliberately excluded and why.
_SCANNED_GLOBS = [
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "User_Guide.md",
    ".claude/launch.json",
    "Dockerfile",
    ".dockerignore",
    "docker/*.yml",
    "docker/*.yaml",
    "scripts/*.py",
    "docs/architecture/*.md",
    "docs/decisions/*.md",
]

_BAD_PATTERNS = [
    # Path-shaped only — a bare mention of the word "na180" in prose
    # explaining this very rule (this file's own docstring, bootstrap.py's
    # "doesn't need na180" comment) is not a violation; an actual path into
    # a conda env, or any other user-specific absolute path, is.
    re.compile(r"miniconda3[/\\]envs"),
    re.compile(r"[A-Za-z]:[/\\]Users[/\\]\w+[/\\]"),  # any user-specific absolute path
]

_REQUIRED_FILES = [
    ".env.example",
    ".python-version",
    "scripts/bootstrap.py",
    "Dockerfile",
    ".dockerignore",
]


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for pattern in _SCANNED_GLOBS:
        if "*" in pattern:
            files.extend(sorted(ROOT.glob(pattern)))
        else:
            p = ROOT / pattern
            if p.exists():
                files.append(p)
    return files


def check_hardcoded_paths() -> list[str]:
    problems = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in _BAD_PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(ROOT)
                    problems.append(f"{rel}:{lineno}: machine-specific path — {line.strip()[:120]}")
                    break
    return problems


def check_required_files() -> list[str]:
    return [
        f"{f} is missing (required for the from-scratch path)"
        for f in _REQUIRED_FILES
        if not (ROOT / f).exists()
    ]


def main() -> int:
    problems = check_hardcoded_paths() + check_required_files()
    if problems:
        print(f"{len(problems)} portability issue(s):\n")
        for p in problems:
            print(f"  {p}")
        print(
            "\nInstructional files (CLAUDE.md, launch.json, README, Dockerfile, "
            "scripts/) must work for a clone that has never heard of this "
            "machine's conda env. If this is flagging a genuine historical "
            "record (a profiling report, TODO.md, docs/plans/), it shouldn't "
            "be in _SCANNED_GLOBS — see this script's module docstring."
        )
        return 1
    print("Portability guard: OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
