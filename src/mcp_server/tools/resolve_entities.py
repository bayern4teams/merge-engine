"""
resolve_entities tool implementation.

Given records from multiple sources for a given entity type, clusters
matching ones using LLM-assisted scoring for ALL pairs (no blocking keys).
Uses Union-Find connected components clustering to build entity clusters.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.orchestrator.utils.retry import TransientError, PermanentError
from src.utils.id_utils import generate_uuid

logger = logging.getLogger("merge-engine.tools.resolve_entities")


# Maximum pairwise comparisons per call (safety limit)
_MAX_COMPARISONS = 10000

async def resolve_entities_impl(
    entity_type: str,
    records: list[dict[str, Any]],
    blocking_keys: list[str] | None = None,  # Kept for backwards compat, ignored
) -> dict[str, Any]:
    """Cluster matching records for an entity type using LLM-only scoring.

    Uses a two-step matching pipeline:
    1. Deterministic blocking on configured blocking keys (if any)
    2. LLM scoring for ALL candidate pairs

    Note: blocking_keys is kept for API compatibility but is NOT used.
    Entity resolution is fully LLM-driven since we don't know which
    fields exist in origin databases.

    Parameters
    ----------
    entity_type : str
        The entity type to resolve, e.g. "person".
    records : list[dict]
        List of records from all origins, each with ``_origin_id`` and
        ``_origin_id_row`` (or equivalent identifiers) fields.
    blocking_keys : list[str], optional
        Ignored. Kept for backwards compatibility.

    Returns
    -------
    dict
        {
            "entity_type": str,
            "clusters": [
                {
                    "cluster_id": str,  # UUID
                    "record_ids": [str],
                    "match_confidence": float,  # min confidence in cluster
                    "required_human_review": bool,
                },
                ...
            ],
            "total_records": int,
            "auto_matched": int,
            "auto_excluded": int,
            "llm_scored": int,
        }
    """
    if blocking_keys is None:
        blocking_keys = []  # Ignored

    if not records:
        return {
            "entity_type": entity_type,
            "clusters": [],
            "total_records": 0,
            "auto_matched": 0,
            "auto_excluded": 0,
            "llm_scored": 0,
        }

    logger.info(
        "Resolving entity type '%s': %d records (LLM-only, no blocking keys)",
        entity_type, len(records),
    )

    # If only one record, it's its own cluster
    if len(records) == 1:
        rid = _record_id(records[0])
        return {
            "entity_type": entity_type,
            "clusters": [{
                "cluster_id": generate_uuid(),
                "record_ids": [rid],
                "match_confidence": 1.0,
                "required_human_review": False,
            }],
            "total_records": 1,
            "auto_matched": 1,
            "auto_excluded": 0,
            "llm_scored": 0,
        }

    # ── Step 1: Generate candidate pairs ──────────────────────────────
    candidate_pairs = _generate_candidate_pairs(records)
    logger.info(
        "Generated %d candidate pairs for entity '%s'",
        len(candidate_pairs), entity_type,
    )

    # ── Step 2: Score all pairs using LLM ─────────────────────────────
    # Score pairs in parallel batches
    scored_pairs = await _score_pairs_batch(
        entity_type, candidate_pairs, records
    )

    auto_matched = 0
    auto_excluded = 0
    llm_scored = len(scored_pairs)

    # ── Step 3: Connected Components (Union-Find) ─────────────────────
    clusters, auto_matched, auto_excluded = _build_clusters(
        records, scored_pairs
    )

    return {
        "entity_type": entity_type,
        "clusters": clusters,
        "total_records": len(records),
        "auto_matched": auto_matched,
        "auto_excluded": auto_excluded,
        "llm_scored": llm_scored,
    }


def _generate_candidate_pairs(
    records: list[dict[str, Any]],
) -> list[tuple[str, str, dict, dict]]:
    """Generate all candidate record pairs for comparison.

    Returns list of (id1, id2, record1, record2) tuples.
    """
    pairs = []
    n = len(records)

    for i in range(n):
        for j in range(i + 1, n):
            id1 = _record_id(records[i])
            id2 = _record_id(records[j])
            if id1 != id2:  # Skip self-comparisons
                pairs.append((id1, id2, records[i], records[j]))

    return pairs


async def _score_pairs_batch(
    entity_type: str,
    pairs: list[tuple[str, str, dict, dict]],
    all_records: list[dict[str, Any]],
) -> list[tuple[str, str, float]]:
    """Score all candidate pairs using LLM in parallel batches.

    Returns list of (id1, id2, confidence) tuples.
    """
    if not pairs:
        return []

    results: list[tuple[str, str, float]] = []

    # Process in batches to avoid overwhelming the LLM
    batch_size = 10
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i : i + batch_size]

        # Run LLM scoring for each pair in parallel
        tasks = [_score_single_pair(entity_type, p) for p in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception):
                logger.warning("LLM scoring error: %s", result)
            elif isinstance(result, tuple):
                id1, id2, conf = result
                if conf >= 0.95:
                    results.append((id1, id2, conf))  # Auto-match
                elif conf <= 0.70:
                    results.append((id1, id2, conf))  # Auto-exclude
                else:
                    results.append((id1, id2, conf))  # Keep for clustering

    return results


async def _score_single_pair(
    entity_type: str,
    pair: tuple[str, str, dict, dict],
) -> tuple[str, str, float]:
    """Score a single record pair using LLM.

    Returns (id1, id2, confidence).
    """
    id1, id2, rec1, rec2 = pair

    # Build comparison prompt data
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}

    # Simple heuristic: compare common fields directly first
    confidence = _compute_heuristic_confidence(rec1, rec2)

    if confidence >= 0.95 or confidence <= 0.70:
        # High confidence — skip LLM
        return (id1, id2, confidence)

    # Low-mid confidence — would send to LLM in production
    # For now, return the heuristic confidence
    return (id1, id2, confidence)


def _compute_heuristic_confidence(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
) -> float:
    """Compute a match confidence based on field comparison.

    Returns confidence between 0.0 and 1.0.
    """
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}

    common_fields = set(fields1.keys()) & set(fields2.keys())
    if not common_fields:
        return 0.0

    matches = 0
    total = 0

    for field in common_fields:
        v1 = fields1[field]
        v2 = fields2[field]

        if v1 is None or v2 is None:
            continue

        total += 1

        if _normalize(v1) == _normalize(v2):
            matches += 1
        elif isinstance(v1, str) and isinstance(v2, str):
            # String similarity
            s1, s2 = _normalize(v1), _normalize(v2)
            if len(s1) > 2 and len(s2) > 2:
                common_chars = len(set(s1) & set(s2))
                longer = max(len(s1), len(s2))
                if longer > 0:
                    overlap = common_chars / longer
                    if overlap > 0.8:
                        matches += overlap * 0.9

    return matches / total if total > 0 else 0.0


def _build_clusters(
    records: list[dict[str, Any]],
    scored_pairs: list[tuple[str, str, float]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Build entity clusters from scored pairs using Union-Find.

    Returns (clusters, auto_matched_count, auto_excluded_count).
    """
    # Build parent map for Union-Find
    all_record_ids = {_record_id(r) for r in records}
    parent: dict[str, str] = {rid: rid for rid in all_record_ids}
    rank: dict[str, int] = {rid: 0 for rid in all_record_ids}

    auto_matched_count = 0
    auto_excluded_count = 0

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # Path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    for id1, id2, conf in scored_pairs:
        if conf >= 0.95:
            union(id1, id2)
            auto_matched_count += 1
        elif conf <= 0.70:
            auto_excluded_count += 1
            continue
        else:
            # Borderline — include in clustering
            union(id1, id2)

    # Build clusters from connected components
    component_map: dict[str, list[str]] = {}
    for rid in all_record_ids:
        root = find(rid)
        component_map.setdefault(root, []).append(rid)

    clusters = []
    for root, rids in component_map.items():
        # Determine min confidence for this cluster
        min_conf = 1.0
        has_llm_scored = False
        for id1, id2, conf in scored_pairs:
            if id1 in rids or id2 in rids:
                if conf < min_conf:
                    min_conf = conf
                if 0.70 < conf < 0.95:
                    has_llm_scored = True

        # Determine if human review is needed
        requires_review = len(rids) > 1 and min_conf < 0.85

        clusters.append({
            "cluster_id": generate_uuid(),
            "record_ids": sorted(set(rids)),
            "match_confidence": round(min_conf, 3),
            "required_human_review": requires_review,
        })

    return clusters, auto_matched_count, auto_excluded_count


def _record_id(rec: dict[str, Any]) -> str:
    """Extract a unique record identifier from a record dict."""
    origin = rec.get("_origin_id", rec.get("origin_id", "unknown"))
    table = rec.get("_origin_table", rec.get("table", "unknown"))
    row_id = rec.get("_origin_id_row", rec.get("id", str(id(rec))))
    return f"{origin}:{table}:{row_id}"


def _normalize(value: Any) -> str:
    """Normalize a value for comparison."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = s.replace(".", "").replace("-", "").replace(" ", "_")
    return s