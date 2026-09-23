"""
MCP server definition and tool registration for merge-engine.

Exposes all database and LLM tools as MCP resources/tools for external
access (other AI agents, automation scripts). Tool implementations are
shared plain async functions in `src/mcp_server/tools/` that are also
called directly by LangGraph orchestrator nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.types import TextContent, Tool

if not hasattr(Server, "list_tools"):

    def _list_tools_decorator(self: Any) -> Any:
        def decorator(fn: Any) -> Any:
            async def _handler(req: types.ListToolsRequest) -> types.ListToolsResult:
                tools = await fn()
                return types.ListToolsResult(tools=tools)

            self.add_request_handler("tools/list", types.ListToolsRequest, _handler)
            return fn

        return decorator

    Server.list_tools = _list_tools_decorator  # type: ignore[attr-defined]

if not hasattr(Server, "call_tool"):

    def _call_tool_decorator(self: Any) -> Any:
        def decorator(fn: Any) -> Any:
            async def _handler(req: types.CallToolRequest) -> types.CallToolResult:
                result = await fn(req.params.name, req.params.arguments or {})
                return types.CallToolResult(content=result)

            self.add_request_handler("tools/call", types.CallToolRequest, _handler)
            return fn

        return decorator

    Server.call_tool = _call_tool_decorator  # type: ignore[attr-defined]

# Pydantic v2 models expose fields via model_fields, not as class attributes,
# so a plain `hasattr(Tool, "inputSchema")` on the class is False even when
# the field exists — checking that way installed the fallback property below
# unconditionally and shadowed the real field with one reading a nonexistent
# `input_schema` attribute (every tool then reported an empty inputSchema).
if "inputSchema" not in getattr(Tool, "model_fields", {}):
    Tool.inputSchema = property(lambda self: getattr(self, "input_schema", {}))  # type: ignore[attr-defined]

from src.mcp_server.connectors import create_connector

logger = logging.getLogger("merge-engine.mcp_server")

# Create the MCP server instance
app = Server("merge-engine")

# Pre-loaded config (populated on startup)
_config: dict[str, Any] | None = None


def initialize_server(config: Any) -> None:
    """Initialize the MCP server with resolved application config.

    Parameters
    ----------
    config : Config
        Validated Pydantic config from config_loader.
    """
    global _config
    _config = config


def _get_config() -> Any:
    """Get the currently loaded config."""
    if _config is None:
        raise RuntimeError("MCP server not initialized. Call initialize_server() first.")
    return _config


# ── Tool Definitions ─────────────────────────────────────────────────


@app.list_tools()  # type: ignore[attr-defined]
async def list_tools() -> list[Tool]:
    """Return the list of tools exposed by this MCP server."""
    return [
        Tool(  # type: ignore[call-arg]
            name="discover_schema",
            description=(
                "Connect to an origin database and return all tables/collections "
                "with column names, types, one sample value, and foreign keys."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "origin_id": {
                        "type": "string",
                        "description": "The origin database ID from config.",
                    },
                },
                "required": ["origin_id"],
            },
        ),
        Tool(  # type: ignore[call-arg]
            name="extract_data",
            description=(
                "Extract rows from a specific origin table/entity, filtered by "
                "_imported flag or custom filter. Supports batch-oriented cursor-based "
                "pagination (SQL: OFFSET/LIMIT; MongoDB: _id-based skip)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "origin_id": {
                        "type": "string",
                        "description": "The origin database ID from config.",
                    },
                    "table_name": {
                        "type": "string",
                        "description": "Name of the table or collection to extract from.",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to extract. Use ['*'] for all.",
                    },
                    "batch_size": {
                        "type": "integer",
                        "default": 1000,
                        "description": "Maximum rows per batch.",
                    },
                    "cursor": {
                        "type": "string",
                        "nullable": True,
                        "description": "Pagination cursor for next batch.",
                    },
                    "filter": {
                        "type": "object",
                        "nullable": True,
                        "description": "Additional filter conditions.",
                    },
                },
                "required": ["origin_id", "table_name", "columns"],
            },
        ),
        Tool(  # type: ignore[call-arg]
            name="resolve_entities",
            description=(
                "Given records from multiple sources for a given entity type, "
                "cluster matching ones using deterministic blocking + LLM scoring "
                "for borderline pairs. Assigns UUIDs to each cluster."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "The entity type to resolve, e.g. 'person'.",
                    },
                    "records": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of records from all origins.",
                    },
                },
                "required": ["entity_type", "records"],
            },
        ),
        Tool(  # type: ignore[call-arg]
            name="unify_entity_group",
            description=(
                "Merge attributes within a cluster: complement nulls, flag "
                "conflicts, produce unified record with conflict details."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_id": {
                        "type": "string",
                        "description": "Unique identifier for the cluster.",
                    },
                    "records": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "All records in this cluster.",
                    },
                },
                "required": ["cluster_id", "records"],
            },
        ),
        Tool(  # type: ignore[call-arg]
            name="map_fields",
            description=(
                "Map unified fields to destination columns using LLM. "
                "Returns mapping, confidence scores, and any schema changes needed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "The entity type being mapped.",
                    },
                    "unified_schema": {
                        "type": "object",
                        "description": "Field names and sample values from unified records.",
                    },
                    "destination_schema": {
                        "type": "object",
                        "description": "Destination table column definitions.",
                    },
                },
                "required": ["entity_type", "unified_schema", "destination_schema"],
            },
        ),
        Tool(  # type: ignore[call-arg]
            name="load_data",
            description=(
                "Upsert unified records into the destination table "
                "(Delta) using MERGE. Handles conflict column and provenance tracking."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "The entity type being loaded.",
                    },
                    "records": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Unified records to load.",
                    },
                    "mapping": {
                        "type": "object",
                        "nullable": True,
                        "description": "Field mapping used for transformation.",
                    },
                },
                "required": ["entity_type", "records"],
            },
        ),
        Tool(  # type: ignore[call-arg]
            name="extend_schema",
            description=(
                "Add new columns to one or more destination tables. "
                "Useful when unmapped fields require schema extension."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "table": {"type": "string"},
                                "column": {"type": "string"},
                                "data_type": {"type": "string"},
                            },
                            "required": ["table", "column", "data_type"],
                        },
                        "description": "List of column additions.",
                    },
                },
                "required": ["changes"],
            },
        ),
    ]


# ── Tool Dispatch ─────────────────────────────────────────────────────


@app.call_tool()  # type: ignore[attr-defined]
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Dispatch a tool call to the appropriate implementation.

    Parameters
    ----------
    name : str
        Tool name.
    arguments : dict
        Tool-specific input arguments.

    Returns
    -------
    list[TextContent]
        Tool result as JSON-encoded text content.
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: '{name}'")

    result = await handler(arguments)
    return [TextContent(type="text", text=json.dumps(result, default=str))]


# ── Tool Handler Registry ─────────────────────────────────────────────

# Import all tool handlers and register them. Deliberately not at the top of
# the file: src.mcp_server.tools imports back from this module's package at
# import time in some tool submodules, so moving this above the handler
# functions it registers below risks a circular import (noqa: E402).
from src.mcp_server.tools import (  # noqa: E402
    discover_schema_handler,
    extract_data_handler,
    load_data_handler,
    map_fields_handler,
    resolve_entities_impl,
    unify_entity_group_handler,
)


async def _extend_schema_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """Handler for extend_schema tool."""
    from src.mcp_server.server import _get_config
    from src.orchestrator.utils.retry import PermanentError

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


_TOOL_HANDLERS: dict[str, Any] = {
    "discover_schema": discover_schema_handler,
    "extract_data": extract_data_handler,
    "resolve_entities": resolve_entities_impl,
    "unify_entity_group": unify_entity_group_handler,
    "map_fields": map_fields_handler,
    "load_data": load_data_handler,
    "extend_schema": _extend_schema_handler,
}


def register_tool(name: str, handler: Any) -> None:
    """Register a tool handler in the global registry."""
    _TOOL_HANDLERS[name] = handler
