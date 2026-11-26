"""Request-scoped orchestration for the B.4 Splink workbench (Branch_Plan.md B.4).

The per-phase modules (``splink_recommender`` / ``splink_training`` /
``splink_inference`` / ``splink_evaluation`` / ``splink_model``) are pure
functions over a DataFrame or a ``Linker``. The API
layer must stay a thin delegator (``src/api/main.py`` holds no business
logic — the project's own invariant), so *this* module is where the
multi-step flows the UI drives are sequenced and where the ephemeral
``Linker`` lives for the duration of a single request.

Design — **model-first, never re-train to iterate**:

* ``recommend`` (S-A/B/C/D, + S-E cost) is pure over the records; it needs no
  linker.
* ``train`` builds a ``SettingsCreator`` from the caller's (possibly
  AUTO-recommended) config, runs S-G/H/I in-request, and **saves a model
  version** (S-N) — that saved payload is what every later step loads.
* ``predict`` / ``explain`` / ``cluster`` / ``review`` / ``evaluate`` all take
  ``model_name`` + ``version`` + the records, build a fresh linker from the
  *saved* payload (no retraining — fast, and the numbers are exactly the
  trained model's), and run one phase.

Keeping the linker request-scoped (built, used, discarded) is what makes this
module stateless — there is no server-side model cache, so a restart loses
nothing and two concurrent requests can never share a linker (Splink's own
docs warn against sharing a DuckDB handle). The cost of rebuilding a linker
from a saved payload is a single DuckDB ``CREATE TABLE`` of the records;
training (EM) is what's expensive, and that happens exactly once per model.

``records`` arrive inline from the UI. The workbench is a tuning surface over
*profiled/sampled* data (P-3's own sampling; S-A is "backed by ... our own P-3
numbers"), so inline records are the right shape — not a spool reference. A
cap on record count is enforced so a caller that accidentally posts a 2.4M-row
entity cannot build an in-process DuckDB that outgrows the process.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.mcp_server.tools import splink_inference as infer
from src.mcp_server.tools import splink_model as model
from src.mcp_server.tools import splink_recommender as rec

# imported under a distinct alias because this module defines its own
# `train` function that would otherwise shadow the name inside every call.
from src.mcp_server.tools import splink_training as splink_training_mod
from src.mcp_server.tools.splink_common import (
    DEFAULT_UNIQUE_ID_COLUMN,
    records_to_dataframe,
)
from src.mcp_server.tools.splink_compat import (
    imported_version,
    require_splink,
    splink_available,
)

logger = logging.getLogger("merge-engine.tools.splink_workbench")

# A workbench request is interactive tuning, not a batch run. More records
# than this means the caller is trying to use the workbench as the production
# merge path (that's resolve_entities' job); cap the request, not the model.
MAX_WORKBENCH_RECORDS = 50_000

# Default model store root. Matches the project's "artefacts under logs/"
# convention (spool: logs/spool/<run_id>.duckdb). Overridable per call for
# tests.
DEFAULT_MODEL_STORE = "logs/splink_models"


def _splink_config() -> dict[str, Any]:
    """The ``splink:`` section of config/config.yaml, as a plain dict.

    Config is a *presentation* default layer only: it supplies the workbench's
    banding thresholds and pair budget when the caller does not, and it may
    never declare the default engine (ADR 0004: the promotion surface is the
    per-entity ``matching_rules`` + the BP-B4 gate record). A missing section,
    a missing file, or a non-dict value all fall back to the module constants
    below, so the workbench stays usable on a checkout without a config file.
    """
    import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs

    from src.utils.config_loader import load_raw_config_dict

    try:
        section = load_raw_config_dict().get("splink")
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return section if isinstance(section, dict) else {}


def _config_float(key_path: tuple[str, ...], fallback: float) -> float:
    node: Any = _splink_config()
    for key in key_path:
        if not isinstance(node, dict) or key not in node:
            return fallback
        node = node[key]
    try:
        return float(node)
    except (TypeError, ValueError):
        return fallback


def _config_int(key_path: tuple[str, ...], fallback: int) -> int:
    node: Any = _splink_config()
    for key in key_path:
        if not isinstance(node, dict) or key not in node:
            return fallback
        node = node[key]
    try:
        return int(node)
    except (TypeError, ValueError):
        return fallback


# ── shared input handling ──────────────────────────────────────────────────


def _dataframe(records: list[dict[str, Any]], *, unique_id_column: str) -> Any:
    """Records to a comparison DataFrame, enforcing the record cap.

    ``records_to_dataframe`` adds the unique-id column from each record's
    spool identity (``resolve_entities._record_id``), so workbench output is
    wire-compatible with the production merge path (the same ids a spool row
    would carry).
    """
    if not records:
        raise ValueError("at least one record is required")
    if len(records) > MAX_WORKBENCH_RECORDS:
        raise ValueError(
            f"{len(records)} records exceeds the workbench cap of "
            f"{MAX_WORKBENCH_RECORDS:,} — the workbench is for tuning on "
            "profiled/sampled data, not batch merging"
        )
    return records_to_dataframe(records, unique_id_column=unique_id_column)


def _linker_for(
    model_name: str,
    version: int,
    records: list[dict[str, Any]],
    store_root: str,
    unique_id_column: str,
) -> Any:
    """Build a live Linker from a *saved* model version over ``records``.

    No training — the payload is already trained. This is the cheap half of
    the model-first design: the UI iterates on threshold/cluster/review
    without paying EM again.
    """
    v = model.load_version(store_root, model_name, version)
    df = _dataframe(records, unique_id_column=unique_id_column)
    from splink import DuckDBAPI, Linker

    return Linker(df, v["payload"], DuckDBAPI())


# ── S0 status ──────────────────────────────────────────────────────────────


def status() -> dict[str, Any]:
    """The S0 gate, as the UI's "is the workbench usable?" banner reads it."""
    available = splink_available()
    if not available:
        return {"available": False, "reason": "Splink is not installed", "version": None}
    return {
        "available": True,
        "version": imported_version(),
        "required_major": 4,
        "refuse_on_major_mismatch": True,
    }


# ── S-A/B/C/D (+S-E) — recommend ──────────────────────────────────────────


def recommend(
    records: list[dict[str, Any]],
    semantic_types: dict[str, str] | None = None,
    *,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    pair_budget: int | None = None,
) -> dict[str, Any]:
    """The AUTO-mode recommendation (S-A/B/C/D) over the given records.

    ``semantic_types`` is the column→P-4-type map the UI passes through from
    the stored inferences; absent columns fall back to S-B's ``UNKNOWN``
    handling (its own documented fuzzy/exact default). This is read-only —
    no linker, no training, no state written.

    ``pair_budget`` (S-E's "⚠ Too broad" line) defaults to the ``splink:``
    section of config.yaml, then to the module constant.
    """
    require_splink()
    if pair_budget is None:
        pair_budget = _config_int(("pair_budget",), 600_000)
    df = _dataframe(records, unique_id_column=unique_id_column)
    result = rec.auto_recommend(df, semantic_types or {})
    result["n_records"] = len(records)
    return result


# ── S-G/H/I + S-N — train & save ──────────────────────────────────────────


def _normalise_rule(rule: str) -> str:
    """A user-entered blocking rule to Splink's l./r.-prefixed form.

    The UI's ADVANCED/EXPERT fields accept ``name = name`` (what a human
    writes); Splink 4.0.16 requires ``l."name" = r."name"`` (it rejected
    the bare form with "Invalid table names provided (only l. and r. are
    valid)"). This normaliser handles the common bare-equality and
    AND-of-bare-equalities shapes; a rule that already references ``l.`` or
    ``r.`` is passed through unchanged, and a rule we don't recognise is
    passed through too — Splink's own settings validation will reject it
    with an actionable message, which is better than us guessing.
    """
    r = rule.strip()
    if "l." in r or "r." in r:
        return r
    # split on top-level AND (case-insensitive), prefix each equality
    parts = re.split(r"\s+and\s+", r, flags=re.IGNORECASE)
    out = []
    for part in parts:
        m = re.fullmatch(
            r'\s*"?([A-Za-z_][A-Za-z0-9_]*)"?\s*=\s*"?([A-Za-z_][A-Za-z0-9_]*)"?\s*', part
        )
        if not m:
            out.append(part)
            continue
        left, right = m.group(1), m.group(2)
        out.append(f'l."{left}" = r."{right}"')
    return " AND ".join(out)


def train(
    model_name: str,
    records: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    store_root: str = DEFAULT_MODEL_STORE,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    parent_version: int | None = None,
) -> dict[str, Any]:
    """S-G/H/I in-request, then save the trained model as a new version (S-N).

    ``config`` is the (possibly AUTO-recommended, possibly hand-tuned) model
    description:

    * ``comparisons``  — list of ``{semantic_type, field, extra_fields?}``
      (S-B's recommendation shape) OR ``{comparator, field, extra_fields?}``.
    * ``blocking_rules``      — list of SQL-ish strings (prediction blocking).
    * ``training_rules``      — list of SQL-ish strings (S-F; EM runs on these).
    * ``deterministic_rule``  — a SQL-ish string for the prior estimate.
    * ``seed``, ``max_pairs`` — S-G reproducibility (seed is required here
      and on the training module; a missing seed is a 422, not a guess).
    * ``recall`` — the prior-estimate recall assumption.

    Returns the S-I training record (steps, EM passes, coverage, warnings),
    the saved version's number and directory, and its round-trip-verified
    flag. This is the only workbench step that *mutates* anything — it writes
    a model file — and it is the one the UI labels with a real "training…"
    status (S-I's record carries ``status: running/converged/...``).
    """
    require_splink()
    df = _dataframe(records, unique_id_column=unique_id_column)
    comparisons = [_config_comparison(c, unique_id_column) for c in config["comparisons"]]
    pred_rules = [_normalise_rule(r) for r in config["blocking_rules"]]
    training_rules = [
        _normalise_rule(r) for r in (config.get("training_rules") or config["blocking_rules"])
    ]
    det_rules = (
        [_normalise_rule(config["deterministic_rule"])] if config.get("deterministic_rule") else []
    )

    # settings via the shared helper (same path the production matcher uses)
    import splink.comparison_library  # noqa: F401  (ensures the lib is importable)

    settings_comparisons = comparisons
    linker = _build_linker_from_comparisons(df, settings_comparisons, pred_rules, unique_id_column)

    pipeline = splink_training_mod.run_training_pipeline(
        linker,
        training_rules=training_rules,
        deterministic_matching_rules=det_rules,
        seed=int(config["seed"]),
        max_pairs=float(config.get("max_pairs", 1_000_000)),
        recall=float(config.get("recall", 0.7)),
    )
    pipeline["entity"] = model_name

    saved = model.save_version(
        store_root,
        model_name,
        linker,
        df=df,
        metadata={
            "training": pipeline,
            "training_blocking_rules": training_rules,
            "prediction_blocking_rules": pred_rules,
            "n_training_records": len(records),
        },
        parent_version=parent_version,
        status="trained",
    )
    return {
        "training": pipeline,
        "version": saved["version"],
        "directory": saved["directory"],
        "round_trip_verified": saved["round_trip_verified"],
        "payload_keys": saved["payload_keys"],
    }


def _build_linker_from_comparisons(
    df: Any, comparisons: list[Any], pred_rules: list[Any], unique_id_column: str
) -> Any:
    from splink import DuckDBAPI, Linker, SettingsCreator

    settings = SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name=unique_id_column,
        comparisons=list(comparisons),
        blocking_rules_to_generate_predictions=pred_rules,
    )
    return Linker(df, settings, DuckDBAPI())


def _config_comparison(c: dict[str, Any], unique_id_column: str) -> Any:
    """One config comparison entry → the actual Splink comparison object.

    Accepts either the S-B recommendation shape (``semantic_type`` +
    ``field``) or an explicit shape (``comparator`` + ``field``). Both shapes
    go through :func:`splink_recommender.build_comparator` — the one
    ``name -> comparison object`` builder in the workbench — so a hand-tuned
    comparator and an AUTO one can never construct differently.
    """
    if "semantic_type" in c:
        extra = tuple(c.get("extra_fields") or ())
        return rec.comparison_to_settings_hit(c["semantic_type"], c["field"], extra_fields=extra)
    if "comparator" in c:
        extra = tuple(c.get("extra_fields") or ())
        return rec.build_comparator(
            c["comparator"], c["field"], extra_fields=extra, thresholds=c.get("thresholds")
        )
    raise ValueError(f"config comparison must carry semantic_type or comparator: {c!r}")


# ── S-J / S-P — predict & review ──────────────────────────────────────────


def predict(
    model_name: str,
    version: int,
    records: list[dict[str, Any]],
    *,
    store_root: str = DEFAULT_MODEL_STORE,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    match_threshold: float | None = None,
    exclude_threshold: float | None = None,
    review_limit: int | None = None,
) -> dict[str, Any]:
    """S-J predict + S-P banding in one pass, from a saved model version.

    Returns the scored pairs, the band summary, and the prioritised review
    queue — the three things the Predictions/Review screens render. The
    thresholds are the S-P banding boundaries (a *view* over the prediction,
    not the model's output), so re-calling with different thresholds does not
    re-predict — though for API simplicity we re-score here (cheap relative
    to training).

    Thresholds default to the ``splink.predict:`` section of config.yaml,
    then to the module constants (the API's Pydantic defaults match the
    config values, so an explicit request body always wins).
    """
    require_splink()
    if match_threshold is None:
        match_threshold = _config_float(
            ("predict", "match_threshold"), infer.DEFAULT_MATCH_THRESHOLD
        )
    if exclude_threshold is None:
        exclude_threshold = _config_float(
            ("predict", "exclude_threshold"), infer.DEFAULT_EXCLUDE_THRESHOLD
        )
    if review_limit is None:
        review_limit = _config_int(("predict", "review_limit"), 200)
    linker = _linker_for(model_name, version, records, store_root, unique_id_column)
    pred = infer.run_prediction(linker, match_probability_threshold=match_threshold)
    review = infer.prioritise_review(
        pred["predictions"],
        match_threshold=match_threshold,
        exclude_threshold=exclude_threshold,
        limit=review_limit,
    )
    return {"model_version": version, **pred, **review}


def explain(
    model_name: str,
    version: int,
    records: list[dict[str, Any]],
    record_a: dict[str, Any],
    record_b: dict[str, Any],
    *,
    store_root: str = DEFAULT_MODEL_STORE,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
) -> dict[str, Any]:
    """S-J/§33: the per-field Bayes-factor decomposition of one pair's score.

    ``record_a``/``record_b`` are two *raw* records (the same shape as
    ``records`` — original field names plus the ``_origin_*`` lineage keys),
    not pre-built linker rows. They are run through the same
    ``records_to_dataframe`` transform the training records went through
    (unique-id computed, lineage keys stripped, values stringified) before
    reaching ``compare_two_records`` — without it, Splink never sees the
    configured unique-id column on either dict and silently substitutes its
    own ``_left``/``_right`` placeholders, which is indistinguishable from
    Bug 2's ``id_l``/``id_r`` always being ``None`` from the caller's side.
    """
    require_splink()
    linker = _linker_for(model_name, version, records, store_root, unique_id_column)
    a = records_to_dataframe([record_a], unique_id_column=unique_id_column).to_dict("records")[0]
    b = records_to_dataframe([record_b], unique_id_column=unique_id_column).to_dict("records")[0]
    return {"model_version": version, **infer.explain_candidate(linker, a, b)}


# ── S-O — cluster ─────────────────────────────────────────────────────────


def cluster(
    model_name: str,
    version: int,
    records: list[dict[str, Any]],
    *,
    store_root: str = DEFAULT_MODEL_STORE,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    match_probability_threshold: float = infer.DEFAULT_MATCH_THRESHOLD,
) -> dict[str, Any]:
    """S-O: cluster the saved model's predictions at a threshold."""
    require_splink()
    linker = _linker_for(model_name, version, records, store_root, unique_id_column)
    return {
        "model_version": version,
        **infer.run_clustering(linker, match_probability_threshold=match_probability_threshold),
    }


# ── S-K/S-L/S-M — evaluate ────────────────────────────────────────────────


def evaluate(
    model_name: str,
    version: int,
    records: list[dict[str, Any]],
    labelled_pairs: list[list[Any]],
    *,
    store_root: str = DEFAULT_MODEL_STORE,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    current_threshold: float = 0.5,
    recall_floor: float = 0.8,
    precision_floor: float = 0.8,
) -> dict[str, Any]:
    """S-K (register labels, in-request) + S-L (accuracy + thresholds) +
    S-M (FP/FN error analysis), from a saved model version.

    ``labelled_pairs`` is ``[[id_l, id_r, clerical_match_score], ...]`` using
    the *same* record ids the UI showed (the spool identities the DataFrame's
    unique-id column carries). Labels are registered on the per-request linker
    (not persisted — they are evaluation input, not model state); S-N's
    ``metadata`` records *which* labels a version was evaluated against via
    the count + balance, and a caller that wants them on disk stores them
    itself.
    """
    require_splink()
    from src.mcp_server.tools import splink_evaluation as evaln

    linker = _linker_for(model_name, version, records, store_root, unique_id_column)
    pairs = [(str(a), str(b), float(s)) for a, b, s in labelled_pairs]
    reg = evaln.register_labels(linker, pairs)
    acc = evaln.accuracy_report(
        linker,
        reg["table_name"],
        current_threshold=current_threshold,
        recall_floor=recall_floor,
        precision_floor=precision_floor,
    )
    errs = evaln.error_analysis(linker, reg["table_name"])
    return {
        "model_version": version,
        "labels": reg,
        "accuracy": acc,
        "errors": errs,
    }


# ── S-N — model management ────────────────────────────────────────────────


def list_models(store_root: str = DEFAULT_MODEL_STORE) -> dict[str, Any]:
    """All models and their versions (S-N). ``store_root`` is a *directory*
    of model dirs; we list each child as a model."""
    require_splink()
    from pathlib import Path

    base = Path(store_root)
    out: list[dict[str, Any]] = []
    if base.exists():
        for child in sorted(base.iterdir()):
            if child.is_dir():
                out.append(
                    {
                        "model_name": child.name,
                        "versions": model.list_versions(store_root, child.name),
                    }
                )
    return {"models": out}


def get_model(store_root: str, model_name: str, version: int) -> dict[str, Any]:
    """One version's payload + metadata (S-N) — this is the
    ``[View Generated Splink Configuration]`` endpoint's data: the *actual*
    ``save_model_to_json()`` dict, never an illustrative one."""
    require_splink()
    return model.load_version(store_root, model_name, version)


def get_active_config(store_root: str, model_name: str) -> dict[str, Any]:
    """The active version's settings dict — ``[View Generated Splink
    Configuration]``. Returns the payload exactly as saved."""
    require_splink()
    from pathlib import Path

    pointer = Path(store_root) / model_name / "active.json"
    if not pointer.exists():
        raise FileNotFoundError(f"no active version for model {model_name!r}")
    import json

    version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
    v = model.load_version(store_root, model_name, version)
    return {
        "model_name": model_name,
        "version": version,
        "payload": v["payload"],
        "metadata": v["metadata"],
    }


def activate(store_root: str, model_name: str, version: int) -> dict[str, Any]:
    require_splink()
    return model.activate_version(store_root, model_name, version)


def rollback(store_root: str, model_name: str) -> dict[str, Any]:
    require_splink()
    return model.rollback(store_root, model_name)


def archive(store_root: str, model_name: str, version: int) -> dict[str, Any]:
    require_splink()
    return model.archive_version(store_root, model_name, version)


def compare(store_root: str, model_name: str, version_a: int, version_b: int) -> dict[str, Any]:
    require_splink()
    return model.compare_versions(store_root, model_name, version_a, version_b)
