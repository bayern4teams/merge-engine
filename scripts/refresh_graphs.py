"""Rebuild the three code graphs agents read before touching source.

One definition of "refresh the graphs", called from two places -- the
post-commit hook and scripts/bootstrap.py -- so the fresh-clone build and the
per-commit update cannot drift apart.

Every command here is the tool's own no-LLM path. None of them call a model,
none need an API key, and none cost anything:

    graphify update . --no-cluster   "re-extract code files ... (no LLM needed)"
    graft build .                    "wiring graph + per-file cards ($0, no key)"
    codebase-memory-mcp index_repository

Deliberately NOT used: `graphify extract --mode deep`, `graphify label`
(community naming), and `graft build --deep`. Those are the LLM paths.

All three outputs are gitignored. They are local caches rebuilt in ~8s, not
artifacts to commit -- graphify-out/ alone was 750k lines of tracked JSON,
three quarters of the repo, and one update rewrites most of it.

Usage:
    python scripts/refresh_graphs.py              # run all three, in order
    python scripts/refresh_graphs.py --detach     # spawn and return immediately
    python scripts/refresh_graphs.py --if-missing # only build what isn't there
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / ".tmp" / "refresh_graphs.log"
STAMP = ROOT / ".tmp" / "refresh_graphs.stamp"

# graft 0.18.0 finished its work, printed this line, and THEN aborted with
# SIGABRT (exit 134) every single run -- output fully written and correct each
# time. 0.19.0 fixed it and exits 0, so this is now a safety net rather than a
# live workaround: it keeps a teammate still on 0.18.x from getting a red
# refresh after every commit, which is how a red signal becomes one people
# ignore. A genuine graft failure prints no marker and is still caught.
_GRAFT_OK_MARKER = "✓ wiring:"

# (tool, argv, output path proving it ran, stdout marker that overrides exit)
_GRAPHS: list[tuple[str, list[str], Path, str]] = [
    ("graphify", ["update", ".", "--no-cluster"], ROOT / "graphify-out" / "graph.json", ""),
    ("graft", ["build", "."], ROOT / "graft", _GRAFT_OK_MARKER),
    (
        "codebase-memory-mcp",
        ["cli", "--quiet", "index_repository", '{"repo_path":"."}'],
        # Indexed into the tool's own store outside the repo, so there is no
        # in-tree path to test. Always rebuilt; it is the fastest of the three.
        Path(),
        "",
    ),
]

# A graph rebuild must never be what breaks someone's commit. The hook runs
# after the commit already landed, so there is nothing to roll back -- the
# worst honest outcome is a stale graph and a line in the log.
_TIMEOUT_S = 600


def _head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def refresh(*, if_missing: bool = False) -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []

    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} HEAD={_head()[:8]} ===\n")

        for tool, args, proof, marker in _GRAPHS:
            exe = shutil.which(tool)
            if exe is None:
                # Not an error: a clone without the tooling still works, it
                # just falls further down the lookup ladder in CLAUDE.md.
                log.write(f"skip {tool}: not on PATH\n")
                continue
            if if_missing and proof != Path() and proof.exists():
                log.write(f"skip {tool}: {proof.name} already present\n")
                continue

            started = time.monotonic()
            try:
                done = subprocess.run(
                    [exe, *args],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                log.write(f"FAIL {tool}: no exit after {_TIMEOUT_S}s\n")
                failed.append(tool)
                continue
            except OSError as exc:
                log.write(f"FAIL {tool}: {exc}\n")
                failed.append(tool)
                continue

            elapsed = time.monotonic() - started
            aborted_but_built = (
                done.returncode != 0 and bool(marker) and marker in (done.stdout or "")
            )
            if done.returncode == 0:
                log.write(f"ok   {tool} ({elapsed:.1f}s)\n")
            elif aborted_but_built:
                log.write(
                    f"ok   {tool} ({elapsed:.1f}s) "
                    f"[exit {done.returncode} on shutdown, output verified]\n"
                )
            else:
                # Tail only: graft prints 270 progress lines, and a full dump
                # of a failing run buries the error that matters.
                tail = "\n".join((done.stdout or "").splitlines()[-5:])
                err = "\n".join((done.stderr or "").splitlines()[-15:])
                log.write(f"FAIL {tool} (exit {done.returncode})\n{tail}\n{err}\n")
                failed.append(tool)

        if failed:
            log.write(f"failed: {', '.join(failed)}\n")
        else:
            STAMP.write_text(_head(), encoding="utf-8")
            log.write("all graphs refreshed\n")

    return 1 if failed else 0


def _detach() -> int:
    """Re-spawn ourselves without --detach and return at once.

    The commit has already landed by the time the hook fires, so blocking the
    terminal for ~8s buys nothing. stdin/stdout/stderr go to devnull because
    a background writer on the terminal would scribble over the next prompt;
    everything lands in LOG instead.
    """
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(os.devnull, "rb") as devnull_in, open(os.devnull, "wb") as devnull_out:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=ROOT,
            stdin=devnull_in,
            stdout=devnull_out,
            stderr=devnull_out,
            start_new_session=True,
        )
    return 0


def main(argv: list[str]) -> int:
    if "--detach" in argv:
        return _detach()
    return refresh(if_missing="--if-missing" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
