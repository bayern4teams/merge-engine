---
type: orchestrator node
title: Preflight Node
description: Validates run prerequisites including database connectivity, LLM availability, and disk space before the pipeline begins. Produces warnings (non-blocking) and errors (blocking) that can escalate to human review.
tags: [node, preflight, validation, prerequisites]
---

# Preflight Node

## Purpose

Validates all run prerequisites before the pipeline begins execution. Checks database connectivity, LLM availability, and disk space. Produces warnings (non-blocking) and errors (blocking) that feed into the error handling gate.

## File

`src/orchestrator/nodes/preflight.py` (~16KB)

## Checks Performed

1. **Origin database connectivity** — attempts to connect to each origin database (PostgreSQL, ClickHouse, MongoDB) using their connection configs. Failure produces a `PermanentError` with `disposition: "fail"` (unrecoverable).
2. **Destination database connectivity** — checks the destination is reachable. Same error disposition as origins.
3. **LLM availability** — makes a lightweight call to the configured LLM (with fallback model chain). A `TransientError` is raised on network failures, a `PermanentError` on quota/billing errors.
4. **Disk space** — checks available disk space for the spool directory (`logs/spool/`) and checkpoint database (`data/checkpoints.db`). Low space produces a warning but does not block the run.

## Error Disposition

| Error Type | Disposition | Behavior |
|------------|-------------|----------|
| Database unreachable | `fail` | Abort immediately |
| Auth refused | `fail` | Abort immediately |
| LLM quota exceeded | `fail` | Abort (same-account retry won't help) |
| Low disk space | Warning | Non-blocking warning in `preflight_report` |
| Checkpoint DB unavailable | Warning | Falls back to in-memory checkpointer |

## State

Produces `MergeState.preflight_report` dict:

```python
{
    "origins_ok": True / False,
    "destination_ok": True / False,
    "llm_ok": True / False,
    "warnings": ["list of warning strings"],
    "errors": ["list of error strings"],
}
```

The `has_low_confidence_clusters` conditional edge reads this to determine if the pipeline should proceed or stop.

## Retry Policy

Carries `NETWORK_RETRY` — database and LLM calls can transiently fail.

## Test Coverage

- `tests/unit/` — preflight validation is tested through the full pipeline integration tests

## Cross-References

| Concern | Page |
|---------|------|
| Graph wiring | [State Graph](/openwiki/orchestrator/graph.md) |
| Error taxonomy | [Error Handling](/openwiki/orchestrator/utils/error_handling.md) |
| Retry policy | [State Graph](/openwiki/orchestrator/graph.md) |
