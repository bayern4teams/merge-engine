"""
LangGraph state graph builder and compiled graph for merge-engine.

Wires all orchestrator nodes, edges, conditional edges, and interrupt points
into a single compiled LangGraph workflow.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph, END

from src.orchestrator.nodes import (
    parse_request,
    discover_schemas,
    analyze_dependencies,
    extract_data,
    resolve_entities,
    has_low_confidence_clusters,
    unify_entity_groups,
    map_fields,
    has_unmapped_fields,
    load_data_node,
    flag_origins_node,
    human_review_deps,
    human_review_matches,
    human_review_mappings,
    human_final_approval,
    handle_error,
)

logger = logging.getLogger("merge-engine.orchestrator.graph")

# Node names for interrupt configuration
HITL_NODES = [
    "human_review_deps",
    "human_review_matches",
    "human_review_mappings",
    "human_final_approval",
]


def build_graph() -> Any:
    """Build and compile the LangGraph state graph.

    Returns
    -------
    Any
        A compiled LangGraph workflow ready for execution.

    Notes
    -----
    This uses dict-based state (not TypedDict) for flexibility with
    LangGraph's state mutation patterns. The actual state schema is
    documented in src/orchestrator/state.py.
    """
    builder = StateGraph(dict)

    # ── Add all nodes ───────────────────────────────────────────────
    builder.add_node("parse_request", parse_request)
    builder.add_node("discover_schemas", discover_schemas)
    builder.add_node("analyze_dependencies", analyze_dependencies)
    builder.add_node("human_review_deps", human_review_deps)
    builder.add_node("extract_data", extract_data)
    builder.add_node("resolve_entities", resolve_entities)
    builder.add_node("human_review_matches", human_review_matches)
    builder.add_node("unify_entities", unify_entity_groups)
    builder.add_node("map_fields", map_fields)
    builder.add_node("human_review_mappings", human_review_mappings)
    builder.add_node("human_final_approval", human_final_approval)
    builder.add_node("load_data", load_data_node)
    builder.add_node("flag_origins", flag_origins_node)
    builder.add_node("handle_error", handle_error)

    # ── Main flow edges ─────────────────────────────────────────────
    builder.set_entry_point("parse_request")

    builder.add_edge("parse_request", "discover_schemas")
    builder.add_edge("discover_schemas", "analyze_dependencies")
    builder.add_edge("analyze_dependencies", "human_review_deps")
    builder.add_edge("human_review_deps", "extract_data")
    builder.add_edge("extract_data", "resolve_entities")

    # Conditional: low-confidence entity matches → human review
    builder.add_conditional_edges(
        "resolve_entities",
        has_low_confidence_clusters,
        {True: "human_review_matches", False: "unify_entities"},
    )

    builder.add_edge("human_review_matches", "unify_entities")
    builder.add_edge("unify_entities", "map_fields")

    # Conditional: unmapped fields → human review
    builder.add_conditional_edges(
        "map_fields",
        has_unmapped_fields,
        {True: "human_review_mappings", False: "human_final_approval"},
    )

    builder.add_edge("human_review_mappings", "human_final_approval")
    builder.add_edge("human_final_approval", "load_data")
    builder.add_edge("load_data", "flag_origins")
    builder.add_edge("flag_origins", END)

    # ── Error routing ───────────────────────────────────────────────
    # After each processing node, check for errors
    error_nodes = [
        "parse_request",
        "discover_schemas",
        "analyze_dependencies",
        "extract_data",
        "resolve_entities",
        "unify_entities",
        "map_fields",
        "load_data",
        "flag_origins",
    ]

    for node_name in error_nodes:
        builder.add_conditional_edges(
            node_name,
            _error_router,
            {"error": "handle_error", "continue": node_name},
        )

    # After handle_error, retry the original node or escalate
    builder.add_edge("handle_error", "parse_request")  # Simplified: always retry from current

    # Compile with checkpointing (enables resume after interrupts)
    try:
        from langgraph.checkpoint.memory import MemorySaver
        memory = MemorySaver()
        graph = builder.compile(
            checkpointer=memory,
            interrupt_before=HITL_NODES,
            interrupt_after=[],
        )
        logger.info("Compiled merge workflow graph with %d HITL interrupt points", len(HITL_NODES))
    except ImportError:
        logger.warning("langgraph-checkpoint not installed; compiling without checkpointer")
        graph = builder.compile()

    return graph


def _error_router(state: dict[str, Any]) -> str:
    """Route to error handler if errors list is non-empty."""
    errors = state.get("errors", [])
    if errors:
        return "error"
    return "continue"


def get_initial_state(
    user_request: str,
    config: Any,
) -> dict[str, Any]:
    """Create the initial state dict for a new workflow.

    Parameters
    ----------
    user_request : str
        User's natural language prompt.
    config : Config
        Validated application config from config_loader.

    Returns
    -------
    dict
        Initial state ready for graph.stream().
    """
    # Flatten config for state
    origins_list = []
    for o in config.origins:
        origin_dict = {
            "id": o.id,
            "type": o.type,
            "connection": o.connection.model_dump(),
            "priority": o.priority,
        }
        origins_list.append(origin_dict)

    destination_dict = {
        "type": config.destination.type,
        "host": config.destination.host,
        "http_path": config.destination.http_path,
        "token": config.destination.token,
        "catalog": config.destination.catalog,
        "schema": config.destination.schema_name,
        "tables": config.destination.tables,
    }

    llm_dict = {
        "provider": config.llm.provider,
        "model": config.llm.model,
        "api_key": config.llm.api_key,
        "base_url": config.llm.base_url,
        "temperature": config.llm.temperature,
    }

    return {
        "user_request": user_request,
        "target_entities": [],
        "origins": origins_list,
        "destination": destination_dict,
        "llm_config": llm_dict,
        "extraction_config": {
            "default_batch_size": config.extraction.default_batch_size,
        },
        "schemas": {},
        "dependency_tree": {},
        "extraction_order": [],
        "extracted_data": {},
        "entity_clusters": {},
        "unified_entities": {},
        "field_mappings": {},
        "pending_schema_changes": [],
        "human_feedback": None,
        "load_approval": None,
        "errors": [],
        "retry_counts": {},
        "interrupt_data": None,
    }