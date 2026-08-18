---
type: orchestrator node
title: Map Fields Node
description: Maps unified entity fields to destination columns using a hybrid approach with heuristic scoring at 0.85 confidence for straightforward mappings and LLM fallback for borderline cases.
tags: [node, mapping, field_mapping, llm]
---

# Map Fields Node

## Purpose

Maps unified entity fields to destination columns. Uses a hybrid approach:
- **Heuristic scoring** — column name similarity ≥ 0.85 confidence gets auto-mapped
- **LLM fallback** — borderline cases (0.70–0.95 band) and unmapped fields are escalated to the LLM for judgment

## File

`src/orchestrator/nodes/map_fields_node.py` (~16KB)

## Implementation

### Sample Schema Building

Extracts sample column names from the discovered schemas to provide context for the mapping:

```python
_MAX_SAMPLE_RECORDS = 500
_MAX_SAMPLE_VALUES = 10
```

### Mapping Process

1. **Heuristic phase** — compares unified field names against destination column names using string similarity. Scores ≥ 0.85 are auto-mapped.
2. **LLM fallback** — fields in the 0.70–0.95 band and completely unmapped fields are sent to the LLM via the `field_mapping` prompt template.
3. **Validation** — validates mappings against the destination schema before returning.

### Retry Hints Bridge

Uses `with_retry_hint()` from `retry_hints.py` to carry reviewer feedback into the LLM prompt. This bridges the state → tool-config gap for nodes that build prompts in the tools layer.

## State

Produces:

```python
{
    "field_mappings": {
        "entity_type": {
            "unified_field_name": "destination_column_name",
            ...
        }
    },
    "unmapped_fields": {
        "entity_type": ["field1", "field2"],
    },
    "pending_schema_changes": [
        {"table": "...", "column": "...", "type": "STRING"},
    ],
}
```

## Retry Policy

Carries `NETWORK_RETRY` — the LLM fallback is a network call.

## Test Coverage

- `tests/unit/test_map_fields_node.py` — mapping correctness (17KB)
- `tests/unit/test_mapping_gate.py` — gate validation logic
- `tests/unit/test_map_fields_detail.py` — detailed mapping edge cases
- `tests/integration/test_field_mapping.py` — end-to-end mapping

## Cross-References

| Concern | Page |
|---------|------|
| MCP tool | [map_fields Tool](/openwiki/mcp/tools/map_fields.md) |
| Retry hints | [Retry Hints](/openwiki/orchestrator/utils/retry_hints.md) |
| Prompt templates | [LLM Prompts](/openwiki/llm/prompts.md) |
