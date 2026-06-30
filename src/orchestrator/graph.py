"""
LangGraph state graph builder and compiled graph for merge-engine.

Wires all orchestrator nodes, edges, conditional edges, and interrupt points
into a single compiled LangGraph workflow.

Supports two modes:
1. In-process mode: build_graph() with MemorySaver (for direct WorkflowManager use)
2. Server mode: graph module-level export for LangGraph CLI server
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


def _build_builder() -> StateGraph:
    """Build the raw StateGraph (nodes + edges) without compiling."""
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

    # Conditional: low-confidence entity matches -> human review
    builder.add_conditional_edges(
        "resolve_entities",
        has_low_confidence_clusters,
        {True: "human_review_matches", False: "unify_entities"},
    )

    builder.add_edge("human_review_matches", "unify_entities")
    builder.add_edge("unify_entities", "map_fields")

    # Conditional: unmapped fields -> human review
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
    # NOTE: Error routing conditional edges are NOT added here because
    # StateGraph(dict) uses an overwrite reducer that only keeps what
    # nodes return. Adding conditional edges that route "continue" back
    # to the same node causes state loss (the original fields like
    # user_request are lost after the first node runs).
    #
    # Each node handles errors internally by returning them in the
    # "errors" list, and the orchestrator workflow_manager.py tracks
    # and logs errors appropriately.

    return builder


def build_graph(checkpointer: Any = None) -> Any:
    """Build and compile the LangGraph state graph.

    Parameters
    ----------
    checkpointer : Any, optional
        LangGraph checkpointer. If None, uses MemorySaver.
        For production use, pass SqliteSaver or PostgresSaver.

    Returns
    -------
    Any
        A compiled LangGraph workflow ready for execution.
    """
    builder = _build_builder()

    # Compile with checkpointing (enables resume after interrupts)
    if checkpointer is not None:
        try:
            graph = builder.compile(
                checkpointer=checkpointer,
                interrupt_before=HITL_NODES,
                interrupt_after=[],
            )
            logger.info("Compiled merge workflow graph with %d HITL interrupt points", len(HITL_NODES))
        except Exception:
            logger.warning("Failed to compile with checkpointer, compiling without")
            graph = builder.compile()
    else:
        try:
            from langgraph.checkpoint.memory import MemorySaver
            memory = MemorySaver()
            graph = builder.compile(
                checkpointer=memory,
                interrupt_before=HITL_NODES,
                interrupt_after=[],
            )
            logger.info("Compiled merge workflow graph with MemorySaver and %d HITL interrupt points", len(HITL_NODES))
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
    config_path: str | None = None,
    config: Any = None,
    origins: list[dict[str, Any]] | None = None,
    destination: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the initial state dict for a new workflow.

    Parameters can be provided either via a loaded Config object
    (legacy) or directly from the UI (origins + destination dicts).

    Parameters
    ----------
    user_request : str
        User's natural language prompt.
    config_path : str, optional
        Path to config.yaml (used when config is None).
    config : Config, optional
        Validated application config from config_loader (legacy).
        If provided, origins/destination are extracted from it.
    origins : list[dict], optional
        Origin database configurations from the UI.
        Each dict: {id, type, connection: {host, port, user, password, database}, priority}
    destination : dict, optional
        Destination database configuration from the UI.
        {type, host, port, user, password, database, schema}

    Returns
    -------
    dict
        Initial state ready for graph.stream().
    """
    # Handle UI-originated workflows (origins + destination dicts)
    if origins is not None and destination is not None:
        llm_config = _load_llm_config(config_path)
        return {
            "user_request": user_request,
            "target_entities": [],
            "origins": origins,
            "destination": destination,
            "llm_config": llm_config,
            "extraction_config": {
                "default_batch_size": 1000,
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

    # Legacy path: extract from Config object
    if config is not None:
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
            "port": config.destination.port or 5432,
            "user": config.destination.user or "",
            "password": config.destination.password or "",
            "database": config.destination.database or "",
            "schema": config.destination.schema_name or "public",
        }

        llm_config = {
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
            "llm_config": llm_config,
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

    raise ValueError("Must provide either (config) or (origins and destination)")


def _load_llm_config(config_path: str | None) -> dict[str, Any]:
    """Load LLM configuration from config file.

    Parameters
    ----------
    config_path : str | None
        Path to config.yaml. Uses default if None.

    Returns
    -------
    dict
        LLM configuration dict.
    """
    try:
        from src.utils.config_loader import load_config
        cfg = load_config(config_path)
        return {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "api_key": cfg.llm.api_key,
            "base_url": cfg.llm.base_url,
            "temperature": cfg.llm.temperature,
        }
    except Exception:
        # Return placeholder -- LLM will be configured via env vars
        return {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "",
            "base_url": None,
            "temperature": 0.0,
        }


# ── Server Mode Export ──────────────────────────────────────────────
# LangGraph CLI server expects a compiled `graph` at module level.
# The server handles checkpointer/persistence automatically — it does NOT
# allow custom checkpointer in the compiled graph. We compile WITHOUT
# a checkpointer for the CLI server path. The server adds its own
# persistence layer at runtime.

def _get_server_graph():
    """Create a compiled graph for LangGraph CLI server mode.
    
    The LangGraph API handles persistence automatically. Custom
    checkpointer (MemorySaver/SqliteSaver/etc.) are NOT allowed in
    server mode — the API overrides them with its own persistence.
    """
    builder = _build_builder()
    graph = builder.compile(
        interrupt_before=HITL_NODES,
        interrupt_after=[],
    )
    logger.info("Server graph compiled (no custom checkpointer — API handles persistence)")
    return graph


# Module-level graph export for LangGraph CLI
graph = _get_server_graph()
