"""pre-commit hook: reject any staged path under sample_databases/.

6 GB of SQL dumps nearly went into git once already. sample_databases/ is
gitignored for exactly this reason; this hook is the second layer in case a
future -f/--force add bypasses the ignore.

Usage (pre-commit passes the staged files): python -m scripts.hooks.no_sample_databases_commit <files...>
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    bad = [a for a in argv if a.replace("\\", "/").split("/")[0] == "sample_databases"]
    if bad:
        for f in bad:
            print(
                f"{f}: sample_databases/ holds multi-GB seed-data dumps and "
                f"must never be committed - it is gitignored on purpose. If "
                f"this was force-added, unstage it."
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
