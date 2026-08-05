"""pre-commit hook: reject a NEWLY ADDED `except Exception:\n    pass` block.

`_get_workflow_summary` once swallowed every failure into `{}` this way -
whatever went wrong was invisible until a user reported "nothing happened."
Existing instances aren't retroactively blocked (that would make this hook
unusable on day one); only a block that is new relative to HEAD fails.
contextlib.suppress(...) is the same swallow but explicit about which
exceptions and why - it is not flagged.

Usage: python -m scripts.hooks.no_new_bare_except_pass <files...>
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_PATTERN = re.compile(r"except\s+Exception\s*(?:as\s+\w+\s*)?:\s*\n\s*pass\b")


def _block_count(text: str) -> int:
    # Count only, not exact text: a pure reformat (e.g. ruff dropping an
    # unused `as e` binding, or re-indenting) changes the matched substring
    # without adding a genuinely new swallow - comparing counts instead of
    # set membership is immune to that whitespace/binding-name churn.
    return len(_PATTERN.findall(text))


def _head_content(path: Path) -> str:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path.as_posix()}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout if result.returncode == 0 and result.stdout else ""


def main(argv: list[str]) -> int:
    bad = False
    for arg in argv:
        path = Path(arg)
        if path.suffix != ".py" or not path.exists():
            continue
        current = path.read_text(encoding="utf-8")
        current_count = _block_count(current)
        if not current_count:
            continue
        head_count = _block_count(_head_content(path))
        if current_count > head_count:
            print(
                f"{path}: {current_count - head_count} new "
                f"'except Exception: pass' block(s) - a swallowed exception "
                f"with no logging makes a real failure invisible. Log it "
                f"(logger.warning/error) or use "
                f"contextlib.suppress(SpecificException) if the swallow is "
                f"genuinely intentional."
            )
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
