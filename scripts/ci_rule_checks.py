"""CI rule checks for AGENTS.md rules C4 and J13 (cheap, K4-style).

    python scripts/ci_rule_checks.py stub-markers <base> <head>
        C4: fail when the change adds `TODO` or `FIXME` (whole word) to the
        project's source (src/**, so tests and docs are out of scope; that is
        where behaviour lives). `ponytail:` comments are allowed (C4).
        `XXX` is deliberately NOT matched: it is real data (ISO 4217 "no
        currency"). Only ADDED lines are checked, so existing markers cannot
        block CI; how many exist today is printed either way.

    python scripts/ci_rule_checks.py docs-line <base> <head>
        J13: fail when a non-merge commit in <base>..<head> touches src/**
        and its message has no line starting `Docs:` (e.g.
        `Docs: none, internal refactor`). Failure output shows the expected
        line and how to add it (`git commit --amend` before pushing).

Exit 0 with a clear notice when <base> is not a commit we can diff against
(new branch, force-push, shallow fetch): a CI that goes red for something
nobody can fix trains everyone to ignore the red badge (audit.yml's lesson).
"""

from __future__ import annotations

import re
import subprocess
import sys

# The project's source for both checks (C4/J13 share the scope, per prompt 4b).
SCOPE = "src/"
_MARKER_RE = re.compile(r"\b(TODO|FIXME)\b")
_ZERO_SHA = "0" * 40


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return done.stdout


def _exists(rev: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{rev}^{{commit}}"],
            capture_output=True,
        ).returncode
        == 0
    )


def _existing_marker_count() -> int:
    """How many markers exist today in scope (report, never block)."""
    try:
        out = _git("grep", "-nE", r"\b(TODO|FIXME)\b", "--", SCOPE)
    except subprocess.CalledProcessError:  # git grep exits 1 on no match
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def stub_markers(base: str, head: str) -> int:
    print(f"C4 stub-marker check: added lines in {SCOPE} between {base[:8]} and {head[:8]}")
    print(f"  markers already in scope today (not blocking): {_existing_marker_count()}")
    if not base or base == _ZERO_SHA or not _exists(base):
        print(
            "  base commit not available (new branch or shallow fetch) — nothing to compare; passing."
        )
        return 0
    diff = _git("diff", "--unified=0", f"{base}..{head}", "--", SCOPE)
    offenders: list[str] = []
    current_file = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and _MARKER_RE.search(line):
            offenders.append(f"{current_file}: {line[1:].strip()[:120]}")
    if offenders:
        print(f"\n  {len(offenders)} added TODO/FIXME marker(s) in {SCOPE} (rule C4):")
        for o in offenders:
            print(f"    {o}")
        print("\n  C4 forbids TODO/FIXME for missing behaviour. Implement it, or — if the")
        print("  code works and you are naming a known limit — use a `ponytail:` comment")
        print("  naming the limit and the upgrade path.")
        return 1
    print("  no added TODO/FIXME markers. OK.")
    return 0


def docs_line(base: str, head: str) -> int:
    print(f"J13 Docs:-line check: non-merge commits in {base[:8]}..{head[:8]}")
    if not base or base == _ZERO_SHA or not _exists(base):
        print(
            "  base commit not available (new branch or shallow fetch) — nothing to compare; passing."
        )
        return 0
    commits = _git("rev-list", "--no-merges", f"{base}..{head}").split()
    offenders: list[str] = []
    checked = 0
    for sha in commits:
        files = _git("show", "--name-only", "--format=", sha).splitlines()
        if not any(f.startswith(SCOPE) for f in files):
            continue
        checked += 1
        message = _git("log", "-1", "--format=%B", sha)
        if not any(line.startswith("Docs:") for line in message.splitlines()):
            short = sha[:8]
            subject = message.splitlines()[0] if message.splitlines() else ""
            offenders.append(f'  {short} "{subject}" touches {SCOPE} but has no `Docs:` line')
    if offenders:
        print(f"\n  {len(offenders)} of {checked} {SCOPE}-touching commit(s) missing the J13")
        print("  docs decision line. Add one line anywhere in the commit message, e.g.:")
        print("      Docs: none, internal refactor")
        print("      Docs: TECHNICAL_IMPLEMENTATION.md, Workflows")
        print("  Before pushing, amend:  git commit --amend   (then force-push this branch)")
        for o in offenders:
            print(o)
        return 1
    print(f"  {checked} {SCOPE}-touching commit(s), all carry a Docs: line. OK.")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("stub-markers", "docs-line"):
        print(__doc__)
        return 2
    base, head = argv[1], argv[2]
    return {"stub-markers": stub_markers, "docs-line": docs_line}[argv[0]](base, head)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
