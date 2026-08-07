"""Relational and collective entity resolution module (Task B4.2).

Injects resolved neighbour cluster IDs (employer, CEO/founder, contact, location)
as extra comparison signals during multi-pass entity resolution.

Safety Invariant:
Neighbour evidence weight is strictly capped (MAX_NEIGHBOUR_WEIGHT = 0.15), so neighbour
similarity ALONE can NEVER push a non-matching pair over the _AUTO_MATCH threshold (0.95).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("merge-engine.tools.relational_evidence")

# Hard-cap on relational evidence score boost.
# Non-matching pairs with 0.0 base score reach at most 0.15, preventing cascading over-merges.
MAX_NEIGHBOUR_WEIGHT = 0.15

# Fields containing relational references to other entity clusters
RELATIONAL_FIELDS = (
    "employer_cluster_id",
    "company_cluster_id",
    "ceo_cluster_id",
    "founder_cluster_id",
    "contact_cluster_id",
    "location_cluster_id",
)


def inject_relational_evidence(
    records: list[dict[str, Any]],
    resolved_cluster_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Enrich records with resolved neighbour cluster IDs from prior resolution passes.

    Args:
        records: List of record dicts to enrich.
        resolved_cluster_map: Map of record_id -> resolved cluster_id.

    Returns:
        Enriched records with neighbour cluster IDs populated.
    """
    enriched_records: list[dict[str, Any]] = []

    for rec in records:
        rec_copy = dict(rec)
        # Check if record's employer / company reference maps to a resolved cluster
        employer_ref = rec.get("company_name") or rec.get("org_name") or rec.get("employer")
        if employer_ref and str(employer_ref) in resolved_cluster_map:
            rec_copy["employer_cluster_id"] = resolved_cluster_map[str(employer_ref)]

        enriched_records.append(rec_copy)

    return enriched_records


def compute_relational_score_boost(
    rec_a: dict[str, Any],
    rec_b: dict[str, Any],
) -> float:
    """Calculate the match confidence boost from shared neighbour cluster IDs.

    Returns a float in range [0.0, MAX_NEIGHBOUR_WEIGHT].
    Asserted by unit test to never exceed MAX_NEIGHBOUR_WEIGHT (0.15).
    """
    matching_neighbours = 0
    total_relational_checked = 0

    for field in RELATIONAL_FIELDS:
        val_a = rec_a.get(field)
        val_b = rec_b.get(field)
        if val_a and val_b:
            total_relational_checked += 1
            if val_a == val_b:
                matching_neighbours += 1

    if not total_relational_checked or matching_neighbours == 0:
        return 0.0

    raw_boost = 0.10 * (matching_neighbours / total_relational_checked)
    # Strictly bound by MAX_NEIGHBOUR_WEIGHT (0.15)
    return min(raw_boost, MAX_NEIGHBOUR_WEIGHT)
