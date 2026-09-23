"""S-K / S-L / S-M: labels & review, accuracy + thresholds, error analysis
(Branch_Plan.md B.4; Splink.txt §32-§36, §44, §45).

The evaluation half of the workbench. It answers the only question no other
phase can: **is the model actually right, and where is it wrong?**

* **S-K labels & review workflow** (§32, §44): the label *schema* is the
  installed 4.0.16 one — ``{uid}_l``/``{uid}_r``/``clerical_match_score``
  (plus ``source_dataset`` columns when configured; the workbench does not
  configure one). ``register_labels`` builds that table from plain
  ``(id_l, id_r, score)`` triples (``splink_common.labels_dataframe``) and
  registers it on the linker, returning the registered table handle. Labels
  are *data*, never a model change — but S-K also exposes
  ``estimate_m_from_labels`` (``training.estimate_m_from_pairwise_labels``),
  the one labelled path that *does* touch the model, and it is explicit in
  its record that it is a mutation, that it treats every label as a perfect
  match (Splink ignores ``clerical_match_score`` there — verified
  installed 4.0.16), and that it is a separate, logged step (§27).
* **S-L accuracy & threshold optimisation** (§34, §35):
  ``accuracy_analysis_from_labels_table`` with ``output_type="table"``
  returns the *truth-space* table — one row per distinct model score, with
  TP/TN/FP/FN, precision, recall, f1, specificity, npv, accuracy at that
  score (verified installed 4.0.16). S-L's threshold recommendation is read
  off that table: the score with the best F1 (balanced) — and, because a
  human reviewing the §34 "precision/recall trade-off" needs both, the
  score with the best precision above a configurable recall floor, and the
  score with the best recall above a precision floor. The current default
  (0.5) is evaluated at its own row so the UI can show "your current
  threshold does X; the data says Y".
* **S-M error analysis** (§36, §45): ``prediction_errors_from_labels_table``
  returns exactly the pairs where the model and the clerical label
  disagree (FP/FN), each with its per-field ``gamma_*`` levels — the
  "which field drove the error" question. S-M groups the errors by the
  *field that most contradicted the label* (the field whose level was most
  extreme in the wrong direction) and reports the top offenders, so a
  reviewer fixing the model knows *which comparison to re-tune*, not just
  that the model is wrong somewhere.

Pure of state (CLAUDE.md); every public function calls
:func:`splink_compat.require_splink`.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.splink_common import (
    labels_dataframe,
    unique_id_column_of,
)
from src.mcp_server.tools.splink_compat import imported_version, require_splink

logger = logging.getLogger("merge-engine.tools.splink_evaluation")


# ── S-K — labels & review ─────────────────────────────────────────────────


def register_labels(
    linker: Any,
    labelled_pairs: list[tuple[str, str, float]],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """S-K: build the 4.0.16 labels table and register it on the linker.

    Returns a record with the registered table's templated name, the row
    count, and the label balance (how many confirmed matches vs non-matches
    — a labels table of all-ones is a training set with no negative
    contrast, and S-L's recall will be unmeasurable on it, so the balance is
    surfaced, not assumed).
    """
    require_splink()
    # Build the labels table with the linker's *actual* unique-id column —
    # not the default. Verified installed 4.0.16: Splink joins the labels
    # table on ``{settings.unique_id_column_name}_l/_r``; a labels table
    # built from a different column name fails the join with a Binder
    # Error (reproduced 2026-08-23: linker on "id", labels built on
    # "_record_id_str" -> 'Referenced column "id_l" not found').
    uid_col = unique_id_column_of(linker)
    df = labels_dataframe(labelled_pairs, unique_id_column=uid_col)
    sdf = linker.table_management.register_labels_table(df, overwrite=overwrite)
    n_pos = sum(1 for _, _, s in labelled_pairs if s >= 0.5)
    n_neg = len(labelled_pairs) - n_pos
    return {
        # Verified installed 4.0.16: the *physical* name is the handle the
        # evaluation functions accept reliably — passing the templated name
        # back in breaks the truth-space SQL ("SELECT clause without
        # selection list"). The SDF itself also works; the physical name is
        # returned because it is JSON-serialisable for the API layer.
        "table_name": sdf.physical_name,
        "n_labels": len(labelled_pairs),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "balanced": n_pos > 0 and n_neg > 0,
        "splink_version": imported_version(),
    }


def estimate_m_from_labels(
    linker: Any,
    labels_table: Any,
) -> dict[str, Any]:
    """S-K (the labelled *mutation*): re-estimate m-probabilities from
    confirmed-match pairs.

    This is the one S-K path that changes the model, and the record says so
    plainly: ``mutates_model: True``. Verified installed 4.0.16: Splink
    ``estimate_m_from_pairwise_labels`` **ignores ``clerical_match_score``**
    and treats every row as a perfect match — so a caller passing
    mixed-score labels would silently train on the non-matches as if they
    were matches. This function therefore *requires* the caller to pass only
    confirmed matches (it filters to ``score >= 0.5`` before registering and
    reports how many rows it dropped), and the dropped count is in the
    record so a mistake is visible.
    """
    require_splink()
    # labels_table here is a raw list of (l, r, score) triples or an already
    # registered SDF/str; normalise to "register only confirmed matches".
    if isinstance(labels_table, str) or hasattr(labels_table, "templated_name"):
        # already a table: register as-is but record that filtering was not
        # applied (the caller is responsible) — we cannot re-read it cheaply.
        linker.training.estimate_m_from_pairwise_labels(labels_table)
        return {
            "mutates_model": True,
            "n_rows_used": None,
            "n_rows_dropped": None,
            "note": "pre-registered table passed through; non-match rows (if any) are NOT filtered here — Splink treats every row as a match",
        }
    confirmed = [(a, b) for a, b, s in labels_table if s >= 0.5]
    dropped = len(labels_table) - len(confirmed)
    if not confirmed:
        raise ValueError("estimate_m_from_labels needs at least one confirmed-match label")
    # same column rule as register_labels: the linker's actual unique-id column
    df = labels_dataframe(
        [(a, b, 1.0) for a, b in confirmed], unique_id_column=unique_id_column_of(linker)
    )
    sdf = linker.table_management.register_labels_table(df, overwrite=True)
    linker.training.estimate_m_from_pairwise_labels(sdf)
    return {
        "mutates_model": True,
        "n_rows_used": len(confirmed),
        "n_rows_dropped": dropped,
        "note": "only confirmed matches (clerical >= 0.5) used; Splink ignores clerical_match_score in this call",
    }


# ── S-L — accuracy & threshold optimisation ───────────────────────────────


def _chart_rows(chart: Any) -> list[dict[str, Any]]:
    """An altair chart's InlineData values to a list of row-dicts (the same
    unwrap the training module does; kept local so this module doesn't import
    its sibling for a 5-line helper)."""
    if chart is None:
        return []
    data = getattr(chart, "data", None)
    values = getattr(data, "values", None)
    if isinstance(values, (list, tuple)):
        return [dict(v) if hasattr(v, "items") else v for v in values]
    return []


def accuracy_report(
    linker: Any,
    labels_table: Any,
    *,
    current_threshold: float = 0.5,
    recall_floor: float = 0.8,
    precision_floor: float = 0.8,
) -> dict[str, Any]:
    """S-L: the full accuracy picture against the labels, plus the threshold
    recommendation the data supports.

    ``labels_table`` is the handle :func:`register_labels` returned (a
    templated-name str or a registered SDF). The truth-space table
    (``output_type="table"``, verified installed 4.0.16) is the curve; from
    it we read:

    * ``at_current`` — precision/recall/f1 at ``current_threshold`` (the
      model's present operating point, so the UI shows what the default
      *does*, not just what the optimum *would*).
    * ``best_f1`` — the score bin maximising F1 (balanced precision/recall;
      the §34 default recommendation).
    * ``best_precision_at_recall`` — the highest score whose recall is still
      >= ``recall_floor`` (for a use-case where missing a match is worse).
    * ``best_recall_at_precision`` — the highest score whose precision is
      still >= ``precision_floor`` (the opposite trade-off).

    Each recommendation carries its row's full metrics, so the UI renders
    the number *and* the trade-off it implies. No recommendation is made
    from a chart alone — everything is read off the tabular truth space.
    """
    require_splink()
    table = linker.evaluation.accuracy_analysis_from_labels_table(labels_table, output_type="table")
    rows = _sdf_records(table)
    if not rows:
        return {
            "error": "truth-space table is empty — no scored label pairs; "
            "check that the labels reference records the model actually scored",
        }

    def _nearest(threshold: float) -> dict[str, Any]:
        return min(rows, key=lambda r: abs(float(r["match_probability"]) - threshold))

    at_current = _nearest(current_threshold)

    def _score(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_probability": r["match_probability"],
            "precision": r.get("precision"),
            "recall": r.get("recall"),
            "f1": r.get("f1"),
            "specificity": r.get("specificity"),
            "npv": r.get("npv"),
            "accuracy": r.get("accuracy"),
            "tp": r.get("tp"),
            "fn": r.get("fn"),
            "fp": r.get("fp"),
        }

    best_f1 = max(rows, key=lambda r: r.get("f1") or 0.0)
    prec_recall_rows = [r for r in rows if (r.get("recall") or 0.0) >= recall_floor]
    best_prec = (
        max(prec_recall_rows, key=lambda r: r.get("precision") or 0.0) if prec_recall_rows else None
    )
    recall_prec_rows = [r for r in rows if (r.get("precision") or 0.0) >= precision_floor]
    best_recall = (
        max(recall_prec_rows, key=lambda r: r.get("recall") or 0.0) if recall_prec_rows else None
    )

    return {
        "rows": rows,
        "at_current": _score(at_current),
        "best_f1": _score(best_f1),
        "best_precision_at_recall": _score(best_prec) if best_prec else None,
        "best_recall_at_precision": _score(best_recall) if best_recall else None,
        "recall_floor": recall_floor,
        "precision_floor": precision_floor,
        "n_scored_label_pairs": int(sum(r.get("total_clerical_labels", 0) for r in rows) or 0),
        "splink_version": imported_version(),
    }


def _sdf_records(sdf: Any) -> list[dict[str, Any]]:
    """A SplinkDataFrame to JSON-safe row-dicts."""
    df = sdf.as_pandas_dataframe()
    records = df.to_dict("records")
    import math

    def _fix(v: Any) -> Any:
        if isinstance(v, float) and math.isnan(v):
            return None
        if hasattr(v, "item"):
            return _fix(v.item())
        return v

    return [{k: _fix(v) for k, v in row.items()} for row in records]


# ── S-M — FP/FN error analysis ────────────────────────────────────────────


def error_analysis(
    linker: Any,
    labels_table: Any,
    *,
    top_fields: int = 5,
) -> dict[str, Any]:
    """S-M: the pairs the model got wrong, grouped by the field that most
    contradicted the label.

    ``prediction_errors_from_labels_table`` (verified installed 4.0.16)
    returns exactly the FP/FN rows, each with its per-field ``gamma_*``
    levels and a ``truth_status``. For each error we find the *decisive
    field*: the compared field whose level is most extreme against the
    label's direction — a false positive is "explained" by the field that
    said "match" most strongly; a false negative by the field that said
    "no match" most strongly (the lowest level). Grouping by that field is
    the answer to "which comparison do I re-tune?" — the §45 "error
    analysis that tells you what to fix" requirement.

    The per-field counts are a *recommendation*, not a diagnosis: the UI
    presents them as "errors most associated with X", and the reviewer
    confirms. A single error field is not proof that field is broken; it is
    where the human should look first.
    """
    require_splink()
    errors = linker.evaluation.prediction_errors_from_labels_table(labels_table)
    rows = _sdf_records(errors)
    n_fp = sum(1 for r in rows if _is_fp(r))
    n_fn = len(rows) - n_fp

    # decisive field per error
    from collections import Counter

    field_counter: Counter[str] = Counter()
    for r in rows:
        fp = _is_fp(r)
        # candidate fields: those with a gamma_<field> column
        candidates: list[tuple[str, float]] = []
        for key, val in r.items():
            if not key.startswith("gamma_") or val is None:
                continue
            field = key[len("gamma_") :]
            if val == -1:  # null level: carries no signal
                continue
            # level semantics: higher comparison_vector_value == "more like a
            # match" for NameComparison-style comparators; for a FP we want the
            # highest, for a FN the lowest (but not -1).
            candidates.append((field, float(val)))
        if not candidates:
            continue
        decisive = (
            max(candidates, key=lambda fv: fv[1])[0]
            if fp
            else min(candidates, key=lambda fv: fv[1])[0]
        )
        field_counter[decisive] += 1

    return {
        "n_errors": len(rows),
        "n_false_positives": n_fp,
        "n_false_negatives": n_fn,
        "errors_by_decisive_field": [
            {"field": field, "count": count, "share": round(count / len(rows), 4) if rows else 0.0}
            for field, count in field_counter.most_common(top_fields)
        ],
        "error_rows": rows,
        "note": "decisive-field grouping is a 'where to look first' recommendation, not a diagnosis",
    }


def _is_fp(row: dict[str, Any]) -> bool:
    """A false positive: the clerical label says *not* a match (or the
    pair was not a confirmed match) but the model scored it as one.
    ``truth_status`` carries Splink's own verdict; fall back to the clerical
    score when it is absent."""
    status = str(row.get("truth_status", "")).lower()
    if "positive" in status and "false" in status:
        return True
    if "negative" in status and "false" in status:
        return False
    score = row.get("clerical_match_score")
    prob = row.get("match_probability")
    if score is None or prob is None:
        return bool(status.endswith("false positive"))
    # model says match, label says not
    return prob >= 0.5 and float(score) < 0.5
