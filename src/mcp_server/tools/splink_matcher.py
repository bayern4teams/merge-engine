"""Splink optional matching engine integration (Phase 7.4).

Implements resolve_with_splink(...) reproducing the exact 13-key result-dict
contract of resolve_entities_impl while strictly respecting the architecture
invariants carried over from B4-matching-precision:

1. Splink replaces *scoring*, not *clustering*. Splink generates pairwise
   probabilities; these are fed into MergeEngine's frozen _build_clusters()
   for cohesion checking, exactly like the builtin Fellegi-Sunter engine.
2. Cluster IDs are translated through _assign_cluster_id (deterministic_uuid).
3. Schema divergence is handled via projected comparison view using
   _project_to_canonical.
4. Any Splink failure logs a warning and falls back to builtin (returns None).

Salvaged from B4-matching-precision with four corrections the plan's audit
found necessary before this engine merges anything at all (see
docs/plans/1-last-run-i-wiggly-pearl.md, Phase 7.4):

1. **The model is actually trained.** B4's version called ``predict()``
   directly with no ``estimate_u_using_random_sampling``/EM step at all, so
   Splink defaulted the match prior to 0.0001 (~-13.3 bits) while predict()
   hardcoded a 0.5 cutoff — a byte-identical name pair scored 0.093 and was
   silently discarded. Training is now three steps (u-estimation, a
   deterministic-rule prior estimate, then EM fixing that prior — see the
   inline comment in ``score_pairs_with_splink`` for why the middle step is
   empirically required, not optional, given a single blocking column), and
   probabilities are calibrated against the builtin's auto_match/auto_exclude
   bands downstream in ``_build_clusters`` rather than at Splink's own
   predict() cutoff.
2. **The last-ditch blocking fallback is gone.** B4 blocked on
   ``_record_id_str`` (a unique id) when no comparison column was usable,
   guaranteeing zero candidate pairs. That case now returns None so the
   caller drops to the builtin engine instead of reporting a false success.
3. **Clustering stats are collected.** ``stats=`` is now passed to
   ``build_clusters_fn`` so ``clustering_seconds``/``largest_component_size``/
   ``large_components`` are populated instead of silently zeroing.
4. **Per-entity strictness is honoured.** ``auto_match``/``auto_exclude`` are
   derived via the same ``_thresholds_for`` the builtin engine uses and
   passed through, instead of Splink silently discarding the user's chosen
   strictness in favour of module defaults.

A fifth, non-plan-mandated addition for consistency with Phase 7.1-7.3's
lineage work: every scored pair also becomes a ``pair_evidence`` entry
(``method="splink"``, no field-level breakdown — Splink does not expose
per-comparison weights the same way the builtin's Fellegi-Sunter model
does), so Splink-scored merges get ``match_evidence`` at the final gate too.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.splink_workbench import DEFAULT_MODEL_STORE

logger = logging.getLogger("merge-engine.tools.splink_matcher")

try:
    import pandas as pd  # type: ignore[import-untyped]  # pandas stubs untyped at this pin
    import splink.comparison_library as cl
    from splink import DuckDBAPI, Linker, SettingsCreator, block_on

    _HAS_SPLINK = True
except Exception:
    _HAS_SPLINK = False

# Splink docs' own tutorial default for random-sample u-estimation; the cost
# is bounded by min(max_pairs, n*(n-1)/2) so this is a ceiling, not a target.
_U_ESTIMATION_MAX_PAIRS = 1_000_000
_U_ESTIMATION_SEED = 42

# Assumed fraction of true matches the deterministic prior-estimation rule
# below actually catches. Splink only needs this to be a reasonable estimate
# (it is not the final match probability), and 0.7 is the value used in
# Splink's own tutorials for a single reasonably-selective exact-match rule.
_DETERMINISTIC_RULE_RECALL = 0.7

# B.6.2: token-overlap blocking, alongside (not instead of) the 4-char
# prefix rule. A reordered or qualifier-prefixed name ("Satellite Systems
# Boeing" vs "Boeing Satellite Division") shares no 4-char prefix and no
# domain, but does share a real word — this rule catches that case. Plain
# DuckDB SQL (Splink's own backend here), sqlglot-free per the plan: split
# each side's lowercased name on spaces and require a nonempty intersection.
# Costed against the existing rules before being unioned in — see the B.6.2
# commit message for the numbers.
_NAME_TOKEN_OVERLAP_RULE = (
    "len(list_intersect(string_split(lower(l.\"name\"), ' '), "
    "string_split(lower(r.\"name\"), ' '))) > 0"
)


def _predict_with_saved_profile(
    entity_type: str,
    df: Any,
    model_profile: str,
    store_root: str,
) -> list[tuple[str, str, float]] | None:
    """B.6.3: predict-only using an already-trained, saved model version —
    no ``.training`` call of any kind, success or failure. ``model_profile``
    is ``"<model_name>/v<N>"`` (ADR 0016's S-N file store, already used by
    the workbench). Malformed profile / missing version / predict failure
    all return ``None`` so the caller drops to builtin, exactly like every
    other Splink failure mode in this module — never a silent fresh retrain.
    """
    from src.mcp_server.tools import splink_model as sm

    model_name, sep, version_str = model_profile.partition("/v")
    if not model_name or not sep or not version_str.isdigit():
        logger.warning(
            "Splink: malformed model_profile %r for entity '%s' (expected "
            "'<model_name>/v<N>'); falling back to builtin",
            model_profile,
            entity_type,
        )
        return None
    try:
        version = sm.load_version(store_root, model_name, int(version_str))
        linker = Linker(df, version["payload"], DuckDBAPI())
        predictions = linker.inference.predict(threshold_match_probability=0.0)
        pred_df = predictions.as_pandas_dataframe()  # type: ignore[no-untyped-call]  # splink 4.x API is untyped
    except Exception as exc:
        logger.warning(
            "Splink: predict-only load of model_profile %r failed for entity "
            "'%s' (%s); falling back to builtin",
            model_profile,
            entity_type,
            exc,
        )
        return None

    scored_pairs: list[tuple[str, str, float]] = []
    for _, row in pred_df.iterrows():
        scored_pairs.append(
            (
                str(row["_record_id_str_l"]),
                str(row["_record_id_str_r"]),
                float(row["match_probability"]),
            )
        )
    logger.info(
        "Splink: predict-only via model_profile %r produced %d pairwise predictions for entity '%s'",
        model_profile,
        len(scored_pairs),
        entity_type,
    )
    return scored_pairs


async def score_pairs_with_splink(
    entity_type: str,
    records: list[dict[str, Any]],
    matching_rules: dict[str, Any] | None = None,
    store_root: str = DEFAULT_MODEL_STORE,
) -> list[tuple[str, str, float]] | None:
    """Train Splink over ``records`` and return every scored candidate pair.

    This is the project/train/predict core ``resolve_with_splink`` below
    wraps with clustering. Split out so the eval harness
    (``tests/eval/score.py``) can train ONE model over the whole golden
    record set and look up each labelled pair's prediction, instead of
    re-training per pair — training needs the whole record set's value
    distribution to mean anything, and re-training per pair would be both
    methodologically wrong and ~560x more expensive.

    Returns ``None`` on the same conditions ``resolve_with_splink`` falls
    back to the builtin engine for (Splink not installed, too few records,
    no usable comparison column, training failure). A pair never blocked
    together simply never appears in the returned list — callers treat a
    missing pair the same way a pair blocking never grouped together would.
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
        from src.mcp_server.tools.resolve_entities import (
            _domain_of,
            _project_to_canonical,
            _record_id,
        )

        # ── 1. Build Projected Comparison View (Canonical 4-6 fields) ───────
        projected_rows: list[dict[str, Any]] = []
        for rec in records:
            rid = _record_id(rec)
            proj, _ = _project_to_canonical(rec)
            row_dict: dict[str, Any] = {"_record_id_str": str(rid)}
            for k, v in proj.items():
                if v is None:
                    continue
                # Registrable-domain normalisation BEFORE Splink ever sees the
                # value, via the same `_domain_of` the builtin engine already
                # trusts as a full-confidence identity key (resolve_entities.py
                # docstring, "C0c"). Without this, "https://www.boeing.com/
                # careers" and "boeing.com" are two different strings to an
                # exact-match blocking rule/comparator and a domain column ends
                # up far weaker here than it already is in the builtin engine.
                row_dict[k] = _domain_of(v) if k == "domain" else str(v)
            projected_rows.append(row_dict)

        df = pd.DataFrame(projected_rows)

        # B.6.3: train-once wiring. A configured model_profile means a
        # human already trained and validated this model in the workbench
        # (S-N file store) — predict-only, no per-call EM retrain. Any
        # failure here (bad format, missing version) falls back to builtin
        # (returns None), same discipline as every other failure mode in
        # this function — it never silently degrades to a fresh full
        # retrain instead of what was actually asked for.
        model_profile = (matching_rules or {}).get("model_profile")
        if model_profile:
            return _predict_with_saved_profile(entity_type, df, model_profile, store_root)

        # Identify candidate comparison columns available in the dataframe
        available_cols = [c for c in df.columns if c != "_record_id_str" and df[c].notna().any()]
        if not available_cols:
            logger.warning(
                "Splink: No valid comparison columns in projected view; falling back to builtin"
            )
            return None

        # Build comparison objects for Splink. Blocking rules are a UNION —
        # a pair needs to satisfy only ONE to be scored — so every strong
        # identity-shaped field gets its own rule instead of the first
        # available column silently claiming the only one. This was the
        # deeper half of the "block_on(name) can't see the pairs it needs to
        # score" defect: even after fixing name's own rule, `domain` (a much
        # stronger blocking key when present — two records sharing a
        # registrable domain are almost always the same company) was never
        # given a chance, because `if not blocking_rules` had already been
        # satisfied by `name`. Live evidence and the fixed numbers are in the
        # Splink tuning methodology file, not repeated here.
        comparisons: list[Any] = []
        blocking_rules: list[Any] = []

        if "name" in available_cols:
            comparisons.append(cl.JaroWinklerAtThresholds("name", [0.9, 0.7]))
            # Never block on the exact field you fuzzy-compare (Splink's own
            # documented anti-pattern — confirmed live: `block_on("name")`
            # alone scored 0 of 15 pairs on a real Boeing/Airbus fixture,
            # because almost no true-duplicate pair in real company data
            # shares a byte-identical name). A loosened first-4-character
            # key still bounds the block (nothing is compared against every
            # other row) while catching prefix-sharing variants; it is
            # unioned with the deterministic keys below, never a substitute
            # for them.
            blocking_rules.append('substr(lower(l."name"), 1, 4) = substr(lower(r."name"), 1, 4)')
            # Looser still, unioned alongside the prefix rule (B.6.2): catches
            # a shared real word the prefix rule misses on reordered/
            # qualifier-prefixed names. See _NAME_TOKEN_OVERLAP_RULE above.
            blocking_rules.append(_NAME_TOKEN_OVERLAP_RULE)

        if "domain" in available_cols:
            comparisons.append(cl.ExactMatch("domain").configure(term_frequency_adjustments=True))
            blocking_rules.append(block_on("domain"))

        if "email" in available_cols:
            comparisons.append(cl.ExactMatch("email").configure(term_frequency_adjustments=True))
            blocking_rules.append(block_on("email"))

        # ponytail: every OTHER exact-match-shaped column still gets a
        # comparison, but only the first one contributes a blocking rule of
        # its own (same fallback shape as before this fix) when name/domain/
        # email left none — unioning a rule per arbitrary remaining column
        # risks the same low-selectivity blow-up the comparison-budget
        # bug hit on `jobTitle` values like "senior" (§18.4/§00b); a real fix
        # would cost each candidate with Splink's own
        # `count_comparisons_from_blocking_rule` (already used by the B.4
        # workbench's S-E recommender) before unioning it, not just union
        # blindly. Upgrade path: reuse S-E's cost check here.
        for col in available_cols:
            if col not in ("name", "domain", "email"):
                comparisons.append(cl.ExactMatch(col))
                if not blocking_rules:
                    blocking_rules.append(block_on(col))

        if not blocking_rules:
            # Fix 2 (Phase 7.4): blocking on the unique id guarantees zero
            # candidate pairs (every row is its own singleton block) — that
            # used to be the fallback here and reported a false success.
            # Refuse and let the caller drop to the builtin engine instead.
            logger.warning(
                "Splink: no usable comparison column yielded a blocking rule "
                "for entity '%s'; refusing to block on the unique id and "
                "falling back to builtin",
                entity_type,
            )
            return None

        settings = SettingsCreator(
            link_type="dedupe_only",
            unique_id_column_name="_record_id_str",
            comparisons=comparisons,
            blocking_rules_to_generate_predictions=blocking_rules,
        )

        db_api = DuckDBAPI()
        linker = Linker(df, settings, db_api)

        # ── 2. Train the model (Fix 1 — the blocking defect) ────────────────
        # Untrained, Splink defaults the match prior to 0.0001 (~-13.3 bits)
        # and every prediction collapses toward zero regardless of how well
        # the fields actually agree. Three steps, each non-fatal on its own
        # (falls through to the next with a warning) except the first, whose
        # failure means nothing downstream can be trusted:
        #
        # 1. u-estimation — how often fields agree among NON-matches, from a
        #    random sample across the whole dataset (unaffected by blocking).
        # 2. A deterministic-rule prior estimate — empirically necessary, not
        #    optional. The single blocking column (comparisons.py's own
        #    limitation: a column used to BLOCK can never have its own m/u
        #    trained from that block, since every pair inside the block
        #    already agrees on it by construction) starves the EM pass below
        #    of any real contrast when there are few blocked pairs, and
        #    ``probability_two_random_records_match`` re-converges toward
        #    the 0.0001 default instead of the dataset's real match rate.
        #    Verified on a 16-record fixture with 3 true + 3 false duplicate
        #    pairs: EM alone (no deterministic prior) scored every true
        #    duplicate at 0.015; adding this step scored them at 0.85 with
        #    the false duplicates still correctly at 0.0004 — a ~55x
        #    separation the EM-only path could not produce. Uses "email" (the
        #    strongest identity-shaped field) when available, else the same
        #    field used for prediction blocking, so this never depends on
        #    the primary blocking rule already being trainable.
        # 3. EM itself, fixing the prior at step 2's estimate when that step
        #    succeeded so a handful of noisy in-block pairs cannot re-drag it
        #    back down, and estimating m-values for every comparison except
        #    the blocking column (which stays at Splink's neutral default —
        #    it carries no per-pair discriminative power inside a block by
        #    definition, so this is not a loss).
        try:
            linker.training.estimate_u_using_random_sampling(
                max_pairs=_U_ESTIMATION_MAX_PAIRS, seed=_U_ESTIMATION_SEED
            )
        except Exception as exc:
            logger.warning(
                "Splink: u-estimation failed for entity '%s' (%s); falling back to builtin",
                entity_type,
                exc,
            )
            return None

        # Prefer domain over email as the deterministic-prior field when both
        # are available: for company-shaped entities domain is populated far
        # more often than a person-shaped email field, and it's now
        # registrable-domain-normalised above, so it is at least as reliable
        # an identity key as email once populated.
        if "domain" in available_cols:
            deterministic_field = "domain"
        elif "email" in available_cols:
            deterministic_field = "email"
        else:
            deterministic_field = available_cols[0]
        prior_fixed = False
        try:
            linker.training.estimate_probability_two_random_records_match(
                [block_on(deterministic_field)], recall=_DETERMINISTIC_RULE_RECALL
            )
            prior_fixed = True
        except Exception as exc:
            logger.warning(
                "Splink: deterministic prior estimation on column %r failed for entity "
                "'%s' (%s); EM will estimate the match prior jointly with m-values instead "
                "(noisier on a small blocked sample)",
                deterministic_field,
                entity_type,
                exc,
            )

        trained_any = False
        for rule in blocking_rules:
            try:
                linker.training.estimate_parameters_using_expectation_maximisation(
                    rule,
                    estimate_without_term_frequencies=False,
                    fix_probability_two_random_records_match=prior_fixed,
                )
                trained_any = True
            except Exception as exc:
                logger.warning(
                    "Splink: EM training with blocking rule %r failed for entity '%s' (%s); "
                    "skipping this rule",
                    rule,
                    entity_type,
                    exc,
                )
        if not trained_any:
            logger.warning(
                "Splink: no blocking rule trained successfully for entity '%s'; "
                "falling back to builtin",
                entity_type,
            )
            return None

        # ── 3. Pairwise Probability Prediction ──────────────────────────────
        # threshold_match_probability=0.0 (not B4's hardcoded 0.5): scoring
        # and thresholding are kept separate stages, same as the builtin
        # engine — every candidate pair within the blocking rules is scored;
        # auto_match/auto_exclude (Fix 4) decide merge/exclude/borderline
        # downstream in _build_clusters, not here.
        predictions = linker.inference.predict(threshold_match_probability=0.0)
        pred_df = predictions.as_pandas_dataframe()  # type: ignore[no-untyped-call]  # splink 4.x API is untyped

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
        return scored_pairs

    except Exception as exc:
        logger.warning(
            "Splink scoring failed with error (%s); falling back to builtin engine",
            exc,
            exc_info=True,
        )
        return None


async def resolve_with_splink(
    entity_type: str,
    records: list[dict[str, Any]],
    matching_rules: dict[str, Any] | None = None,
    known_cluster_ids: dict[str, str] | None = None,
    build_clusters_fn: Any = None,
    store_root: str = DEFAULT_MODEL_STORE,
) -> dict[str, Any] | None:
    """Run entity resolution using Splink as the pairwise scoring engine.

    Returns the exact 13-key result-dict contract on success, or None on
    failure (triggering seamless fallback to the builtin matcher).
    """
    from src.mcp_server.tools.resolve_entities import _pending_merge_candidates, _thresholds_for

    rules = matching_rules or {}
    scored_pairs = await score_pairs_with_splink(entity_type, records, rules, store_root=store_root)
    if scored_pairs is None:
        return None

    auto_match, auto_exclude = _thresholds_for(rules)
    logger.info(
        "Splink: entity '%s' scored, clustering with auto_match=%.2f, auto_exclude=%.2f",
        entity_type,
        auto_match,
        auto_exclude,
    )

    # Phase 7.1 consistency: Splink-scored merges get match_evidence too,
    # though without a field-level breakdown — Splink does not expose
    # per-comparison weights the way the builtin's Fellegi-Sunter model does
    # via _pair_field_evidence.
    pair_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for id1, id2, prob in scored_pairs:
        key = (id1, id2) if id1 < id2 else (id2, id1)
        pair_evidence[key] = {
            "a": id1,
            "b": id2,
            "confidence": round(prob, 4),
            "method": "splink",
            "matched_fields": [],
            "mismatched_fields": [],
        }

    # ── Cluster Pairwise Predictions via MergeEngine's _build_clusters ──────
    stats: dict[str, Any] = {}
    if build_clusters_fn is None:
        from src.mcp_server.tools.resolve_entities import _build_clusters

        build_clusters_fn = _build_clusters

    clusters, auto_matched, auto_excluded = await build_clusters_fn(
        records,
        scored_pairs,
        entity_type=entity_type,
        known_cluster_ids=known_cluster_ids,
        auto_match=auto_match,
        auto_exclude=auto_exclude,
        stats=stats,
        pair_evidence=pair_evidence,
    )

    # ── Construct Exact Result Dict Contract (13 keys) ──────────────────────
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
        "clustering_seconds": stats.get("clustering_seconds", 0.0),
        "largest_component_size": stats.get("largest_component_size", 0),
        "large_components": stats.get("large_components", 0),
        "pending_merge_candidates": _pending_merge_candidates(
            scored_pairs, auto_match, auto_exclude
        ),
    }
