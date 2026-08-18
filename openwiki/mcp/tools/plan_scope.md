---
type: mcp tool
title: Plan Scope Tool
description: >
  MCP tool for scoped merge planning — per-entity extraction filters for
  subject scoping (e.g. "all information about Nova").
tags: [mcp-tool, scoped-merge, planning]
---

# Plan Scope Tool

## Purpose

Scoped merge planning tool. Builds per-entity extraction filters for a
subject entity (e.g., "all information about Nova"). Not an orchestrator
node itself but a reusable implementation also called by the `plan_scope`
orchestrator node.

## File

`src/mcp_server/tools/plan_scope.py` (~20KB)

## Key Functions

| Function | Purpose |
|----------|---------|
| `plan_scope_impl(...)` | Core planning logic |
| `plan_scope_handler(...)` | MCP-style handler |

## Integration

The orchestrator `plan_scope` node wraps this implementation:

```python
from src.mcp_server.tools.plan_scope import plan_scope_impl

scope_plan = await plan_scope_impl(
    llm_client=client,
    entity_type=entity,
    subject=subject_value,
    schemas=schemas,
)
```

Uses the `scope_planning` prompt template from `src/llm/prompts/`.

## Test Coverage

- `tests/unit/test_plan_scope.py` — scope planning logic
