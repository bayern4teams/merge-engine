"""Quality state fragment for merge-engine orchestrator."""

from __future__ import annotations

from typing import Any, TypedDict


class QualityState(TypedDict, total=False):
    """Data quality state fragment added to MergeState."""

    quality_report: dict[str, Any]
    has_quality_failure: bool
