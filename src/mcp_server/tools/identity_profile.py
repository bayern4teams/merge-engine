"""Identity-key discovery and column profiling tool (Task B4.1).

Profiles origin table columns for:
- within-origin uniqueness ratio (cardinality / non-null count)
- cross-origin value overlap (after normalisation)
- null rate, cardinality, sample values

Classifies column roles into:
- identity_key: high uniqueness (~1.0), high cross-origin overlap (e.g. website, ticker, tax_id)
- surrogate_key: high uniqueness (~1.0), ~0.0 cross-origin overlap (e.g. companyID, person_id)
- strong_signal: moderate uniqueness / high signal (e.g. full_name, email)
- weak_signal / noise
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

logger = logging.getLogger("merge-engine.tools.identity_profile")

_HAS_DESBORDANTE = importlib.util.find_spec("desbordante") is not None


def normalize_val(val: Any) -> str:
    """Normalize string value for cross-origin overlap comparison."""
    if val is None:
        return ""
    s = str(val).strip().lower()
    # Strip protocols for URLs
    for prefix in ("https://", "http://", "www."):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    return s.strip("/")


def profile_table_columns(
    rows: list[dict[str, Any]],
    origin_id: str = "origin1",
    table_name: str = "table",
    cross_rows: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Profile columns of an origin table.

    Returns a dict mapping col_name -> profile_dict containing:
    uniqueness_ratio, cross_overlap_ratio, null_rate, cardinality, role, samples.
    """
    if not rows:
        return {}

    total_rows = len(rows)
    cols = [k for k in rows[0] if not str(k).startswith("_")]

    # Pre-build cross-origin set per normalized column value if cross_rows provided
    cross_values: set[str] = set()
    if cross_rows:
        for r in cross_rows:
            for k, v in r.items():
                if not str(k).startswith("_") and v is not None:
                    norm = normalize_val(v)
                    if norm:
                        cross_values.add(norm)

    profiles: dict[str, dict[str, Any]] = {}

    for col in cols:
        vals = [r.get(col) for r in rows]
        non_nulls = [v for v in vals if v is not None and str(v).strip() != ""]
        null_count = total_rows - len(non_nulls)
        null_rate = null_count / total_rows if total_rows > 0 else 1.0

        distinct_vals = set(non_nulls)
        cardinality = len(distinct_vals)
        uniqueness_ratio = cardinality / len(non_nulls) if non_nulls else 0.0

        # Overlap with cross-origin values
        overlap_count = 0
        norm_distinct = {normalize_val(v) for v in distinct_vals if normalize_val(v)}
        if cross_values and norm_distinct:
            overlap_count = sum(1 for v in norm_distinct if v in cross_values)
            cross_overlap_ratio = overlap_count / len(norm_distinct)
        else:
            cross_overlap_ratio = 0.0

        # Classify role mechanically from profile measurements
        role = classify_column_role(col, uniqueness_ratio, cross_overlap_ratio, null_rate)

        profiles[col] = {
            "origin_id": origin_id,
            "table_name": table_name,
            "column_name": col,
            "total_rows": total_rows,
            "null_rate": round(null_rate, 4),
            "cardinality": cardinality,
            "uniqueness_ratio": round(uniqueness_ratio, 4),
            "cross_overlap_ratio": round(cross_overlap_ratio, 4),
            "role": role,
            "samples": [str(v) for v in list(distinct_vals)[:5]],
        }

    return profiles


def classify_column_role(
    col_name: str,
    uniqueness_ratio: float,
    cross_overlap_ratio: float,
    null_rate: float,
) -> str:
    """Classify column role based on profile measurements.

    Roles:
    - identity_key: ~1.0 uniqueness, high cross-origin overlap
    - surrogate_key: ~1.0 uniqueness, ~0.0 cross-origin overlap
    - strong_signal: moderate/high uniqueness, ordinary signal
    - weak_signal: low uniqueness or high null rate
    - noise: irrelevant / internal columns
    """
    col_lower = col_name.lower()

    # Identity key hints (website, domain, ticker, tax_id, reg_no)
    if (
        any(
            h in col_lower
            for h in (
                "website",
                "homepage",
                "domain",
                "url",
                "ticker",
                "tax_id",
                "vat",
                "ssn",
                "registration",
            )
        )
        and uniqueness_ratio >= 0.8
    ):
        return "identity_key"

    # Surrogate key hints (companyID, person_id, row_id)
    if (
        any(h in col_lower for h in ("companyid", "personid", "surrogate", "internal_id", "row_id"))
        and uniqueness_ratio >= 0.9
        and cross_overlap_ratio <= 0.1
    ):
        return "surrogate_key"

    if uniqueness_ratio >= 0.95:
        if cross_overlap_ratio >= 0.2:
            return "identity_key"
        return "surrogate_key"

    if null_rate > 0.8:
        return "weak_signal"

    if uniqueness_ratio >= 0.3:
        return "strong_signal"

    return "weak_signal"


def align_cross_origin_fields(
    profile_a: dict[str, dict[str, Any]],
    profile_b: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Align field names across two profiles (origin1 -> origin2).

    Returns a dict mapping col_a -> col_b.
    """
    alignment: dict[str, str] = {}
    used_b: set[str] = set()

    for col_a, p_a in profile_a.items():
        if p_a["role"] in ("noise", "surrogate_key"):
            continue
        col_a_lower = col_a.lower()

        best_b = None
        best_score = -1.0

        for col_b, p_b in profile_b.items():
            if col_b in used_b or p_b["role"] in ("noise", "surrogate_key"):
                continue
            col_b_lower = col_b.lower()

            # Exact name match
            if col_a_lower == col_b_lower:
                best_b = col_b
                break

            # Synonymous name hints
            if (
                ("name" in col_a_lower and "name" in col_b_lower)
                or ("mail" in col_a_lower and "mail" in col_b_lower)
                or ("phone" in col_a_lower and "phone" in col_b_lower)
                or ("org" in col_a_lower and "company" in col_b_lower)
            ):
                score = 0.8
                if score > best_score:
                    best_score = score
                    best_b = col_b

        if best_b:
            alignment[col_a] = best_b
            used_b.add(best_b)

    return alignment
