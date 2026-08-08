---
type: utility
title: Span Tracing
description: >
  Generic span context manager for connector-level tracing using LangChain's
  runnables config. Not raw OTel — spans are LangChain callback-based.
tags: [utils, tracing, spans, langchain]
---

# Span Tracing

## Purpose

Provides a generic span context manager for connector-level tracing. Uses
LangChain's runnables config rather than raw OTel, ensuring spans integrate
with the existing Langfuse callback system.

## File

`src/utils/tracing.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `span(name: str, **attrs)` | Create a span context manager |

## Usage

```python
from src.utils.tracing import span


async def discover_schema(self):
    with span("postgres.discover_schema", table="users"):
        # connector logic here
        pass
```

## Test Coverage

- `tests/unit/test_utils_tracing.py` — span creation and context management
