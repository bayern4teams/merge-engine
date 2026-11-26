"""Mechanical doc-rot detection (Phase 0 Sec 0.4).

Per OpenAI's harness-engineering finding: "linters and CI verify cross-links
remain intact." This repo's docs and memory files are dense with relative
links and `path/to/file.py:NNN` citations, and they rot faster than
anything else in the repo. Two checks, both purely mechanical:

1. Every relative markdown link (`[text](path)`) in docs/ and CLAUDE.md
   resolves to an existing file (relative to the linking file's directory).
   http(s):// links and mailto: are skipped - only repo-relative links are
   checked.
2. Every `path/to/file.py:NNN` citation points at a file that exists and
   has at least NNN lines.

Usage: python scripts/lint_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_CITATION_RE = re.compile(
    r"\b((?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|js|jsx|yaml|yml|json|toml)):(\d+)\b"
)
# Only validate citations that plausibly point into this repo (first path
# segment is a real top-level dir, or a dot-prefixed one like .claude) -
# otherwise a citation into an installed third-party package
# (pytest_playwright/pytest_playwright.py:112, explaining an environment
# quirk) gets misread as repo doc-rot.
_REPO_TOP_LEVEL = {
    "src",
    "tests",
    "scripts",
    "docs",
    "config",
    "docker",
    "sample_databases",
    "claude",
}


def _scan_files() -> list[Path]:
    files = list((ROOT / "docs").rglob("*.md"))
    for name in ("CLAUDE.md", "AGENTS.md", "README.md"):
        f = ROOT / name
        if f.exists():
            files.append(f)
    return files


def check_links(errors: list[str]) -> None:
    for md_file in _scan_files():
        text = md_file.read_text(encoding="utf-8")
        for match in _LINK_RE.finditer(text):
            target = match.group(1).split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (md_file.parent / target).resolve()
            if not resolved.exists():
                line_no = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{md_file.relative_to(ROOT)}:{line_no}: broken link "
                    f"'{target}' -> {resolved} does not exist."
                )


def _resolve_citation_path(rel_path: str) -> Path | None:
    """Resolve a citation to a real file, tolerating the repo's common
    shorthand of dropping a leading src/ or a leading dot-dir (e.g. citing
    'tools/load_data.py' for 'src/mcp_server/tools/load_data.py', or
    'claude/settings.local.json' for '.claude/settings.local.json')."""
    direct = (ROOT / rel_path).resolve()
    if direct.exists():
        return direct
    suffix_matches = [
        p
        for p in ROOT.rglob(rel_path)
        if "__pycache__" not in p.parts and "node_modules" not in p.parts
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    dotted = ROOT / ("." + rel_path)
    if dotted.exists():
        return dotted
    return None


def check_citations(errors: list[str]) -> None:
    for md_file in _scan_files():
        text = md_file.read_text(encoding="utf-8")
        for match in _CITATION_RE.finditer(text):
            rel_path, line_str = match.group(1), match.group(2)
            if rel_path.split("/")[0] not in _REPO_TOP_LEVEL:
                continue
            candidate = _resolve_citation_path(rel_path)
            if candidate is None:
                line_no = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{md_file.relative_to(ROOT)}:{line_no}: stale citation "
                    f"'{rel_path}:{line_str}' - no file matching {rel_path} "
                    f"exists anywhere in the repo."
                )
                continue
            try:
                actual_lines = sum(1 for _ in candidate.open(encoding="utf-8"))
            except UnicodeDecodeError:
                continue
            cited_line = int(line_str)
            if cited_line > actual_lines:
                line_no = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{md_file.relative_to(ROOT)}:{line_no}: stale citation "
                    f"'{rel_path}:{cited_line}' - {rel_path} only has "
                    f"{actual_lines} lines now."
                )


def main() -> int:
    errors: list[str] = []
    check_links(errors)
    check_citations(errors)

    if errors:
        for e in sorted(errors):
            print(e)
        print(f"\n{len(errors)} doc-rot issue(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
