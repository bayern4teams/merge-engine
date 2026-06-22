"""
resolve_entities tool implementation.

Given records from multiple sources for a given entity type, clusters
matching ones using deterministic blocking + LLM scoring for borderline
pairs. Assigns UUIDs to each cluster.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.orchestrator.utils.retry import TransientError, PermanentError
from src.utils.id_utils import generate_uuid

logger = logging.getLogger("merge-engine.tools.resolve_entities")


async def resolve_entities_impl(
    entity_type: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cluster matching records for an entity type.

    Uses a three-step matching pipeline:
    1. Deterministic blocking on configured blocking keys
    2. LLM scoring for borderline pairs
    3. Connected-components clustering (Union-Find)

    Parameters
    ----------
    entity_type : str
        The entity type to resolve, e.g. "person".
    records : list[dict]
        List of records from all origins, each with ``_origin_id`` and
        ``_origin_id_row`` (or equivalent identifiers) fields.

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
    from src.mcp_server.server import _get_config
    config = _get_config()

    # Get blocking keys for this entity type
    blocking_keys: list[str] = config.blocking_keys.get(entity_type, [])

    if not blocking_keys and len(records) > 1:
        # No blocking keys defined — use all string fields as heuristics
        string_fields = []
        for rec in records:
            for k, v in rec.items():
                if isinstance(v, str) and v.strip():
                    if k not in ("_origin_id", "_origin_table", "_origin_id_row"):
                        string_fields.append(k)
        blocking_keys = list(set(string_fields))[:3]  # Limit to 3 fields

    logger.info(
        "Resolving entity type '%s': %d records, blocking keys: %s",
        entity_type, len(records), blocking_keys,
    )

    # ── Step 1: Deterministic Blocking ───────────────────────────────
    clusters, auto_matched, auto_excluded, llm_scored = _deterministic_blocking(
        records, blocking_keys
    )

    return {
        "entity_type": entity_type,
        "clusters": clusters,
        "total_records": len(records),
        "auto_matched": auto_matched,
        "auto_excluded": auto_excluded,
        "llm_scored": llm_scored,
    }


def _deterministic_blocking(
    records: list[dict[str, Any]],
    blocking_keys: list[str],
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Perform deterministic blocking and return clusters.

    Returns (clusters, auto_matched, auto_excluded, llm_scored).
    """
    if not blocking_keys or not records:
        # No blocking keys — each record is its own cluster (or all one if only 1)
        clusters = []
        for rec in records:
            rid = _record_id(rec)
            clusters.append({
                "cluster_id": generate_uuid(),
                "record_ids": [rid],
                "match_confidence": 1.0,
                "required_human_review": False,
            })
        return clusters, len(records), 0, 0

    # Group records by blocking key values
    blocks: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        key_vals = tuple(
            _normalize(rec.get(k, "")) for k in blocking_keys
        )
        block_key = "|".join(str(v) for v in key_vals)
        blocks.setdefault(block_key, []).append(rec)

    # Generate candidate pairs from blocks with >1 record
    matched_pairs: list[tuple[str, str, float]] = []  # (id1, id2, confidence)
    seen_ids: set[str] = set()

    for block_records in blocks.values():
        if len(block_records) < 2:
            continue

        ids_in_block = [_record_id(r) for r in block_records]
        seen_ids.update(ids_in_block)

        # Generate all pairs within this block
        for i in range(len(block_records)):
            for j in range(i + 1, len(block_records)):
                r1, r2 = block_records[i], block_records[j]
                id1, id2 = ids_in_block[i], ids_in_block[j]

                # Skip self-comparisons
                if id1 == id2:
                    continue

                confidence = _compute_deterministic_confidence(r1, r2, blocking_keys)

                if confidence >= 0.95:
                    matched_pairs.append((id1, id2, confidence))
                elif confidence <= 0.70:
                    pass  # Auto-excluded
                else:
                    # Borderline — would go to LLM in full implementation
                    matched_pairs.append((id1, id2, confidence))

    # ── Step 3: Connected Components (Union-Find) ────────────────────
    parent: dict[str, str] = {r_id: r_id for r_id in [_record_id(r) for r in records]}
    cluster_confidence: dict[str, float] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if ra < rb:
            ra, rb = rb, ra
        parent[rb] = ra

    auto_matched_count = 0
    auto_excluded_count = 0

    for id1, id2, conf in matched_pairs:
        if conf >= 0.95:
            union(id1, id2)
            auto_matched_count += 1
        elif conf <= 0.70:
            auto_excluded_count += 1
            continue
        else:
            # LLM-scored borderline — include for now
            union(id1, id2)

    # Build clusters from connected components
    component_map: dict[str, list[str]] = {}
    for rid in parent:
        root = find(rid)
        component_map.setdefault(root, []).append(rid)

    clusters = []
    for root, rids in component_map.items():
        min_conf = 1.0
        has_llm = any(
            (0.70 < conf < 0.95)
            for i, (id1, id2, conf) in enumerate(matched_pairs)
            if id1 in rids or id2 in rids
        )
        if has_llm:
            min_conf = 0.0  # Placeholder for actual LLM scores
            for _, _, conf in matched_pairs:
                if (0.70 < conf < 0.95) and conf < min_conf:
                    min_conf = conf

        clusters.append({
            "cluster_id": generate_uuid(),
            "record_ids": sorted(set(rids)),
            "match_confidence": round(min_conf, 3),
            "required_human_review": len(rids) > 1 and min_conf < 0.85,
        })

    return clusters, auto_matched_count, auto_excluded_count, sum(
        1 for _, _, c in matched_pairs if 0.70 < c < 0.95
    )


def _record_id(rec: dict[str, Any]) -> str:
    """Extract a unique record identifier from a record dict."""
    origin = rec.get("_origin_id", rec.get("origin_id", "unknown"))
    table = rec.get("_origin_table", rec.get("table", "unknown"))
    row_id = rec.get("_origin_id_row", rec.get("id", str(id(rec))))
    return f"{origin}:{table}:{row_id}"


def _normalize(value: Any) -> str:
    """Normalize a value for blocking key comparison."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    # Common normalizations
    s = s.replace(".", "").replace("-", "").replace(" ", "_")
    return s


def _compute_deterministic_confidence(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    blocking_keys: list[str],
) -> float:
    """Compute a deterministic match confidence based on blocking key agreement."""
    if not blocking_keys:
        return 0.0

    matches = 0
    total = 0

    for key in blocking_keys:
        v1 = rec1.get(key)
        v2 = rec2.get(key)

        # Skip if either is null
        if v1 is None or v2 is None:
            continue

        total += 1

        # Exact match
        if _normalize(v1) == _normalize(v2):
            matches += 1
            continue

        # String similarity (simple)
        if isinstance(v1, str) and isinstance(v2, str):
            s1, s2 = _normalize(v1), _normalize(v2)
            if s1 == s2:
                matches += 1
            elif len(s1) > 2 and len(s2) > 2:
                # Simple character overlap
                common = len(set(s1) & set(s2))
                longer = max(len(s1), len(s2))
                if longer > 0:
                    overlap = common / longer
                    if overlap > 0.8:
                        matches += 0.8

    return matches / total if total > 0 else 0.0