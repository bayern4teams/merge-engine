"""Apply a reviewer's decisions from the entity-matches gate.

The matches gate shows clusters the pipeline is unsure about and asks a human
to confirm or break them. Those decisions have to actually change
``entity_clusters`` before ``unify_entities`` runs, or the review is theatre.

Merging two different real entities destroys data irreversibly, while leaving
duplicates is recoverable — so ``split`` is always honoured, and an unparseable
decision splits rather than merges.
"""

from __future__ import annotations

import logging
from typing import Any

from src.utils.id_utils import deterministic_uuid

logger = logging.getLogger("merge-engine.cluster_edits")

_KEEP = {"keep", "confirm", "approve", "accept", "yes", "true"}
_SPLIT = {"split", "reject", "separate", "no", "false"}


def apply_cluster_decisions(
    entity_clusters: dict[str, list[dict[str, Any]]],
    decisions: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], list[dict[str, Any]]]:
    """Rebuild ``entity_clusters`` from the reviewer's decisions.

    Each decision is ``{"cluster_id": str, "entity_type": str, "action": str}``
    and may carry ``groups``: a list of record-id lists describing exactly how
    to partition the cluster. Without ``groups``, ``split`` breaks the cluster
    into one cluster per record.

    Returns ``(updated, stats, split_pairs)``:

    - ``updated`` / ``stats``: the rebuilt clusters and a ``{kept, split,
      unknown}`` tally, as before.
    - ``split_pairs``: every pair from a SPLIT cluster's own ``match_evidence``
      (Phase 7.1), tagged with the original ``cluster_id`` — the evidence a
      split would otherwise destroy outright, since the new per-partition
      clusters carry no ``match_evidence`` of their own. The caller persists
      these as ``human_override: "split"`` merge-audit rows (Phase 7.2) so a
      human's "these are NOT the same entity" decision is queryable exactly
      like an auto/LLM merge decision is, instead of vanishing.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions or []:
        if not isinstance(decision, dict):
            continue
        cid = str(decision.get("cluster_id") or "").strip()
        if cid:
            by_id[cid] = decision

    stats = {"kept": 0, "split": 0, "unknown": 0}
    split_pairs: list[dict[str, Any]] = []
    if not by_id:
        return entity_clusters, stats, split_pairs

    updated: dict[str, list[dict[str, Any]]] = {}
    for entity_type, clusters in (entity_clusters or {}).items():
        rebuilt: list[dict[str, Any]] = []
        for cluster in clusters or []:
            decision = by_id.get(str(cluster.get("cluster_id") or ""))
            action = str((decision or {}).get("action") or "").strip().lower()
            record_ids = list(cluster.get("record_ids") or [])

            if decision is None or action in _KEEP:
                if decision is not None:
                    stats["kept"] += 1
                    # Reviewed and confirmed — don't re-flag it downstream.
                    cluster = {**cluster, "required_human_review": False, "reviewed_by_human": True}
                rebuilt.append(cluster)
                continue

            if action not in _SPLIT:
                logger.warning(
                    "Unknown cluster action %r for %s — splitting, because "
                    "an unintended merge is not recoverable.",
                    action,
                    cluster.get("cluster_id"),
                )
                stats["unknown"] += 1

            # Capture the original cluster's per-pair evidence BEFORE it is
            # torn apart below — the new per-partition clusters start with
            # no match_evidence of their own, so this is the only chance to
            # keep it.
            original_cluster_id = str(cluster.get("cluster_id") or "")
            for pair in (cluster.get("match_evidence") or {}).get("pairs", []):
                record_a, record_b = pair.get("a"), pair.get("b")
                if not record_a or not record_b:
                    continue
                split_pairs.append(
                    {
                        "entity_type": entity_type,
                        "cluster_id": original_cluster_id,
                        "record_a": record_a,
                        "record_b": record_b,
                        "confidence": pair.get("confidence"),
                        "method": pair.get("method", ""),
                        "matched_fields": pair.get("matched_fields", []),
                        "mismatched_fields": pair.get("mismatched_fields", []),
                        "reason": pair.get("reason"),
                    }
                )

            groups = decision.get("groups")
            if isinstance(groups, list) and groups:
                partitions = [
                    [r for r in group if r in record_ids]
                    for group in groups
                    if isinstance(group, list)
                ]
                # Anything the reviewer didn't place stays on its own rather
                # than being silently dropped from the run.
                placed = {r for group in partitions for r in group}
                partitions.extend([r] for r in record_ids if r not in placed)
            else:
                partitions = [[r] for r in record_ids]

            partitions = [p for p in partitions if p]
            stats["split"] += 1
            for part in partitions:
                members = sorted(set(part))
                rebuilt.append(
                    {
                        "cluster_id": deterministic_uuid(entity_type, *members),
                        "record_ids": members,
                        "match_confidence": 1.0
                        if len(members) == 1
                        else cluster.get("match_confidence", 1.0),
                        "required_human_review": False,
                        "reviewed_by_human": True,
                    }
                )
        updated[entity_type] = rebuilt

    logger.info(
        "Applied %d cluster decisions: %d kept, %d split, %d unrecognised (%d evidence pair(s) preserved)",
        len(by_id),
        stats["kept"],
        stats["split"],
        stats["unknown"],
        len(split_pairs),
    )
    return updated, stats, split_pairs
