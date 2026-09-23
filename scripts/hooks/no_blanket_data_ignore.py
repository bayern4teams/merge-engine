"""pre-commit hook: reject a bare `*.json` / `*.csv` / `*.tsv` line in .gitignore.

A blanket `*.json` line silently untracked src/ui/frontend/package.json (and
package-lock.json, tsconfig.json), so a fresh clone could not `npm install`
with no error message explaining why.

The identical bug then recurred with `*.csv`, two lines below .gitignore's own
comment warning about the `*.json` case: it silently untracked
tests/eval/pagila_staff.csv, so `tests.eval.golden.load_staff()` fell back to a
2-row stub, the eval record set collapsed to 3 distinct names, and Splink's
blocking never returned -- hanging `scripts/check.py eval`, and with it every
GitHub Actions run, until the runner was killed an hour later.

A data-file ignore must always be scoped to a specific path.

Usage (pre-commit passes the changed file(s)): python -m scripts.hooks.no_blanket_data_ignore .gitignore
"""

from __future__ import annotations

import sys
from pathlib import Path

# Extensions that have actually bitten this repo, plus .tsv -- the format the
# ground-truth caches use, i.e. the next one someone would reach for a blanket
# rule on. Deliberately not "every extension": a rule nobody trips is noise.
_GUARDED = ("json", "csv", "tsv")

_WHY = {
    "json": "including src/ui/frontend/package.json - a fresh clone could not npm install",
    "csv": "including tests/eval/pagila_staff.csv - check.py eval then hangs forever on the stub dataset",
    "tsv": "including any tracked .tsv fixture",
}


def main(argv: list[str]) -> int:
    bad = False
    banned = {f"{prefix}*.{ext}": ext for ext in _GUARDED for prefix in ("", "**/", "/")}
    for arg in argv:
        path = Path(arg)
        if path.name != ".gitignore" or not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            ext = banned.get(line.strip())
            if ext is None:
                continue
            print(
                f"{path}:{lineno}: bare '{line.strip()}' would blanket-ignore "
                f"every .{ext} file in the repo, {_WHY[ext]}. Scope it to a "
                f"specific path instead (e.g. 'docs/verification/*.{ext}')."
            )
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
