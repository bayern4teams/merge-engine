# The Harness Ratchet Process

## 1. Core Principle (Rule R11)
> **Every failure becomes a rule.**

When any branch hits a real bug, failure, or unexpected constraint during development or verification:
1. Identify the underlying root cause rather than applying a superficial patch.
2. Formulate a mechanical enforcement rule (a linter in `scripts/lint_*.py`, a pre-commit hook in `.pre-commit-config.yaml`, or a hard constraint in `CLAUDE.md`).
3. Ensure every line in `CLAUDE.md` remains traceable to the failure or decision that created it.

---

## 2. Ratchet Lifecycle

```
[ Real Failure / Incident ]
            │
            ▼
 [ Root Cause Analysis ] ──> Record decision in docs/decisions/
            │
            ▼
[ Mechanical Rule Added ] ──> Create hook or script rule in scripts/lint_*.py
            │
            ▼
[ Traceable CLAUDE.md Line ] ──> Document under "Rules that are mechanically enforced"
```

---

## 3. Mandatory Rules for Adding Ratchet Constraints
- **No Prompt-Only Rules**: A instruction in markdown is a suggestion; only code/linter/hook failures constitute a rule.
- **Traceability**: Every constraint in `CLAUDE.md` must link to its historical rationale or ADR.
- **Shrink-Only Baselines**: Baseline files (`mypy-baseline.txt`, `arch-lint-baseline.txt`) may only shrink over time.
