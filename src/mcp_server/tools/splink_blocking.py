"""S-E / S-F: prediction blocking and training blocking (Branch_Plan.md B.4;
Splink.txt §18-§20).

Two *separate* blocking concerns with a *separate* data model, because
Splink.txt §20 is explicit that the separation is ours to enforce (the API is
shared):

* **S-E prediction blocking** — the rules that generate the candidate pairs a
  model *scores* at prediction time. Every rule is **costed before
  execution** (``count_comparisons_from_blocking_rule`` + ``n_largest_blocks``)
  and flagged ``"⚠ Too broad"`` when it would exceed the configured pair
  budget. This is the structural fix for the 2.4M-uncompared-pairs failure
  (ADR 0014): instead of generating pairs and truncating them silently, the
  cost is known in advance and the budget is a visible, enforced number.
* **S-F training blocking** — the rule(s) EM *trains on*. A different screen
  and a different rule set on purpose: training blocking should be
  *match-heavy* (it needs real positive pairs in the block to estimate m),
  while prediction blocking should be *recall-heavy* (it must not miss a true
  pair, whatever the cost). Conflating the two is how a model trained on a
  too-narrow rule under-scores true matches at prediction time.

Neither function claims recall without labels — §19 is explicit, and the
return shapes carry a ``recall_claims_allowed: False`` marker so the UI can
refuse to render a recall badge off these numbers alone.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.splink_common import DEFAULT_UNIQUE_ID_COLUMN, LinkType
from src.mcp_server.tools.splink_compat import require_splink

logger = logging.getLogger("merge-engine.tools.splink_blocking")

# Default pair budget: a single prediction-blocking rule that would generate
# more candidate pairs than this is flagged "⚠ Too broad". 2.4M was the
# number that silently truncated a real run (Branch_Plan.md §0.5-q); a
# quarter of that, ~600k, is already large enough to score in seconds and
# small enough that "too broad" means *something* on this data.
DEFAULT_PAIR_BUDGET = 600_000


def _rule_to_str(rule: Any) -> str:
    """A blocking rule to its SQL-ish string form for display.

    Verified installed-4.0.16: ``BlockingRuleCreator`` (and its concrete
    ``ExactMatchRule``/``AndRule``/...) objects have *no* meaningful
    ``__str__`` (they render as ``<... object at 0x...>``), but they do
    expose ``create_sql()``, which is the actual blocking SQL — exactly what
    the UI's cost table wants to show. A plain string rule (Splink accepts
    those too) is returned unchanged.
    """
    create_sql = getattr(rule, "create_sql", None)
    if callable(create_sql):
        try:
            return str(create_sql())
        except Exception:
            pass
    return str(rule)


def cost_rule(
    df: Any,
    rule: Any,
    *,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    link_type: LinkType = "dedupe_only",
) -> dict[str, Any]:
    """S-E core: cost ONE blocking rule against ``df`` (installed 4.0.16's
    ``count_comparisons_from_blocking_rule``), returning the pre-filter and
    post-filter pair counts, the equi-join conditions Splink derived, and the
    link-type join condition.

    This is the number the budget is checked against. It is computed by
    *counting*, not by generating the pairs — the entire point of the
    function, and the reason the 2.4M-truncation failure cannot recur: a
    rule that is too broad is known to be too broad before a single pair is
    materialised.
    """
    require_splink()
    import splink.blocking_analysis as ba
    from splink import DuckDBAPI

    result = ba.count_comparisons_from_blocking_rule(
        table_or_tables=[df],
        blocking_rule=rule,
        link_type=link_type,
        db_api=DuckDBAPI(),
        unique_id_column_name=unique_id_column,
    )
    # Prefer the clean SQL Splink itself derived (e.g. `l."email" = r."email"`)
    # over the rule object's own rendering — it is unambiguous and is the
    # exact condition that will be executed.
    rule_display = result.get("equi_join_conditions_identified") or _rule_to_str(rule)
    return {
        "rule": rule_display,
        "pairs_pre_filter": int(
            result.get("number_of_comparisons_generated_pre_filter_conditions", 0)
        ),
        "pairs_to_score": int(
            result.get("number_of_comparisons_to_be_scored_post_filter_conditions", 0)
        ),
        "filter_conditions": result.get("filter_conditions_identified", ""),
        "equi_join_conditions": result.get("equi_join_conditions_identified", ""),
        "link_type_join_condition": result.get("link_type_join_condition", ""),
    }


def largest_blocks(
    df: Any,
    rule: Any,
    *,
    n_largest: int = 5,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    link_type: LinkType = "dedupe_only",
) -> list[dict[str, Any]]:
    """S-E: the ``n_largest`` blocks a rule would produce (installed 4.0.16's
    ``n_largest_blocks``), each with its member count. A single block holding
    most of a table is the signature of a blocking rule that is *too broad*
    in the dangerous sense — not too many pairs overall, but one giant
    block where everything is compared to everything else inside it.
    """
    require_splink()
    import splink.blocking_analysis as ba
    from splink import DuckDBAPI

    sdf: Any = ba.n_largest_blocks(
        table_or_tables=[df],
        blocking_rule=rule,
        link_type=link_type,
        db_api=DuckDBAPI(),
        n_largest=n_largest,
    )
    # Verified installed-4.0.16 columns: key_0 (the block's key value),
    # count_l / count_r (members on each side), block_count (l*r, i.e. the
    # pairs the block generates). block_count is what the budget cares about.
    out: list[dict[str, Any]] = []
    for row in sdf.as_pandas_dataframe().to_dict("records"):
        out.append(
            {
                "block": str(row.get("key_0")),
                "size": int(row.get("count_l", 0)),
                "block_count": int(row.get("block_count", 0)),
            }
        )
    return out


def cumulative_costs(
    df: Any,
    rules: list[Any],
    *,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    link_type: LinkType = "dedupe_only",
) -> list[dict[str, Any]]:
    """S-E: the cumulative pair count across an *ordered* list of rules
    (installed 4.0.16's ``cumulative_comparisons_to_be_scored_from_blocking_
    rules_data``). Returns one row per rule with its own row count and the
    running total — the data behind the cumulative chart, and the basis for
    the "which rule is actually expensive" question the UI asks.

    Rule order matters: the cumulative total is order-dependent because each
    rule only counts pairs the *previous* rules did not already generate.
    The UI should present rules in the order the caller ranked them (most
    selective first) so the cumulative line is monotonic and meaningful.
    """
    require_splink()
    import splink.blocking_analysis as ba
    from splink import DuckDBAPI

    sdf = ba.cumulative_comparisons_to_be_scored_from_blocking_rules_data(
        table_or_tables=[df],
        blocking_rules=rules,
        link_type=link_type,
        db_api=DuckDBAPI(),
        unique_id_column_name=unique_id_column,
    )
    # Verified installed-4.0.16: this one returns a plain pandas DataFrame
    # (not a SplinkDataFrame), columns blocking_rule/row_count/cumulative_rows.
    return list(sdf.to_dict("records"))


def recommend_prediction_blocking(
    df: Any,
    rules: list[Any],
    *,
    pair_budget: int = DEFAULT_PAIR_BUDGET,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
) -> dict[str, Any]:
    """S-E end-to-end: cost every candidate prediction-blocking rule against
    the pair budget and flag the ones that blow it.

    ``rules`` is an *ordered* list of ``BlockingRuleCreator`` objects (most
    selective first — the ordering is the caller's, typically S-A's
    blocking-star ranking). Returns:

    * ``rules`` — one entry per rule with its cost, its
      ``too_broad`` flag, and the budget it was checked against.
    * ``cumulative`` — the running pair count across the list.
    * ``total_pairs`` — the final cumulative total (what predict() will
      actually score if every rule is kept).
    * ``recommended_rules`` — the rules up to (and including) the last one
      whose *cumulative* total still fits the budget. This is the set the
      UI's AUTO mode would apply; a human in ADVANCED/EXPERT can add more
      back, and the budget flag stays visible so the choice is informed.
    * ``recall_claims_allowed`` — always ``False``; §19.

    No rule is silently dropped: every rule appears in ``rules`` with its
    flag, and ``recommended_rules`` is a *recommendation*, never a
    truncation — the difference between this and the old 2.4M silent
    truncation is that here the dropped rules are named, counted, and
    recoverable with one click.
    """
    require_splink()
    costs = [cost_rule(df, r, unique_id_column=unique_id_column) for r in rules]
    cumulative = cumulative_costs(df, rules, unique_id_column=unique_id_column)
    # cumulative row_count is per-rule *new* pairs; pair the flags to them.
    for c, cum in zip(costs, cumulative, strict=True):
        c["cumulative_pairs"] = int(cum.get("cumulative_rows", 0))
        c["too_broad"] = int(cum.get("row_count", 0)) > pair_budget
        c["budget"] = pair_budget

    running = 0
    recommended: list[str] = []
    for c in costs:
        running = c["cumulative_pairs"]
        if running <= pair_budget:
            recommended.append(c["rule"])

    return {
        "rules": costs,
        "cumulative": cumulative,
        "total_pairs": costs[-1]["cumulative_pairs"] if costs else 0,
        "pair_budget": pair_budget,
        "recommended_rules": recommended,
        "dropped_rules": [c["rule"] for c in costs if c["rule"] not in recommended],
        "recall_claims_allowed": False,
    }


def recommend_training_blocking(
    df: Any,
    rules: list[Any],
    *,
    match_heavy_warn: float = 0.1,
    max_pairs_warn: int = DEFAULT_PAIR_BUDGET,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
) -> dict[str, Any]:
    """S-F: cost every *training* blocking rule and warn on the two failure
    modes that specifically hurt training (not prediction):

    * **too match-heavy** — if a rule's blocks are dominated by true matches
      (a high fraction of the rule's pairs are same-entity), EM has no
      contrast: every pair looks like a match, so the m-probabilities for
      *distinguishing* levels collapse toward the prior. We cannot know
      "true match" without labels (§19 again), so the *observable* proxy is
      a rule whose pair count is tiny relative to the table (a block of a
      handful of rows has no non-match pairs inside it by construction).
    * **too large** — the same budget as S-E, for the same reason.

    Returns one entry per rule with its pair count, both warnings, and a
    ``suitable_for_training`` verdict. The verdict is a *recommendation* the
    UI surfaces, never an automatic change — §27 (never auto-modify the
    model).
    """
    require_splink()
    n_rows = len(df)
    costs = [cost_rule(df, r, unique_id_column=unique_id_column) for r in rules]
    out = []
    for c in costs:
        pairs = c["pairs_to_score"]
        # tiny rule relative to the table: few pairs => mostly-match blocks
        # (a dedupe block of n rows has only n*(n-1)/2 pairs; when that is
        # << the table, the rule is so selective it is almost all true dupes)
        too_match_heavy = pairs > 0 and pairs < max(1, int(n_rows * match_heavy_warn))
        too_large = pairs > max_pairs_warn
        out.append(
            {
                **c,
                "too_match_heavy": too_match_heavy,
                "too_large": too_large,
                "suitable_for_training": not (too_match_heavy or too_large),
            }
        )
    return {
        "rules": out,
        "match_heavy_warn_ratio": match_heavy_warn,
        "max_pairs_warn": max_pairs_warn,
        "recall_claims_allowed": False,
    }
