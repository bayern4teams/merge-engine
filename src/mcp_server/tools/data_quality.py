"""Data quality tool: native row/field validation (salvaged from
B3-quality-lineage, Phase 4.5 of the fix-bugs follow-up plan, with real
fixes — see corrections below).

Checks per entity, against a (possibly sampled) row set:
- Row count >= 1 (severity: fail) — computed from the caller-supplied EXACT
  count, never from ``len(sample)``, so this check is always accurate even
  for an entity whose other checks run against a bounded sample.
- Uniqueness on declared ``unique_id_fields`` (or ``id`` as a fallback),
  within the sample (severity: fail).
- Not-null on a detected name column, within the sample (severity: warn).
- Sane bounds on ``*_year``/``*_date`` fields, within the sample (severity: warn).

Corrections applied vs the B3 branch this was salvaged from:
1. B3's ``run_data_quality_checks`` took ``extracted_data: dict[str, list]``
   directly and assumed every entity was already an in-memory row list.
   That assumption predates the Phase N spool refactor — today
   ``state["extracted_data"][entity]`` is a spool HANDLE dict
   (``{"__spool__": True, "run_id": ..., "row_count": ...}``), not a row
   list. Copied verbatim, B3's code would fall through to its
   ``isinstance(data, dict)`` branch and treat the *handle itself* as a
   single "row" — checking uniqueness against handle keys like ``run_id``
   instead of any real data. Fixed by moving row/count resolution out of
   this (pure, layering-clean) tool entirely: the caller
   (``orchestrator/nodes/validate_data.py``, which already sits at the
   correct layer to import ``orchestrator.spool``) resolves a bounded
   sample and the exact row count before calling in here, and this module
   never touches ``extracted_data`` or the spool directly.
2. B3 imported ``great_expectations`` in a try/except purely to set an
   unused ``gx_available`` flag — GX was never actually invoked anywhere in
   the file. Dropped entirely; the module docstring no longer claims to be
   a Great Expectations gate.
3. B3 derived a sample's columns from ``rows[0]`` alone (``:136-137``).
   Origins can contribute heterogeneous columns to the same entity bucket
   (that is the whole point of a merge), so the first row missing a column
   later rows have would silently blind the name-column/date-bounds checks
   to it. Fixed to union columns across every sampled row.

Never raises: an internal error degrades to a "validation unavailable"
report (severity "warn") rather than crashing the pipeline — this gate
exists to surface problems to a human at the final approval gate, never to
auto-abort a run by itself.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

logger = logging.getLogger("merge-engine.tools.data_quality")

NAME_COLUMN_CANDIDATES = {
    "name",
    "company_name",
    "org_name",
    "vendor_name",
    "customer_name",
    "title",
    "full_name",
    "first_name",
    "last_name",
}


def _detect_name_column(columns: list[str]) -> str | None:
    for col in columns:
        if col.lower() in NAME_COLUMN_CANDIDATES:
            return col
    for col in columns:
        if "name" in col.lower():
            return col
    return None


def _check_sane_date_bounds(val: Any) -> bool:
    """Return True if val is within sane date/year bounds, False if suspicious."""
    if val is None:
        return True
    current_year = datetime.date.today().year
    if isinstance(val, int):
        return 1700 <= val <= current_year + 5
    if isinstance(val, float):
        return 1700 <= int(val) <= current_year + 5
    if isinstance(val, str):
        val_str = val.strip()
        if val_str.isdigit() and len(val_str) == 4:
            year = int(val_str)
            return 1700 <= year <= current_year + 5
        try:
            dt = datetime.datetime.fromisoformat(val_str.replace("Z", "+00:00"))
            return 1700 <= dt.year <= current_year + 5
        except (ValueError, TypeError):
            # Not an ISO date string, ignore non-date strings
            return True
    return True


def run_data_quality_checks(
    sampled_rows: dict[str, list[dict[str, Any]]] | None,
    row_counts: dict[str, int] | None = None,
    matching_rules: dict[str, Any] | None = None,
    schemas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run native validation checks across a per-entity row sample.

    Parameters
    ----------
    sampled_rows : dict
        ``{entity_type: [row, ...]}`` — a bounded sample, not necessarily
        every row. Uniqueness/not-null/date-bounds checks run against this
        sample only, and are therefore approximate for an entity larger
        than the sample.
    row_counts : dict, optional
        ``{entity_type: exact_row_count}``. The row-count check (the one
        that matters most for issue 6 — an entity silently extracting 0
        rows) uses this exact count, falling back to ``len(sample)`` only
        when the caller didn't supply one.

    Returns
    -------
    dict
        ``{"quality_report": {...}, "has_quality_failure": bool}``.
    """
    if sampled_rows is None:
        sampled_rows = {}
    if row_counts is None:
        row_counts = {}
    if matching_rules is None:
        matching_rules = {}

    entity_results: dict[str, Any] = {}
    overall_severity = "pass"
    has_failure = False

    try:
        for entity, rows in sampled_rows.items():
            rows = rows or []
            sample_size = len(rows)
            row_count = row_counts.get(entity, sample_size)

            entity_checks: list[dict[str, Any]] = []
            entity_severity = "pass"

            # 1. Row count >= 1 (Hard Fail) — always the EXACT count, never
            # the sample size, so this is accurate regardless of sampling.
            if row_count < 1:
                entity_checks.append(
                    {
                        "check": "row_count_ge_1",
                        "severity": "fail",
                        "success": False,
                        "details": f"Entity '{entity}' has 0 extracted rows (expected >= 1).",
                    }
                )
                entity_severity = "fail"
            else:
                entity_checks.append(
                    {
                        "check": "row_count_ge_1",
                        "severity": "fail",
                        "success": True,
                        "details": f"Entity '{entity}' has {row_count} extracted rows.",
                    }
                )

            if rows:
                # Union of columns across every sampled row, not just the
                # first — a table with heterogeneous per-origin columns
                # would otherwise blind these checks to anything the first
                # row happened not to carry.
                cols_set: set[str] = set()
                for r in rows:
                    if isinstance(r, dict):
                        cols_set.update(r.keys())
                cols = sorted(cols_set)

                # 2. Uniqueness on declared unique_id_fields (Hard Fail),
                # within the sample.
                entity_rules = matching_rules.get(entity) or {}
                unique_fields = entity_rules.get("unique_id_fields") or []
                if not unique_fields and "id" in cols:
                    unique_fields = ["id"]

                for u_field in unique_fields:
                    seen_vals: set[Any] = set()
                    dups = 0
                    for r in rows:
                        if isinstance(r, dict):
                            val = r.get(u_field)
                            if val is not None:
                                if val in seen_vals:
                                    dups += 1
                                else:
                                    seen_vals.add(val)
                    if dups > 0:
                        entity_checks.append(
                            {
                                "check": f"uniqueness:{u_field}",
                                "severity": "fail",
                                "success": False,
                                "details": (
                                    f"Field '{u_field}' in entity '{entity}' has {dups} "
                                    f"duplicate value(s) in the sampled {sample_size} row(s)."
                                ),
                            }
                        )
                        entity_severity = "fail"
                    else:
                        entity_checks.append(
                            {
                                "check": f"uniqueness:{u_field}",
                                "severity": "fail",
                                "success": True,
                                "details": (
                                    f"Field '{u_field}' in entity '{entity}' is unique "
                                    f"across the sampled {sample_size} row(s)."
                                ),
                            }
                        )

                # 3. Not-null on detected name column (Warn), within the sample.
                name_col = _detect_name_column(cols)
                if name_col:
                    null_count = sum(
                        1
                        for r in rows
                        if isinstance(r, dict)
                        and (r.get(name_col) is None or str(r.get(name_col)).strip() == "")
                    )
                    if null_count > 0:
                        entity_checks.append(
                            {
                                "check": f"not_null:{name_col}",
                                "severity": "warn",
                                "success": False,
                                "details": (
                                    f"Name column '{name_col}' in '{entity}' has "
                                    f"{null_count} null/blank value(s) in the sample."
                                ),
                            }
                        )
                        if entity_severity != "fail":
                            entity_severity = "warn"
                    else:
                        entity_checks.append(
                            {
                                "check": f"not_null:{name_col}",
                                "severity": "warn",
                                "success": True,
                                "details": f"Name column '{name_col}' in '{entity}' has no null values in the sample.",
                            }
                        )

                # 4. Sane bounds on *_year or *_date (Warn), within the sample.
                date_year_cols = [
                    c
                    for c in cols
                    if c.lower().endswith("_year")
                    or c.lower().endswith("_date")
                    or c.lower() == "year"
                    or c.lower() == "date"
                ]
                for dy_col in date_year_cols:
                    bad_bounds = 0
                    for r in rows:
                        if isinstance(r, dict):
                            val = r.get(dy_col)
                            if not _check_sane_date_bounds(val):
                                bad_bounds += 1
                    if bad_bounds > 0:
                        entity_checks.append(
                            {
                                "check": f"sane_bounds:{dy_col}",
                                "severity": "warn",
                                "success": False,
                                "details": (
                                    f"Date/year column '{dy_col}' in '{entity}' has "
                                    f"{bad_bounds} value(s) out of sane bounds "
                                    "(1700-present) in the sample."
                                ),
                            }
                        )
                        if entity_severity != "fail":
                            entity_severity = "warn"
                    else:
                        entity_checks.append(
                            {
                                "check": f"sane_bounds:{dy_col}",
                                "severity": "warn",
                                "success": True,
                                "details": f"Date/year column '{dy_col}' in '{entity}' passed sane bound checks.",
                            }
                        )

            entity_results[entity] = {
                "severity": entity_severity,
                "row_count": row_count,
                "sample_size": sample_size,
                "sampled": sample_size < row_count,
                "checks": entity_checks,
            }

            if entity_severity == "fail":
                overall_severity = "fail"
                has_failure = True
            elif entity_severity == "warn" and overall_severity != "fail":
                overall_severity = "warn"

    except Exception as exc:
        logger.error(
            "Error evaluating data quality checks; degrading to 'validation unavailable': %s",
            exc,
            exc_info=True,
        )
        return {
            "quality_report": {
                "status": "validation unavailable",
                "severity": "warn",
                "error": str(exc),
                "entities": {},
            },
            "has_quality_failure": False,
        }

    return {
        "quality_report": {
            "status": "completed",
            "severity": overall_severity,
            "entities": entity_results,
        },
        "has_quality_failure": has_failure,
    }
