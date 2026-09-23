"""The one verification command (Phase 0 Sec 0.2).

Before this script, there was no documented way to run ruff or mypy - an
agent (or a person) cannot self-verify against a gate with no invocation
path. Every subcommand here is also what CI runs, so "passes locally, fails
in CI" is a bug in this script, never a discrepancy to work around.

Design rule from the harness literature: success is silent, failure is
verbose. A passing check prints one line; a failing one prints the full
tool output so it feeds straight back into an agent's context without a
second round-trip.

Every stage runs under a hard 30-minute timeout and announces itself before
it starts. Both exist because `eval` once hung inside Splink's DuckDB
blocking and emitted nothing for 61 minutes, until GitHub killed the runner
with a bare exit 143 -- the log could not even say which check was stuck.
A hang is now a normal, named, verbose failure like any other.

Subcommands:
    lint        ruff check (src, tests, scripts)
    format      ruff format --check (src, tests, scripts)
    types       mypy src, ratcheted against mypy-baseline.txt
    arch        scripts/lint_arch.py, ratcheted against arch-lint-baseline.txt
    docs        scripts/lint_docs.py (broken links / stale file:line citations)
    portability scripts/lint_portability.py (no hardcoded conda/na180 paths; from-scratch files present)
    test        pytest tests/unit tests/integration (full unit + integration suite)
    test-fast   pytest tests/unit -x (stop on first failure, for tight loops)
    frontend    npm run typecheck && npm run build (src/ui/frontend)
    eval        python -m tests.eval.score (matcher precision/recall gate)
    prompts     python -m tests.prompts.harness (prompt golden-case scorer)
    all         every check above, in the order most likely to fail fast

NOTE on `format`: this codebase went through its one deliberate, whole-tree
`ruff format` pass (Phase 0 Sec 0.2/0.3) and every file is clean as of that
commit. `format` is a real, enforced gate from here on - a file that drifts
from ruff's formatting is a `check.py all` failure like any other, not a
warning.

NOTE on `test`: `tests/integration` was not part of this gate until Phase 0
Sec 0.7 - it had never been run in isolation, and doing so for the first
time surfaced a real, pre-existing circular import (postgresql_destination.py
-> src.mcp_server.tools.value_coercion -> tools/__init__.py -> load_data.py
-> postgresql_destination.py) plus a dozen stale mocks/assertions left over
from earlier refactors (sync .stream() -> async .astream(), a removed
load_config re-export, a moved value_coercion module, two files that were
manual driver scripts masquerading as pytest tests via __test__ = False).
All fixed and verified live; see arch-lint-baseline.txt's shrink from 18 to
17 for the value_coercion move specifically.

Usage: python scripts/check.py <subcommand> [<subcommand> ...]
       python scripts/check.py all
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
FRONTEND_DIR = ROOT / "src" / "ui" / "frontend"

# No stage may run forever. `eval` once blocked inside Splink's DuckDB
# blocking on a degenerate record set and produced NO output for 61 minutes,
# until GitHub killed the runner with exit 143 -- a silent hang is the worst
# possible CI failure mode, because the log says nothing at all. 30 min is
# far above the slowest real stage (`test`, ~2.5 min locally) and far below
# the hosted-runner limit, so a tripped timeout always means a genuine hang.
_STAGE_TIMEOUT_S = 1800


def _run(cmd: list[str], cwd: Path = ROOT, label: str = "") -> bool:
    name = label or " ".join(cmd)
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=_STAGE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        print(f"\n=== FAILED: {name} (no exit after {_STAGE_TIMEOUT_S}s - hung) ===")
        for stream in (exc.stdout, exc.stderr):
            if stream:
                print(stream.decode() if isinstance(stream, bytes) else stream)
        return False
    if result.returncode == 0:
        return True
    print(f"\n=== FAILED: {name} ===")
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return False


def check_lint() -> bool:
    return _run(
        [PYTHON, "-m", "ruff", "check", "src", "tests", "scripts"],
        label="ruff check",
    )


def check_format() -> bool:
    return _run(
        [PYTHON, "-m", "ruff", "format", "--check", "src", "tests", "scripts"],
        label="ruff format --check",
    )


def check_types() -> bool:
    try:
        result = subprocess.run(
            [PYTHON, "-m", "mypy", "src"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=_STAGE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print(f"\n=== FAILED: mypy (no exit after {_STAGE_TIMEOUT_S}s - hung) ===")
        return False
    baseline_path = ROOT / "mypy-baseline.txt"
    baseline = set()
    if baseline_path.exists():
        baseline = {
            line
            for line in baseline_path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        }

    import re

    line_num_re = re.compile(r"^(?P<file>[^:]+):\d+(?::\d+)?: (?P<rest>.*)$")

    new_errors = []
    for line in result.stdout.splitlines():
        if ": error:" not in line:
            continue
        m = line_num_re.match(line)
        if not m:
            continue
        normalized = f"{m.group('file')}: {m.group('rest')}"
        if normalized not in baseline:
            new_errors.append(line)

    if new_errors:
        print("\n=== FAILED: mypy (new errors not in mypy-baseline.txt) ===")
        for e in new_errors:
            print(e)
        print(
            f"\n{len(new_errors)} new type error(s). If these are genuine, fix "
            f"them. If the baseline itself is stale, regenerate it with "
            f"scripts/update_mypy_baseline.py — but only after reviewing what "
            f"changed, never to silently swallow a new error."
        )
        return False
    return True


def check_arch() -> bool:
    return _run([PYTHON, "scripts/lint_arch.py"], label="lint_arch")


def check_docs() -> bool:
    return _run([PYTHON, "scripts/lint_docs.py"], label="lint_docs")


def check_portability() -> bool:
    return _run([PYTHON, "scripts/lint_portability.py"], label="lint_portability")


def check_test() -> bool:
    return _run(
        [PYTHON, "-m", "pytest", "tests/unit", "tests/integration", "-q"],
        label="pytest tests/unit tests/integration",
    )


def check_test_fast() -> bool:
    return _run(
        [PYTHON, "-m", "pytest", "tests/unit", "-q", "-x"],
        label="pytest tests/unit -x",
    )


def check_frontend() -> bool:
    if not FRONTEND_DIR.exists():
        print("(no src/ui/frontend - skipping)")
        return True
    npm = shutil.which("npm")
    if npm is None:
        print("(npm not found on PATH - skipping frontend checks)")
        return True
    ok = _run([npm, "run", "typecheck"], cwd=FRONTEND_DIR, label="npm run typecheck")
    ok = _run([npm, "run", "build"], cwd=FRONTEND_DIR, label="npm run build") and ok
    return ok


def check_eval() -> bool:
    return _run([PYTHON, "-m", "tests.eval.score"], label="tests.eval.score")


# The four rules CLAUDE.md calls "mechanically enforced" lived only in
# .pre-commit-config.yaml, so they fired on `git commit` and nowhere else --
# `--no-verify` or a commit made by any tool that skips hooks slipped straight
# past them, and CI never ran them at all. Each one exists because of a real
# incident (a blanket *.csv line silently untracking the eval fixture; 6 GB of
# sample dumps nearly committed; .format() on a prompt full of JSON braces;
# except Exception: pass swallowing every failure), so "enforced" has to mean
# enforced on every commit, not just the ones made through a hooked checkout.
#
# These are all `language: system` hooks, so pre-commit runs them with no repo
# clone and no env install. Delegating to pre-commit rather than re-deriving
# each hook's file filter here keeps one filter, not two that drift apart.
_GUARD_HOOKS = (
    "no-blanket-data-ignore",
    "no-sample-databases-commit",
    "no-str-format-on-prompts",
    "no-new-bare-except-pass",
)


def check_guards() -> bool:
    ok = True
    for hook in _GUARD_HOOKS:
        ok = (
            _run(
                [PYTHON, "-m", "pre_commit", "run", "--all-files", hook],
                label=f"pre-commit {hook}",
            )
            and ok
        )
    return ok


def check_prompts() -> bool:
    return _run([PYTHON, "-m", "tests.prompts.harness"], label="tests.prompts.harness")


_SUBCOMMANDS = {
    "lint": check_lint,
    "format": check_format,
    "types": check_types,
    "arch": check_arch,
    "docs": check_docs,
    "portability": check_portability,
    "test": check_test,
    "test-fast": check_test_fast,
    "frontend": check_frontend,
    "eval": check_eval,
    "prompts": check_prompts,
    "guards": check_guards,
}

# Order for `all`: cheap/fast checks first, so a broken lint rule fails in
# seconds instead of after a multi-minute test run. `portability` is a pure
# grep over a handful of files — as cheap as arch/docs, so it sits with them.
# The much heavier venv-from-scratch and docker-build smoke tests (Part
# 1.6.C's actual boot proof) are deliberately NOT here — see docs/decisions/
# for why they run in CI instead of on every local `check.py all`.
_ALL_ORDER = [
    "lint",
    "format",
    "arch",
    "docs",
    "portability",
    "guards",
    "types",
    "frontend",
    "test",
    "eval",
    "prompts",
]


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1

    requested = _ALL_ORDER if argv == ["all"] else argv
    unknown = [c for c in requested if c not in _SUBCOMMANDS]
    if unknown:
        print(f"Unknown subcommand(s): {unknown}. Valid: {sorted(_SUBCOMMANDS)}")
        return 1

    failed: list[str] = []
    for name in requested:
        # Progress goes to the log BEFORE the stage runs. Without it a hung
        # stage is indistinguishable from a slow one: CI showed a 61-minute
        # blank gap and no way to tell which check was stuck.
        print(f"--> {name}", flush=True)
        started = time.monotonic()
        ok = _SUBCOMMANDS[name]()
        print(
            f"    {'ok' if ok else 'FAIL'} {name} ({time.monotonic() - started:.1f}s)", flush=True
        )
        if not ok:
            failed.append(name)

    if failed:
        print(f"\n{len(failed)}/{len(requested)} check(s) failed: {failed}")
        return 1
    print(f"All {len(requested)} check(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
