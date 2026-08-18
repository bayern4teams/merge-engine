---
type: mcp tool
title: extend_schema Tool
description: MCP tool that adds new columns to Databricks destination tables for unmapped fields requiring schema extension. Uses the DatabricksConnector's extend_schema_batch method.
tags: [mcp, tool, extend_schema, schema, databricks]
---

# extend_schema Tool

## Purpose

Adds new columns to Databricks destination tables when unified fields have no corresponding destination column. This is the mechanism that allows the merge pipeline to adapt the destination schema dynamically when new fields are discovered during the merge.

## Handler

**File:** `src/mcp_server/server.py:251-276` (tool definition), `src/mcp_server/server.py:323-345` (handler `_extend_schema_handler`)

## Tool Definition

```python
Tool(
    name="extend_schema",
    description="Add new columns to Databricks destination tables for unmapped fields.",
    input_schema={"type": "object", "properties": {
        "changes": [                   // Column additions
            {
                "table": "string",
                "column": "string",
                "type": "string",
                "nullable": true,
            }
        ]
    }},
)
```

## Implementation

The `_extend_schema_handler` (lines 323-345):

```python
async def _extend_schema_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    config = _get_config()
    dest_cfg = config.destination
    changes = arguments.get("changes", [])

    connector = None
    try:
        connector = create_connector(dest_cfg.type, dest_cfg.model_dump())
        result = await connector.extend_schema_batch(changes)
        return {
            "success": True,
            "changes_applied": result.get("added", 0),
            "changes_failed": result.get("failed", 0),
        }
    except Exception as exc:
        raise PermanentError(f"Schema extension failed: {exc}") from exc
    finally:
        if connector is not None:
            await connector.close()
```

The handler:
1. Gets the destination config from the server's initialized config
2. Creates a connector via `create_connector()` — supports the destination type configured in the pipeline
3. Calls `connector.extend_schema_batch(changes)` which executes `ALTER TABLE ... ADD COLUMN` statements
4. Returns counts of applied/failed changes
5. Always closes the connector in a `finally` block

## DatabricksConnector Dual Role

The `DatabricksConnector` at `src/mcp_server/connectors/databricks.py` serves dual purpose:
- **Source connector:** inherits `discover_schema()` and `extract()` from `BaseConnector`
- **Destination writer:** implements `load_batch()` (MERGE), `add_column()`, and `extend_schema_batch()`

## Error Handling

Any exception during schema extension is wrapped in `PermanentError` — schema changes are structural and retrying the same change won't help.

## Cross-References

| Concern | Page |
|---------|------|
| Databricks connector | [Databricks Connector](/openwiki/connectors/databricks.md) |
| Destination connectors | [Destination Connectors](/openwiki/connectors/destinations.md) |
| Error handling | [Error Handling](/openwiki/orchestrator/utils/error_handling.md) |
