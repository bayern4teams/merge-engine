"""S-A / S-B / S-C / S-D: field roles, comparison config, levels, term
frequency (Branch_Plan.md B.4; Splink.txt §11-§17).

Four recommendation screens, one module, because they all answer the same
question in sequence — "for this field, what role should it play in the
model?" — and each later answer builds on the measured numbers of the one
before:

* **S-A field roles** (§11, §12, §16): per canonical field, measured
  completeness / uniqueness / cardinality / frequency / collision rate, each
  rolled into a 1-5 star rating for *matching*, *blocking* and *TF*
  suitability — every star carrying a written reason. Business importance is
  passed through as metadata only; §12 is explicit that it must never become
  a weight, so no star here reads it.
* **S-B comparison configuration** (§13): one comparator recommended per
  semantic type, chosen *only* from comparators verified to exist in the
  installed 4.0.16 (the plan's own constraint), with a plain-English reason.
* **S-C levels & fuzzy thresholds** (§14, §15): the *observed* pairwise
  similarity distribution per field (``comparator_score_df`` over a bounded
  sample of candidate pairs) with coverage per candidate threshold. Never
  0.95/0.90/0.80 as universals — the numbers shown are what this data does.
* **S-D term frequency** (§17): the measured value-frequency table per
  field, with a recommendation (and the reason) driven by the skew of that
  distribution.

All functions are pure (no module state — CLAUDE.md) and each calls
:func:`splink_compat.require_splink` at its entry, so the whole subsystem's
"refuse the wrong major" mandate runs from one place. The heavy Splink
imports live inside functions, so the module stays importable (and its
recommendation logic testable) in an environment where Splink is absent —
matching ``splink_matcher.py``'s own import posture.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from src.mcp_server.tools.splink_compat import imported_version, require_splink

logger = logging.getLogger("merge-engine.tools.splink_recommender")

# Candidate thresholds offered in S-C. They are *candidates to test against
# the observed distribution*, not defaults — the UI renders whichever one the
# data actually supports (see observed_similarity_distribution).
CANDIDATE_THRESHOLDS = (0.95, 0.90, 0.85, 0.80, 0.70, 0.50)

# S-C samples a bounded number of candidate pairs per field. Sampling is
# deliberate: the distribution is a *display aid for threshold choice*, and
# O(n²) pairs over a 2.4M-row entity is the exact failure mode S-E's costed
# blocking exists to prevent. 500 pairs is enough to read a distribution.
_SIMILARITY_SAMPLE_PAIRS = 500


# ── S-A — field roles & star ratings ──────────────────────────────────────


def _stars(score: float) -> int:
    """A 0..1 score to 1..5 whole stars (never 0 — a field is always *some*
    use; the reason text explains how little)."""
    stars = round(score * 5)
    return max(1, min(5, int(stars)))


def field_stats(df: Any) -> dict[str, dict[str, Any]]:
    """Measured per-column statistics (S-A's inputs), all from the DataFrame
    itself — never asserted.

    ``completeness``   = non-null / total (P-3's own definition)
    ``uniqueness``     = distinct non-null / non-null (1.0 = every value different)
    ``cardinality``    = distinct non-null (absolute count)
    ``collision_rate`` = 1 - uniqueness (the chance two random non-null values agree)
    ``top_values``     = up to 5 most frequent values with counts
    ``max_value_frequency`` = the most common value's share of non-null rows
    """
    stats: dict[str, dict[str, Any]] = {}
    for col in df.columns:
        series = df[col]
        total = len(series)
        non_null = int((series != "").sum())
        values = series[series != ""]
        distinct = values.nunique() if non_null else 0
        top = values.value_counts().head(5)
        stats[str(col)] = {
            "completeness": round(non_null / total, 4) if total else 0.0,
            "uniqueness": round(distinct / non_null, 4) if non_null else 0.0,
            "cardinality": int(distinct),
            "collision_rate": round(1.0 - (distinct / non_null), 4) if non_null else 0.0,
            "max_value_frequency": round(float(top.iloc[0] / non_null), 4) if non_null else 0.0,
            "top_values": [{"value": str(v), "count": int(c)} for v, c in top.items()],
        }
    return stats


def recommend_field_roles(df: Any) -> dict[str, dict[str, Any]]:
    """S-A: per canonical field, star-rated matching / blocking / TF
    suitability, each with a written reason, from the measured numbers.

    The rubrics (deliberately simple and fully documented, because a star
    rating with an opaque formula is the §22 "no opaque score" failure in
    another costume):

    * **matching** — a field discriminates matches when it is complete AND
      unique: ``0.5 * completeness + 0.5 * uniqueness``.
    * **blocking** — a field is a good blocking key when it is selective
      (low collision rate), complete, and not a constant:
      ``0.5 * (1 - collision_rate) + 0.5 * completeness``, with a hard
      floor of 1 star when completeness < 0.2 (a mostly-null column blocks
      nothing) or cardinality < 2 (a constant column blocks *everything* —
      the cartesian-product failure S-E's "⚠ Too broad" guard names).
    * **TF** — term-frequency adjustment earns its keep when value
      frequencies are skewed: a high ``max_value_frequency`` (one common
      value dominates) means unadjusted m/u probabilities over-weight
      agreements on that value. Score = ``min(1, max_value_frequency * 10)``
      on fields with cardinality > 1 (TF on a constant is meaningless).
    """
    require_splink()
    out: dict[str, dict[str, Any]] = {}
    for col, s in field_stats(df).items():
        matching_score = 0.5 * s["completeness"] + 0.5 * s["uniqueness"]

        blocking_score = 0.5 * (1.0 - s["collision_rate"]) + 0.5 * s["completeness"]
        blocking_reason = (
            f"blocks on {s['cardinality']} distinct value(s), "
            f"{s['collision_rate']:.0%} collision rate, {s['completeness']:.0%} complete"
        )
        if s["cardinality"] < 2:
            blocking_score = 0.0
            blocking_reason = "constant column — blocking on it is the cartesian product"
        elif s["completeness"] < 0.2:
            blocking_score = min(blocking_score, 0.2)
            blocking_reason = f"only {s['completeness']:.0%} complete — mostly null, blocks nothing"

        tf_score = 0.0 if s["cardinality"] < 2 else min(1.0, s["max_value_frequency"] * 10.0)
        tf_reason = (
            f"top value '{s['top_values'][0]['value']}' is {s['max_value_frequency']:.0%} of rows"
            if s["top_values"]
            else "no values measured"
        )
        if s["cardinality"] < 2:
            tf_reason = "constant column — TF adjustment is meaningless on it"

        out[col] = {
            "field": col,
            "stats": s,
            "matching": {
                "stars": _stars(matching_score),
                "reason": f"{s['completeness']:.0%} complete, {s['uniqueness']:.0%} unique",
            },
            "blocking": {"stars": _stars(blocking_score), "reason": blocking_reason},
            "tf": {"stars": _stars(tf_score), "reason": tf_reason},
        }
    return out


# ── S-B — comparison configuration ────────────────────────────────────────

# Every entry here was verified to exist in the installed 4.0.16
# comparison_library (introspected, not read from the plan). The
# ``columns`` list is how many columns the comparator consumes.
_COMPARATOR_CATALOG: dict[str, dict[str, Any]] = {
    "EMAIL": {
        "comparator": "EmailComparison",
        "columns": 1,
        "why": "case/domain-normalised exact + fuzzy username matching, built in",
    },
    "PERSON_NAME": {
        "comparator": "NameComparison",
        "columns": 1,
        "why": "Jaro-Winkler name matching with built-in null/typo levels",
    },
    "FORENAME_SURNAME": {
        "comparator": "ForenameSurnameComparison",
        "columns": 2,
        "why": "separate forename/surname levels — a single concatenated name blurs them",
    },
    "ORGANIZATION_NAME": {
        "comparator": "NameComparison",
        "columns": 1,
        "why": "names of organisations match the same way as people's; no dedicated org comparator exists in 4.0.16",
    },
    "DATE_OF_BIRTH": {
        "comparator": "DateOfBirthComparison",
        "columns": 1,
        "why": "date-difference levels (month/year) — an exact match is the wrong test for a mistyped DOB",
    },
    "DATE": {
        "comparator": "AbsoluteDateDifferenceAtThresholds",
        "columns": 1,
        "why": "date-difference thresholds; a DOB-shaped date uses the dedicated comparator above",
    },
    "POSTCODE": {
        "comparator": "PostcodeComparison",
        "columns": 1,
        "why": "geocoded distance levels where lat/long columns exist, postcode buckets otherwise",
    },
    "PHONE": {
        "comparator": "ExactMatch",
        "columns": 1,
        "why": "exact after our normalisation (digits only) — fuzzy phone matching rewards typos that change the number",
    },
    "URL": {
        "comparator": "ExactMatch",
        "columns": 1,
        "why": "normalised (scheme/https stripped) exact match; URLs are identifiers, not prose",
    },
    "ID": {
        "comparator": "ExactMatch",
        "columns": 1,
        "why": "identifiers are compared exactly — similarity is a category error for ids",
    },
}


def recommend_comparison(semantic_type: str, field: str) -> dict[str, Any]:
    """S-B: the comparator to configure for a field of a given semantic type.

    ``semantic_type`` is P-4's taxonomy (``semantic_type_inference.
    SEMANTIC_TYPES``). A type with no dedicated entry falls back to
    ``JaroWinklerAtThresholds`` for text-shaped fields (a defensible fuzzy
    default) and ``ExactMatch`` otherwise — both exist in 4.0.16, and the
    recommendation *says it is a fallback* rather than pretending it was
    chosen.
    """
    require_splink()
    stype = (semantic_type or "").upper()
    hit = _COMPARATOR_CATALOG.get(stype)
    if hit is not None:
        return {
            "field": field,
            "semantic_type": semantic_type,
            "comparator": hit["comparator"],
            "columns_needed": hit["columns"],
            "reason": hit["why"],
            "fallback": False,
        }
    # Fallback: text-ish vs everything else.
    if stype in ("TEXT", "UNKNOWN", "STRING"):
        return {
            "field": field,
            "semantic_type": semantic_type,
            "comparator": "JaroWinklerAtThresholds",
            "columns_needed": 1,
            "reason": "no comparator recommended for this semantic type — "
            "Jaro-Winkler with editable thresholds is the safe fuzzy default",
            "fallback": True,
        }
    return {
        "field": field,
        "semantic_type": semantic_type,
        "comparator": "ExactMatch",
        "columns_needed": 1,
        "reason": "no comparator recommended for this semantic type — "
        "exact match is the safe default when similarity is not the right test",
        "fallback": True,
    }


def build_comparator(
    name: str,
    field: str,
    *,
    extra_fields: tuple[str, ...] = (),
    thresholds: list[float] | None = None,
) -> Any:
    """The one ``comparator name -> Splink comparison object`` builder in the
    workbench. Both callers go through this: ``comparison_to_settings_hit``
    (S-B, driven by a recommended semantic type) and
    ``splink_workbench._config_comparison``'s explicit-comparator path (a
    human picking a comparator directly). One builder means a hand-picked
    comparator and an AUTO-recommended one can never construct differently —
    the drift this function replaces was two independent if-chains that had
    already started to diverge (S-B's fixed list vs the workbench's
    ``getattr``-based dispatch).

    ``getattr`` against the installed ``comparison_library`` (rather than a
    fixed set of direct references) so any comparator the installed 4.0.16
    ships is reachable here, not just the ones S-B's catalog names.

    ``extra_fields`` feeds the 2-column comparators (forename + surname).
    Raises :class:`ValueError` when the catalog says two columns and only one
    is supplied, or when ``name`` is not a real ``comparison_library`` member
    — a mismatched comparator is worse than no recommendation.
    """
    require_splink()
    import splink.comparison_library as cl

    builder = getattr(cl, name, None)
    if builder is None:
        raise ValueError(
            f"comparator {name!r} has no settings-builder — the catalog and the builder have drifted apart"
        )
    if name == "ForenameSurnameComparison":
        if len(extra_fields) < 1:
            raise ValueError(
                f"ForenameSurnameComparison needs a surname column; got {field!r} only"
            )
        return builder(field, extra_fields[0])
    if name == "AbsoluteDateDifferenceAtThresholds":
        return builder(field, input_is_string=True, metrics=["year", "month"], thresholds=[1, 1])
    if name == "DateOfBirthComparison":
        return builder(field, input_is_string=True)
    if name == "JaroWinklerAtThresholds":
        return builder(field, thresholds or [0.9, 0.7])
    if name in ("ExactMatch", "NameComparison", "EmailComparison", "PostcodeComparison"):
        return builder(field)
    return builder(field, *extra_fields)


def comparison_to_settings_hit(
    semantic_type: str, field: str, extra_fields: tuple[str, ...] = ()
) -> Any:
    """S-B's *executable* half: build the actual comparison object (the thing
    goes into ``SettingsCreator``), from the same catalog
    ``recommend_comparison`` reads — the recommendation and the configuration
    can never disagree, because there is one source.

    ``extra_fields`` feeds the 2-column comparators (forename + surname).
    Raises :class:`ValueError` when the catalog says two columns and only one
    is supplied — a mismatched comparator is worse than no recommendation.
    """
    require_splink()
    hit = recommend_comparison(semantic_type, field)
    return build_comparator(hit["comparator"], field, extra_fields=extra_fields)


# ── S-C — observed similarity distribution & thresholds ───────────────────


def _sample_candidate_pairs(df: Any, col: str, n: int) -> list[dict[str, Any]]:
    """A bounded, order-stable sample of candidate pairs for one column.

    Pairs are drawn from consecutive rows in *row order* after dropping
    empties — not random — so a recommendation for the same data is
    reproducible run to run (the §46 reproducibility constraint, applied to
    the display layer). Consecutive rows are a decent candidate proxy for
    text columns (sorted exports cluster near-identical values together), and
    the sample is explicitly labelled as a sample in its output.
    """
    values = [v for v in df[col].tolist() if v != ""]
    if len(values) < 2:
        return []
    step = max(1, len(values) // max(1, n // 2))
    pairs: list[dict[str, Any]] = []
    for i in range(0, len(values) - 1, step):
        pairs.append({"l": values[i], "r": values[i + 1]})
        if len(pairs) >= n:
            break
    return pairs


def observed_similarity_distribution(
    df: Any,
    column: str,
    *,
    sample_size: int = _SIMILARITY_SAMPLE_PAIRS,
) -> dict[str, Any]:
    """S-C: the *observed* pairwise similarity distribution for ``column``.

    Runs ``comparator_score_df`` (installed 4.0.16,
    ``splink.internals.similarity_analysis``) over a bounded sample of
    candidate pairs, then reports, for each candidate threshold in
    :data:`CANDIDATE_THRESHOLDS`, what fraction of the sampled pairs sit at
    or above it — the coverage number the UI shows *next to* each threshold
    so a human can see what that threshold would do on this data. The plan
    is explicit: "Never present 0.95/0.90/0.80 as universal."

    ``jaro_winkler_similarity`` is the headline (the threshold column
    ``NameComparison``/``JaroWinklerAtThresholds`` actually use); the raw
    distance columns ride along for the detail view.
    """
    require_splink()
    from splink.internals.similarity_analysis import comparator_score_df

    pairs = _sample_candidate_pairs(df, column, sample_size)
    if not pairs:
        return {"column": column, "pairs_sampled": 0, "distribution": {}, "threshold_coverage": {}}
    sim = comparator_score_df(pairs, "l", "r")  # type: ignore[no-untyped-call]
    dist = {
        "jaro_winkler_similarity": [round(float(v), 4) for v in sim["jaro_winkler_similarity"]],
        "jaro_similarity": [round(float(v), 4) for v in sim["jaro_similarity"]],
        "jaccard_similarity": [round(float(v), 4) for v in sim["jaccard_similarity"]],
    }
    coverage = {
        f">={t}": round(float((sim["jaro_winkler_similarity"] >= t).mean()), 4)
        for t in CANDIDATE_THRESHOLDS
    }
    return {
        "column": column,
        "pairs_sampled": len(pairs),
        "distribution": dist,
        "threshold_coverage": coverage,
    }


def recommended_thresholds(
    df: Any | None = None,
    column: str | None = None,
    *,
    sample_size: int = _SIMILARITY_SAMPLE_PAIRS,
    observed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """S-C's recommendation: the two candidate thresholds that best split the
    observed distribution — the highest threshold whose coverage is still
    substantial (a candidate match bar) and the lowest one with near-total
    coverage (a candidate exclusion bar). No magic 0.95/0.70: the split is
    read off this data's own distribution, and the reasoning is returned so
    the UI can explain it.

    ``observed`` may be passed in (from a caller that already ran
    :func:`observed_similarity_distribution` for the same column) to avoid
    recomputing it; otherwise ``df``/``column``/``sample_size`` are used.
    Returns ``None`` recommendations (with a reason) when the sample is too
    small to read a split — an honest "not enough data" beats a guessed bar.
    """
    if observed is None:
        if df is None or column is None:
            raise ValueError("pass either observed= or both df and column")
        observed = observed_similarity_distribution(df, column, sample_size=sample_size)
    column = observed["column"]
    if observed["pairs_sampled"] < 20:
        return {
            "column": column,
            "match_threshold": None,
            "exclude_threshold": None,
            "reason": f"only {observed['pairs_sampled']} candidate pairs sampled — "
            "not enough to read a threshold split; label more pairs (S-K) "
            "and let S-L optimise from the labels instead",
        }
    cov = observed["threshold_coverage"]
    # Coverage is monotonic DECREASING in the threshold (fewer pairs sit at
    # or above a higher bar). CANDIDATE_THRESHOLDS is ordered high→low, so:
    # match bar: HIGHEST threshold still catching >= 40% of sampled pairs —
    # the first hit in natural (high→low) order. (Iterating reversed here
    # returned the *lowest* matching one — a match bar pinned at ~0.5, which
    # would sit at or below the exclude bar and collapse the review band.)
    match_bar = next((t for t in CANDIDATE_THRESHOLDS if cov[f">={t}"] >= 0.4), None)
    # exclude bar: LOWEST threshold with >= 90% coverage — the first hit in
    # reversed (low→high) order.
    exclude_bar = next((t for t in reversed(CANDIDATE_THRESHOLDS) if cov[f">={t}"] >= 0.90), None)
    return {
        "column": column,
        "match_threshold": match_bar,
        "exclude_threshold": exclude_bar,
        "threshold_coverage": cov,
        "reason": f"observed over {observed['pairs_sampled']} sampled candidate pairs — "
        f"match bar {match_bar} still covers {cov.get(f'>={match_bar}', 0):.0%} of pairs, "
        f"exclude bar {exclude_bar} covers {cov.get(f'>={exclude_bar}', 0):.0%}",
    }


# ── S-D — term frequency ──────────────────────────────────────────────────

# A value this common (or commoner) carries almost no identifying power —
# the whole point of TF adjustment. Splink's own tutorials adjust on columns
# with a similar skew; 2% is a deliberately conservative trigger so the
# recommendation fires only where unadjusted weights measurably lie.
_TF_TRIGGER_FREQUENCY = 0.02


def recommend_term_frequency(df: Any, column: str, *, top_n: int = 10) -> dict[str, Any]:
    """S-D: the measured value-frequency table for ``column`` plus a
    TF-adjustment recommendation driven by its skew.

    The recommendation is binary and honest: ``recommended`` is True only
    when the most common value hits :data:`_TF_TRIGGER_FREQUENCY` (or a
    column of near-constants — where *every* value is common). The frequency
    table itself is always returned, because §17 wants it *shown*, and a
    human can override the recommendation either way.
    """
    require_splink()
    values = [v for v in df[column].tolist() if v != ""]
    if not values:
        return {
            "column": column,
            "recommended": False,
            "frequency_table": [],
            "reason": "column is entirely empty",
        }
    counts = Counter(values)
    total = len(values)
    table = [
        {"value": str(v), "count": int(c), "frequency": round(c / total, 4)}
        for v, c in counts.most_common(top_n)
    ]
    top_count = counts.most_common(1)[0][1] if counts else 0
    max_freq: float = round(top_count / total, 4) if total else 0.0
    distinct = len(counts)
    # near-constant: the top value covers most of the column
    near_constant = max_freq >= 0.5 and distinct <= 3
    recommended = max_freq >= _TF_TRIGGER_FREQUENCY or near_constant
    if recommended:
        reason = (
            f"top value '{table[0]['value']}' is {max_freq:.0%} of {total} rows "
            f"({distinct} distinct) — agreements on it carry little information "
            "and unadjusted m/u probabilities over-weight them"
        )
    else:
        reason = (
            f"most common value is only {max_freq:.0%} of rows ({distinct} distinct) — "
            "frequencies are flat enough that TF adjustment would add noise, not signal"
        )
    return {
        "column": column,
        "recommended": recommended,
        "max_value_frequency": max_freq,
        "distinct_count": distinct,
        "frequency_table": table,
        "reason": reason,
    }


# ── convenience: the whole AUTO-mode model in one call ────────────────────


def auto_recommend(df: Any, semantic_types: dict[str, str]) -> dict[str, Any]:
    """The AUTO mode of the workbench (Splink §50): S-A + S-B + S-C + S-D
    together, for every column, keyed by column. ``semantic_types`` maps
    column → P-4's semantic type (the UI passes P-4's stored inferences
    through; a missing entry falls back to S-B's own ``UNKNOWN`` handling).

    This is what the UI's "Generate automatically" button calls; ADVANCED and
    EXPERT modes take the per-field outputs of the individual functions and
    let a human edit them, which is why those functions are public, not
    buried inside this one.
    """
    require_splink()
    roles = recommend_field_roles(df)
    fields: dict[str, Any] = {}
    for col in df.columns:
        observed = observed_similarity_distribution(df, col)
        fields[col] = {
            **roles[col],
            "comparison": recommend_comparison(semantic_types.get(col, "UNKNOWN"), col),
            "term_frequency": recommend_term_frequency(df, col),
            "similarities": observed,
            "thresholds": recommended_thresholds(observed=observed),
        }
    return {
        "fields": fields,
        "splink_version": imported_version(),
    }
