"""S-J / S-O / S-P: prediction + explainability, clustering, review
prioritisation (Branch_Plan.md B.4; Splink.txt §30-§38).

The three read-only phases of the workbench. "Read-only" is load-bearing:
none of these mutate the model or the linker — they *score*, *cluster* and
*rank* against a model that S-N has (or will) version. The mutations live in
training (S-G..S-I) and activation (S-N); everything here is a query the UI
can re-issue for a different threshold without retraining.

* **S-J prediction & explainability** (§30, §31, §33):
  ``linker.inference.predict()`` scores every blocked pair, returning
  ``match_probability`` and ``match_weight`` per pair. The §33 "no opaque
  score" requirement is satisfied by ``explain_candidate``: for any one pair
  we call ``compare_two_records`` and report, per comparison and per level,
  the Bayes factor ``bf_<field>`` (the level's m/u ratio) and the
  TF-adjusted weight ``bf_tf_adj_<field>`` — the exact multiplicative
  decomposition behind that pair's score, in plain rows, not a blob.
* **S-O clustering** (§35, §36, §37):
  ``cluster_pairwise_predictions_at_threshold`` builds the cluster map at a
  probability threshold; ``compute_graph_metrics`` returns the graph
  metrics (``clusters``/``edges``/``nodes`` — installed 4.0.16 returns a
  ``GraphMetricsResults`` of ``DuckDBDataFrame``s, read via
  ``as_pandas_dataframe``). Cluster-size distribution is computed here from
  the map (the largest-cluster share is the over-merge alarm the §36
  "clustering is where matches silently become wrong" warning exists for).

  KNOWN LIMITATION, verified installed 4.0.16 (2026-08-23):
  ``cluster_using_single_best_links`` is **broken** for ``dedupe_only``
  linkers — it renders ``None AS source_dataset`` into its generated SQL
  even when a ``source_dataset`` column is configured, and fails with a
  ``BinderException``. S-O therefore uses threshold clustering only, and
  the limitation is recorded here so the next upgrade re-tests it instead
  of rediscovering it.

  SECOND KNOWN LIMITATION, live-reproduced against installed splink 4.0.16 +
  igraph 1.0.0 (2026-08-26): ``compute_graph_metrics`` always raises
  ``TypeError: 'numpy.float64' object cannot be interpreted as an integer``
  — reproduced with a plain Python ``float`` threshold and with
  ``numpy.float64`` alike, so it is not a type our call site is passing in.
  The failure is inside Splink's own dependency chain
  (``edge_metrics.compute_igraph_metrics`` → ``igraph.Graph.DataFrame`` →
  ``igraph.Graph.__init__``, which receives a numpy-typed vertex count where
  ``igraph`` 1.0.0 requires a native ``int``). ``run_clustering`` already
  catches this (graph metrics are a display extra, not core to S-O) and
  reports ``graph_metrics: {"error": ...}`` instead of failing the whole
  clustering call — this note exists so the next Splink/igraph upgrade
  re-tests it instead of re-discovering it via a silent empty graph-metrics
  panel.
* **S-P review prioritisation** (§38, §39): the candidate pairs are split
  into three bands by the two thresholds — auto-match (``>= match_threshold``),
  auto-keep-apart (``<= exclude_threshold``), and *review* (between them).
  The review band is ordered by closeness to the decision boundary
  (``|0.5 - p|`` ascending), which is an honest proxy for "needs a human
  most": a pair at 0.51 tells us almost nothing, a pair at 0.99 tells us a
  lot. Every queued pair carries its per-field Bayes factors so the reviewer
  sees *why* it landed in the middle, not just that it did.

All functions are pure of state (CLAUDE.md) — the ``Linker`` and its input
DataFrame are passed in, never cached — and each calls
:func:`splink_compat.require_splink` at entry.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.splink_common import unique_id_column_of
from src.mcp_server.tools.splink_compat import imported_version, require_splink

logger = logging.getLogger("merge-engine.tools.splink_inference")

# Default probability thresholds for banding (S-P). These are *display
# defaults* the UI can override per run — they are not the model's output.
# 0.5 is the natural decision boundary for a match probability; the bands
# around it are where humans spend their time.
DEFAULT_MATCH_THRESHOLD = 0.5
DEFAULT_EXCLUDE_THRESHOLD = 0.2


def _sdf_to_records(sdf: Any) -> list[dict[str, Any]]:
    """A SplinkDataFrame to a plain list of row-dicts (JSON-safe)."""
    df = sdf.as_pandas_dataframe()
    records = df.to_dict("records")
    # pandas -> numpy scalar types are not JSON-serialisable; coerce.
    import math

    def _fix(v: Any) -> Any:
        if isinstance(v, float) and math.isnan(v):
            return None
        if hasattr(v, "item"):  # numpy scalar
            return _fix(v.item())
        return v

    return [{k: _fix(v) for k, v in row.items()} for row in records]


# ── S-J — prediction & explainability ─────────────────────────────────────


def run_prediction(
    linker: Any,
    *,
    match_probability_threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> dict[str, Any]:
    """S-J: score every blocked pair with the (trained) model.

    Returns the full prediction table (``{unique_id_column}_l``,
    ``{unique_id_column}_r`` — the workbench's configured unique-id column,
    ``_record_id_str`` by default, not a fixed ``id_l``/``id_r``,
    ``match_probability``, ``match_weight``, per-field ``gamma_*`` levels
    and ``bf_*`` Bayes factors — the raw Splink 4.0.16 columns) plus the
    band summary that S-P will prioritise. ``pairs_scored`` is the count the
    UI shows next to the S-E budget, so a human can see the two numbers are
    the same one.

    The threshold is recorded in the output because the *same* prediction
    table supports any banding afterwards — re-band without re-predicting.
    """
    require_splink()
    pred = linker.inference.predict()
    records = _sdf_to_records(pred)
    n = len(records)
    above = sum(
        1 for r in records if (r.get("match_probability") or 0) >= match_probability_threshold
    )
    return {
        "predictions": records,
        "pairs_scored": n,
        "pairs_above_threshold": above,
        "pairs_below_threshold": n - above,
        "match_probability_threshold": float(match_probability_threshold),
        "splink_version": imported_version(),
    }


def explain_candidate(
    linker: Any, record_a: dict[str, Any], record_b: dict[str, Any]
) -> dict[str, Any]:
    """S-J/§33: the per-field, per-level Bayes-factor decomposition of one
    pair's score.

    ``compare_two_records`` (installed 4.0.16: takes two *record dicts*,
    returns the explain table directly) yields, per comparison, ``bf_<field>``
    (the m/u ratio for that level — how much more likely this level is among
    matches than non-matches) and ``bf_tf_adj_<field>`` (the same, after term
    frequency adjustment). The score is the prior times the product of these
    — so this table is the *derivation*, not a re-statement of the score.

    The two record dicts must contain the unique-id column plus every
    compared field, exactly as the linker's input did.
    """
    require_splink()
    uid = unique_id_column_of(linker)
    sdf = linker.inference.compare_two_records(record_a, record_b)
    rows = _sdf_to_records(sdf)
    if not rows:
        return {
            "fields": [],
            "match_probability": None,
            "match_weight": None,
            "note": "compare_two_records returned no rows for this pair",
        }
    row = rows[0]
    # per-field bf, read straight off the columns (field names vary, so scan
    # the keys for the bf_/bf_tf_adj_ prefix rather than assuming a fixed set)
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, val in row.items():
        if key.startswith("bf_") and not key.startswith("bf_tf_adj_"):
            field = key[3:]
            if field in seen:
                continue
            seen.add(field)
            fields.append(
                {
                    "field": field,
                    "level": row.get(f"gamma_{field}"),
                    "bayes_factor": val,
                    "tf_adjusted_bayes_factor": row.get(f"bf_tf_adj_{field}"),
                }
            )
    return {
        "id_l": row.get(f"{uid}_l"),
        "id_r": row.get(f"{uid}_r"),
        "match_probability": row.get("match_probability"),
        "match_weight": row.get("match_weight"),
        "fields": fields,
    }


# ── S-O — clustering ──────────────────────────────────────────────────────


def run_clustering(
    linker: Any,
    *,
    match_probability_threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> dict[str, Any]:
    """S-O: cluster the predictions at a probability threshold and report the
    shape of the result.

    Returns the cluster map (every record → its ``cluster_id``, singletons
    included — a record not in any multi-record cluster is still a cluster
    of one, and counting them is part of the honesty), the size distribution
    (how many clusters of each size, and the largest cluster's share of all
    records — the over-merge alarm), and the graph metrics
    (``compute_graph_metrics``: node/edge counts at this threshold).

    §36's warning is encoded in the output shape: the
    ``largest_cluster_share`` number is what the UI flags, because a single
    giant cluster is the failure mode where "high precision per pair" quietly
    becomes "one wrong merge of 50 records".
    """
    require_splink()
    pred = linker.inference.predict()
    cluster_map_sdf = linker.clustering.cluster_pairwise_predictions_at_threshold(
        pred, threshold_match_probability=match_probability_threshold
    )
    cluster_map = _sdf_to_records(cluster_map_sdf)
    n_records = len(cluster_map)

    # size distribution
    sizes: dict[str, int] = {}
    for row in cluster_map:
        sizes[str(row["cluster_id"])] = sizes.get(str(row["cluster_id"]), 0) + 1
    size_hist: dict[int, int] = {}
    for s in sizes.values():
        size_hist[s] = size_hist.get(s, 0) + 1
    largest = max(sizes.values()) if sizes else 0
    multi = {k: v for k, v in sizes.items() if v > 1}

    # graph metrics (installed 4.0.16: GraphMetricsResults of DuckDBDataFrames)
    graph_metrics: dict[str, Any]
    try:
        gm = linker.clustering.compute_graph_metrics(
            pred, cluster_map_sdf, threshold_match_probability=match_probability_threshold
        )
        nodes = _sdf_to_records(gm.nodes)
        edges = _sdf_to_records(gm.edges)
        clusters_gm = _sdf_to_records(gm.clusters)
        graph_metrics = {
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "n_clusters_from_metrics": len(clusters_gm),
        }
    except Exception as exc:  # graph metrics are a display extra; don't fail the run
        logger.warning("compute_graph_metrics failed: %s", exc)
        graph_metrics = {"error": str(exc)}

    return {
        "cluster_map": cluster_map,
        "n_records": n_records,
        "n_clusters": len(sizes),
        "n_multi_record_clusters": len(multi),
        "size_distribution": size_hist,
        "largest_cluster_size": largest,
        "largest_cluster_share": round(largest / n_records, 4) if n_records else 0.0,
        "graph_metrics": graph_metrics,
        "match_probability_threshold": float(match_probability_threshold),
    }


# ── S-P — review prioritisation ───────────────────────────────────────────


def prioritise_review(
    predictions: list[dict[str, Any]],
    *,
    match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    exclude_threshold: float = DEFAULT_EXCLUDE_THRESHOLD,
    limit: int = 200,
) -> dict[str, Any]:
    """S-P: split the scored pairs into auto-match / auto-keep-apart / review
    and return the review band ordered by "needs a human most".

    Pure function over the S-J ``predictions`` list (no linker needed —
    banding is a view, not a query). Order within the review band is
    ``|0.5 - match_probability|`` ascending: the pair closest to the coin
    flip is the pair that most needs a human, which is the opposite of what
    a naive "lowest score first" queue would show (those are the ones the
    model is *confidently* keeping apart).

    Every queued row keeps its per-field ``gamma_*`` levels so the UI can
    render the "why" inline. ``limit`` caps the queue returned, but the band
    counts are for the *whole* band — the UI shows "N more not shown", not a
    silent truncation.
    """
    if match_threshold < exclude_threshold:
        raise ValueError(
            f"match_threshold ({match_threshold}) must be >= exclude_threshold ({exclude_threshold})"
        )
    auto_match = [r for r in predictions if (r.get("match_probability") or 0) >= match_threshold]
    auto_apart = [r for r in predictions if (r.get("match_probability") or 0) <= exclude_threshold]
    review = [
        r
        for r in predictions
        if exclude_threshold < (r.get("match_probability") or 0) < match_threshold
    ]
    review.sort(key=lambda r: abs(0.5 - (r.get("match_probability") or 0)))
    return {
        "auto_match_count": len(auto_match),
        "auto_apart_count": len(auto_apart),
        "review_count": len(review),
        "review_queue": review[:limit],
        "review_queue_truncated": len(review) > limit,
        "match_threshold": float(match_threshold),
        "exclude_threshold": float(exclude_threshold),
        "ordering": "distance-from-0.5 ascending (most uncertain first)",
    }
