---
type: llm tracing
title: Langfuse Tracing
description: >
  Langfuse integration for merge-engine: shared callback handler on every LLM
  call, trace ID binding for HITL resume linkage, span nesting under graph
  nodes, and graceful degradation when Langfuse is unreachable.
tags: [llm, tracing, langfuse, observability]
---

# Langfuse Tracing

## Purpose

Integrates Langfuse for LLM call observability. Every LLM interaction through
`LLMClient` produces a generation span in Langfuse.

## File

`src/llm/tracing.py`

## Initialization

Sticky initialization with bounded retry for unreachable Langfuse stacks:

```python
def _initialize() -> None:
    """Initialize Langfuse callback handler.
    
    Retries up to 5 times with 1-second intervals if Langfuse host is unreachable.
    After max retries, sets _handler = None (tracing becomes a no-op).
    """
```

The initialization is idempotent — calling it multiple times is safe.

## Callback Handler

A shared `langchain_core.callbacks.BaseCallbackHandler` instance applied
globally to all LLM client calls:

```python
trace_config(thread_id: str) -> dict:
    """Build LangChain run config carrying the Langfuse callback handler."""
```

Every `ainvoke()` call on `LLMClient` passes this config, ensuring every LLM
call produces a trace.

## Trace Structure

```
Run: human_review_deps (graph node)
  └── Span: ChatOpenAI / ChatAnthropic (LLM call)
        └── Span: semantic cache check
```

Spans are nested under the owning graph-node span when invoked inside the
pipeline.

## HITL Resume Linkage

When a workflow is resumed after an interrupt, the trace ID is carried through
to link the resumed execution with the original run. This allows a full
workflow trace to be viewed in Langfuse as a single entity.

## Graceful Degradation

Tracing is a no-op when Langfuse is not configured or unreachable:

- Initialization failure → `_handler = None`
- Every `ainvoke()` works with or without the handler
- No exceptions propagate to the pipeline

## Operational Config

Langfuse runs as a separate self-hosted service in `docker/langfuse-compose.yml`.
The environment variables required are:

- `LANGFUSE_HOST` — Langfuse server URL
- `LANGFUSE_PUBLIC_KEY` — API public key
- `LANGFUSE_SECRET_KEY` — API secret key

## Test Coverage

- `tests/unit/test_tracing.py` — tracing setup and behavior
- `tests/unit/test_utils_tracing.py` — span context management
