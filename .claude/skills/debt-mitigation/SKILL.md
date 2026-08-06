---
name: debt-mitigation
description: Scan for architectural lint debt, stale file:line citations/links in docs, and shrinkable entries in mypy-baseline.txt.
---

# Debt Mitigation Skill

Scans the repository for continuous tech debt:
1. Architecture lint deviations (`scripts/lint_arch.py`) and shrinkable entries in `arch-lint-baseline.txt`.
2. Stale `file:line` citations or broken markdown links in `docs/` and `CLAUDE.md` (`scripts/lint_docs.py`).
3. Resolved type errors in `mypy-baseline.txt` that can be safely removed to tighten the mypy ratchet.

## Invocation Command
```bash
python scripts/lint_debt.py
```

## Description
Use this skill continuously to maintain a clean codebase, shrink baselines as debt is paid down, and prevent doc rot.
