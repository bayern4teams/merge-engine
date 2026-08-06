# Planner / Generator / Evaluator Split Process

## 1. Core Principle

Self-evaluation in autonomous coding agents creates systematic confirmation bias — agents consistently overestimate the correctness of their own implementations and gloss over subtle regressions.

To prevent silent failures, this codebase strictly enforces the **Planner / Generator / Evaluator Split**:
> **The agent or context that implements a change is NEVER the agent that grades it.**

Both roles communicate exclusively through the **filesystem** (code files, verification reports, diff dumps, and database states), never through shared in-memory context or prompt claims.

---

## 2. Role Definitions

### 2.1 Planner & Generator (Implementer)
- **Responsibility**: Researches task context, proposes design changes, writes code, updates documentation, and executes required build steps.
- **Output**: Code edits committed to the worktree, output artifacts, and candidate outputs saved in the filesystem.
- **Constraint**: Cannot certify task completion or check off TODO items based on self-reported success messages.

### 2.2 Evaluator (Grader)
- **Responsibility**: Operates independently with a clean context, executes verification suites (`scripts/check.py all`, `drive_app.py --assert-budgets`, `compare_to_golden.py`), inspects underlying databases and log streams directly.
- **Output**: Numeric, empirical verification verdicts (`PASS` / `FAIL`) logged to `docs/verification/`.
- **Constraint**: Must use independent verification tools (e.g. direct SQL queries, JSON diff dumps, cProfile outputs) rather than relying on status responses from the app or generator.

---

## 3. Inter-Agent Coordination Protocol

```
+-------------------+              Filesystem              +-------------------+
|                   |  ==== Writes Code & Artifacts ===>   |                   |
| Planner /         |                                      | Evaluator Agent   |
| Generator Agent   |  <=== Reads Independent Verdict ==== | (e.g. verifier)   |
|                   |       & Metric Reports               |                   |
+-------------------+                                      +-------------------+
```

1. **Filesystem as Boundary**: The generator saves all code changes and execution results to disk.
2. **Clean Context Invocation**: The evaluator is spawned in a separate, isolated context (such as the `merge-verifier` or `perf-profiler` subagents in `.claude/agents/`).
3. **Empirical Evidence Requirement**: Every check mark in `TODO.md` requires independent filesystem evidence recorded per Rule R9.
