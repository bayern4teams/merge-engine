"""pre-commit hook: reject `.format(` applied to a PROMPT_TEMPLATES value.

Prompt substitution is literal str.replace(), never str.format() (see
src/llm/__init__.py's module docstring and analyze_dependencies.py:124-125
for why): prompts contain literal JSON braces in their worked examples and
output-contract descriptions, so .format() raises on them - and a lost
placeholder token fails *silently* if the exception is ever swallowed. This
is a hard constraint, not a style preference.

Detects the common inline form `PROMPT_TEMPLATES[...][...].format(...)`.
It does not trace a template through an intermediate variable to a later
.format() call - a real static-analysis pass would need type inference this
repo doesn't have. That gap is accepted; this catches the common case.

Usage: python -m scripts.hooks.no_str_format_on_prompts <files...>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PATTERN = re.compile(r"PROMPT_TEMPLATES\s*\[[^\]]+\]\s*\[[^\]]+\]\s*\.format\(")


def main(argv: list[str]) -> int:
    bad = False
    for arg in argv:
        path = Path(arg)
        if path.suffix != ".py" or not path.exists() or "scripts/hooks" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _PATTERN.search(line):
                print(
                    f"{path}:{lineno}: prompt substitution must be "
                    f"str.replace(), never .format() - prompts contain "
                    f"literal JSON braces and .format() will raise (or, if "
                    f"caught, silently drop a placeholder token). Use "
                    f".replace('{{token}}', value) instead."
                )
                bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
