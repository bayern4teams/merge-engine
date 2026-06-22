"""MCP tool implementations for merge-engine.

All tool implementations serve dual purposes:
- Called directly by LangGraph orchestrator nodes (no protocol overhead)
- Registered with the MCP server for external access

Each module exports:
- `*_impl`: The core async function with full input parameters
- `*_handler`: The MCP tool handler entry point that extracts args from dict
"""

from __future__ import annotations

from src.mcp_server.tools.discover_schema import (
    discover_schema_impl,
    discover_schema_handler,
)
from src.mcp_server.tools.extract_data import (
    extract_data_impl,
    extract_data_handler,
)
from src.mcp_server.tools.mark_imported import (
    mark_imported_impl,
    mark_imported_handler,
)
from src.mcp_server.tools.resolve_entities import (
    resolve_entities_impl,
)
from src.mcp_server.tools.unify_entity_group import (
    unify_entity_group_impl,
    unify_entity_group_handler,
)
from src.mcp_server.tools.map_fields import (
    map_fields_impl,
    map_fields_handler,
)
from src.mcp_server.tools.load_data import (
    load_data_impl,
    load_data_handler,
)

__all__ = [
    "discover_schema_impl",
    "discover_schema_handler",
    "extract_data_impl",
    "extract_data_handler",
    "mark_imported_impl",
    "mark_imported_handler",
    "resolve_entities_impl",
    "unify_entity_group_impl",
    "unify_entity_group_handler",
    "map_fields_impl",
    "map_fields_handler",
    "load_data_impl",
    "load_data_handler",
]