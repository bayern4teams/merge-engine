"""Regenerate requirements.txt and requirements-dev.txt from pyproject.toml.

Phase 0 Sec 0.1: pyproject.toml is the single source of truth for
dependencies. requirements.txt/requirements-dev.txt are generated artifacts
for tooling that still wants a flat file (Docker builds, some IDEs) - never
hand-edit them, and never add a dependency only to one of the two files.

Usage: python scripts/generate_requirements.py
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = (
    "# GENERATED FILE - do not hand-edit.\n"
    "# Source of truth is pyproject.toml; regenerate with:\n"
    "#   python scripts/generate_requirements.py\n"
)


def main() -> int:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    deps = project["dependencies"]
    (ROOT / "requirements.txt").write_text(HEADER + "\n" + "\n".join(deps) + "\n", encoding="utf-8")

    dev_deps = project.get("optional-dependencies", {}).get("dev", [])
    (ROOT / "requirements-dev.txt").write_text(
        HEADER
        + "# Also install the runtime deps: pip install -r requirements.txt\n\n"
        + "\n".join(dev_deps)
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote requirements.txt ({len(deps)} deps) and "
        f"requirements-dev.txt ({len(dev_deps)} deps)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
