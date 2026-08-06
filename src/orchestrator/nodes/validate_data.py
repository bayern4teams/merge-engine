"""Data quality validation orchestrator node.

Runs after extract_data and before resolve_entities. Evaluates Great Expectations
and row/field assertions, emitting quality_report and setting has_quality_failure
flag if any hard failures occur.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.data_quality import run_data_quality_checks

logger = logging.getLogger("merge-engine.orchestrator.nodes.validate_data")


async def validate_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validate extracted data quality before entity resolution.

    Reads from MergeState:
        - extracted_data: Dict of extracted datasets per entity
        - matching_rules: Entity-level matching and key definitions
        - schemas: Source database schema metadata

    Updates MergeState with:
        - quality_report: Validation results dict with severity and check details
        - has_quality_failure: True if any hard fail severity was flagged
    """
    logger.debug("ENTER validate_data")

    extracted_data = state.get("extracted_data") or {}
    matching_rules = state.get("matching_rules") or {}
    schemas = state.get("schemas") or {}

    result = run_data_quality_checks(
        extracted_data=extracted_data,
        matching_rules=matching_rules,
        schemas=schemas,
    )

    quality_report = result.get("quality_report") or {}
    has_failure = bool(result.get("has_quality_failure"))

    logger.info(
        "Data quality validation completed with severity '%s' (has_failure=%s).",
        quality_report.get("severity"),
        has_failure,
    )

    logger.debug("EXIT validate_data")
    return {
        "quality_report": quality_report,
        "has_quality_failure": has_failure,
    }
