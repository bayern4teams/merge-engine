"""
MergeState TypedDict definition for the LangGraph orchestrator.

Defines the complete shared state that flows through every node in the
merge workflow graph.
"""

from __future__ import annotations

from typing import NotRequired


class MergeState:
    """Shared state for a single merge workflow execution.

    Fields are documented with their phase of introduction and purpose.
    LangGraph uses this as a TypedDict-like structure; see graph.py
    for the actual StateGraph type annotation.
    """

    # ═══════════════════════════════════════════════════════════
    # ─ Input ─────────────────────────────────────────────────
    # ═══════════════════════════════════════════════════════════

    user_request: str
    """Natural-language user prompt, e.g. 'Import all companies and their contacts'."""

    target_entities: list[str]
    """Extracted entity type names from the user request, e.g. ['company', 'person']."""

    # ═══════════════════════════════════════════════════════════
    # ─ Configuration (resolved at startup) ───────────────────
    # ═══════════════════════════════════════════════════════════

    origins: list[dict]
    """Resolved origin database configs loaded from config.yaml."""

    destination: dict
    """Resolved Databricks destination config."""

    # ═══════════════════════════════════════════════════════════
    # ─ Phase outputs ─────────────────────────────────────────
    # ═══════════════════════════════════════════════════════════

    schemas: dict[str, dict]
    """{origin_id: schema_dict} — discovered schema for every origin."""

    dependency_tree: dict[str, list[str]]
    """{entity_type: [prerequisite_entity_types]} — dependency graph."""

    extraction_order: list[str]
    """Topologically sorted entity types ready for extraction."""

    extracted_data: dict[str, list[dict]]
    """{entity_type: [raw_records]} — all extracted rows per entity."""

    entity_clusters: dict[str, list[dict]]
    """{entity_type: [clusters]} — each cluster is a list of matching record IDs."""

    unified_entities: dict[str, list[dict]]
    """{entity_type: [unified_records]} — merged record per cluster."""

    field_mappings: dict[str, dict]
    """{entity_type: {source_field: (dest_column, confidence)}} — LLM field mapping."""

    pending_schema_changes: list[dict]
    """[{column, data_type, table, entity_type}] — proposed ALTER TABLE additions."""

    # ═══════════════════════════════════════════════════════════
    # ─ Human interaction ─────────────────────────────────────
    # ═══════════════════════════════════════════════════════════

    human_feedback: dict | None
    """Collected user decisions from HITL interrupt handlers."""

    load_approval: bool | None
    """Final human approval flag (True = proceed, False = abort)."""

    # ═══════════════════════════════════════════════════════════
    # ─ Error / retry tracking ────────────────────────────────
    # ═══════════════════════════════════════════════════════════

    errors: list[str]
    """Append-only error log for the workflow."""

    retry_counts: dict[str, int]
    """{node_name: attempt_count} — current retry attempt per node."""

    interrupt_data: dict | None
    """Structured data for the current HITL interrupt (UI panel payload)."""