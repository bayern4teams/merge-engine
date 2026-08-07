"""Planner state schema fragment for merge-engine (B2).

Defines planner-specific state keys and types.
"""

from __future__ import annotations

from typing import Any, TypedDict


class PlannerStateFragment(TypedDict, total=False):
    """Planner-specific state fragment for MergeState."""

    target_entities: list[str]
    explicit_target_entities: list[str]
    merge_scope: dict[str, Any]
    dependency_tree: dict[str, list[str]]
    extraction_order: list[str]
    entity_tables: dict[str, Any]
    table_selection_notes: str
    scope_plan: dict[str, Any]
