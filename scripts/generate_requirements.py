"""Regenerate requirements.txt and requirements-dev.txt from pyproject.toml.

Phase 0 Sec 0.1: pyproject.toml is the single source of truth for
dependencies. requirements.txt/requirements-dev.txt are generated artifacts
for tooling that still wants a flat file (Docker builds, some IDEs) - never
hand-edit them, and never add a dependency only to one of the two files.

Usage: python scripts/generate_requirements.py           # rewrite both files
       python scripts/generate_requirements.py --check   # fail if stale

--check is what makes "never hand-edit them" a rule instead of a comment.
The files had silently drifted both ways: networkx and python-dotenv were
missing (the Dockerfile installs from requirements.txt, so the image
ImportErrored on networkx), while a deleted connector's driver was still
being installed.
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


def _render() -> dict[Path, str]:
    """The single source of both writing and checking - one render path, so
    --check can never disagree with what a regenerate would produce."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    deps = project["dependencies"]
    dev_deps = project.get("optional-dependencies", {}).get("dev", [])
    return {
        ROOT / "requirements.txt": HEADER + "\n" + "\n".join(deps) + "\n",
        ROOT / "requirements-dev.txt": (
            HEADER
            + "# Also install the runtime deps: pip install -r requirements.txt\n\n"
            + "\n".join(dev_deps)
            + "\n"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    rendered = _render()

    if "--check" in argv:
        stale = [
            path
            for path, want in rendered.items()
            if not path.exists() or path.read_text(encoding="utf-8") != want
        ]
        if stale:
            names = ", ".join(p.name for p in stale)
            print(
                f"{names} is out of sync with pyproject.toml.\n"
                "These are generated files and the Dockerfile installs from "
                "requirements.txt, so drift ships a broken image.\n"
                "Fix: python scripts/generate_requirements.py"
            )
            return 1
        print("requirements.txt and requirements-dev.txt are in sync with pyproject.toml.")
        return 0

    for path, content in rendered.items():
        path.write_text(content, encoding="utf-8")
    counts = {p.name: content.count("\n") for p, content in rendered.items()}
    print(f"Wrote {counts}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
