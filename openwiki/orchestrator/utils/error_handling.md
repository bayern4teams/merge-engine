---
type: utility
title: Error Handling
description: Typed error taxonomy for merge-engine, including the MergeEngineError base class with TransientError, PermanentError, DataQualityError, and UserInputError subclasses. Exception classification drives per-node retry policy and error gate routing.
tags: [error-handling, retry, error-taxonomy, resilience]
---

# Error Handling

## Purpose

Provides a typed error taxonomy that drives the pipeline's retry policy and error gate routing. The central principle is: nothing in the pipeline should catch a bare `Exception` and guess — call `classify_exception()` and let the class decide.

## File

`src/orchestrator/utils/retry.py` (~3.4KB)

## Error Taxonomy

```python
class MergeEngineError(RuntimeError):
    """Base for every failure the pipeline classifies and then acts on."""

    disposition: str = "fail"  # "retry", "escalate", or "fail"
    retryable: bool = False
```

### TransientError

```python
class TransientError(MergeEngineError):
    disposition = "retry"
    retryable = True
    retry_after: float | None = None  # Optional: when to retry
```

A recoverable failure: network blip, timeout, rate limit, deadlock. Retrying the identical call is expected to eventually succeed.

### PermanentError

```python
class PermanentError(MergeEngineError):
    disposition = "fail"
```

A failure that retrying cannot fix: bad SQL, auth refused, missing table. Retrying wastes time and hides the real cause.

### DataQualityError

```python
class DataQualityError(MergeEngineError):
    disposition = "escalate"
    entity: str | None = None
    origin_id: str | None = None
    table: str | None = None
    rows_affected: int | None = None
```

The pipeline reached data it cannot honestly process. An unparseable row, a value that will not fit its destination column, a table that returned fewer rows than it claimed. Escalates to a human instead of silently continuing.

### UserInputError

```python
class UserInputError(MergeEngineError):
    disposition = "fail"
```

The user provided invalid input (e.g., an unrecognizable request that the LLM cannot parse into entities).

## Exception Classification

`classify_exception(exc: Exception) -> MergeEngineError` converts raw exceptions (from database drivers, LLM clients, HTTP libraries) into the taxonomy. Heuristic-based: network timeouts → TransientError, auth errors → PermanentError, data format issues → DataQualityError.

Anything that is not explicitly wrapped gets judged on its type's heuristic.

## Retry Policy Integration

The `NETWORK_RETRY` retry policy in `graph.py` uses `_is_transient(exc)` which calls `classify_exception(exc)` and returns True only for `TransientError`. A raw driver exception that nobody thought to wrap still gets judged on its merits.

## State Keys

| Key | Type | Reducer | Purpose |
|-----|------|---------|---------|
| `errors` | `list[str]` | `operator.add` | All errors that occurred |
| `failure` | `dict[str, Any]` | replace | Current open failure (node, error, disposition, context) |
| `handled_failures` | `list[str]` | `operator.add` | Failures dispositioned by a human (retry/skip) |
| `retry_counts` | `dict[str, int]` | replace | Per-node retry attempt count |

## Retry Hints Bridge

See [Retry Hints](/openwiki/orchestrator/utils/retry_hints.md) for how retry feedback is bridged into LLM prompts.

## Test Coverage

- `tests/unit/test_retry.py` — exception classification, retry policy application
- `tests/unit/test_phase_m_resilience.py` — resilience against all error types (24KB)

## Cross-References

| Concern | Page |
|---------|------|
| Retry policy | [State Graph](/openwiki/orchestrator/graph.md) |
| Retry hints | [Retry Hints](/openwiki/orchestrator/utils/retry_hints.md) |
| Error gate | [Human-in-the-Loop](/openwiki/orchestrator/nodes/human_in_the_loop.md) |
