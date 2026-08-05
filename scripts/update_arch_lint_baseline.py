"""Regenerate arch-lint-baseline.txt from the current state of lint_arch.py.

Same ratchet as scripts/update_mypy_baseline.py: records every currently-
tolerated architectural finding so scripts/lint_arch.py fails only on NEW
violations. The baseline must only shrink - run this after fixing a
finding (to record the shrinkage), never to silently swallow a new one.

Usage: python scripts/update_arch_lint_baseline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_arch

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "arch-lint-baseline.txt"


def main() -> int:
    errors: list[str] = []
    lint_arch.check_layering(errors)
    lint_arch.check_no_new_module_mutable_state(errors)
    lint_arch.check_no_module_scope_compile(errors)
    lint_arch.check_node_enter_exit_logging(errors)

    BASELINE_PATH.write_text(
        "# GENERATED FILE - regenerate with: python scripts/update_arch_lint_baseline.py\n"
        "# This baseline may only shrink. A PR that grows it needs a reason in\n"
        "# its description, not a silent bump. All remaining entries are the\n"
        "# same reverse-import case: relocating src/orchestrator/utils/*\n"
        "# pure-utility modules (retry, retry_hints, entity_aliases) down into\n"
        "# src/utils/ so src/mcp_server/* no longer reaches upward for them.\n"
        + "\n".join(sorted(errors))
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(errors)} baseline entries to {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
