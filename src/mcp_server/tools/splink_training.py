"""S-G / S-H / S-I: u-estimation, EM/m-estimation, training sessions &
diagnostics (Branch_Plan.md B.4; Splink.txt §21-§28).

This is the heart of the workbench — the part that replaces the builtin
engine's hand-tuned weights with parameters *fit to this data*. Three
phases, one module, because S-I is literally "run S-G then S-H and record
what happened":

* **S-G u-estimation** (§22): ``estimate_u_using_random_sampling``. The seed
  is a *required, recorded* argument (never an implicit constant) because §46
  reproducibility demands it — two runs of "the same" training must agree, and
  the only way to guarantee that is to store the seed that produced the
  numbers.
* **S-H EM / m-estimation, multiple passes** (§21, §23, §26, §28): the
  prior (``estimate_probability_two_random_records_match`` on a
  deterministic rule) plus ``estimate_parameters_using_expectation_
  maximisation`` per *training* blocking rule (S-F's output — a different
  rule set from prediction blocking, on purpose). Multiple passes = multiple
  training rules, each producing an ``EMTrainingSession``; we run all of them
  and keep each session's record. It is *never* described as neural-network
  fine-tuning (§21 is explicit) — the docstrings say "expectation
  maximisation" and nothing else.
* **S-I training sessions & diagnostics** (§24, §25, §27): each
  ``EMTrainingSession`` becomes a *persistable plain-dict record* —
  iterations, convergence, per-parameter stability, the m/u iteration
  history — plus the chart data for the UI. A parameter is flagged
  ``unstable`` when its last two iterations still moved more than a small
  epsilon. The record is data; it *never* modifies the model (§27) —
  applying a recommendation is a separate, explicit, logged step (S-N).

The module is pure of state (CLAUDE.md) and every public function calls
:func:`splink_compat.require_splink`. The one genuinely stateful thing — the
``Linker`` — is passed in by the caller (built via ``splink_common.make_
linker``), so this module neither holds a cross-call handle nor caches one.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.mcp_server.tools.splink_compat import imported_version, require_splink

logger = logging.getLogger("merge-engine.tools.splink_training")

# A parameter that still moves more than this between its last two EM
# iterations is "unstable" (S-I's flag). 0.001 is small enough to catch a
# still-converging parameter and large enough not to flag numerical noise on
# a converged one.
_UNSTABLE_EPSILON = 0.001

# The prior-estimation deterministic rule's assumed recall. Splink only needs
# a reasonable estimate (it is not the final match probability). 0.7 is the
# value splink_matcher.py already uses for the same step, so the workbench and
# the legacy engine agree on the assumption rather than each picking a number.
_DEFAULT_RECALL = 0.7


def _chart_series(chart: Any) -> list[dict[str, Any]]:
    """An altair chart (or its data) to a plain list of row-dicts.

    Verified installed-4.0.16: the training charts are altair ``InlineData``
    objects whose row list lives on ``.values`` (an ``InlineDataset``, a
    list of dicts). We do *not* use ``.value`` (absent here) nor
    ``.to_dict("records")`` — the latter returns the chart's *schema* dict,
    and iterating that yields string keys, which is exactly the
    ``'str' object has no attribute 'get'`` bug this guard prevents.
    Degrades to ``[]`` on an unrecognised shape: a chart-extraction gap is a
    display issue, not a training failure.
    """
    if chart is None:
        return []
    data = getattr(chart, "data", None)
    if data is None:
        return []
    values = getattr(data, "values", None)
    if isinstance(values, (list, tuple)):
        return [dict(v) if hasattr(v, "items") else v for v in values]
    if isinstance(data, (list, tuple)):
        return list(data)
    if hasattr(data, "to_dict"):
        try:
            out = data.to_dict("records")
            if isinstance(out, list):
                return out
        except Exception:
            pass
    return []


def estimate_u(
    linker: Any,
    *,
    max_pairs: float = 1_000_000,
    seed: int,
) -> dict[str, Any]:
    """S-G: estimate u-probabilities by random sampling, recording the seed.

    ``seed`` is deliberately *not* defaulted: §46 reproducibility means a
    caller that forgets the seed gets a ``TypeError`` here rather than a
    silently non-reproducible model. Returns a record with the seed, the
    max-pairs ceiling, and the u-probabilities actually estimated (read back
    from the linker's settings so the record reflects what really ran, not
    what was requested).
    """
    require_splink()
    linker.training.estimate_u_using_random_sampling(max_pairs=max_pairs, seed=seed)
    u_values = _extract_parameter_history(linker, "u")
    return {
        "step": "u_estimation",
        "seed": int(seed),
        "max_pairs": float(max_pairs),
        "u_values": u_values,
        "plain_language": "u = how often each field agrees among records that do NOT "
        "match. Estimated from a random sample of "
        f"{max_pairs:,.0f} pairs (seed {seed}), so it does not depend on the "
        "blocking rules — a wide or narrow block cannot bias it.",
    }


def estimate_prior(
    linker: Any,
    *,
    deterministic_matching_rules: list[Any],
    recall: float = _DEFAULT_RECALL,
) -> dict[str, Any] | None:
    """S-H (prior half): estimate the probability two random records match,
    from a *deterministic* rule that is assumed to catch a known ``recall``
    fraction of the true matches.

    This is empirically necessary, not optional, given a single blocking
    column — the reasoning lives in ``splink_matcher.py``'s inline comment and
    is reproduced here rather than re-derived: without it, EM re-converges the
    prior toward Splink's 0.0001 default instead of the dataset's real match
    rate. Returns ``None`` (and logs) when no deterministic rule is supplied —
    the caller then runs EM with the prior estimated jointly, which is noisier
    but valid.
    """
    require_splink()
    if not deterministic_matching_rules:
        logger.info(
            "No deterministic rule for prior estimation; EM will estimate the "
            "match prior jointly with the m-values (noisier on a small block)."
        )
        return None
    linker.training.estimate_probability_two_random_records_match(
        deterministic_matching_rules, recall=recall
    )
    prior = _read_prior(linker)
    return {
        "step": "prior_estimation",
        "deterministic_rules": [str(r) for r in deterministic_matching_rules],
        "recall_assumed": float(recall),
        "probability_two_random_records_match": prior,
        "plain_language": f"About 1 in {round(1.0 / prior, 1) if prior else '∞'} pairs of "
        "records is expected to be a genuine match, estimated from a rule "
        "assumed to catch "
        f"{recall:.0%} of true matches.",
    }


def run_em_pass(
    linker: Any,
    training_rule: Any,
    *,
    estimate_without_term_frequencies: bool = False,
    fix_probability_two_random_records_match: bool = False,
) -> dict[str, Any]:
    """S-H (EM half): one EM pass over one *training* blocking rule, returning
    the session's record (S-I's shape). ``fix_probability_two_random_records_
    match`` should be True when ``estimate_prior`` ran first, so the handful
    of noisy in-block pairs cannot drag the prior back down.
    """
    require_splink()
    session = linker.training.estimate_parameters_using_expectation_maximisation(
        training_rule,
        estimate_without_term_frequencies=estimate_without_term_frequencies,
        fix_probability_two_random_records_match=fix_probability_two_random_records_match,
    )
    return _session_to_record(session, training_rule, linker)


def run_training_pipeline(
    linker: Any,
    *,
    training_rules: list[Any],
    deterministic_matching_rules: list[Any] | None = None,
    seed: int,
    max_pairs: float = 1_000_000,
    recall: float = _DEFAULT_RECALL,
) -> dict[str, Any]:
    """S-I end-to-end: u → prior → one EM pass per training rule, returning
    one persistable session record that bundles every step.

    This is what the UI's "Train" button calls. The output is the S-N model
    version's *training provenance* — which seed, which rules, how many
    iterations each EM pass took, whether anything is still unstable, which
    comparisons are still unestimated. It records; it does not apply (the
    model is mutated in-place on ``linker`` by the calls themselves, which is
    how Splink works — but *deciding to use* that model is S-N's job, and
    nothing here auto-activates it, §27).

    Any step's failure is captured in the record's ``warnings`` rather than
    propagated: a training run that estimates u fine but fails one EM rule
    is still a *useful, inspectable* run (the UI shows exactly which rule
    failed and why), not a 500.
    """
    require_splink()
    warnings: list[str] = []
    started = time.monotonic()
    record: dict[str, Any] = {
        "entity": None,  # filled by the caller; this module is entity-agnostic
        "splink_version": imported_version(),
        "seed": int(seed),
        "steps": {},
        "em_passes": [],
        "warnings": warnings,
        "seconds": 0.0,
        "status": "running",  # real status only (§53); "running" until done
    }

    try:
        record["steps"]["u"] = estimate_u(linker, max_pairs=max_pairs, seed=seed)
    except Exception as exc:
        warnings.append(f"u-estimation failed: {exc}")
        record["status"] = "failed_u"
        record["seconds"] = time.monotonic() - started
        return record

    try:
        prior = estimate_prior(
            linker,
            deterministic_matching_rules=deterministic_matching_rules or [],
            recall=recall,
        )
        record["steps"]["prior"] = prior
    except Exception as exc:
        warnings.append(f"prior estimation failed: {exc}")
        prior = None

    for rule in training_rules:
        try:
            pass_record = run_em_pass(
                linker,
                rule,
                fix_probability_two_random_records_match=prior is not None,
            )
            record["em_passes"].append(pass_record)
        except Exception as exc:
            warnings.append(f"EM pass on rule {rule!r} failed: {exc}")

    # Coverage: which comparisons still have unestimated m/u (§26 view).
    record["training_coverage"] = _training_coverage(linker)
    # Overall stability: a session is unstable if any parameter is.
    record["unstable_parameters"] = [
        p
        for p in (
            param
            for pass_record in record["em_passes"]
            for param in pass_record.get("parameters", [])
            if param.get("unstable")
        )
    ]
    record["fully_trained"] = bool(record["training_coverage"]["fully_trained"])
    record["status"] = "converged" if record["fully_trained"] else "partially_trained"
    record["seconds"] = round(time.monotonic() - started, 3)
    return record


# ── readers: turn a live Linker/session into plain records ────────────────


def _read_prior(linker: Any) -> float | None:
    # Verified installed-4.0.16: the prior is a direct attribute of the
    # Settings object's core_model_settings (not a dict key) — and
    # ``as_dict()`` carries it too. Try the attribute first, the dict second.
    try:
        return float(linker._settings_obj.core_model_settings.probability_two_random_records_match)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        return float(linker._settings_obj.as_dict()["probability_two_random_records_match"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _extract_parameter_history(linker: Any, kind: str) -> list[dict[str, Any]]:
    """The current m/u values per comparison/level, as plain rows. Null levels
    have no m-probability (installed 4.0.16 raises on the property), so they
    are skipped with a note rather than crashing the reader."""
    rows: list[dict[str, Any]] = []
    try:
        comparisons = linker._settings_obj.comparisons
    except AttributeError:
        return rows
    for comp in comparisons:
        for level in comp.comparison_levels:
            row: dict[str, Any] = {
                "comparison": comp.output_column_name,
                "level": _level_label(level),
                "comparison_vector_value": level.comparison_vector_value,
            }
            for key, attr in (("m", "m_probability"), ("u", "u_probability")):
                if key != kind:
                    continue
                try:
                    row[f"{key}_probability"] = float(getattr(level, attr))
                except (ValueError, TypeError):
                    row[f"{key}_probability"] = None
                except Exception:
                    row[f"{key}_probability"] = None
            rows.append(row)
    return rows


def _level_label(level: Any) -> str:
    try:
        return str(level.label_for_charts)
    except Exception:
        return type(level).__name__


def _training_coverage(linker: Any) -> dict[str, Any]:
    """Which comparisons/levels are still missing m or u estimates (§26).

    A comparison used in the *training* blocking rule cannot have its own m
    trained from that block (every pair in the block agrees on it by
    construction) — that is expected, not a failure, and is reported as
    ``blocked`` so the UI doesn't show it as a defect."""
    try:
        comparisons = linker._settings_obj.comparisons
    except AttributeError:
        return {"fully_trained": False, "comparisons": []}
    out = []
    fully = True
    for comp in comparisons:
        unest_m: list[str] = []
        unest_u: list[str] = []
        for level in comp.comparison_levels:
            for attr, bucket in (("m_probability", unest_m), ("u_probability", unest_u)):
                try:
                    getattr(level, attr)
                except (ValueError, TypeError):
                    pass  # null level: no such parameter exists, not "missing"
                except Exception:
                    bucket.append(_level_label(level))
        fully = fully and not unest_m and not unest_u
        out.append(
            {"comparison": comp.output_column_name, "missing_m": unest_m, "missing_u": unest_u}
        )
    return {"fully_trained": fully, "comparisons": out}


def _session_to_record(session: Any, training_rule: Any, linker: Any) -> dict[str, Any]:
    """An ``EMTrainingSession`` (installed 4.0.16) to S-I's persistable record.

    Primary source is the session's ``_core_model_settings_history`` — one
    ``CoreModelSettings`` per EM iteration, each carrying the prior and the
    per-level m/u *at that iteration*. Verified installed-4.0.16: this is the
    authoritative source, and it is populated whether or not the prior was
    fixed. The altair iteration chart (``probability_two_random_records_match_
    iteration_chart``) is used only as a fallback for the prior series,
    because it is empty when the prior is fixed (which is the common case —
    we fix it whenever ``estimate_prior`` ran first).

    Per-parameter stability (the §27 "did it actually settle" question) is
    computed over this history: a parameter is ``unstable`` if its last two
    recorded iterations still differ by more than :data:`_UNSTABLE_EPSILON`.
    """
    history = getattr(session, "_core_model_settings_history", None) or []

    # per-iteration prior series
    prior_history: list[dict[str, Any]] = []
    if history:
        for i, h in enumerate(history):
            try:
                prior_history.append(
                    {
                        "iteration": i,
                        "probability_two_random_records_match": float(
                            h.probability_two_random_records_match
                        ),
                    }
                )
            except (AttributeError, TypeError, ValueError):
                continue
    else:
        # fallback: the chart (populated only when the prior was NOT fixed)
        try:
            for i, row in enumerate(
                _chart_series(session.probability_two_random_records_match_iteration_chart())
            ):
                prior_history.append(
                    {
                        "iteration": i,
                        "probability_two_random_records_match": row.get(
                            "probability_two_random_records_match"
                        ),
                    }
                )
        except Exception:
            pass

    final_prior = _read_prior(linker)
    iterations = len(prior_history)

    # per-level m/u trajectory, then final value + stability, per level
    # collected across the full history. A level present in the final
    # settings but with fewer than two history entries is "insufficient
    # history", not "stable" — we don't claim convergence we can't show.
    final = history[-1] if history else None
    # key: (comparison name, level label, comparison_vector_value, "m"/"u").
    # comparison_vector_value's real type comes from Splink internals (Any).
    trajectories: dict[tuple[str, str, Any, str], list[float | None]] = {}
    for i, h in enumerate(history):
        try:
            comps = h.comparisons
        except AttributeError:
            continue
        for comp in comps:
            for level in comp.comparison_levels:
                for kind, attr in (("m", "m_probability"), ("u", "u_probability")):
                    try:
                        val = float(getattr(level, attr))
                    except (ValueError, TypeError):
                        continue  # null level: no such parameter
                    except Exception:
                        continue
                    key = (
                        comp.output_column_name,
                        _level_label(level),
                        level.comparison_vector_value,
                        kind,
                    )
                    trajectories.setdefault(key, [None] * iterations)[i] = val

    parameters: list[dict[str, Any]] = []
    if final is not None:
        for comp in final.comparisons:
            for level in comp.comparison_levels:
                for kind, attr in (("m", "m_probability"), ("u", "u_probability")):
                    param_val: float | None
                    try:
                        param_val = float(getattr(level, attr))
                    except (ValueError, TypeError):
                        continue  # null level
                    except Exception:
                        param_val = None
                    key = (
                        comp.output_column_name,
                        _level_label(level),
                        level.comparison_vector_value,
                        kind,
                    )
                    traj = trajectories.get(key, [])
                    recent = [t for t in traj if t is not None]
                    stable_param = (
                        len(recent) >= 2 and abs(recent[-1] - recent[-2]) <= _UNSTABLE_EPSILON
                    )
                    parameters.append(
                        {
                            "comparison": comp.output_column_name,
                            "level": _level_label(level),
                            "comparison_vector_value": level.comparison_vector_value,
                            f"{kind}_probability": round(param_val, 8)
                            if param_val is not None
                            else None,
                            "trajectory": [round(t, 8) if t is not None else None for t in traj],
                            "unstable": not stable_param,
                        }
                    )

    # prior stability across the last two recorded iterations
    prior_vals = [
        h["probability_two_random_records_match"]
        for h in prior_history
        if h["probability_two_random_records_match"] is not None
    ]
    prior_stable = (
        len(prior_vals) >= 2 and abs(prior_vals[-1] - prior_vals[-2]) <= _UNSTABLE_EPSILON
    )
    unstable_parameters = [
        {
            "comparison": p["comparison"],
            "level": p["level"],
            "kind": "m" if p.get("m_probability") is not None or "m_probability" in p else "u",
        }
        for p in parameters
        if p.get("unstable")
    ]

    return {
        "rule": str(training_rule),
        "iterations": iterations,
        "final_probability_two_random_records_match": final_prior,
        "prior_converged": prior_stable,
        "prior_history": prior_history,
        "parameters": parameters,
        "unstable_parameters": unstable_parameters,
        "unstable": (not prior_stable) or bool(unstable_parameters),
    }
