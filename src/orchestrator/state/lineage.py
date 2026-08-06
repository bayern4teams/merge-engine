"""Lineage state fragment for merge-engine orchestrator."""

from __future__ import annotations

from typing import Any, TypedDict


class LineageState(TypedDict, total=False):
    """Column-level lineage state fragment added to MergeState."""

    lineage: dict[str, Any]
