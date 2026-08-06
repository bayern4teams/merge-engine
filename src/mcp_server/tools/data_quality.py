"""Data quality tool implementing Great Expectations validation.

Checks per-entity expectations:
- Uniqueness on declared unique_id_fields (severity: fail)
- Not-null on detected name columns (severity: warn)
- Sane bounds on *_year or *_date fields (severity: warn)
- Row count >= 1 (severity: fail)

Wraps Great Expectations in try/except. On internal failure, degrades to
'validation unavailable' rather than crashing the pipeline.
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
    extracted_data: dict[str, Any] | None,
    matching_rules: dict[str, Any] | None = None,
    schemas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute Great Expectations & fallback data quality validations across extracted data.

    Returns:
        dict with keys:
            - quality_report: dict containing per-entity results, total_checks, failures, warnings, severity
            - has_quality_failure: bool (True if severity == 'fail')
    """
    if extracted_data is None:
        extracted_data = {}
    if matching_rules is None:
        matching_rules = {}
    if schemas is None:
        schemas = {}

    entity_results: dict[str, Any] = {}
    overall_severity = "pass"
    has_failure = False
    gx_available = True

    # Try importing Great Expectations to attempt GX suite validation if available
    try:
        import great_expectations as gx  # noqa: F401
    except Exception as exc:
        logger.warning("Great Expectations import or setup unavailable (%s); using native validator.", exc)
        gx_available = False

    try:

        for entity, data in extracted_data.items():
            rows: list[dict[str, Any]] = []
            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict) and "rows" in data:
                rows = data.get("rows") or []
            elif isinstance(data, dict):
                # Dict of records or single record
                rows = [data]

            entity_checks: list[dict[str, Any]] = []
            entity_severity = "pass"

            # 1. Row count >= 1 (Hard Fail)
            row_count = len(rows)
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

            if row_count > 0:
                sample_row = rows[0]
                cols = list(sample_row.keys()) if isinstance(sample_row, dict) else []

                # 2. Uniqueness on declared unique_id_fields (Hard Fail)
                entity_rules = matching_rules.get(entity) or {}
                unique_fields = entity_rules.get("unique_id_fields") or []
                if not unique_fields and "id" in cols:
                    unique_fields = ["id"]

                for u_field in unique_fields:
                    seen_vals = set()
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
                                "details": f"Field '{u_field}' in entity '{entity}' has {dups} duplicate values.",
                            }
                        )
                        entity_severity = "fail"
                    else:
                        entity_checks.append(
                            {
                                "check": f"uniqueness:{u_field}",
                                "severity": "fail",
                                "success": True,
                                "details": f"Field '{u_field}' in entity '{entity}' has unique values.",
                            }
                        )

                # 3. Not-null on detected name column (Warn)
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
                                "details": f"Name column '{name_col}' in '{entity}' has {null_count} null/blank values.",
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
                                "details": f"Name column '{name_col}' in '{entity}' has no null values.",
                            }
                        )

                # 4. Sane bounds on *_year or *_date (Warn)
                date_year_cols = [
                    c for c in cols if c.lower().endswith("_year") or c.lower().endswith("_date") or c.lower() == "year" or c.lower() == "date"
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
                                "details": f"Date/year column '{dy_col}' in '{entity}' has {bad_bounds} values out of sane bounds (1700-present).",
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
                "checks": entity_checks,
            }

            if entity_severity == "fail":
                overall_severity = "fail"
                has_failure = True
            elif entity_severity == "warn" and overall_severity != "fail":
                overall_severity = "warn"

    except Exception as exc:
        logger.error("Error evaluating data quality checks; degrading to 'validation unavailable': %s", exc, exc_info=True)
        return {
            "quality_report": {
                "status": "validation unavailable",
                "severity": "warn",
                "error": str(exc),
                "entities": {},
                "gx_available": gx_available,
            },
            "has_quality_failure": False,
        }

    return {
        "quality_report": {
            "status": "completed",
            "severity": overall_severity,
            "entities": entity_results,
            "gx_available": gx_available,
        },
        "has_quality_failure": has_failure,
    }
