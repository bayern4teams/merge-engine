---
type: mcp tool
title: map_fields Tool
description: MCP tool that maps unified entity fields to destination database columns using hybrid heuristic scoring (similarity ≥ 0.85) with LLM fallback for borderline cases. Supports retry with human feedback.
tags: [mcp, tool, field_mapping, destination]
---

# map_fields Tool

## Purpose

Maps unified entity fields to destination database columns. Uses a hybrid approach: deterministic heuristics (column name similarity scoring) for high-confidence mappings, with LLM fallback for borderline cases. Supports retry with human feedback via the retry hint mechanism.

## Handler

**File:** `src/mcp_server/server.py:199-223` (tool definition), `src/mcp_server/tools/map_fields.py:map_fields_handler` (handler), `src/mcp_server/tools/map_fields.py:map_fields_impl` (shared impl)

## Tool Definition

```python
Tool(
    name="map_fields",
    description="Map unified fields to destination columns using LLM; returns mapping, confidence scores, schema changes.",
    input_schema={"type": "object", "properties": {
        "unified_fields": [...],       // Unified schema field names/types
        "destination_schema": {...},   // Target DB schema
        "llm_config": {...},           // LLM provider, model, temperature
        "retry_hint": "string",        // Optional: human feedback for retry
    }},
)
```

## Implementation

`map_fields_impl` (at `src/mcp_server/tools/map_fields.py`, ~29KB) performs:

1. **Heuristic scoring** — for each unified field, compute similarity against every destination column:
   - Exact match → score 1.0
   - Case-insensitive match → score 0.95
   - Contains/contained-by → score 0.85
   - Soundex/Metaphone → score 0.80
   - Lemmatized similarity → score 0.75
   - Fields scoring ≥ 0.85 are auto-mapped
2. **LLM fallback** — fields with no high-confidence heuristic match are sent to the LLM with the `field_mapping` prompt template
3. **Schema change detection** — unmapped fields with high confidence suggest new destination columns
4. **Retry support** — if `retry_hint` is provided, it's appended to the LLM prompt via `apply_retry_hint()` so the LLM sees the reviewer's feedback

## State Integration

The orchestrator node (`map_fields` in `graph.py`) calls this and stores results in `MergeState.field_mappings` (operator.or_ merge) and `MergeState.unmapped_fields`. The `has_unmapped_fields` conditional edge checks if all fields are mapped before proceeding.

## Cross-References

| Concern | Page |
|---------|------|
| Orchestrator node | [Map Fields Node](/openwiki/orchestrator/nodes/map_fields.md) |
| Retry hints | [Retry Hints](/openwiki/orchestrator/utils/retry_hints.md) |
| Prompt templates | [LLM Prompts](/openwiki/llm/prompts.md) |
