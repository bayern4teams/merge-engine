"""validate_data node: data quality gate between extract_data and
resolve_entities (salvaged from B3-quality-lineage, Phase 4.5 of the
fix-bugs follow-up plan).

Runs a native row/field validator (row-count, uniqueness, not-null, sane
date bounds — see data_quality.py, NOT a Great Expectations gate, despite
what the B3 branch this was salvaged from claimed) against each entity's
extracted rows, always emitting ``quality_report`` and ``has_quality_failure``
onto state. Never auto-aborts the run itself — a hard `fail` severity is
surfaced at the human_final_approval gate (workflow_manager.py's
_build_interrupt_data already plumbs both keys through, ahead of this node
existing) so a human decides, exactly as B3's own design intended. What B3
actually shipped computed the flag and then silently dropped it before it
ever reached the gate; wiring that through is the single highest-value part
of this salvage.

This is Phase 4's own standing guard against issue 6 regressing: an entity
whose scope-plan join path silently resolves to nothing (like the 4th live
run's origin_2.Person and origin_2.PersonContact, both 0 rows, both waved
through to a yellow badge) now fails this node's row_count_ge_1 check loudly
instead of continuing unremarked.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.data_quality import run_data_quality_checks
from src.orchestrator.spool import RunSpool, entity_row_count, is_handle

logger = logging.getLogger("merge-engine.orchestrator.nodes.validate_data")

# Deep checks (uniqueness, not-null, date bounds) run against a bounded
# sample rather than every row — exhaustively scanning a 43M-row table in a
# Python loop on every run is exactly the memory/time cost the Phase N spool
# refactor exists to avoid. row_count itself is always exact regardless
# (entity_row_count reads it straight from the handle/spool, never a scan),
# so the one check that matters most for issue 6 is never approximate.
_SAMPLE_SIZE = 2000


def _sample_rows(state: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    """Up to `_SAMPLE_SIZE` rows for an entity, from a spool handle or an
    inline list — never the whole entity when it is spooled."""
    value = (state.get("extracted_data") or {}).get(entity_type)
    if is_handle(value):
        spool = RunSpool(value.get("run_id", ""))
        rows: list[dict[str, Any]] = []
        for row in spool.iter_rows(value.get("entity", entity_type), chunk=_SAMPLE_SIZE):
            rows.append(row)
            if len(rows) >= _SAMPLE_SIZE:
                break
        return rows
    return list(value or [])[:_SAMPLE_SIZE]


async def validate_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validate extracted data quality before entity resolution.

    Reads from MergeState:
        - extracted_data: per-entity spool handles or inline row lists
        - matching_rules: entity-level matching and key definitions
        - schemas: source database schema metadata (reserved for future
          schema-aware checks; not yet used by the native validator)

    Updates MergeState with:
        - quality_report: validation results, keyed by entity, with severity
          and per-check details
        - has_quality_failure: True if any entity hit a hard `fail` severity
    """
    logger.debug("ENTER validate_data")

    extracted_data = state.get("extracted_data") or {}
    matching_rules = state.get("matching_rules") or {}
    schemas = state.get("schemas") or {}

    sampled_rows = {entity: _sample_rows(state, entity) for entity in extracted_data}
    row_counts = {entity: entity_row_count(state, entity) for entity in extracted_data}

    result = run_data_quality_checks(
        sampled_rows=sampled_rows,
        row_counts=row_counts,
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
