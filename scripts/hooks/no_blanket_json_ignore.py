"""pre-commit hook: reject a bare `*.json` line in .gitignore.

A blanket `*.json` line silently untracked src/ui/frontend/package.json (and
package-lock.json, tsconfig.json), so a fresh clone could not `npm install`
with no error message explaining why. .gitignore's json rules must always be
scoped to a specific path.

Usage (pre-commit passes the changed file(s)): python -m scripts.hooks.no_blanket_json_ignore .gitignore
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    bad = False
    for arg in argv:
        path = Path(arg)
        if path.name != ".gitignore" or not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped in ("*.json", "**/*.json", "/*.json"):
                print(
                    f"{path}:{lineno}: bare '{stripped}' would blanket-ignore "
                    f"every .json file in the repo, including "
                    f"src/ui/frontend/package.json - a fresh clone could not "
                    f"npm install. Scope it to a specific path instead "
                    f"(e.g. 'docs/verification/*.json')."
                )
                bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
