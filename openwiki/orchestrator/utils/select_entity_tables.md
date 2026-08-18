---
type: utility
title: Entity Table Selection
description: >
  LLM-based selection of which source tables hold each target entity.
  Called by analyze_dependencies orchestrator node (not an MCP tool).
tags: [entity-tables, llm, schema-selection]
---

# Entity Table Selection

## Purpose

Uses the LLM to select which source tables hold each target entity. Called
directly by the `analyze_dependencies` orchestrator node, not registered as
an MCP tool.

## File

`src/mcp_server/tools/select_entity_tables.py` (~12KB)

## Key Functions

| Function | Purpose |
|----------|---------|
| `select_entity_tables_impl(...)` | Core selection logic using LLM |
| `select_entity_tables_handler(...)` | MCP-style handler (unused) |

## Integration

Called from `analyze_dependencies` node:

```python
from src.mcp_server.tools.select_entity_tables import (
    select_entity_tables_impl,
)

entity_tables = await select_entity_tables_impl(
    llm_client=client,
    schemas=schemas,
    target_entities=target_entities,
)
```

Uses the `entity_table_selection` prompt template from `src/llm/prompts/`.

## Test Coverage

- `tests/unit/test_select_entity_tables.py` — entity table selection logic
