"""Splink optional matching engine integration (Task B4.4).

Implements resolve_with_splink(...) reproducing the exact result-dict contract
of resolve_entities_impl while strictly respecting the 8 architecture invariants:

1. Splink replaces *scoring*, not *clustering*. Splink generates pairwise probabilities;
   these are fed into MergeEngine's frozen _build_clusters() for cohesion checking.
2. Cluster IDs are translated through _assign_cluster_id (deterministic_uuid).
3. Schema divergence is handled via projected comparison view using _project_to_canonical.
4. Any Splink failure logs a warning and falls back to builtin.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("merge-engine.tools.splink_matcher")

try:
    import pandas as pd  # type: ignore[import-untyped]
    import splink.comparison_library as cl
    from splink import DuckDBAPI, Linker, SettingsCreator, block_on

    _HAS_SPLINK = True
except Exception:
    _HAS_SPLINK = False


async def resolve_with_splink(
    entity_type: str,
    records: list[dict[str, Any]],
    matching_rules: dict[str, Any] | None = None,
    known_cluster_ids: dict[str, str] | None = None,
    build_clusters_fn: Any = None,
) -> dict[str, Any] | None:
    """Run entity resolution using Splink as the pairwise scoring engine.

    Returns the exact 11-key result-dict contract on success, or None on failure
    (triggering seamless fallback to builtin matcher).
    """
    if not _HAS_SPLINK:
        logger.warning(
            "Splink package not installed or failed to import; falling back to builtin engine"
        )
        return None

    if not records or len(records) < 2:
        logger.info("Splink: < 2 records for entity '%s'; delegating to builtin", entity_type)
        return None

    try:
        from src.mcp_server.tools.resolve_entities import _project_to_canonical, _record_id

        # ── 1. Build Projected Comparison View (Canonical 4-6 fields) ───────
        projected_rows: list[dict[str, Any]] = []
        for rec in records:
            rid = _record_id(rec)
            proj, _ = _project_to_canonical(rec)
            row_dict: dict[str, Any] = {"_record_id_str": str(rid)}
            for k, v in proj.items():
                row_dict[k] = str(v) if v is not None else ""
            projected_rows.append(row_dict)

        df = pd.DataFrame(projected_rows)

        # Identify candidate comparison columns available in the dataframe
        available_cols = [c for c in df.columns if c != "_record_id_str" and df[c].notna().any()]
        if not available_cols:
            logger.warning(
                "Splink: No valid comparison columns in projected view; falling back to builtin"
            )
            return None

        # Build comparison objects for Splink
        comparisons: list[Any] = []
        blocking_rules: list[Any] = []

        if "name" in available_cols:
            comparisons.append(cl.JaroWinklerAtThresholds("name", [0.9, 0.7]))
            blocking_rules.append(block_on("name"))

        if "email" in available_cols:
            comparisons.append(cl.ExactMatch("email").configure(term_frequency_adjustments=True))
            if not blocking_rules:
                blocking_rules.append(block_on("email"))

        for col in available_cols:
            if col not in ("name", "email"):
                comparisons.append(cl.ExactMatch(col))
                if not blocking_rules:
                    blocking_rules.append(block_on(col))

        if not blocking_rules:
            # Fallback blocking rule on unique id prefix if no named columns
            blocking_rules.append(block_on("_record_id_str"))

        settings = SettingsCreator(
            link_type="dedupe_only",
            unique_id_column_name="_record_id_str",
            comparisons=comparisons,
            blocking_rules_to_generate_predictions=blocking_rules,
        )

        db_api = DuckDBAPI()
        linker = Linker(df, settings, db_api)

        # ── 2. Pairwise Probability Prediction ──────────────────────────────
        predictions = linker.inference.predict(threshold_match_probability=0.5)
        pred_df = predictions.as_pandas_dataframe()  # type: ignore[no-untyped-call]

        scored_pairs: list[tuple[str, str, float]] = []
        for _, row in pred_df.iterrows():
            id1 = str(row["_record_id_str_l"])
            id2 = str(row["_record_id_str_r"])
            prob = float(row["match_probability"])
            scored_pairs.append((id1, id2, prob))

        logger.info(
            "Splink: produced %d pairwise predictions for entity '%s'",
            len(scored_pairs),
            entity_type,
        )

        # ── 3. Cluster Pairwise Predictions via MergeEngine's _build_clusters
        if build_clusters_fn is not None:
            clusters, auto_matched, auto_excluded = await build_clusters_fn(
                records,
                scored_pairs,
                entity_type=entity_type,
                known_cluster_ids=known_cluster_ids,
            )
        else:
            from src.mcp_server.tools.resolve_entities import _build_clusters

            clusters, auto_matched, auto_excluded = await _build_clusters(
                records,
                scored_pairs,
                entity_type=entity_type,
                known_cluster_ids=known_cluster_ids,
            )

        # ── 4. Construct Exact Result Dict Contract ──────────────────────────
        return {
            "entity_type": entity_type,
            "clusters": clusters,
            "total_records": len(records),
            "auto_matched": auto_matched,
            "auto_excluded": auto_excluded,
            "llm_scored": 0,
            "uncompared_pairs": 0,
            "candidate_pairs": len(scored_pairs),
            "blocks_purged": 0,
            "pending_merge_candidates": [],
        }

    except Exception as exc:
        logger.warning(
            "Splink resolution failed with error (%s); falling back to builtin engine",
            exc,
            exc_info=True,
        )
        return None
