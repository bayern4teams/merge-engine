"""
resolve_entities tool implementation.

Given records from multiple sources for a given entity type, clusters
matching ones with a hybrid pipeline:

1. Blocking (cheap): candidate pairs are only generated within blocks
   sharing a normalized first name-token, so comparisons stay far below
   the O(n^2) all-pairs count. Blocks are purged and meta-blocked
   (P2), never truncated; an out-of-memory guard fails loudly.
2. Heuristic scoring (cheap): token-set Jaccard + difflib sequence
   similarity over common fields. Confident pairs (>=0.95 match,
   <=0.70 non-match) are settled here without any LLM call.
3. LLM judgment (borderline only): pairs in the 0.70-0.95 band go to
   the LLM in one batched call per entity type; its confidence replaces
   the heuristic score. On LLM failure the heuristic score is kept —
   the run never blocks on the LLM.

Union-Find connected components then builds the entity clusters.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools.fellegi_sunter import (
    LEVEL_DISAGREE,
    LEVEL_EXACT,
    LEVEL_NULL,
    LEVEL_STRONG,
    LEVEL_WEAK,
    build_token_document_frequencies,
    idf_soft_similarity,
)
from src.utils.id_utils import deterministic_uuid
from src.utils.tracing import span

logger = logging.getLogger("merge-engine.tools.resolve_entities")


# Out-of-memory guard for candidate generation (P2) — NOT a quality knob.
# Pairs are excluded only by block purging and meta-blocking, properties
# computed from the block collection. If the surviving blocks could still
# propose more pairs than this, the run fails loudly
# (CandidatePairBudgetExceeded) instead of silently truncating: the old
# 250,000 cap discarded 711,670 pairs of one entity unexamined while the
# run ended `completed`. Sized for memory, not quality: measured ~300 bytes per
# materialised pair (tuple, edge key, weight), so 5M pairs is ~1.5 GB.
_MAX_CANDIDATE_PAIRS = 5_000_000


class CandidatePairBudgetExceeded(RuntimeError):
    """Blocking would materialise more candidate pairs than memory allows."""


# Confidence thresholds (shared by scoring and clustering)
_AUTO_MATCH = 0.95
_AUTO_EXCLUDE = 0.70

# Per-entity strictness (Phase O.2). Only the auto-merge bar moves: raising it
# sends more pairs to human review, which is the recoverable direction.
# "loose" still sits above the borderline band, so nothing merges on evidence
# the system called uncertain.
_STRICTNESS = {
    "loose": 0.90,
    "normal": _AUTO_MATCH,
    "strict": 0.98,
}

# A multi-record cluster must hold together this well on average, or it is
# split. Guards against chain merges: A-B and B-C both score decently, A-C
# not at all, and connected components would silently fuse three entities.
_MIN_COHESION = 0.90

# Field-name hints for picking the blocking field (best "name-ish" field)
_NAME_FIELD_HINTS = (
    "name",
    "title",
    "full_name",
    "name_full",
    "company",
    "organization",
    "org",
    "label",
    "email",
)

# Field-name hints for identifier-like fields used as a second blocking pass
# (catches cross-origin pairs whose names differ but share a business ref).
# Deliberately excludes "number": it matches phone fields (tel_number), whose
# first token (country code) lumps entire origins into one giant block.
_REF_FIELD_HINTS = (
    "ref",
    "code",
    "external",
    "uid",
    "guid",
    "registration",
)

# Fields holding only *part* of a person's name. They match the "name" hint by
# substring, but two people sharing a first name are not the same person, so
# they must never drive the deterministic (auto-merge) name path.
_PARTIAL_NAME_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "given_name",
        "family_name",
        "surname",
        "middle_name",
        "username",
        "nickname",
    }
)

# Fields that uniquely identify an entity. Two records carrying different
# non-null values here are different entities, however alike their names.
_UNIQUE_IDENTITY_HINTS = ("email", "tax_id", "vat", "ssn")

# Fields an operator would recognise as a genuine identity key (Phase 3.1,
# merge-engine-remediation-1-to-6 project memory): when both records carry a
# non-null value here and it agrees, that settles identity directly — it is
# not subject to the term-frequency heuristic meant to catch ACCIDENTAL
# agreement, because a declared identity key is not accidental. This is what
# fixes a scoped single-subject merge, where every record's name field
# contains the subject and the frequency-based scorer sees nothing rare left
# to reward: a matching domain or ticker still proves identity even when the
# name field can't discriminate at all.
_DETERMINISTIC_ID_HINTS = (
    "website",
    "domain",
    "url",
    "homepage",
    "ticker",
    "stock_symbol",
    "registration_number",
    "tax_id",
)

# The subset of the above that holds a URL/hostname rather than a bare code —
# these need registrable-domain extraction before comparison, or
# "https://www.boeing.com/careers" and "boeing.com" never agree.
_DOMAIN_FIELD_HINTS = ("website", "domain", "url", "homepage")

# Canonical comparison fields — the projection every record is mapped to
# before comparison (BUG B / C0a). Keys are canonical names; values are
# tuples of hint substrings that map an origin's raw field to this slot.
# C0a: canonical comparison projection (Phase 0, Bug B)
# Maps origin-specific field names to canonical slots for cross-origin comparison
# IMPORTANT: Order matters! More specific patterns must come before less specific ones.
_CANONICAL_FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "domain": ("website", "domain", "url", "homepage"),
    "legal_name": ("legal_name", "legalname", "registered_name"),
    "name": ("name", "title", "full_name", "company", "organization", "label", "entityname"),
    "ticker": ("ticker", "stock_symbol"),
    "phone": ("phone", "telephone", "tel"),
    "email": ("email",),
    "description": ("description", "summary", "about", "bio"),
    "founded": ("founded", "founding", "established", "incorporated"),
    "address": ("address", "street", "location", "city", "country"),
    "employee_count": ("employee", "headcount", "staff", "size"),
    "revenue": ("revenue", "sales", "turnover"),
}

# Fields that look like identity keys but are origin-local surrogates
# (BUG B / C0b). A field ~100% distinct within each origin and ~0% value
# overlap across origins is a surrogate key — it must never be scored as
# ``disagree``, because that actively punishes every true cross-origin match.
_SURROGATE_KEY_HINTS = ("id", "_id", "uuid", "guid", "pk", "rowid", "recordid")

# At most this many review-band pairs are LLM-judged per entity type (those
# nearest the band's middle, the most uncertain, first: P8); the rest keep
# their heuristic score and stay in the review band. A cost budget, not a
# property of the data.
_MAX_LLM_PAIRS = 200

# Concurrent LLM judgment calls (one pair per call: P8).
_LLM_CONCURRENCY = 4

# Below this many candidate pairs, m/u probabilities cannot be estimated
# meaningfully and the priors dominate the score — so the heuristic is used
# instead. Small merges are unaffected by this; large ones get the model.
_MIN_PAIRS_FOR_MODEL = 30

# Most uncertain pairs shown at the matches gate. Enough to review, not so
# many that the payload or the reviewer is overwhelmed.
_MAX_PENDING_CANDIDATES = 50

# An agreeing value held by more than this share of records identifies
# nothing, so it cannot be the sole justification for an auto-merge.
_MAX_DISCRIMINATING_FREQUENCY = 0.05

# A connected component above this size makes `_partition_component`'s O(n^2)
# conflict check and cohesion computation expensive enough to be worth
# knowing about (BUG D.1 profiling). This is a visibility threshold, not a
# behaviour change: components above it are still processed exactly as
# before, just logged so a real run's component-size distribution is
# measurable before any algorithmic change is chosen (see
# docs/verification/CLUSTERING_PROFILE.md and docs/diagnostics/PERFORMANCE_BOTTLENECKS.md —
# "choose no fix before you have a profile").
_LARGE_COMPONENT_THRESHOLD = 200


def _normalize(value: Any) -> str:
    """Normalize a value for comparison."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = s.replace(".", "").replace("-", "").replace(" ", "_")
    return s


def _thresholds_for(rules: dict[str, Any] | None) -> tuple[float, float]:
    """The (auto_match, auto_exclude) bar for one entity.

    An explicit ``auto_match`` wins over ``strictness``; both are clamped
    above ``_AUTO_EXCLUDE`` so a mistyped rule can never make the auto-merge
    bar fall inside — or below — the band reserved for human review.
    """
    rules = rules or {}
    auto_exclude = float(rules.get("auto_exclude", _AUTO_EXCLUDE))
    strictness = str(rules.get("strictness", "normal")).lower()
    if "auto_match" in rules:
        auto_match = float(rules["auto_match"])
    elif strictness in _STRICTNESS:
        auto_match = _STRICTNESS[strictness]
    else:
        logger.warning(
            "Unknown strictness %r; using 'normal'. Valid: %s",
            strictness,
            sorted(_STRICTNESS),
        )
        auto_match = _AUTO_MATCH
    if auto_match <= auto_exclude:
        logger.warning(
            "auto_match (%.2f) must sit above auto_exclude (%.2f); falling back to the defaults.",
            auto_match,
            auto_exclude,
        )
        return _AUTO_MATCH, _AUTO_EXCLUDE
    return auto_match, auto_exclude


def _resolve_core(
    entity_type: str,
    records: list[dict[str, Any]],
    blocking_keys: list[str] | None,
    matching_rules: dict[str, Any] | None,
    subject: str | None,
    extra_candidate_pairs: list[tuple[str, str]] | None = None,
    relationship_index: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """The CPU-bound heart of resolve_entities_impl: blocking, model fit, and
    scoring — no LLM calls, no clustering. Synchronous and module-level, and
    every argument and return value is plain data (str/list/dict/tuple), so
    it can run either inline or dispatched to a ProcessPoolExecutor worker
    (Part 1.5 B.3) with no other change. ``relationship_index`` (B.5.2) is
    likewise plain data (dict of str -> set[str]) — safe across that same
    process boundary.

    ``subject`` (a plain string) crosses the process boundary rather than
    the closure ``_subject_stripped_normalize`` builds from it — closures
    are not picklable. The closure is rebuilt here, from the same subject,
    with identical behaviour; ``normalise`` never leaves this function.

    Returns a dict rather than a tuple so caller and callee stay in sync by
    name — a tuple position swap is a far easier bug to introduce silently
    than a missing dict key, which fails loudly.
    """
    rules = matching_rules or {}
    blocking_keys = blocking_keys or rules.get("blocking_keys") or None
    auto_match, auto_exclude = _thresholds_for(rules)
    unique_ids = {str(f) for f in (rules.get("unique_id_fields") or []) if f}
    deterministic_fields = _deterministic_id_fields(records, rules.get("deterministic_id_fields"))
    normalise = _subject_stripped_normalize(_subject_tokens(subject))
    # Operator-declared keys only: a hint-named field is judged by the data (P6).
    declared_keys = {str(f) for f in (rules.get("deterministic_id_fields") or []) if f}

    # Part 1.5 B.3: blocking/fit_model/scoring used to be 3 separate async
    # Langfuse spans (C.4); collapsed into this one synchronous function,
    # they can only ever be one span at the call site. Timed here instead,
    # so the phase breakdown is still visible as that span's attributes
    # rather than as separate child-span bars.
    t_blocking_start = time.monotonic()
    blocking_stats: dict[str, int] = {}
    candidate_pairs = _generate_candidate_pairs(
        records, blocking_keys, stats=blocking_stats, extra_pairs=extra_candidate_pairs
    )
    t_fit_start = time.monotonic()

    record_cache: RecordCache = {}
    corpus = _corpus_evidence(
        records, record_cache, [(r1, r2) for _a, _b, r1, r2 in candidate_pairs]
    )
    match_model, fitted_vectors = _fit_match_model(
        records, candidate_pairs, unique_ids, normalise, record_cache
    )
    t_scoring_start = time.monotonic()

    scored_pairs: list[tuple[str, str, float]] = []
    borderline: list[tuple[str, str, dict, dict, float]] = []
    # Phase 7.1: per-pair evidence, keyed by the same canonical (id1, id2)
    # order _build_clusters' edge_weight uses. Only built when a
    # probabilistic model actually fit (match_model is not None) — a
    # heuristic-only pair has no field-weight breakdown to give, only the
    # single confidence number it already carries. Plain dicts only (str/
    # float/None), so this stays picklable across the ProcessPoolExecutor
    # boundary like every other value this function returns.
    pair_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    possibly_related: list[list[str]] = []
    for pair_idx, (id1, id2, rec1, rec2) in enumerate(candidate_pairs):
        precomputed_vector = fitted_vectors[pair_idx] if fitted_vectors is not None else None
        conf = _score_pair(
            rec1,
            rec2,
            match_model,
            unique_ids,
            deterministic_fields,
            normalise,
            record_cache,
            precomputed_vector,
            relationship_index,
            corpus,
        )
        conf = _three_way(
            conf,
            rec1,
            rec2,
            corpus,
            auto_match,
            auto_exclude,
            record_cache,
            _has_conflicting_identity(
                {k: v for k, v in rec1.items() if not k.startswith("_")},
                {k: v for k, v in rec2.items() if not k.startswith("_")},
                unique_ids,
                declared_only=True,
            )
            or (bool(declared_keys) and _deterministic_conflict(rec1, rec2, declared_keys))
            # PRE-D2: two codes that disagree are no weaker than a declared key;
            # a shared price must not lift the pair back to Match.
            or _conflicting_codes(rec1, rec2, record_cache),
        )
        if conf < auto_exclude and _shares_identifying_value(rec1, rec2, corpus, record_cache):
            possibly_related.append([id1, id2] if id1 < id2 else [id2, id1])
        if auto_exclude <= conf < auto_match:
            borderline.append((id1, id2, rec1, rec2, conf))
        else:
            scored_pairs.append((id1, id2, conf))
            if match_model is not None and precomputed_vector is not None:
                matched, mismatched = _pair_field_evidence(
                    rec1, rec2, precomputed_vector, match_model, record_cache
                )
                key = (id1, id2) if id1 < id2 else (id2, id1)
                pair_evidence[key] = {
                    "a": id1,
                    "b": id2,
                    "confidence": round(conf, 4),
                    "method": "fellegi_sunter",
                    "matched_fields": matched,
                    "mismatched_fields": mismatched,
                }
        # No periodic yield here (unlike the pre-B.3 inline loop): this
        # function is either dispatched to a worker process with no event
        # loop to yield to, or (below threshold) run inline where its cost
        # is short enough not to need one. The yield's job — not blocking
        # the parent's event loop for minutes — is done by the process
        # boundary itself whenever the pool path is taken.

    t_end = time.monotonic()
    return {
        "auto_match": auto_match,
        "auto_exclude": auto_exclude,
        "candidate_pair_count": len(candidate_pairs),
        "blocking_stats": blocking_stats,
        "scored_pairs": scored_pairs,
        "borderline": borderline,
        "pair_evidence": pair_evidence,
        "model_fitted": match_model is not None,
        # P21g-3: JSON-safe form of what was actually fitted this run, so the
        # node can persist it as er_model ("the bars the last run used",
        # P21b). Plain data (str/float/dict), picklable across the process
        # pool. None when no model was fit — the online scorer then keeps the
        # previously persisted model rather than this run's heuristic.
        "match_model_params": match_model.to_params() if match_model is not None else None,
        "possibly_related": possibly_related,
        "blocking_seconds": t_fit_start - t_blocking_start,
        "fit_model_seconds": t_scoring_start - t_fit_start,
        "scoring_seconds": t_end - t_scoring_start,
    }


# Below this many records, a ProcessPoolExecutor's pickling + IPC overhead
# is not reliably recovered by the CPU time it saves — measured live
# (docs/verification/PROCESS_POOL_PROFILE.md): a 1,503-record entity
# (technology, ~20-25s of blocking+scoring) was worth dispatching once
# there was a second entity to run it alongside; the node caller
# additionally requires >=2 qualifying entities before it creates a pool
# at all (see nodes/resolve_entities.py), so a single large entity in an
# otherwise-small run still gets the zero-overhead inline path.
_PROCESS_POOL_MIN_RECORDS = 500


async def resolve_entities_impl(
    entity_type: str,
    records: list[dict[str, Any]],
    blocking_keys: list[str] | None = None,
    llm_config: dict[str, Any] | None = None,
    known_cluster_ids: dict[str, str] | None = None,
    matching_rules: dict[str, Any] | None = None,
    subject: str | None = None,
    process_pool: Any | None = None,
    connector: Any | None = None,
    matching_config: dict[str, Any] | None = None,
    relationship_index: dict[str, set[str]] | None = None,
    llm_semaphore: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Cluster matching records for an entity type (hybrid heuristic + LLM).

    Parameters
    ----------
    entity_type : str
        The entity type to resolve, e.g. "person".
    records : list[dict]
        List of records from all origins, each with ``_origin_id`` and
        ``_origin_id_row`` (or equivalent identifiers) fields.
    blocking_keys : list[str], optional
        Field names to block on. When omitted, the best name-ish field is
        auto-detected and pairs are only compared within blocks sharing a
        normalized first token of that field.
    llm_config : dict, optional
        LLM configuration. When present (and
        ``entity_resolution_enabled`` is not False), borderline pairs are
        judged by the LLM; when absent, scoring is heuristic-only.
    subject : str, optional
        The scoped merge's subject name (e.g. "Airbus"), when this run is
        restricted to one organisation. Every record then shares the subject
        token in its name field, which would otherwise make term-frequency
        weighting see nothing rare to reward (Phase 3.2) — see
        ``_subject_stripped_normalize``. ``None`` for an ordinary unscoped
        run, which is unaffected by any of this.
    process_pool : ProcessPoolExecutor, optional
        Part 1.5 B.3. When given, blocking/model-fit/scoring — the CPU-bound
        core, ``_resolve_core`` — runs in a worker process instead of
        blocking this coroutine's event loop, for entities with at least
        ``_PROCESS_POOL_MIN_RECORDS`` records (small entities always run
        inline; dispatch overhead would not be recovered). ``None`` (the
        default) always runs inline — every existing caller is unaffected
        unless it opts in. LLM adjudication and clustering always run here,
        in this process, regardless.
    connector : optional
        B.1.3. A live destination connector exposing
        ``execute_sql(sql, params=None)`` (e.g.
        ``PostgreSQLDestinationConnector``). When given, and
        ``matching_config`` enables it, pg_trgm trigram candidate pairs are
        generated and UNIONED with the existing blocking pass. ``None``
        (the default) skips this entirely — every existing caller is
        unaffected unless it opts in.
    matching_config : dict, optional
        ``{"trigram_blocking_enabled": bool, "trigram_blocking_min_records":
        int, "trigram_similarity_threshold": float}`` — the ``matching.*``
        section of ``config.yaml`` (``MatchingConfig``), passed as a plain
        dict the same way ``llm_config`` already is.
    relationship_index : dict, optional
        B.5.2. ``{company_native_id: {related_native_id, ...}}`` from
        ``build_relationship_index`` — built ONCE per entity-resolution
        call by the caller (from the rows of the junctions P4 found
        between company keys, P7) and threaded down as plain data, never
        rebuilt or cached at module scope. Feeds
        ``_compute_heuristic_confidence``'s bounded relational-agreement
        boost. ``None`` (the default) disables the signal entirely —
        every existing caller is unaffected unless it opts in.

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
    logger.debug(
        "ENTER resolve_entities_impl: entity_type=%s, records_count=%d", entity_type, len(records)
    )

    if not records:
        logger.debug("No records to resolve, returning empty clusters")
        return {
            "entity_type": entity_type,
            "clusters": [],
            "total_records": 0,
            "auto_matched": 0,
            "auto_excluded": 0,
            "llm_scored": 0,
        }

    # If only one record, it's its own cluster
    if len(records) == 1:
        rid = _record_id(records[0])
        logger.debug("Single record, creating singleton cluster: %s", rid)
        return {
            "entity_type": entity_type,
            "clusters": [
                {
                    "cluster_id": _assign_cluster_id(entity_type, [rid], known_cluster_ids),
                    "record_ids": [rid],
                    "match_confidence": 1.0,
                    "required_human_review": False,
                }
            ],
            "total_records": 1,
            "auto_matched": 1,
            "auto_excluded": 0,
            "llm_scored": 0,
        }

    logger.info(
        "Resolving entity type '%s': %d records",
        entity_type,
        len(records),
    )

    # Per-entity rules (Phase O.2). An unconfigured entity keeps exactly the
    # behaviour it had before: auto-detected blocking, hint-derived unique
    # identifiers, and the standard thresholds.
    rules = matching_rules or {}
    # Phase 7.4: an optional per-entity engine override. Splink replaces only
    # scoring+clustering-input (steps 1-3 below); resolve_cache, the LLM
    # borderline pass and _build_clusters are untouched. A clean, pure
    # insertion — the rest of this function is not rewritten. On any Splink
    # failure (untrainable model, no usable blocking rule, exception)
    # resolve_with_splink returns None and execution falls through to the
    # builtin path below exactly as if no engine were configured.
    engine = rules.get("engine", "builtin")
    if engine == "splink":
        from src.mcp_server.tools.splink_matcher import resolve_with_splink

        # P15: the band fields need Splink's scored pairs, which only reach
        # this side through the clustering call -- captured there.
        splink_pairs: list[tuple[str, str, float]] = []

        async def _capture_and_cluster(
            recs: list[dict[str, Any]], pairs: list[tuple[str, str, float]], **kwargs: Any
        ) -> tuple[list[dict[str, Any]], int, int]:
            splink_pairs.extend(pairs)
            return await _build_clusters(recs, pairs, **kwargs)

        splink_res = await resolve_with_splink(
            entity_type,
            records,
            matching_rules=rules,
            known_cluster_ids=known_cluster_ids,
            build_clusters_fn=_capture_and_cluster,
        )
        if splink_res is not None:
            # Splink scores are posterior probabilities: log-odds scale.
            return {**splink_res, **_band_fields(splink_pairs, *_thresholds_for(rules), True)}
    blocking_keys = blocking_keys or rules.get("blocking_keys") or None
    # auto_match/unique_ids here are for the log line below and the cache
    # key only — _resolve_core (Part 1.5 B.3) re-derives its own copies
    # (plus deterministic_fields/normalise, which nothing up here needs)
    # from the same `rules`, deterministically, whether it runs inline or
    # in a worker process.
    auto_match, _ = _thresholds_for(rules)
    unique_ids = {str(f) for f in (rules.get("unique_id_fields") or []) if f}
    if subject:
        logger.info(
            "Entity '%s': scoped merge for subject %r — term frequencies and "
            "discriminating-agreement checks use the subject-stripped "
            "residual, not the raw value.",
            entity_type,
            subject,
        )
    if rules:
        logger.info(
            "Entity '%s' uses saved matching rules: blocking=%s, "
            "unique_ids=%s, strictness=%s (auto_match=%.2f)",
            entity_type,
            blocking_keys or "auto",
            sorted(unique_ids) or "auto",
            rules.get("strictness", "normal"),
            auto_match,
        )

    # Part 1.5 D.7: a load_data failure used to force blocking, scoring and
    # clustering to redo from zero on the next attempt, even when the input
    # records were byte-identical to a run that already resolved them
    # correctly — this is what made the fix-and-retry loop during
    # development cost ~1-1.5h per attempt. Cache key covers everything
    # that can change the decision (records, rules, subject, blocking_keys,
    # whether LLM adjudication was requested); known_cluster_ids is
    # deliberately excluded — see resolve_cache's module docstring for why
    # that is still safe.
    llm_requested = bool(llm_config) and llm_config.get("entity_resolution_enabled", True)
    cache_key = resolve_cache.content_hash(
        entity_type, records, rules, subject, blocking_keys, llm_requested
    )
    cached = resolve_cache.get(cache_key)
    if cached is not None:
        logger.info(
            "Entity '%s': resolve_cache HIT (%d records) — skipping blocking/"
            "scoring/clustering entirely.",
            entity_type,
            len(records),
        )
        return resolve_cache.apply_known_cluster_ids(cached, entity_type, known_cluster_ids)

    # B.1.3: pg_trgm trigram blocking, UNIONED (never replacing) the
    # existing Python blocking pass below. Off by default; only attempted
    # with a live destination connector and enough records to be worth a
    # staging round-trip. A destination read must never be what stops a
    # merge — caught and logged, same ethos as
    # _inject_subject_destination_rows in the orchestrator node.
    extra_candidate_pairs: list[tuple[str, str]] | None = None
    mcfg = matching_config or {}
    if (
        connector is not None
        and mcfg.get("trigram_blocking_enabled")
        and len(records) >= int(mcfg.get("trigram_blocking_min_records", 1000))
    ):
        name_field = _best_name_field(records[0]) if records else None
        if name_field:
            try:
                extra_candidate_pairs = trigram_candidate_pairs(
                    connector,
                    records,
                    name_field,
                    threshold=float(mcfg.get("trigram_similarity_threshold", 0.6)),
                )
                logger.info(
                    "Entity '%s': pg_trgm trigram blocking proposed %d pair(s), "
                    "unioned with the existing blocking pass.",
                    entity_type,
                    len(extra_candidate_pairs),
                )
            except Exception as exc:
                logger.warning(
                    "Entity '%s': pg_trgm trigram blocking skipped (%s); "
                    "falling back to the existing blocking pass alone.",
                    entity_type,
                    exc,
                )

    # ── Steps 1+2: Blocking + probabilistic scoring ────────────────────
    # Part 1.5 B.3: this used to be inline here, as 3 separate async spans
    # (blocking / fit_model / scoring) yielding every 1,000 pairs so a
    # multi-minute scoring loop didn't stall the event loop other work
    # shares. Extracted into _resolve_core — synchronous, module-level,
    # picklable in and out — so a large entity's CPU-bound cost can be
    # dispatched to a worker process instead: real parallelism across
    # entity types (this was pure-Python GIL-bound work; asyncio.gather
    # alone, as Part 1.5 B.1 used for map_fields, achieves nothing here).
    # A small entity still runs it inline, in this coroutine, exactly as
    # before (dispatch overhead would not be recovered) — same function
    # either way, so behaviour cannot diverge between the two paths.
    clustering_stats: dict[str, Any] = {}
    dispatched_to_pool = process_pool is not None and len(records) >= _PROCESS_POOL_MIN_RECORDS
    async with span(
        f"resolve.{entity_type}.match",
        records=len(records),
        dispatched=("process_pool" if dispatched_to_pool else "inline"),
    ) as obs:
        if dispatched_to_pool:
            loop = asyncio.get_running_loop()
            core = await loop.run_in_executor(
                process_pool,
                _resolve_core,
                entity_type,
                records,
                blocking_keys,
                rules,
                subject,
                extra_candidate_pairs,
                relationship_index,
            )
        else:
            core = _resolve_core(
                entity_type,
                records,
                blocking_keys,
                rules,
                subject,
                extra_candidate_pairs,
                relationship_index,
            )
        obs.update(
            output={
                "blocking_stats": core["blocking_stats"],
                "candidate_pairs": core["candidate_pair_count"],
                "scored": len(core["scored_pairs"]),
                "borderline": len(core["borderline"]),
                "blocking_seconds": core["blocking_seconds"],
                "fit_model_seconds": core["fit_model_seconds"],
                "scoring_seconds": core["scoring_seconds"],
            }
        )
    auto_match, auto_exclude = core["auto_match"], core["auto_exclude"]
    blocking_stats = core["blocking_stats"]
    scored_pairs: list[tuple[str, str, float]] = list(core["scored_pairs"])
    borderline: list[tuple[str, str, dict, dict, float]] = list(core["borderline"])
    # Phase 7.1: per-pair evidence keyed by canonical (id1, id2) order,
    # covering every fellegi_sunter-scored pair so far. The LLM path below
    # adds its own entries (and can overwrite a pair that started borderline
    # once the LLM's judgment replaces the heuristic score) into this same
    # dict before it reaches _build_clusters.
    pair_evidence: dict[tuple[str, str], dict[str, Any]] = dict(core.get("pair_evidence") or {})
    logger.info(
        "Generated %d candidate pairs for entity '%s' (blocked from %d possible); "
        "scored=%d borderline=%d (dispatched=%s)",
        core["candidate_pair_count"],
        entity_type,
        len(records) * (len(records) - 1) // 2,
        len(scored_pairs),
        len(borderline),
        "process_pool" if dispatched_to_pool else "inline",
    )

    # ── Step 3: LLM judgment for the borderline band ──────────────────
    llm_scored = 0
    llm_enabled = bool(llm_config) and llm_config.get("entity_resolution_enabled", True)
    if borderline and llm_enabled:
        if len(borderline) > _MAX_LLM_PAIRS:
            # P8: judge the most uncertain pairs -- nearest the middle of the
            # band this run committed -- not the nearly-decided ones next to
            # auto-match; the rest keep their heuristic score (still review).
            middle = (auto_match + auto_exclude) / 2
            borderline.sort(key=lambda p: (abs(p[4] - middle), p[0], p[1]))
            overflow = borderline[_MAX_LLM_PAIRS:]
            borderline = borderline[:_MAX_LLM_PAIRS]
            logger.warning(
                "%d borderline pairs for entity '%s' exceed the LLM budget "
                "(%d); keeping heuristic scores for the %d pairs farthest "
                "from the band's middle",
                len(borderline) + len(overflow),
                entity_type,
                _MAX_LLM_PAIRS,
                len(overflow),
            )
            scored_pairs.extend((i1, i2, c) for i1, i2, _r1, _r2, c in overflow)
        logger.info(
            "Sending %d borderline pairs for entity '%s' to LLM judgment",
            len(borderline),
            entity_type,
        )
        llm_results = await _judge_pairs_with_llm(
            entity_type,
            borderline,
            llm_config,
            evidence_sink=pair_evidence,
            auto_match=auto_match,
            semaphore=llm_semaphore,
        )
        llm_scored = len(llm_results)
        scored_pairs.extend(llm_results)
    else:
        if borderline and not llm_enabled:
            logger.info(
                "LLM entity resolution disabled; keeping heuristic scores for %d borderline pairs",
                len(borderline),
            )
        scored_pairs.extend((i1, i2, c) for i1, i2, _r1, _r2, c in borderline)

    # ── Step 4: Connected Components (Union-Find) ─────────────────────
    async with span(f"resolve.{entity_type}.clustering", scored_pairs=len(scored_pairs)) as obs:
        clusters, auto_matched, auto_excluded = await _build_clusters(
            records,
            scored_pairs,
            entity_type,
            known_cluster_ids,
            auto_match=auto_match,
            auto_exclude=auto_exclude,
            stats=clustering_stats,
            pair_evidence=pair_evidence,
        )
        obs.update(
            output={
                "clusters": len(clusters),
                "auto_matched": auto_matched,
                "auto_excluded": auto_excluded,
                **clustering_stats,
            }
        )
    logger.debug(
        "Built %d clusters: matched=%d, excluded=%d", len(clusters), auto_matched, auto_excluded
    )

    logger.debug(
        "EXIT resolve_entities_impl: clusters=%d, matched=%d, excluded=%d, llm=%d",
        len(clusters),
        auto_matched,
        auto_excluded,
        llm_scored,
    )
    result = {
        "entity_type": entity_type,
        "clusters": clusters,
        "total_records": len(records),
        "auto_matched": auto_matched,
        "auto_excluded": auto_excluded,
        "llm_scored": llm_scored,
        # Pairs the comparison budget could not reach. A human reviewing the
        # final gate must be able to see "N pairs were never compared" rather
        # than have it hidden in a log line.
        "uncompared_pairs": blocking_stats.get("uncompared_pairs", 0),
        "candidate_pairs": blocking_stats.get("candidate_pairs", 0),
        # P2: pairs excluded by a computed property of the block collection
        # (not by a budget) — the measurable cost of meta-blocking. Each is
        # an UPPER BOUND (D9): a pair lost from one block can survive via
        # another, so these are per-shared-block counts, never exact.
        "block_memberships_filtered": blocking_stats.get("block_memberships_filtered", 0),
        "pairs_lost_by_block_filtering_ub": blocking_stats.get(
            "pairs_lost_by_block_filtering_ub", 0
        ),
        "pairs_pruned_by_purging_ub": blocking_stats.get("pairs_pruned_by_purging_ub", 0),
        "pairs_pruned_by_meta_blocking": blocking_stats.get("pairs_pruned_by_meta_blocking", 0),
        "blocks_purged": blocking_stats.get("blocks_purged", 0),
        # Clustering cost profile (BUG D.1) — largest component, how many
        # exceeded the visibility threshold, and total wall time through
        # _partition_component. Feeds docs/verification/CLUSTERING_PROFILE.md.
        "clustering_seconds": clustering_stats.get("clustering_seconds", 0.0),
        "largest_component_size": clustering_stats.get("largest_component_size", 0),
        "large_components": clustering_stats.get("large_components", 0),
        # Pairs that scored in the uncertain band and were therefore NOT
        # merged. Without surfacing these the human never learns they
        # existed: they form no multi-record cluster, so the matches gate
        # would have nothing to show and would silently auto-skip.
        "pending_merge_candidates": _pending_merge_candidates(
            scored_pairs, auto_match, auto_exclude
        ),
        **_band_fields(scored_pairs, auto_match, auto_exclude, core.get("model_fitted", False)),
        # P21g-3: the model params ride to the node in the result (the node,
        # not this tool, owns destination writes — and it is the one that
        # knows whether this is a dry run, which must not persist a model).
        "match_model_params": core.get("match_model_params"),
        # P6: Possibly Related -- typed edges, no human, never a merge.
        "possibly_related": _related_edges(core.get("possibly_related", []), clusters),
    }
    # Part 1.5 D.7: cached cluster_id values are meaningless (they were
    # derived from THIS run's known_cluster_ids) — apply_known_cluster_ids
    # re-derives them from scratch on every read, so caching them verbatim
    # here is harmless, not just convenient.
    resolve_cache.put(cache_key, entity_type, result)
    return result


def _related_edges(pairs: list[list[str]], clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Possibly Related edges (report P6 / §3.7) between records left in different clusters."""
    cluster_of = {r: c.get("cluster_id") for c in clusters for r in c.get("record_ids", [])}
    return [
        {"record_ids": [a, b], "type": "possibly_related"}
        for a, b in sorted({(a, b) for a, b in pairs})
        if cluster_of.get(a) != cluster_of.get(b)
    ]


def _pending_merge_candidates(
    scored_pairs: list[tuple[str, str, float]],
    auto_match: float = _AUTO_MATCH,
    auto_exclude: float = _AUTO_EXCLUDE,
) -> list[dict[str, Any]]:
    """Uncertain pairs the system declined to merge, strongest first."""
    undecided = [
        {"record_ids": [id1, id2], "confidence": round(conf, 3)}
        for id1, id2, conf in scored_pairs
        if auto_exclude <= conf < auto_match
    ]
    undecided.sort(key=lambda p: -p["confidence"])
    return undecided[:_MAX_PENDING_CANDIDATES]


def _band_fields(
    scored_pairs: list[tuple[str, str, float]],
    auto_match: float,
    auto_exclude: float,
    log_odds: bool,
) -> dict[str, Any]:
    """What the matches gate needs to let the operator commit the band (P15).

    ``band_summary``: the thresholds in force, the predicted pair count per
    band at those thresholds, and the distribution of EVERY scored pair --
    on the log2-odds scale when the score is a model posterior (log m/u plus
    the prior: RELAIS's plotted mixture), else on the raw score. Bin count by
    Sturges' rule, edges from the data's own range.
    ``retained_pairs``: every pair at or above the exclude bar, unrounded, so
    a decision or a committed threshold is resolved within the run
    (``rebuild_band_clusters``) without re-scoring.

    ponytail: pairs below the exclude bar are not retained, so a committed
    exclude bar LOWER than the run's needs a retry of the stage; the band's
    width is policy until the score is calibrated (report P15 Ceiling).
    Deferred: Possible Match as a persisted, revocable state with its own
    queue (report section 3.7), trigger Part 3.
    """
    # A NaN score carries no evidence: Exclude, never a crash or a lost pair.
    scores = [0 if math.isnan(conf) else conf for _a, _b, conf in scored_pairs]
    values = scores
    if log_odds:
        finite = [math.log2(p / (1 - p)) for p in scores if 0 < p < 1]
        lo, hi = (min(finite), max(finite)) if finite else (0, 0)
        values = [math.log2(p / (1 - p)) if 0 < p < 1 else (hi if p >= 1 else lo) for p in scores]
    counts: list[int] = []
    edges: list[float] = []
    if values:
        bins = math.ceil(math.log2(len(values))) + 1
        lo, hi = min(values), max(values)
        width = (hi - lo) / bins
        counts = [0] * bins
        for v in values:
            counts[min(int((v - lo) / width), bins - 1) if width else 0] += 1
        edges = [round(lo + i * width, 4) for i in range(bins + 1)]
    return {
        "band_summary": {
            "thresholds": {"auto_match": auto_match, "auto_exclude": auto_exclude},
            "predicted_counts": {
                "match": sum(1 for c in scores if c >= auto_match),
                "possible": sum(1 for c in scores if auto_exclude <= c < auto_match),
                "exclude": sum(1 for c in scores if c < auto_exclude),
            },
            "distribution": {
                "scale": "log2 odds (log m/u + prior)" if log_odds else "match score",
                "bin_edges": edges,
                "counts": counts,
            },
        },
        "retained_pairs": [[a, b, c] for a, b, c in scored_pairs if c >= auto_exclude],
    }


def rebuild_band_clusters(
    entity_type: str,
    clusters: list[dict[str, Any]],
    retained_pairs: list[list[Any]],
    accepted: set[frozenset[str]],
    rejected: set[frozenset[str]],
    auto_match: float,
    records_by_id: dict[str, dict[str, Any]],
    min_cohesion: float = _MIN_COHESION,
) -> list[dict[str, Any]]:
    """Re-derive one entity's clusters after the matches gate (P15).

    Edges are the retained scored pairs; a human-accepted band pair counts as
    certain. Edges at or over the (possibly committed) ``auto_match`` bar are
    joined strongest first, and a join is skipped when it would put a
    rejected pair in one cluster, or would join a cluster a human already
    decided (``reviewed_by_human``: kept, split, or formed by an earlier band
    decision) by anything but an accepted pair -- so a "keep apart" removes
    one link, never the Match links around it, and a split in the same
    feedback stays split. Components no human touched go through the same
    conflict repair and cohesion cut as ``_build_clusters``. A cluster whose
    membership is unchanged is returned as it was; a changed one was formed
    by the operator's decision, so it is not flagged for review again.

    ponytail: a changed cluster's id is derived from its new membership, not
    from a previous run's id (``known_cluster_ids`` are not in gate state).
    """
    edge_weight: dict[tuple[str, str], float] = {}
    for a, b, conf in retained_pairs:
        key = (a, b) if a < b else (b, a)
        edge_weight[key] = max(edge_weight.get(key, float(conf)), float(conf))
    for pair in accepted:
        a, b = sorted(pair)
        edge_weight[(a, b)] = 1  # a human-confirmed pair is certain
    for pair in rejected:
        a, b = sorted(pair)
        edge_weight.pop((a, b), None)

    all_ids = sorted({rid for c in clusters for rid in c.get("record_ids", [])})
    parent = {rid: rid for rid in all_ids}
    members_of = {rid: {rid} for rid in all_ids}
    human: set[str] = set()  # roots of components a human decided

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> str:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
            members_of[ra] |= members_of.pop(rb)
            if rb in human:
                human.discard(rb)
                human.add(ra)
        return ra

    for c in clusters:
        ids = [r for r in c.get("record_ids", []) if r in parent]
        if c.get("reviewed_by_human") and ids:
            for r in ids[1:]:
                union(ids[0], r)
            human.add(find(ids[0]))
    kept_apart = [tuple(p) for p in rejected if len(p) == 2]
    for (a, b), weight in sorted(edge_weight.items(), key=lambda e: (-e[1], e[0])):
        if weight < auto_match or a not in parent or b not in parent:
            continue
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        is_accepted = frozenset((a, b)) in accepted
        if (ra in human or rb in human) and not is_accepted:
            continue
        if any(
            (x in members_of[ra] and y in members_of[rb])
            or (y in members_of[ra] and x in members_of[rb])
            for x, y in kept_apart
        ):
            continue
        root = union(a, b)
        if is_accepted:
            human.add(root)

    old = {frozenset(c.get("record_ids", [])): c for c in clusters}
    # P20: components no human touched are split exactly as _build_clusters does.
    confident = {k: w for k, w in edge_weight.items() if w >= auto_match}
    kept_edges = _wnp_prune(confident)
    corpus = _corpus_evidence(list(records_by_id.values()))
    rebuilt: list[dict[str, Any]] = []
    for root in sorted(members_of):
        members = sorted(members_of[root])
        parts = (
            _split_component(members, kept_edges, confident, records_by_id, corpus)[0]
            if len(members) > 1 and root not in human
            else [members]
        )
        for part in parts:
            part_ids = sorted(part)
            kept = old.get(frozenset(part_ids))
            if kept is not None:
                rebuilt.append(kept)
                continue
            cluster: dict[str, Any] = {
                "cluster_id": _assign_cluster_id(entity_type, part_ids, None),
                "record_ids": part_ids,
                "match_confidence": round(_min_internal_confidence(part_ids, edge_weight), 3),
                "required_human_review": False,
            }
            if len(part_ids) > 1:
                cluster["reviewed_by_human"] = True
            rebuilt.append(cluster)
    return rebuilt


# ── Blocking ─────────────────────────────────────────────────────────


def _best_name_field(rec: dict[str, Any]) -> str | None:
    """The record's own best name-ish string field, by hint rank.

    Per-record (not global) because different origins name the same concept
    differently (pg ``company_name`` vs ch ``org_title``) — a single global
    blocking field would drop every record from the other origin into the
    empty-key fallback block and cross-origin pairs would never be compared.
    """
    best: str | None = None
    best_rank = len(_NAME_FIELD_HINTS)
    for k, v in rec.items():
        if k.startswith("_") or not isinstance(v, str) or not v.strip():
            continue
        kl = k.lower()
        for i, hint in enumerate(_NAME_FIELD_HINTS):
            if hint in kl:
                if i < best_rank:
                    best_rank, best = i, k
                break
    return best


# Letters and digits of any script ("müller", "株式会社" stay whole tokens).
_WORD_RE = re.compile(r"[^\W_]+")


def _token_keys(rec: dict[str, Any], fields: set[str] | None = None) -> set[str]:
    """Token blocking (P5): every alphanumeric token of every data value.

    Schema-agnostic by construction -- attribute names are never read, so a
    new origin with unfamiliar column names blocks exactly like a known one
    ("one block for every token in the attribute values" [JED]). Engine
    provenance fields (``_``-prefixed) are skipped; ``fields``, when given,
    is the operator's explicit ``blocking_keys`` and limits the values used.
    The blocks this produces are deliberately unselective; purging and
    meta-blocking in ``_generate_candidate_pairs`` are what make it usable.
    ponytail: a typo inside every shared token (Smith/Smyth with nothing else
    in common) no longer co-blocks -- the old phonetic key did; token
    blocking trades that for schema independence (report P5).
    """
    keys: set[str] = set()
    for key, value in rec.items():
        if key.startswith("_") or value is None or (fields and key not in fields):
            continue
        keys.update(_WORD_RE.findall(str(value).casefold()))
    return keys


def trigram_candidate_pairs(
    connector: Any,
    records: list[dict[str, Any]],
    name_field: str,
    *,
    staging_table: str = "merge_engine_trigram_staging",
    threshold: float = 0.6,
) -> list[tuple[str, str]]:
    """pg_trgm GIN-indexed candidate-pair blocking (audit §13 #13, B.1.3).

    Stages ``(rec_id, name)`` into a session-scoped TEMP table on the
    destination, GIN-indexes it with ``gin_trgm_ops``, and returns pairs
    whose names are similar under pg_trgm's ``%`` operator — an indexed SQL
    alternative to this module's O(n^2) in-Python blocking pass for large
    entities. The caller UNIONS the result with the existing blocking pairs
    (``_generate_candidate_pairs``'s ``extra_pairs``) — never a
    replacement, so recall on non-Postgres origins can't regress.

    ``connector`` is duck-typed: anything exposing
    ``execute_sql(sql, params=None) -> list[tuple]`` (e.g.
    ``PostgreSQLDestinationConnector``) works, so this is unit-testable
    with a fake connector that just records the SQL it was asked to run. A
    TEMP table is connection-scoped by Postgres itself — no manual
    staging-table CREATE/TRUNCATE/DROP lifecycle to get wrong; the session
    owning ``connector`` cleans it up when it ends.
    """
    rows = [(_record_id(r), r.get(name_field)) for r in records]
    rows = [(rid, name) for rid, name in rows if name]
    if len(rows) < 2:
        return []

    connector.execute_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    connector.execute_sql(
        f"CREATE TEMP TABLE IF NOT EXISTS {staging_table} (rec_id text PRIMARY KEY, name text)"
    )
    connector.execute_sql(f"TRUNCATE {staging_table}")
    placeholders = ", ".join(["(%s, %s)"] * len(rows))
    params = tuple(v for pair in rows for v in pair)
    connector.execute_sql(
        f"INSERT INTO {staging_table} (rec_id, name) VALUES {placeholders}", params
    )
    connector.execute_sql(
        f"CREATE INDEX IF NOT EXISTS idx_{staging_table}_name_trgm "
        f"ON {staging_table} USING gin (name gin_trgm_ops)"
    )
    connector.execute_sql("SELECT set_limit(%s)", (threshold,))
    result = connector.execute_sql(
        f"SELECT a.rec_id AS l, b.rec_id AS r FROM {staging_table} a "
        f"JOIN {staging_table} b ON a.name % b.name AND a.rec_id < b.rec_id"
    )
    return [(str(left), str(right)) for left, right in result]


def _generate_candidate_pairs(
    records: list[dict[str, Any]],
    blocking_keys: list[str] | None = None,
    stats: dict[str, int] | None = None,
    extra_pairs: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str, dict, dict]]:
    """Generate candidate record pairs: block building, block cleaning, comparison cleaning.

    P2 (TECHNICAL_REPORT_2026-09-26.md): a pair is excluded only because a
    property computed from the block collection says so, never because a
    counter ran out.

    1. **Block building** — token blocking (P5): every token of every data
       value is a key, attribute names never read (``_token_keys``); records with
       no usable key share a fallback block, so missing data never silently
       excludes a record. Blocks with an identical member set (one block
       reached under two keys) are kept once.
    2. **Block filtering** — each record keeps only the blocks whose token IDF
       is at least the mean IDF of its own tokens (geometric mean of sizes;
       chosen over the arithmetic mean by measurement — the arithmetic mean
       let a 10k company corpus exceed the guard); a block links two records
       when either keeps it.
       **Block purging** — largest blocks first, a block is purged while it
       holds more comparisons than every other surviving block together, or its token
       is held by most records (IDF < 1 bit), *and* each of its members still
       sits in another surviving block. Nothing to tune.
       Both work per record, not per pair: a true pair whose ONLY shared
       block is dropped is lost (pinned by a test; counted in the stats).
    3. **Meta-blocking (WNP, ARCS weights, node-centric)** — each candidate
       edge weighs ``sum(1 / comparisons(b))`` over the blocks it shares; an
       edge survives when it reaches the mean weight of at least one
       endpoint's edges. Memory holds one neighbourhood at a time.

    CEP/CNP (the report's other comparison-cleaning options) are not used:
    WNP is one of the listed alternatives and already prunes per record
    against a local mean.

    ponytail: meta-blocking is lossy by design — a recall/cost trade, measured
    here as counts (``block_memberships_filtered`` — memberships, not pairs;
    ``pairs_lost_by_block_filtering_ub`` and ``pairs_pruned_by_purging_ub`` —
    upper bounds, a pair counted once per shared block;
    ``pairs_pruned_by_meta_blocking``); pair completeness needs ground truth
    and is computed by the eval harness, never guessed here (P19). A name in a
    script without spaces (CJK) is one token, so a one-character variant does
    not co-block (upgrade: character n-gram keys). Deferred alternative:
    progressive (pay-as-you-go) resolution, if a run is ever time-boxed
    rather than quality-boxed.

    ``_MAX_CANDIDATE_PAIRS`` is an out-of-memory guard only: when more pairs
    than that survive meta-blocking, the call RAISES
    ``CandidatePairBudgetExceeded`` instead of truncating.

    ``extra_pairs`` (B.1.3): additional ``(record_id, record_id)`` pairs from
    an external source (e.g. ``trigram_candidate_pairs``) — unioned into the
    result after pruning, deduped, never a replacement. A pair naming an id
    not present in ``records`` is silently ignored.

    ``stats``, when given, is filled with this call's blocking outcome. It
    belongs to the caller: these counts used to live in a module-level dict,
    so two concurrent entity resolutions each reported the other's numbers
    (Phase 0 Sec 0.8). ``uncompared_pairs`` stays for the run report and is
    always 0: no pair is dropped by a budget any more.
    """
    if stats is None:
        stats = {}
    explicit = {f for f in (blocking_keys or []) if f}

    # 1. Block building — one entry per record id, so a record reached twice
    # (same id) never pairs with itself.
    records_by_id: dict[str, dict[str, Any]] = {}
    raw_blocks: dict[str, set[str]] = {}
    for rec in records:
        rid = _record_id(rec)
        records_by_id.setdefault(rid, rec)
        # No usable key at all — a shared fallback block.
        for key in _token_keys(rec, explicit) or {"__nokey__"}:
            raw_blocks.setdefault(key, set()).add(rid)

    # The same member set under two keys (e.g. ``tok:`` and ``snd:`` both
    # holding every record) is one block: counting it twice inflated both the
    # weights and the old overflow estimate (5.6x on the Phase 0 fixture).
    by_members: dict[frozenset[str], str] = {}
    for key in sorted(raw_blocks):
        member_set = frozenset(raw_blocks[key])
        if len(member_set) >= 2:
            by_members.setdefault(member_set, key)
    blocks = [(key, sorted(member_set)) for member_set, key in by_members.items()]

    # 2a. Block filtering, local mean: each record keeps only the blocks whose
    # token IDF log2(N/|b|) is at least the mean IDF of its own tokens, i.e.
    # blocks no larger than the GEOMETRIC mean of its block sizes. A record's
    # largest blocks are its least informative tokens ("com", "street"); its
    # smallest carry the identity. The literature's fixed ratio r is replaced
    # by each record's own mean (the WNP idea one stage earlier), so nothing
    # is tuned. The arithmetic mean was measured too lenient: a 10k-record
    # company corpus still exceeded the out-of-memory guard.
    sizes_of: dict[str, list[int]] = {}
    for _key, members in blocks:
        for rid in members:
            sizes_of.setdefault(rid, []).append(len(members))
    # Compared as count * log|b| <= sum(log sizes), never via exp(): a record
    # whose blocks all tie must keep them all, and exp(log(800)) < 800.
    log_sum = {rid: (math.fsum(map(math.log, v)), len(v)) for rid, v in sizes_of.items()}

    def _keeps(rid: str, size: int) -> bool:
        total, count = log_sum[rid]
        return count * math.log(size) <= total

    # A block links two records when EITHER of them keeps it (the union rule
    # WNP also uses): filtering per record alone dropped a typo'd pair whose
    # one side kept the first-name block and the other only its rarer
    # surname block. So a block keeps all its members and remembers its
    # keepers; a non-keeper is linked only to the keepers.
    keepers_of: dict[str, frozenset[str]] = {}
    filtered_out = 0
    filter_pairs_lost_ub = 0  # D9: upper bound, see the stats block below
    for key, members in blocks:
        keepers = frozenset(r for r in members if _keeps(r, len(members)))
        filtered_out += len(members) - len(keepers)
        # sum of C(size,2) before minus C(keepers,2) after — cheap because
        # the sizes are already in hand; no enumeration of the pair space.
        filter_pairs_lost_ub += (
            len(members) * (len(members) - 1) - len(keepers) * (len(keepers) - 1)
        ) // 2
        keepers_of[key] = keepers
    blocks = [(key, members) for key, members in blocks if keepers_of[key]]

    # 2b. Block purging.
    def _comparisons(members: list[str]) -> int:
        return len(members) * (len(members) - 1) // 2

    blocks.sort(key=lambda kb: (-_comparisons(kb[1]), kb[0]))
    # Coverage counts only the blocks a record KEEPS: a record merely linked
    # to another block's keepers is not covered by it.
    membership: dict[str, int] = {}
    for key, _members in blocks:
        for rid in keepers_of[key]:
            membership[rid] = membership.get(rid, 0) + 1
    surviving_total = sum(_comparisons(m) for _k, m in blocks)
    purged: set[str] = set()
    purged_comparisons = 0
    population = len(records_by_id)
    for key, members in blocks:
        size = _comparisons(members)
        # Purge when the block holds more comparisons than every other
        # surviving block together, or when its token is held by most records
        # (IDF log2(N/df) < 1 bit: it cannot tell a record from the majority).
        # Blocks arrive largest first, so once neither holds, none will.
        if 2 * size <= surviving_total and 2 * len(members) <= population:
            break
        if all(membership[rid] > 1 for rid in keepers_of[key]):
            purged.add(key)
            purged_comparisons += size
            surviving_total -= size
            for rid in keepers_of[key]:
                membership[rid] -= 1
    kept = sorted(
        ((key, members) for key, members in blocks if key not in purged),
        key=lambda kb: (len(kb[1]), kb[0]),
    )
    if purged:
        logger.info(
            "Blocking: purged %d dominant block(s) (%d comparisons); every member "
            "remains in another block.",
            len(purged),
            purged_comparisons,
        )

    # 3. Meta-blocking: ARCS edge weights, then WNP against each node's mean,
    # node-centric (JedAI style): a record's neighbour weights are built from
    # its own blocks, used and dropped, so memory holds one neighbourhood at a
    # time and only the RETAINED pairs -- never every candidate edge. Pass 1
    # learns each node's mean edge weight, pass 2 emits the edges that reach
    # it at either end. The out-of-memory guard counts retained pairs.
    inverse = [1.0 / _comparisons(members) for _key, members in kept]
    blocks_of: dict[str, list[int]] = {}
    for index, (_key, members) in enumerate(kept):
        for rid in members:
            blocks_of.setdefault(rid, []).append(index)

    keepers_at = [keepers_of[key] for key, _members in kept]

    def _neighbours(node: str) -> dict[str, float]:
        # Blocks are visited in one global order, so an edge's weight is the
        # same float from both ends; "either end keeps the block" is
        # symmetric too.
        weights: dict[str, float] = {}
        for index in blocks_of[node]:
            w = inverse[index]
            keepers = keepers_at[index]
            for other in kept[index][1] if node in keepers else keepers:
                if other != node:
                    weights[other] = weights.get(other, 0.0) + w
        return weights

    # Per node, the distinct weights and their counts: equal weights then sum
    # as one product, so a node whose edges all tie keeps all of them (a
    # running float sum would drift by an ulp and could prune every edge).
    node_total: dict[str, tuple[float, int]] = {}
    for node in blocks_of:
        counts: dict[float, int] = {}
        for w in _neighbours(node).values():
            counts[w] = counts.get(w, 0) + 1
        node_total[node] = (math.fsum(w * c for w, c in counts.items()), sum(counts.values()))

    def _passes(node: str, w: float) -> bool:
        total, degree = node_total[node]
        return w * degree >= total

    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str, dict, dict]] = []
    # External (e.g. pg_trgm) pairs first: evidence from outside the block
    # collection, never pruned by it.
    for raw_id1, raw_id2 in extra_pairs or ():
        rec1 = records_by_id.get(raw_id1)
        rec2 = records_by_id.get(raw_id2)
        if raw_id1 == raw_id2 or rec1 is None or rec2 is None:
            continue
        edge = _ordered(raw_id1, raw_id2)
        if edge not in seen_pairs:
            seen_pairs.add(edge)
            pairs.append((raw_id1, raw_id2, rec1, rec2))
    meta_pruned = 0
    for node in sorted(blocks_of):
        for other, w in sorted(_neighbours(node).items()):
            if other < node or (node, other) in seen_pairs:
                continue
            if _passes(node, w) or _passes(other, w):
                pairs.append((node, other, records_by_id[node], records_by_id[other]))
                if len(pairs) > _MAX_CANDIDATE_PAIRS:
                    raise CandidatePairBudgetExceeded(
                        f"Blocking retained more than {_MAX_CANDIDATE_PAIRS} distinct "
                        f"candidate pairs over {len(records)} records. Refusing to "
                        "truncate silently; tighten the blocking keys or raise the guard."
                    )
            else:
                meta_pruned += 1

    stats["uncompared_pairs"] = 0
    stats["candidate_pairs"] = len(pairs)
    stats["blocks_used"] = len(kept)
    stats["blocks_purged"] = len(purged)
    # D9: every "pairs" count below is an UPPER BOUND on distinct pairs lost
    # (a pair lost from one block may survive through another); pair
    # completeness against ground truth is the eval harness's job (P19).
    # Units stated in the name: memberships, not pairs.
    stats["block_memberships_filtered"] = filtered_out
    # sum C(size,2) before minus C(keepers,2) after, per filtered block —
    # the cheap bound on pairs lost to local-mean block filtering (D9): a
    # pair is counted once per shared block, never enumerated.
    stats["pairs_lost_by_block_filtering_ub"] = filter_pairs_lost_ub
    stats["pairs_pruned_by_purging_ub"] = purged_comparisons
    stats["pairs_pruned_by_meta_blocking"] = meta_pruned
    logger.debug(
        "_generate_candidate_pairs: %d pairs (%d pruned by meta-blocking, %d block "
        "comparisons purged) from %d blocks",
        len(pairs),
        meta_pruned,
        purged_comparisons,
        len(kept),
    )
    return pairs


# ── Heuristic scoring ────────────────────────────────────────────────


# Comparing candidate pairs is dominated by difflib — profiling a 1500-record
# merge put 90% of the scoring time (33s of 37s) inside _string_similarity,
# almost all of it in SequenceMatcher. The values being compared repeat
# constantly, though: pairs are candidates precisely *because* they share
# names, tokens and email locals, so the same two strings get compared over
# and over. Memoising these pure functions changes no result and removes most
# of that work. Bounded so a long-lived process cannot grow without limit.
#
# Keys are order-normalised because the comparisons are symmetric — difflib's
# autojunk heuristic, the one thing that would break that, only engages on
# sequences of 200+ characters, and these are names and emails.
_SIMILARITY_CACHE_SIZE = 500_000


def _ordered(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _token_ratio(t1: str, t2: str) -> float:
    """Symmetric, cached character similarity of two tokens (P14)."""
    return 1.0 if t1 == t2 else _seq_ratio(*_ordered(t1, t2))


@lru_cache(maxsize=_SIMILARITY_CACHE_SIZE)
def _seq_ratio(a: str, b: str) -> float:
    """difflib's sequence ratio, computed once per distinct pair."""
    return difflib.SequenceMatcher(None, a, b).ratio()


@lru_cache(maxsize=_SIMILARITY_CACHE_SIZE)
def _tokenize(s: str) -> tuple[str, ...]:
    """Distinct alphanumeric tokens, in order of first appearance."""
    return tuple(dict.fromkeys(re.findall(r"[a-z0-9]+", s)))


@lru_cache(maxsize=_SIMILARITY_CACHE_SIZE)
def _tokens_match(t1: str, t2: str) -> bool:
    """Fuzzy token equality: exact, abbreviation-prefix, or near-typo."""
    if t1 == t2:
        return True
    # Abbreviation prefixes: corp/corporation, inc/incorporated, ...
    if len(t1) >= 3 and len(t2) >= 3 and (t1.startswith(t2) or t2.startswith(t1)):
        return True
    # Part 1.5 D.5: this is the single hottest call in scoring — the O(t1*t2)
    # token loop in _string_similarity_cached drives ~75% of all _seq_ratio
    # calls (126k of 167k on the technology profile). difflib's ratio is
    # exactly 2*M/T with M <= min(len(t1), len(t2)) and T = len(t1)+len(t2),
    # so when that ceiling is already below the 0.85 bar the answer is
    # provably False and difflib cannot change it. Two tokens of very
    # different length (the common case for unrelated words) are rejected
    # here without ever building a matching-block table. Exact, not an
    # approximation: same result, less work.
    if 2 * min(len(t1), len(t2)) < 0.85 * (len(t1) + len(t2)):
        return False
    return _seq_ratio(*_ordered(t1, t2)) >= 0.85


def _string_similarity(s1: str, s2: str) -> float:
    """Fuzzy token-set Jaccard combined with difflib sequence similarity.

    Tokens match fuzzily (abbreviation prefixes, near-typos), and the
    stronger of the two signals wins: token overlap catches word-order /
    extra-word variants, sequence ratio catches spelling variants. Any
    inflation this causes lands pairs in the borderline band — where the
    LLM or a human reviews them — rather than silently auto-matching.
    """
    if s1 == s2:
        return 1.0
    return _string_similarity_cached(*_ordered(s1, s2))


@lru_cache(maxsize=_SIMILARITY_CACHE_SIZE)
def _string_similarity_cached(s1: str, s2: str) -> float:
    # Part 1.5 D.5: profiling after A.1-A.7 (docs/verification/SCORING_PROFILE.md)
    # found difflib's get_matching_blocks() — not the token loop — is now the
    # dominant cost here (~31s of technology's 68s run). Two fixes, both
    # provably exact (zero output change, not an approximation):
    tokens1 = _tokenize(s1)
    tokens2 = _tokenize(s2)
    jaccard = 0.0
    if tokens1 and tokens2:
        matched = 0
        # (1) `unused.remove(u)` was an O(n) linear scan-and-shift per match,
        # on top of the O(t1*t2) double loop. A boolean "used" array makes
        # marking-as-matched O(1); the match order and result are identical.
        used = [False] * len(tokens2)
        for t in tokens1:
            for idx in range(len(tokens2)):
                if used[idx]:
                    continue
                if _tokens_match(*_ordered(t, tokens2[idx])):
                    matched += 1
                    used[idx] = True
                    break
        denom = len(tokens1) + len(tokens2) - matched
        jaccard = matched / denom if denom else 0.0
    # (2) difflib's ratio() is exactly bounded: ratio = 2*M/T where
    # T = len(a)+len(b) and M <= min(len(a), len(b)) (Ratcliff/Obershelp,
    # difflib's own documented property). When the cheap jaccard score
    # already meets that ceiling, seq can mathematically never exceed it, so
    # max(jaccard, seq) == jaccard exactly — skip the expensive
    # get_matching_blocks() call rather than compute a value that cannot
    # change the result.
    total_len = len(s1) + len(s2)
    if total_len and jaccard >= 2 * min(len(s1), len(s2)) / total_len:
        return jaccard
    # A tighter character-multiset ceiling (difflib's own quick_ratio bound)
    # was tried here and MEASURED SLOWER (19.5s -> 20.5s on the technology
    # profile): building the character counts costs more than the
    # get_matching_blocks calls it avoids at these string lengths. Not kept.
    seq = _seq_ratio(s1, s2)
    return max(jaccard, seq)


def _fit_match_model(
    records: list[dict[str, Any]],
    candidate_pairs: list[tuple[str, str, dict, dict]],
    extra_unique_ids: set[str] | None = None,
    normalise: Any = _normalize,
    record_cache: RecordCache | None = None,
) -> tuple[Any, list[dict[str, tuple[str, str | None]]] | None]:
    """Estimate the probabilistic match model for this entity type.

    Unsupervised: term frequencies come from all records, m/u probabilities
    from an EM pass over the candidate pairs. Returns ``(None, None)`` if the
    model can't be built, in which case scoring falls back to the heuristic
    alone.

    ``normalise`` is the value-normalisation function used both to build the
    term-frequency table and to render each pair's comparison vector — they
    must be the SAME function or a frequency lookup silently misses (see
    ``_subject_stripped_normalize`` for the scoped-merge case).

    Returns ``(model, vectors)``: Part 1.5 A.2. ``vectors`` is the same
    per-pair comparison vector list, in ``candidate_pairs`` order, that this
    function builds internally to fit the model. Previously it was built
    here, handed to ``estimate_model``, and discarded — then rebuilt
    identically inside the scoring loop's call to ``_score_pair``. The caller
    now reuses it positionally instead of recomputing.
    """
    if len(candidate_pairs) < _MIN_PAIRS_FOR_MODEL:
        # EM has nothing to learn from a handful of pairs: the priors
        # dominate and a genuine match can score near zero. Measured on a
        # single pair, the model returned 0.002 where the heuristic
        # correctly said 0.82. Below this size the heuristic is simply the
        # better estimator.
        logger.debug(
            "Only %d candidate pair(s) — too few to estimate a match model; "
            "using heuristic scoring.",
            len(candidate_pairs),
        )
        return None, None

    try:
        from src.mcp_server.tools.fellegi_sunter import (
            SAMPLE_PAIRS,
            build_term_frequencies,
            build_token_document_frequencies,
            estimate_model,
            sample_random_pairs,
        )

        # P14: token document frequencies over the canonical projection, the
        # same field names the comparison vectors carry.
        token_df = build_token_document_frequencies(
            [_cached_projection(r, record_cache)[0] for r in records]
        )
        vectors = [
            _build_comparison_vector(rec1, rec2, normalise, record_cache, token_df)
            for _id1, _id2, rec1, rec2 in candidate_pairs
        ]
        # P13: u from random pairs of the UNBLOCKED cross product (the blocked
        # candidates are look-alikes, so EM's u there is far too high). Sorted
        # by record id so the sample does not depend on extraction order; the
        # seed is fixed so two runs over the same records draw the same pairs.
        population = sorted(records, key=_record_id)
        n = len(population)
        sampled = [
            _build_comparison_vector(
                population[i], population[j], normalise, record_cache, token_df
            )
            for i, j in sample_random_pairs(n, SAMPLE_PAIRS, seed=0)
        ]
        model = estimate_model(
            vectors,
            build_term_frequencies(records, normalise),
            unique_id_fields=_unique_id_fields(records) | (extra_unique_ids or set()),
            sampled_vectors=sampled or None,
            population_pairs=n * (n - 1) // 2 if sampled else None,
        )
        model.token_df = token_df
        logger.debug(
            "Match model fitted over %d candidate pairs (match_prior=%.3f)",
            len(vectors),
            model.match_prior,
        )
        return model, vectors
    except Exception as exc:
        logger.warning(
            "Could not fit the probabilistic match model (%s); scoring falls "
            "back to the heuristic.",
            exc,
        )
        return None, None


def _score_pair(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    model: Any,
    extra_unique_ids: set[str] | None = None,
    deterministic_fields: set[str] | None = None,
    normalise: Any = _normalize,
    record_cache: RecordCache | None = None,
    precomputed_vector: dict[str, tuple[str, str | None]] | None = None,
    relationship_index: dict[str, set[str]] | None = None,
    corpus: CorpusEvidence | None = None,
) -> float:
    """Confidence that two records are the same entity.

    The probabilistic model decides, but the heuristic guards still apply:
    contradicting unique identifiers and partial-name-only evidence can never
    reach the auto-merge threshold, however the model scores them. A shared
    deterministic identity key (website/domain/ticker/...) settles the pair
    directly, ahead of every other signal, unless it conflicts with a
    contradicting unique identifier.

    ``record_cache`` (Part 1.5 A.1/A.5) memoizes per-record projections.
    ``precomputed_vector`` (Part 1.5 A.2), when given, is this exact pair's
    comparison vector as already built by ``_fit_match_model`` — reused here
    instead of rebuilt, since it is a pure function of ``(rec1, rec2,
    normalise)`` and both call sites use the same ``normalise``.
    ``relationship_index`` (B.5.2) feeds ``_compute_heuristic_confidence``'s
    bounded relational-agreement boost — see there for the guard.
    """
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}
    # P6: with the corpus in hand, exclusivity is measured (and a generic value
    # conflicts with nothing); an operator-declared unique id still counts.
    suppressed = (
        _conflicting_exclusives(rec1, rec2, corpus, record_cache) if corpus is not None else set()
    )
    conflicting = (
        bool(suppressed)
        or _has_conflicting_identity(
            fields1, fields2, extra_unique_ids, declared_only=corpus is not None
        )
        or (
            bool(deterministic_fields) and _deterministic_conflict(rec1, rec2, deterministic_fields)
        )
    )

    if (
        deterministic_fields
        and not conflicting
        and _deterministic_match(rec1, rec2, deterministic_fields)
    ):
        return 1.0
    # PRE-D2: a code mismatch is a name-shape rule, weaker than a shared key.
    conflicting = conflicting or _conflicting_codes(rec1, rec2, record_cache)

    heuristic = _compute_heuristic_confidence(
        rec1, rec2, extra_unique_ids, record_cache, relationship_index, corpus, suppressed
    )
    if conflicting:
        # A disagreeing deterministic field (different domain, different
        # ticker, ...) is proof of non-identity even when the names look
        # alike — never let a same-name auto-merge (_cross_name_similarity)
        # override it.
        heuristic = min(heuristic, 0.93)
    if model is None:
        return heuristic

    try:
        vector = (
            precomputed_vector
            if precomputed_vector is not None
            else _build_comparison_vector(
                rec1, rec2, normalise, record_cache, getattr(model, "token_df", None)
            )
        )
        # P6: a conflicting exclusive suppresses its own rule, never the pair --
        # the rest of the evidence decides, and the pair stays out of Match below.
        probability = model.probability(
            {f: c for f, c in vector.items() if f not in suppressed} if suppressed else vector
        )
    except Exception:
        return heuristic

    if conflicting:
        return min(probability, 0.93)

    # The heuristic already refuses to auto-merge on a shared partial name;
    # keep that ceiling rather than letting the model override it.
    if (
        heuristic <= 0.93
        and probability >= _AUTO_MATCH
        and _only_partial_name_evidence(rec1, rec2, normalise, record_cache)
    ):
        return 0.93

    # An auto-merge needs agreement on something that actually distinguishes
    # this pair from every other pair. Without this, two records agree on a
    # first name and on a field that is identical across the whole table
    # (Pagila stamps every actor row with the same last_update), and the
    # accumulated weight crosses the threshold on evidence that identifies
    # nobody. Observed live: 25 pairs of different actors merged this way.
    if probability >= _AUTO_MATCH and not _has_discriminating_agreement(
        rec1, rec2, model, normalise, record_cache
    ):
        return 0.93

    return round(probability, 4)


def _has_discriminating_agreement(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    model: Any,
    normalise: Any = _normalize,
    record_cache: RecordCache | None = None,
) -> bool:
    """Do these records agree on anything that actually narrows them down?

    A field qualifies when it is not merely part of a name AND the agreed
    value is not shared by a large share of the record set. Term frequency
    is what tells us the latter: a value held by 30% of rows distinguishes
    nothing, however exactly it matches. In a scoped merge, ``normalise``
    strips the subject token first, so a pair agreeing on the residual name
    ("Airbus SE" vs "Airbus SE") still qualifies while a pair agreeing only
    on the bare subject ("Airbus" vs "Airbus Helicopters") correctly does not.
    """
    fields = getattr(model, "fields", {})

    def rarity(field: str, value: str | None) -> float:
        """How common the agreed value is; 0.0 when unknown (treat as rare)."""
        field_model = fields.get(field)
        if field_model is None or value is None:
            return 0.0
        return field_model.term_frequency.get(value, 0.0)

    name_parts: list[float] = []
    vector = _build_comparison_vector(
        rec1, rec2, normalise, record_cache, getattr(model, "token_df", None)
    )
    for field, (level, value) in vector.items():
        if level not in (LEVEL_EXACT, LEVEL_STRONG):
            continue
        if field.lower() in _PARTIAL_NAME_FIELDS:
            name_parts.append(rarity(field, value))
            continue
        if rarity(field, value) <= _MAX_DISCRIMINATING_FREQUENCY:
            return True

    # No single discriminating field — but agreement on EVERY part of a name
    # is a different proposition from agreement on one part. "Rory" alone
    # identifies 4 people; "Rory Ledner" identifies one. Approximate the
    # combination's frequency as the product of its parts'.
    if len(name_parts) >= 2:
        combined = 1.0
        for freq in name_parts:
            combined *= freq
        return combined <= _MAX_DISCRIMINATING_FREQUENCY

    return False


def _only_partial_name_evidence(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    normalise: Any = _normalize,
    record_cache: RecordCache | None = None,
) -> bool:
    """True when the sole agreement between two records is a partial name."""
    vector = _build_comparison_vector(rec1, rec2, normalise, record_cache)
    agreeing = [f for f, (level, _v) in vector.items() if level in (LEVEL_EXACT, LEVEL_STRONG)]
    if not agreeing:
        return False
    return all(f.lower() in _PARTIAL_NAME_FIELDS for f in agreeing)


def _project_to_canonical(
    rec: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Project a record onto the canonical comparison schema (BUG B / C0a).

    Maps each raw field to a canonical slot using hint substrings, so that
    ``company_name`` and ``org_title`` both land in the ``name`` slot.
    Surrogate keys (_SURROGATE_KEY_HINTS) are excluded entirely — they are
    origin-local and comparing them cross-origin always produces spurious
    disagreements (BUG B / C0b).

    Returns ``(projected, aliased)`` — ``aliased`` names the canonical keys
    whose value came from a raw field name that actually differed from the
    canonical name (e.g. ``company_name`` -> ``name``). That distinction
    matters to callers applying a fuzzy-match ceiling: a field already
    literally called ``name`` needs no such caution, but an *inferred*
    alignment does.
    """
    projected: dict[str, Any] = {}
    aliased: set[str] = set()
    for key, value in rec.items():
        if key.startswith("_") or value is None:
            continue
        kl = key.lower()
        # Skip origin-local surrogate keys (C0b)
        if any(hint in kl for hint in _SURROGATE_KEY_HINTS):
            continue
        matched_canonical = next(
            (
                canonical
                for canonical, hints in _CANONICAL_FIELD_HINTS.items()
                if any(hint in kl for hint in hints)
            ),
            None,
        )
        if matched_canonical is not None and matched_canonical not in projected:
            # First writer wins — if multiple raw fields map to the same
            # canonical slot, prefer the one whose name matches best.
            # (e.g. ``company_name`` over ``org_title`` for ``name``)
            projected[matched_canonical] = value
            if kl != matched_canonical:
                aliased.add(matched_canonical)
        elif key not in projected:
            # No canonical slot matched, OR one did but a field seen earlier
            # already claimed it — e.g. ``first_name`` and ``last_name``
            # both loosely match the ``name`` hint, and the loser must NOT
            # be silently dropped. Carry it under its own raw name instead,
            # so an unaliased exact-key match (the common case: both origins
            # already spell ``last_name`` the same way) still compares.
            projected[key] = value
    return projected, aliased


# Part 1.5 A.1/A.5: per-record memoization for _project_to_canonical (:1125)
# and _best_name_field. Both are pure functions of ONE record, but were being
# recomputed 4-8x per candidate PAIR across _build_comparison_vector,
# _compute_heuristic_confidence and _cross_name_similarity — up to ~350M
# substring comparisons per entity type on a 250k-pair run to recompute
# values that never change (measured: SCORING_PROFILE.md,
# _project_to_canonical alone was 44% of resolve_entities_impl's wall time).
# Keyed on _record_id(rec) (a stable string), never id(rec) — object identity
# would silently break if a record is ever copied. `None` (the default
# everywhere below) preserves the original recompute-every-call behaviour
# for any direct caller — unit tests included — that doesn't build a cache.
RecordCache = dict[str, tuple[dict[str, Any], set[str], "str | None"]]


def _cached_projection(
    rec: dict[str, Any], record_cache: RecordCache | None
) -> tuple[dict[str, Any], set[str], str | None]:
    """(canonical projection, aliased fields, best name field) for ``rec``.

    Looks up ``record_cache`` when given; computes and memoizes on miss.
    Byte-identical output to calling ``_project_to_canonical`` and
    ``_best_name_field`` directly — this only removes redundant recomputation.
    """
    if record_cache is None:
        proj, aliased = _project_to_canonical(rec)
        return proj, aliased, _best_name_field(rec)
    key = _record_id(rec)
    cached = record_cache.get(key)
    if cached is not None:
        return cached
    proj, aliased = _project_to_canonical(rec)
    entry = (proj, aliased, _best_name_field(rec))
    record_cache[key] = entry
    return entry


def _is_domain_field(canonical_key: str) -> bool:
    """True when a canonical field holds a URL/domain value (BUG C / C0c)."""
    return canonical_key == "domain"


# Canonical fields whose fuzzy (non-exact) agreement must stay capped at the
# same ceiling _cross_name_similarity uses, so the projection (C0a) cannot
# let a merely-similar name reach full confidence on its own — that ceiling
# is what keeps "Titan Labs Incorporated" vs "Titan Labs Inc" in the review
# band instead of auto-merging on name similarity alone.
_NAME_LIKE_CANONICAL_FIELDS = frozenset({"name", "legal_name"})
_FUZZY_NAME_CAP = 0.93


def _comparison_level(
    v1: Any,
    v2: Any,
    normalise: Any = _normalize,
    field_name: str = "",
    token_df: tuple[dict[str, int], int] | None = None,
) -> tuple[str, str | None]:
    """Classify how two field values agree, for the probabilistic model.

    Returns (level, normalised_value). The value is carried so an exact
    agreement can be weighted by how rare that value actually is — using
    whichever ``normalise`` function fed the term-frequency table, so the
    two line up (see ``_subject_stripped_normalize`` for the scoped case).

    Type-aware (BUG C / C0c): domain-shaped fields compare on registrable
    domain via ``_domain_of``, so ``https://www.boeing.com/careers`` and
    ``boeing.com`` register as LEVEL_EXACT instead of LEVEL_WEAK.

    ``token_df`` (P14, D-14): this field's ``({token: df}, N)``. When given
    and either value is multi-word text, the level is chosen from the
    IDF-weighted soft token similarity instead of plain string similarity, so a shared
    high-df token cannot make two different names look alike.
    Atomic values (no whitespace, or no letters: emails, phones, ids,
    timestamps) keep the plain path. Tokens compare character by character,
    so a misspelling is not mistaken for a different name.
    ponytail: four levels cannot tell "two rare tokens agree" from "five
    common ones agree" (report D-14 ceiling). Deferred: a continuous
    ``sum(-log2(df/N))`` text weight, once this version is measured.
    """
    if v1 is None or v2 is None:
        return LEVEL_NULL, None

    # C0c: type-aware comparison for domain/URL fields
    if _is_domain_field(field_name):
        d1, d2 = _domain_of(v1), _domain_of(v2)
        if not d1 or not d2:
            return LEVEL_NULL, None
        if d1 == d2:
            return LEVEL_EXACT, d1
        return LEVEL_DISAGREE, None

    n1, n2 = normalise(v1), normalise(v2)
    if not n1 or not n2:
        return LEVEL_NULL, None
    if n1 == n2:
        return LEVEL_EXACT, n1

    s1, s2 = str(v1).strip().lower(), str(v2).strip().lower()
    if len(s1) < 3 or len(s2) < 3:
        return LEVEL_DISAGREE, None

    sim: float | None = None
    # Text = words: both values carry letters and one has several tokens.
    # A spaced phone or a timestamp is still atomic.
    if (
        token_df is not None
        and (" " in s1 or " " in s2)
        and any(c.isalpha() for c in s1)
        and any(c.isalpha() for c in s2)
    ):
        sim = idf_soft_similarity(
            set(_tokenize(s1)), set(_tokenize(s2)), *token_df, token_sim=_token_ratio
        )
    if sim is None:
        sim = _string_similarity(s1, s2)
    if sim >= 0.90:
        return LEVEL_STRONG, None
    if sim >= 0.70:
        return LEVEL_WEAK, None
    return LEVEL_DISAGREE, None


def _build_comparison_vector(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    normalise: Any = _normalize,
    record_cache: RecordCache | None = None,
    token_df: tuple[dict[str, dict[str, int]], dict[str, int]] | None = None,
) -> dict[str, tuple[str, str | None]]:
    """Per-field comparison outcomes for one candidate pair.

    Projects both records onto the canonical schema first (BUG B / C0a),
    so cross-origin pairs with different raw field names still share a
    comparison intersection. Surrogate keys are excluded (C0b). Each
    field's comparison uses type-aware normalisation (C0c).

    ``record_cache`` (Part 1.5 A.1) memoizes the per-record projection so it
    is computed once, not once per pair — see ``_cached_projection``.
    """
    proj1, _, _ = _cached_projection(rec1, record_cache)
    proj2, _, _ = _cached_projection(rec2, record_cache)
    df, totals = token_df if token_df is not None else ({}, {})
    return {
        field: _comparison_level(
            proj1.get(field),
            proj2.get(field),
            normalise,
            field_name=field,
            token_df=(df[field], totals[field]) if field in df else None,
        )
        for field in set(proj1) & set(proj2)
    }


def _pair_field_evidence(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    vector: dict[str, tuple[str, str | None]],
    model: Any,
    record_cache: RecordCache | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split one pair's comparison vector into matched vs mismatched fields.

    The Splink-style waterfall breakdown Phase 7.1 asks for (issue 7): which
    fields carried a match, not one opaque confidence number. Built entirely
    from ``MatchModel.weight`` (fellegi_sunter.py) — the exact bits-of-evidence
    figure the scorer itself already sums into one probability — so this is
    the SAME number decomposed per field, not a second, possibly-diverging
    calculation. ``model.explain()`` computes something similar but only as
    formatted text with no callers anywhere in the codebase; this returns
    structured dicts instead, for the cluster's ``match_evidence`` and
    eventually the review gate/lineage view (Phase 7.3).

    ``LEVEL_NULL`` fields (at least one side missing the field entirely)
    contribute exactly 0 weight and are dropped — they are not evidence
    either way, just absent data, and would only clutter the breakdown.
    """
    proj1, _, _ = _cached_projection(rec1, record_cache)
    proj2, _, _ = _cached_projection(rec2, record_cache)
    matched: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    for field_name, (level, value) in vector.items():
        if level == LEVEL_NULL:
            continue
        field_model = model.fields.get(field_name)
        if field_model is None:
            continue
        weight = field_model.weight(level, value)
        entry = {
            "field": field_name,
            "a": proj1.get(field_name),
            "b": proj2.get(field_name),
            "level": level,
            "weight": round(weight, 3),
        }
        (matched if weight > 0 else mismatched).append(entry)
    matched.sort(key=lambda e: -e["weight"])
    mismatched.sort(key=lambda e: e["weight"])
    return matched, mismatched


def _unique_id_fields(records: list[dict[str, Any]]) -> set[str]:
    """Fields that uniquely identify an entity (email, tax_id, ...)."""
    found: set[str] = set()
    for rec in records[:50]:
        for key in rec:
            if key.startswith("_"):
                continue
            kl = key.lower()
            if any(hint in kl for hint in _UNIQUE_IDENTITY_HINTS):
                found.add(key)
    return found


def _domain_of(value: Any) -> str:
    """Registrable domain from a URL/website value.

    ``https://www.boeing.com/careers`` and ``boeing.com`` must compare equal,
    or a website field can never serve as a deterministic identity key across
    two origins that stored it differently (a bare domain vs a full URL).
    """
    s = str(value).strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s


def _deterministic_id_fields(
    records: list[dict[str, Any]], extra: list[str] | None = None
) -> set[str]:
    """Fields that settle identity directly when they agree (Phase 3.1).

    ``extra`` names operator-declared fields from the entity's saved
    ``matching_rules`` (``deterministic_id_fields``) — a business ref no
    generic hint would recognise, matched exactly rather than by substring.
    """
    found: set[str] = {str(f) for f in (extra or []) if f}
    for rec in records[:50]:
        for key in rec:
            if key.startswith("_"):
                continue
            kl = key.lower()
            if any(hint in kl for hint in _DETERMINISTIC_ID_HINTS):
                found.add(key)
    return found


def _deterministic_values(
    rec1: dict[str, Any], rec2: dict[str, Any], fields: set[str]
) -> list[tuple[str, str]]:
    """Normalised (v1, v2) pairs for every deterministic field both sides carry.

    Website/domain/url/homepage-shaped fields compare on registrable domain;
    everything else (ticker, tax id, registration number) compares on the
    normalised raw value.
    """
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}
    out: list[tuple[str, str]] = []
    for f in fields:
        v1, v2 = fields1.get(f), fields2.get(f)
        if v1 is None or v2 is None:
            continue
        if any(h in f.lower() for h in _DOMAIN_FIELD_HINTS):
            n1, n2 = _domain_of(v1), _domain_of(v2)
        else:
            n1, n2 = _normalize(v1), _normalize(v2)
        if n1 and n2:
            out.append((n1, n2))
    return out


def _deterministic_match(rec1: dict[str, Any], rec2: dict[str, Any], fields: set[str]) -> bool:
    """True when both records agree on at least one deterministic id field."""
    return any(n1 == n2 for n1, n2 in _deterministic_values(rec1, rec2, fields))


def _deterministic_conflict(rec1: dict[str, Any], rec2: dict[str, Any], fields: set[str]) -> bool:
    """True when both records carry a deterministic field and it disagrees.

    The reciprocal of ``_deterministic_match``: a declared identity key that
    settles a match when it agrees settles a NON-match just as firmly when it
    disagrees — two different domains means two different organisations,
    however similar their names look.
    """
    return any(n1 != n2 for n1, n2 in _deterministic_values(rec1, rec2, fields))


def _subject_tokens(subject: str | None) -> frozenset[str]:
    """Normalised word tokens of a scoped merge's subject name.

    Empty for an unscoped run, so every scoped-mode code path below is a
    strict no-op when there is no subject — general dedup precision must be
    unaffected by any of this.
    """
    if not subject:
        return frozenset()
    return frozenset(re.findall(r"[a-z0-9]+", subject.lower()))


def _subject_stripped_normalize(subject_tokens: frozenset[str]):
    """A ``_normalize``-compatible function with subject tokens removed.

    In a scoped ("everything about Nova") merge, every record's name field
    contains the subject token, so its term frequency is ~1.0 and it can
    never be treated as discriminating evidence — which is exactly what
    collapsed matching in a scoped run (Airbus 20 -> 20 clusters, 0 merged).
    Stripping the subject before frequency counting lets the RESIDUAL tokens
    ("SE", "SAS", "Helicopters", ...) carry the discriminating weight instead.
    Falls back to the un-stripped value when nothing would remain, so a value
    that is purely the subject name doesn't collapse to an empty key.
    """
    if not subject_tokens:
        return _normalize

    def normalise(value: Any) -> str:
        n = _normalize(value)
        if not n:
            return n
        parts = [p for p in n.split("_") if p not in subject_tokens]
        stripped = "_".join(parts)
        return stripped or n

    return normalise


class RelationshipIndex(dict[str, set[str]]):
    """``id -> related ids``, plus the referenced key column(s) a record's own id
    is read from (P7: the column the junction's FKs point at, found by structure)."""

    key_columns: frozenset[str] = frozenset()


def build_relationship_index(
    relation_records: list[dict[str, Any]] | None,
    columns: tuple[str, str],
    key_column: str | None = None,
) -> RelationshipIndex:
    """RELATER-style relational-matching signal, adopted natively rather
    than from RELATER's own unmaintained code (ADR 0018), B.5.1.

    Maps an entity's own origin-native id -> the set of other ids of the
    same kind it is related to, from the rows of a junction whose two FK
    ``columns`` reference the same key (P7: the junction is found by P4's
    structure test, never by a table or column name). Symmetric: a relation
    asserted from A's side also indexes B as related to A, since a candidate
    pair can be checked in either order. Built once per entity-resolution
    call and threaded down as a parameter (like ``matching_rules``) — never
    module state (``scripts/lint_arch.py``'s no-module-mutable-state rule).
    ``key_column``: the referenced key both FKs point at; a record's id is read
    from that column only (without it no record finds itself in the index).
    """
    left, right = columns
    index = RelationshipIndex()
    if key_column:
        index.key_columns = frozenset({key_column.lower()})
    for row in relation_records or []:
        a = row.get(left)
        b = row.get(right)
        if a is None or b is None:
            continue
        a_key = str(a).strip().lower()
        b_key = str(b).strip().lower()
        if not a_key or not b_key:
            continue
        index.setdefault(a_key, set()).add(b_key)
        index.setdefault(b_key, set()).add(a_key)
    return index


def _company_native_id(rec: dict[str, Any], relationship_index: dict[str, set[str]]) -> set[str]:
    """The record's own ids in the index: its values in the index's referenced key
    column(s), normalized the way ``build_relationship_index`` normalizes its keys.
    A plain index that names no key column finds no record (never a guess from
    whatever value happens to equal an indexed id)."""
    columns: frozenset[str] = getattr(relationship_index, "key_columns", frozenset())
    ids: set[str] = set()
    for key, value in rec.items():
        if value is None or key.lower() not in columns:
            continue
        text = str(value).strip().lower()
        if text in relationship_index:
            ids.add(text)
    return ids


def _shared_neighbors_beyond_common_parent(
    relationship_index: dict[str, set[str]],
    rec1: dict[str, Any],
    rec2: dict[str, Any],
) -> set[str]:
    """The pair's shared asserted-relationship neighbors — guarded
    against the audit's own §00a2 Airbus-blob failure mode (B.5.2).

    Two subsidiaries of the same parent share exactly ONE neighbor (that
    parent) purely because of where they sit in an org chart — that is
    evidence they are SIBLINGS, not evidence they are the SAME entity,
    and boosting on it is exactly what reproduced the Airbus "Defence and
    Space" blob. Requires >= 2 distinct shared neighbors — never just the
    one common parent — before this counts as relational agreement.
    """
    ids1 = _company_native_id(rec1, relationship_index)
    ids2 = _company_native_id(rec2, relationship_index)
    if not ids1 or not ids2:
        return set()
    n1 = set().union(*(relationship_index[i] for i in ids1))
    n2 = set().union(*(relationship_index[i] for i in ids2))
    shared = (n1 & n2) - ids1 - ids2
    if len(shared) < 2:
        return set()
    return shared


def _relational_agreement(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    relationship_index: dict[str, set[str]],
) -> float:
    """Bounded additive confidence boost from shared asserted
    relationships (RELATER-style, B.5.2) — never sole justification for
    an auto-merge (see ``_compute_heuristic_confidence``'s weighted sum,
    the same discipline ``_has_discriminating_agreement`` already uses:
    a boost alone can never reach ``_AUTO_MATCH`` on its own)."""
    shared = _shared_neighbors_beyond_common_parent(relationship_index, rec1, rec2)
    if not shared:
        return 0.0
    return min(0.15 * len(shared), 0.3)


def _compute_heuristic_confidence(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    extra_unique_ids: set[str] | None = None,
    record_cache: RecordCache | None = None,
    relationship_index: dict[str, set[str]] | None = None,
    corpus: CorpusEvidence | None = None,
    suppressed: set[str] | None = None,
) -> float:
    """Compute a match confidence based on field comparison.

    Projects both records onto the canonical schema first (BUG B / C0a),
    so cross-origin pairs with different raw field names still share a
    comparison intersection. Surrogate keys are excluded (C0b). Domain
    fields compare on registrable domain (C0c).

    ``record_cache`` (Part 1.5 A.1) memoizes the per-record projection —
    see ``_cached_projection``.

    ``relationship_index`` (B.5.2, RELATER-style), when given, adds
    ``_relational_agreement``'s bounded additive boost — shared asserted
    relationships to the same third parties, guarded against the
    common-parent trap (``_shared_neighbors_beyond_common_parent``).
    Applied AFTER the identity-conflict cap below, so it can never
    override a contradicting unique identifier.

    Returns confidence between 0.0 and 1.0.
    """
    proj1, aliased1, _ = _cached_projection(rec1, record_cache)
    proj2, aliased2, _ = _cached_projection(rec2, record_cache)
    aliased = aliased1 | aliased2

    common_fields = set(proj1.keys()) & set(proj2.keys())

    score = 0.0
    total = 0

    for field in common_fields:
        v1 = proj1[field]
        v2 = proj2[field]

        if v1 is None or v2 is None:
            continue
        # P6: a suppressed (conflicting exclusive) field and a generic value
        # carry no evidence either way.
        if suppressed and field in suppressed:
            continue
        if corpus is not None and (corpus.generic(field, v1) or corpus.generic(field, v2)):
            continue

        total += 1

        # C0c: type-aware comparison for domain/URL fields
        if _is_domain_field(field):
            d1, d2 = _domain_of(v1), _domain_of(v2)
            if d1 and d2 and d1 == d2:
                score += 1.0
            elif d1 and d2:
                score += 0.0  # different domains = disagree
            continue

        # An *inferred* cross-origin name alignment (e.g. `company_name` and
        # `org_title` both mapped to the `name` slot) is a judgment call,
        # not a verified schema fact — cap it at the same ceiling
        # _cross_name_similarity uses. A field already literally named
        # `name`/`legal_name` on both sides needs no such caution.
        name_needs_cap = field in _NAME_LIKE_CANONICAL_FIELDS and field in aliased

        n1, n2 = _normalize(v1), _normalize(v2)
        if n1 == n2:
            score += _FUZZY_NAME_CAP if name_needs_cap else 1.0
        elif isinstance(v1, str) and isinstance(v2, str):
            s1 = str(v1).strip().lower()
            s2 = str(v2).strip().lower()
            if len(s1) > 2 and len(s2) > 2:
                sim = (
                    _text_similarity(
                        s1, s2, corpus.token_df.get(field), corpus.token_n.get(field, 0)
                    )
                    if corpus is not None
                    else _string_similarity(s1, s2)
                )
                if sim > 0.5:
                    if name_needs_cap:
                        sim = min(sim, _FUZZY_NAME_CAP)
                    score += sim

    field_score = score / total if total > 0 else 0.0
    confidence = max(field_score, _cross_name_similarity(rec1, rec2, record_cache, corpus))

    if relationship_index:
        confidence = min(1.0, confidence + _relational_agreement(rec1, rec2, relationship_index))

    # Use original records (not projections) for identity conflict check,
    # since _has_conflicting_identity needs the raw fields.
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}
    if suppressed or _has_conflicting_identity(
        fields1, fields2, extra_unique_ids, declared_only=corpus is not None
    ):
        # Never auto-merge over a contradicting unique id — route it to review.
        return min(confidence, 0.93)
    return confidence


# ── P6: what the record set says about its own values ─────────────────


@dataclass(frozen=True)
class CorpusEvidence:
    """Value sharing measured over one entity's whole record set (report P6, §3.5).

    Built once per run, before any pair is scored, so every label is a
    property of the corpus and record order cannot change it (the report's
    "re-evaluates all prior records").

    ``cut``: the most records one entity contributes when each origin holds
    it once (never fewer than the two records of a pair). A value held by
    more records than that is *generic* when its holders' names also form
    more than ``cut`` groups -- it belongs to several entities ("labels that
    SSN as generic"), so it is neither a conflict nor evidence. Grouping by
    name keeps a within-origin triplicate (one entity, name variants) from
    making its own email generic.
    ``exclusive``: fields measured as exclusive identifiers (class 4), see
    ``_exclusive_fields``. ``token_df``: P14's per-field token document
    frequencies; ``name_token_df`` the same over each record's best name.
    """

    cut: int
    generic_values: dict[str, frozenset[str]]
    exclusive: frozenset[str]  # candidates; see exclusive_against
    agree: dict[str, int]
    disagree: dict[str, int]
    token_df: dict[str, dict[str, int]]
    token_n: dict[str, int]
    name_token_df: dict[str, int]
    name_n: int

    def generic(self, field: str, value: Any) -> bool:
        return _identity_value(field, value) in self.generic_values.get(field, frozenset())

    def exclusive_against(self, field: str, this_pair_disagrees: bool) -> bool:
        """Is ``field`` exclusive (class 4) when judging one pair? Within-entity
        cardinality 1 means no OTHER evident duplicate disagrees on it, so the
        judged pair's own disagreement is left out; a hint-named field with no
        observation at all keeps its hint."""
        if field not in self.exclusive:
            return False
        agree = self.agree.get(field, 0)
        disagree = self.disagree.get(field, 0) - this_pair_disagrees
        if agree + disagree <= 0:
            return any(hint in field.lower() for hint in _UNIQUE_IDENTITY_HINTS)
        return disagree <= 0

    def names(self, n1: str | None, n2: str | None, among: int = 0) -> str:
        """How two names relate: ``same`` (exact or strong, P14's IDF levels),
        ``partial`` (they share a token held by no more than ``cut`` names --
        or, when grouping one value's ``among`` holders, by no more than
        them), else ``different``. A missing name is ``different``."""
        if not n1 or not n2:
            return "different"
        level, _ = _comparison_level(n1, n2, _normalize, "name", (self.name_token_df, self.name_n))
        if level in (LEVEL_EXACT, LEVEL_STRONG):
            return "same"
        shared = set(_tokenize(n1.lower())) & set(_tokenize(n2.lower()))
        if any(self.name_token_df.get(t, 0) <= max(self.cut, among) for t in shared):
            return "partial"
        return "different"


def _identity_value(field: str, value: Any) -> str:
    """The value as identity sees it: a registrable domain for a domain field (a bare
    domain and a full URL are one website), else the normalised value."""
    return (_domain_of(value) or "") if _is_domain_field(field) else _normalize(value)


def _id_core(field: str, value: Any) -> str:
    """The identifier itself: ``_identity_value`` without whitespace and hyphens, so
    "GB 100000", "GB-100000" and "GB100000" are one value (formatting, not identity)."""
    norm = _identity_value(field, value)
    return norm if _is_domain_field(field) else re.sub(r"[\s-]+", "", norm)


def _one_edit_apart(a: str, b: str) -> bool:
    """One insertion, deletion or substitution turns ``a`` into ``b``: a keying slip,
    not a second identifier."""
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) >= len(b)) :] == b[i + (len(b) >= len(a)) :]


def _identity_view(rec: dict[str, Any], record_cache: RecordCache | None) -> dict[str, Any]:
    """The canonical projection plus any raw field the identity hints name that the
    projection dropped as a surrogate key (``tax_id`` contains ``id``): the fields
    exclusivity and conflicts are measured on."""
    proj, _, _ = _cached_projection(rec, record_cache)
    extra = {
        k: v
        for k, v in rec.items()
        if v is not None
        and k not in proj
        and not k.startswith("_")
        and any(hint in k.lower() for hint in _UNIQUE_IDENTITY_HINTS)
    }
    return {**proj, **extra} if extra else proj


def _record_name(rec: dict[str, Any], record_cache: RecordCache | None) -> str | None:
    _, _, field = _cached_projection(rec, record_cache)
    return str(rec[field]) if field and rec.get(field) is not None else None


def _is_name_field(field: str) -> bool:
    return field in _NAME_LIKE_CANONICAL_FIELDS or field.lower() in _PARTIAL_NAME_FIELDS


def _corpus_evidence(
    records: list[dict[str, Any]],
    record_cache: RecordCache | None = None,
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> CorpusEvidence:
    """Measure the record set once (see ``CorpusEvidence``). ``pairs`` are the
    run's compared pairs: the evident duplicates among them are where a
    field's within-entity cardinality is observed (``_exclusive_fields``)."""
    projections = [_cached_projection(r, record_cache) for r in records]
    views = [_identity_view(r, record_cache) for r in records]
    names = [_record_name(r, record_cache) for r in records]
    holders: dict[str, dict[str, list[int]]] = {}
    text: set[str] = set()  # fields with a value that is not one identifier-shaped token
    for i, view in enumerate(views):
        for field, value in view.items():
            norm = _identity_value(field, value)
            if norm:
                holders.setdefault(field, {}).setdefault(norm, []).append(i)
                raw = str(value).strip()
                if any(ch.isspace() for ch in raw) or _is_quantity_or_date(raw):
                    text.add(field)
    origins = {r.get("_origin_id") for r in records}
    cut = max(len(origins), 2)
    token_df, token_n = build_token_document_frequencies([p for p, _a, _n in projections])
    name_df, name_n = build_token_document_frequencies([{"name": n} for n in names if n])
    probe = CorpusEvidence(
        cut,
        {},
        frozenset(),
        {},
        {},
        token_df,
        token_n,
        name_df.get("name", {}),
        name_n.get("name", 0),
    )

    def several_entities(idx: list[int]) -> bool:
        """More than ``cut`` name groups among the holders; stops as soon as it knows."""
        groups: list[str | None] = []
        for i in idx:
            if names[i] is None or all(
                probe.names(names[i], g, len(idx)) == "different" for g in groups
            ):
                groups.append(names[i])
                if len(groups) > cut:
                    return True
        return False

    generic_values = {
        field: frozenset(
            v for v, idx in by_value.items() if len(idx) > cut and several_entities(idx)
        )
        for field, by_value in holders.items()
    }
    evidence = CorpusEvidence(
        cut,
        generic_values,
        frozenset(),
        {},
        {},
        token_df,
        token_n,
        probe.name_token_df,
        probe.name_n,
    )
    exclusive, agree, disagree = _exclusive_fields(
        holders, text, evidence, pairs or [], record_cache
    )
    return CorpusEvidence(
        cut,
        generic_values,
        exclusive,
        agree,
        disagree,
        token_df,
        token_n,
        probe.name_token_df,
        probe.name_n,
    )


def _is_quantity_or_date(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        pass
    try:
        date.fromisoformat(value.split("T")[0])
        return True
    except ValueError:
        return False


def _exclusive_fields(
    holders: dict[str, dict[str, list[int]]],
    text: set[str],
    corpus: CorpusEvidence,
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    record_cache: RecordCache | None,
) -> tuple[frozenset[str], dict[str, int], dict[str, int]]:
    """Fields that behave as exclusive identifiers (report §3.5 class 4), measured.

    Class 4 is "near-unique across the corpus AND within-entity cardinality
    1". A field qualifies when (a) it is not a name or a name part; (b) most
    of its records hold a non-generic value; (c) its values are one
    whitespace-free token and not a quantity or a date (text and attributes
    vary between origins for one entity), or the identity hints name it;
    (d) it is not one of a set of repeated columns (``phone1``/``phone2``:
    one origin already holds several values, the report's class-2 test); and
    (e) within-entity cardinality 1, observed on the run's evident duplicates
    -- pairs whose names agree and that share another non-generic value.
    Returns the candidates of (a)-(d) and the agree/disagree counts of (e);
    ``CorpusEvidence.exclusive_against`` applies (e) per judged pair.

    ponytail: Ceiling -- with few evident duplicates (a small or a
    single-origin corpus) (e) has little to go on, so an identifier may be
    missed (no conflict raised: the pre-P6 behaviour) and a class-2 field
    that happens to agree on every observed duplicate is taken for class 4.
    The report's full classifier (D-26: entropy, length distribution, FD
    membership, child-table cardinality) is the upgrade; deferred to the
    corpus (Part 3), where values are classified once per origin.
    """
    stems = Counter(re.sub(r"\d+$", "", f) for f in holders)
    candidates = set()
    for field, by_value in holders.items():
        if _is_name_field(field) or stems[re.sub(r"\d+$", "", field)] > 1:
            continue
        generic = corpus.generic_values.get(field, frozenset())
        total = sum(len(idx) for idx in by_value.values())
        held = sum(len(idx) for v, idx in by_value.items() if v not in generic)
        named = any(hint in field.lower() for hint in _UNIQUE_IDENTITY_HINTS)
        if 2 * held > total and (field not in text or named):
            candidates.add(field)
    agree: Counter[str] = Counter()
    disagree: Counter[str] = Counter()
    for rec1, rec2 in pairs:
        v1, v2 = _identity_view(rec1, record_cache), _identity_view(rec2, record_cache)
        same, differ = set(), set()
        for f in set(v1) & set(v2):
            if _is_name_field(f):
                continue
            n1, n2 = _id_core(f, v1[f]), _id_core(f, v2[f])
            if n1 and n1 == n2 and not corpus.generic(f, v1[f]):
                same.add(f)
            elif n1 and n2 and not _one_edit_apart(n1, n2):
                differ.add(f)  # a one-edit slip is neither agreement nor a second value
        if (
            not same
            or corpus.names(_record_name(rec1, record_cache), _record_name(rec2, record_cache))
            != "same"
        ):
            continue
        for f in candidates & (same | differ):
            if same - {f}:
                agree[f] += f in same
                disagree[f] += f in differ
    return frozenset(candidates), dict(agree), dict(disagree)


def _conflicting_exclusives(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    corpus: CorpusEvidence,
    record_cache: RecordCache | None = None,
) -> set[str]:
    """Exclusive fields on which the two records hold different, non-generic values.

    A conflict suppresses the matching rule, never the pair (report P6): the
    caller scores the pair without these fields and keeps it out of Match.
    A generic value conflicts with nothing -- it never identified anyone.
    """
    v1 = _identity_view(rec1, record_cache)
    v2 = _identity_view(rec2, record_cache)
    common = set(v1) & set(v2)
    differ = {
        f
        for f in corpus.exclusive & common
        if _id_core(f, v1[f]) != _id_core(f, v2[f])
        and not corpus.generic(f, v1[f])
        and not corpus.generic(f, v2[f])
    }
    if not differ:
        return set()
    # Was this pair itself one of the evident duplicates _exclusive_fields counted?
    shared = {
        f
        for f in common
        if not _is_name_field(f)
        and _id_core(f, v1[f])
        and _id_core(f, v1[f]) == _id_core(f, v2[f])
        and not corpus.generic(f, v1[f])
    }
    observed = (
        bool(shared)
        and corpus.names(_record_name(rec1, record_cache), _record_name(rec2, record_cache))
        == "same"
    )
    return {
        f
        for f in differ
        if corpus.exclusive_against(
            f,
            observed
            and bool(shared - {f})
            and not _one_edit_apart(_id_core(f, v1[f]), _id_core(f, v2[f])),
        )
    }


def _text_similarity(s1: str, s2: str, df: dict[str, int] | None, n: int) -> float:
    """``_string_similarity``, IDF-weighted for multi-word text (P14's comparator,
    used by the heuristic too, so a shared common token such as a legal form
    cannot make two different names look alike on either path)."""
    if (
        df
        and (" " in s1 or " " in s2)
        and any(c.isalpha() for c in s1)
        and any(c.isalpha() for c in s2)
    ):
        t1, t2 = set(_tokenize(s1)), set(_tokenize(s2))
        # The two names under comparison are not evidence that a token is
        # common: count each token once for the pair, so a two-record corpus
        # weighs its tokens evenly instead of zeroing the one they share.
        pair_df = {t: max(df.get(t, 1) - (t in t1) - (t in t2) + 1, 1) for t in t1 | t2}
        sim = idf_soft_similarity(t1, t2, pair_df, n, token_sim=_token_ratio)
        if sim is not None:
            return sim
    return _string_similarity(s1, s2)


def _shares_identifying_value(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    corpus: CorpusEvidence,
    record_cache: RecordCache | None = None,
) -> bool:
    """Do two records hold the same non-generic value on a field other than a name?

    The Possibly Related test (report P6, §3.7): a pair the score excludes
    that still shares an address, a phone, ... held by no other entity is
    related, not unrelated -- a typed edge, no human. No direction is
    claimed (the report's honest limit: direction needs a declared edge).
    """
    proj1, _, _ = _cached_projection(rec1, record_cache)
    proj2, _, _ = _cached_projection(rec2, record_cache)
    for f in set(proj1) & set(proj2):
        if f in _NAME_LIKE_CANONICAL_FIELDS or f.lower() in _PARTIAL_NAME_FIELDS:
            continue
        n1 = _identity_value(f, proj1[f])
        if n1 and n1 == _identity_value(f, proj2[f]) and not corpus.generic(f, proj1[f]):
            return True
    return False


def _three_way(
    conf: float,
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    corpus: CorpusEvidence,
    auto_match: float,
    auto_exclude: float,
    record_cache: RecordCache | None = None,
    declared_conflict: bool = False,
) -> float:
    """The report's three outcomes as rules over measured evidence (P6, [S] p. 4).

    The rules fire only on an *anchor* -- the pair holds the same non-generic
    value on a field other than a name (an email, an address, a phone ...
    that no other entity holds) -- and only when the names are not disjoint:

    - names agree, no conflicting exclusive          -> Match ("same email");
    - a conflicting exclusive, or names that share
      only a non-generic token                       -> at least Possible
      Match, the review band ("same address, different tax IDs": the rule is
      suppressed, the pair is not);
    - disjoint names                                 -> the score stands; if
      it excludes the pair it becomes a Possibly Related edge, no human.

    An operator-declared unique id or deterministic key that disagrees
    (``declared_conflict``) is the operator's rule and always wins: the
    score, already capped by ``_score_pair``, stands. Without an anchor the
    score alone decides. The bars are the run's own.
    """
    if declared_conflict or not _shares_identifying_value(rec1, rec2, corpus, record_cache):
        return conf
    relation = corpus.names(_record_name(rec1, record_cache), _record_name(rec2, record_cache))
    if relation == "different":
        return conf
    if _conflicting_exclusives(rec1, rec2, corpus, record_cache):
        return min(max(conf, auto_exclude), _FUZZY_NAME_CAP)
    if relation == "same":
        return max(conf, auto_match)
    return max(conf, auto_exclude)


def _has_conflicting_identity(
    fields1: dict[str, Any],
    fields2: dict[str, Any],
    extra_unique_ids: set[str] | None = None,
    declared_only: bool = False,
) -> bool:
    """True when both records carry a unique identifier and they disagree.

    ``extra_unique_ids`` names columns the operator declared unique for this
    entity (Phase O.2) — a customer_no or member_id that no generic hint
    would recognise, but which settles identity just as firmly as an email.
    """
    declared = extra_unique_ids or set()
    for key1, val1 in fields1.items():
        kl = key1.lower()
        if key1 not in declared and (
            declared_only or not any(hint in kl for hint in _UNIQUE_IDENTITY_HINTS)
        ):
            continue
        val2 = fields2.get(key1)
        if val1 is None or val2 is None:
            continue
        if _normalize(val1) != _normalize(val2):
            return True
    return False


def _code_tokens(value: Any) -> frozenset[str]:
    """Tokens mixing letters and digits -- the shape of a model or part code."""
    return frozenset(
        t
        for t in _WORD_RE.findall(str(value).lower())
        if any(c.isdigit() for c in t) and any(c.isalpha() for c in t)
    )


def _conflicting_codes(
    rec1: dict[str, Any], rec2: dict[str, Any], record_cache: RecordCache | None = None
) -> bool:
    """PRE-D2: both names carry a code and neither code appears in the other name.

    "Mount - 20AB" vs "Mount - 20AC" are two products; the rest of the name
    and the description agree, so nothing else in the vector can tell them
    apart. Codes are compared against the other name with separators removed,
    so "PS-LX350H" still finds "pslx350h". A name with no code conflicts with
    nothing.
    ponytail: ceiling -- a variant whose code sits only in another field, or
    a code written differently on each side, is not caught / is sent to
    review. Upgrade path: an identifier-token comparison level in the model.
    """
    _, _, f1 = _cached_projection(rec1, record_cache)
    _, _, f2 = _cached_projection(rec2, record_cache)
    if f1 is None or f2 is None or not rec1.get(f1) or not rec2.get(f2):
        return False
    n1, n2 = str(rec1[f1]), str(rec2[f2])
    c1, c2 = _code_tokens(n1), _code_tokens(n2)
    if not c1 or not c2:
        return False
    q1, q2 = ("".join(_WORD_RE.findall(n.lower())) for n in (n1, n2))
    return not any(c in q2 for c in c1) and not any(c in q1 for c in c2)


def _cross_name_similarity(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    record_cache: RecordCache | None = None,
    corpus: CorpusEvidence | None = None,
) -> float:
    """Similarity of the records' best name fields, across field names.

    Origins name the same concept differently (``company_name`` vs
    ``org_title``), so common-field comparison alone can never match their
    records. An exact normalized name match scores just above _AUTO_MATCH
    (deterministic cross-origin merge); fuzzy name-only evidence is capped
    below it so it lands in the LLM/human review band instead.

    ``record_cache`` (Part 1.5 A.5) memoizes ``_best_name_field`` alongside
    the canonical projection — see ``_cached_projection``.
    """
    _, _, f1 = _cached_projection(rec1, record_cache)
    _, _, f2 = _cached_projection(rec2, record_cache)
    if not f1 or not f2:
        return 0.0
    s1 = str(rec1[f1]).strip().lower()
    s2 = str(rec2[f2]).strip().lower()
    if len(s1) < 3 or len(s2) < 3:
        return 0.0
    partial = f1.lower() in _PARTIAL_NAME_FIELDS or f2.lower() in _PARTIAL_NAME_FIELDS
    if s1 == s2:
        # A shared first name is evidence, not proof — keep it in the review band.
        return 0.93 if partial else 0.96
    sim = (
        _text_similarity(s1, s2, corpus.name_token_df, corpus.name_n)
        if corpus is not None
        else _string_similarity(s1, s2)
    )
    return min(sim, 0.93) if sim > 0.5 else 0.0


# ── LLM judgment for borderline pairs ────────────────────────────────


def _format_pairs_block(
    borderline: list[tuple[str, str, dict, dict, float]],
) -> str:
    """Format the pair(s) (non-null public fields only) for the prompt."""
    lines: list[str] = []
    for _id1, _id2, rec1, rec2, _conf in borderline:
        f1 = {k: v for k, v in rec1.items() if not k.startswith("_") and v is not None}
        f2 = {k: v for k, v in rec2.items() if not k.startswith("_") and v is not None}
        lines.append(f"Record A: {json.dumps(f1, ensure_ascii=False, default=str)}")
        lines.append(f"Record B: {json.dumps(f2, ensure_ascii=False, default=str)}")
    return "\n".join(lines)


def _probability(result: Any) -> float | None:
    """The reply's ``probability`` when it is one: a finite number in [0, 1]
    (not a bool). Anything else is malformed and judges nothing."""
    p = result.get("probability") if isinstance(result, dict) else None
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return None
    return float(p) if 0 <= p <= 1 else None  # NaN fails both comparisons


async def _judge_pairs_with_llm(
    entity_type: str,
    borderline: list[tuple[str, str, dict, dict, float]],
    llm_config: dict[str, Any],
    evidence_sink: dict[tuple[str, str], dict[str, Any]] | None = None,
    auto_match: float = _AUTO_MATCH,
    semaphore: asyncio.Semaphore | None = None,
) -> list[tuple[str, str, float]]:
    """Judge review-band pairs with the LLM, ONE pair per call (report P8).

    Each call returns ``{"probability": p, "reason": ...}``. A decisive answer --
    ``p`` or ``1 - p`` at least the run's ``auto_match`` -- becomes the pair's
    score, so the band decides (merge or apart); an unsure one leaves the
    heuristic score, so the pair stays in human review rather than being
    dropped because an honest "about 0.5" sits under the exclude bar. A pair per call means one malformed reply, a timeout or an
    out-of-range number damages that pair only: it keeps its heuristic score.
    Calls run concurrently, at most ``_LLM_CONCURRENCY`` in flight -- across the
    whole run when the caller passes its ``semaphore`` (D13: entities resolve
    concurrently, and the endpoint is the ceiling, not one entity). Returns one
    ``(id1, id2, score)`` per input pair, in input order.

    ``evidence_sink`` (Phase 7.1), when given, is filled in place with one
    entry per pair the LLM actually judged -- ``{"method": "llm", "reason":
    ..., "llm_probability": p}``. A pair the LLM did not judge gets no entry.

    ponytail: ``p`` is the model's stated probability, not a calibrated one --
    calibration is the open item of report P13 / section 2.17 (a labelled
    sample scored against the stated p); until then the band reads it as is.
    """
    from src.llm import PROMPT_TEMPLATES, LLMClient
    from src.orchestrator.utils.retry_hints import apply_retry_hint

    hint = str((llm_config or {}).get("retry_hint") or "").strip()
    prompts = PROMPT_TEMPLATES.get("entity_resolution", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")
    if not system_prompt or not human_template:
        logger.warning("entity_resolution prompts missing; keeping heuristic scores")
        return [(i1, i2, c) for i1, i2, _r1, _r2, c in borderline]

    client = LLMClient(llm_config)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def judge(
        pair: tuple[str, str, dict, dict, float],
    ) -> tuple[str, str, float, bool]:
        id1, id2, _r1, _r2, heuristic = pair
        try:
            user_prompt = human_template.replace("{entity_type}", entity_type).replace(
                "{pairs_block}", _format_pairs_block([pair])
            )
            # Set when a reviewer sent the matches gate back for rework.
            user_prompt = apply_retry_hint(
                user_prompt,
                {"retry_hints": {"resolve_entities": hint}},
                "resolve_entities",
            )
            async with semaphore:
                result = await client.call_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
        except Exception as exc:
            logger.warning("LLM judgment failed for %s vs %s (%s); heuristic kept", id1, id2, exc)
            return id1, id2, heuristic, False
        p = _probability(result)
        if p is None:
            logger.warning(
                "LLM reply for %s vs %s carries no probability in [0, 1]; heuristic kept",
                id1,
                id2,
            )
            return id1, id2, heuristic, False
        reason = str(result.get("reason") or "")
        score = p if max(p, 1 - p) >= auto_match else heuristic
        logger.debug("LLM judgment %s vs %s: p=%s -> %s (%s)", id1, id2, p, score, reason)
        if evidence_sink is not None:
            key = (id1, id2) if id1 < id2 else (id2, id1)
            evidence_sink[key] = {
                "a": id1,
                "b": id2,
                "confidence": score,
                "method": "llm",
                "matched_fields": [],
                "mismatched_fields": [],
                "reason": reason,
                "llm_probability": p,
            }
        return id1, id2, score, True

    outputs = await asyncio.gather(*(judge(pair) for pair in borderline))
    results = [(i1, i2, score) for i1, i2, score, _ok in outputs]
    judged = sum(ok for *_rest, ok in outputs)

    logger.info(
        "LLM judged %d/%d borderline pairs for entity '%s'",
        judged,
        len(borderline),
        entity_type,
    )
    return results


# ── Clustering ───────────────────────────────────────────────────────


def _assign_cluster_id(
    entity_type: str,
    member_ids: list[str],
    known_cluster_ids: dict[str, str] | None,
) -> str:
    """The id this cluster should carry in the destination.

    Reuses the id a member was given on a previous run when there is one, so
    the destination row is updated rather than duplicated. Only when no
    member has been seen before is a fresh id derived from the membership.

    Why this matters: under incremental extraction a later run sees only a
    subset of the records, so membership — and therefore any id derived
    purely from membership — legitimately changes between runs.
    """
    if known_cluster_ids:
        seen = [known_cluster_ids[rid] for rid in member_ids if rid in known_cluster_ids]
        if seen:
            # Deterministic tie-break when a cluster now spans records that
            # previously belonged to different entities: the smallest id
            # wins, so both runs agree regardless of iteration order.
            return min(seen)
    return deterministic_uuid(entity_type, *member_ids)


async def _build_clusters(
    records: list[dict[str, Any]],
    scored_pairs: list[tuple[str, str, float]],
    entity_type: str = "",
    known_cluster_ids: dict[str, str] | None = None,
    auto_match: float = _AUTO_MATCH,
    auto_exclude: float = _AUTO_EXCLUDE,
    stats: dict[str, Any] | None = None,
    pair_evidence: dict[tuple[str, str], dict[str, Any]] | None = None,
    one_to_one: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    """Build entity clusters from scored pairs (report P20).

    Connected components alone over-merges badly, so:

    1. **Only confident edges.** A pair below ``auto_match`` -- evidence the
       system flagged as "not sure" -- is never joined here (it goes to review).
       Measured cost when it was: 26 of 400 distinct people destroyed.
    2. **Weighted node pruning** (meta-blocking's own rule, as in P5): an edge
       survives when it is at least the mean weight at one of its ends. No
       hand-set confidence cut; a weak bridge goes here. Then **components**,
       and per component:
       a. **conflict repair** -- two records whose exclusive identifiers
          disagree cannot share a cluster (P6);
       b. **triangle support** (``_triangle_parts``) -- where a part has
          corroborated edges (a shared neighbour), an uncorroborated edge is a
          bridge, however strong; a part with no triangle (a chain) stays whole.
    3. ``one_to_one`` (opt-in, the caller knows the destination needs it):
       a part spanning two sources is reduced to its maximum-weight 1:1
       assignment.

    Async (BUG D.3): this loop is pure CPU-bound Python with no natural await
    point, same as the scoring loop above it — a component-dense entity type
    (v1 measured 'technology' at 1,503 records taking 9-11 minutes) froze the
    event loop for the whole run. Yields every ``_CLUSTER_YIELD_INTERVAL``
    components, same rationale and cadence style as the scoring loop's yield.

    Returns (clusters, auto_matched_count, auto_excluded_count). ``stats``,
    when given, is filled with this call's clustering profile
    (``clustering_seconds``, ``largest_component_size``, ``large_components``,
    ``component_count``) — caller-owned for the same concurrency reason as
    ``_generate_candidate_pairs``' ``stats``.

    ``pair_evidence`` (Phase 7.1), when given, is the per-pair breakdown
    ``_resolve_core``/``_judge_pairs_with_llm`` built while scoring — every
    multi-member cluster gets a ``match_evidence: {"pairs": [...]}`` key
    covering its own internal edges (looked up from ``edge_weight``'s keys,
    the same internal-edge enumeration ``_min_internal_confidence`` and
    ``_average_linkage`` already use), instead of collapsing straight to
    ``min()`` as ``match_confidence`` with everything else thrown away.
    """
    if stats is None:
        stats = {}
    all_record_ids = {_record_id(r) for r in records}
    records_by_id = {_record_id(r): r for r in records}
    # P6: the conflict repair measures exclusivity the way scoring did.
    corpus = _corpus_evidence(
        records,
        pairs=[
            (records_by_id[a], records_by_id[b])
            for a, b, _c in scored_pairs
            if a in records_by_id and b in records_by_id
        ],
    )
    auto_matched_count = 0
    auto_excluded_count = 0
    borderline_count = 0

    # Edge weights (a pair naming a record not in ``records`` is ignored).
    edge_weight: dict[tuple[str, str], float] = {}
    for id1, id2, conf in scored_pairs:
        if id1 not in records_by_id or id2 not in records_by_id or id1 == id2:
            continue
        key = (id1, id2) if id1 < id2 else (id2, id1)
        edge_weight[key] = max(edge_weight.get(key, 0.0), conf)
    confident = {k: w for k, w in edge_weight.items() if w >= auto_match}
    kept = _wnp_prune(confident)

    for _id1, _id2, conf in scored_pairs:
        if conf >= auto_match:
            auto_matched_count += 1
        elif conf < auto_exclude:
            auto_excluded_count += 1
        else:
            # Borderline: the system is NOT confident. Merging here is what
            # silently destroyed records; leave them separate and let the
            # review gate decide. Under-merging is recoverable, over-merging
            # is not.
            borderline_count += 1

    component_map = {
        component[0]: component for component in _components(sorted(all_record_ids), kept)
    }

    if borderline_count:
        logger.info(
            "%d borderline pair(s) for entity '%s' left unmerged pending "
            "review (they are surfaced at the matches gate).",
            borderline_count,
            entity_type,
        )

    clusters: list[dict[str, Any]] = []
    repaired = 0
    cut = 0
    large_components = 0
    largest_component_size = 0
    clustering_started = time.monotonic()
    for component_idx, rids in enumerate(component_map.values()):
        members = sorted(set(rids))
        largest_component_size = max(largest_component_size, len(members))
        if len(members) > _LARGE_COMPONENT_THRESHOLD:
            large_components += 1
            logger.info(
                "Clustering: component of %d members for entity '%s' exceeds "
                "the %d-member visibility threshold — _partition_component's "
                "conflict check and cohesion computation are O(n^2)/O(n^3) "
                "in component size.",
                len(members),
                entity_type,
                _LARGE_COMPONENT_THRESHOLD,
            )
        if len(members) > 1:
            parts, was_repaired = _split_component(members, kept, confident, records_by_id, corpus)
            repaired += was_repaired
            if one_to_one:
                parts = [
                    one
                    for part in parts
                    for one in _one_to_one_parts(part, confident, records_by_id)
                ]
            cut += not was_repaired and len(parts) > 1
        else:
            parts = [members]

        # Same rationale as the scoring loop's yield above: this loop is
        # pure CPU-bound Python with no natural await point, and a dense
        # entity type can spend minutes here uninterrupted (BUG D.3).
        if component_idx % 50 == 49:
            await asyncio.sleep(0)

        for part in parts:
            part_ids = sorted(part)
            min_conf = _min_internal_confidence(part_ids, edge_weight)
            cluster: dict[str, Any] = {
                # Stable across runs: a previously-assigned id is reused, and
                # only a genuinely new entity derives one from its members.
                # The id is the destination primary key, so instability here
                # means duplicate rows.
                "cluster_id": _assign_cluster_id(entity_type, part_ids, known_cluster_ids),
                "record_ids": part_ids,
                "match_confidence": round(min_conf, 3),
                "required_human_review": len(part_ids) > 1 and min_conf < 0.85,
            }
            if pair_evidence and len(part_ids) > 1:
                pairs = _evidence_for_members(part_ids, edge_weight, pair_evidence)
                if pairs:
                    cluster["match_evidence"] = {"pairs": pairs}
            clusters.append(cluster)

    if repaired or cut:
        logger.info(
            "Cluster repair for entity '%s': %d component(s) split on "
            "conflicting identifiers, %d split on structure.",
            entity_type,
            repaired,
            cut,
        )

    clustering_seconds = time.monotonic() - clustering_started
    stats["clustering_seconds"] = round(clustering_seconds, 3)
    stats["largest_component_size"] = largest_component_size
    stats["large_components"] = large_components
    stats["component_count"] = len(component_map)
    if large_components:
        logger.info(
            "Clustering profile for entity '%s': %d component(s), largest=%d "
            "member(s), %d over the %d-member threshold, %.2fs total "
            "(D.1 — see docs/verification/CLUSTERING_PROFILE.md).",
            entity_type,
            len(component_map),
            largest_component_size,
            large_components,
            _LARGE_COMPONENT_THRESHOLD,
            clustering_seconds,
        )

    return clusters, auto_matched_count, auto_excluded_count


def _split_component(
    members: list[str],
    kept: dict[tuple[str, str], float],
    support: dict[tuple[str, str], float],
    records_by_id: dict[str, dict[str, Any]],
    corpus: CorpusEvidence,
) -> tuple[list[list[str]], bool]:
    """One component's clusters (shared with the matches-gate rebuild so the
    two cannot drift): triangle support over the ``kept`` edges, a triangle
    being looked for among the ``support`` edges (all confident edges) --
    pruning a true clique's weaker edges must not make its members look like
    bridges -- then conflict repair (P6) inside each part, so one conflict
    between two bridged entities does not dissolve both. Returns (parts, repaired).

    ponytail: two strong wrong edges into one node make a real triangle, and
    names are not consulted (P20 narrowed by user decision c: name coherence
    is a P16 flag, never a cut). Upgrade: correlation clustering (report P20
    Deferred) once flagged clusters prove to be real wrong merges."""
    parts: list[list[str]] = []
    repaired = False
    for component in _components(members, kept):
        for part in _triangle_parts(component, kept, support):
            split, was_repaired, _ = _partition_component(
                part, kept, records_by_id, min_cohesion=0, corpus=corpus
            )
            repaired = repaired or bool(was_repaired)
            parts += split if was_repaired else [part]
    return parts, repaired


def _wnp_prune(edges: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    """Weighted node pruning (meta-blocking, node-centric; the rule P5 blocks
    with): keep an edge that is at least the mean weight of the edges at one
    of its two ends. The threshold is each node's own neighbourhood, never a
    hand-set confidence. ponytail: lossy by design -- a true edge under the
    mean at BOTH ends is dropped (report P20 Ceiling: recall for precision)."""
    total: dict[str, float] = {}
    degree: dict[str, int] = {}
    for pair, w in edges.items():
        for node in pair:
            total[node] = total.get(node, 0) + w
            degree[node] = degree.get(node, 0) + 1

    def at_least_mean(w: float, node: str) -> bool:
        mean = total[node] / degree[node]
        return w >= mean or math.isclose(w, mean)

    return {(a, b): w for (a, b), w in edges.items() if at_least_mean(w, a) or at_least_mean(w, b)}


def _components(members: list[str], edges: Iterable[tuple[str, str]]) -> list[list[str]]:
    """Connected components of ``members`` over the edges between them, each
    sorted, in order of their smallest member."""
    root = {m: m for m in members}

    def find(x: str) -> str:
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for a, b in edges:
        if a in root and b in root:
            ra, rb = find(a), find(b)
            if ra != rb:
                root[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = {}
    for m in sorted(members):
        groups.setdefault(find(m), []).append(m)
    return list(groups.values())


def _triangle_parts(
    members: list[str],
    edges: dict[tuple[str, str], float],
    support: dict[tuple[str, str], float],
) -> list[list[str]]:
    """Where a part has corroborated edges (their ends share a neighbour among
    the ``support`` edges inside the part), an uncorroborated edge is a bridge
    between two groups -- dropped however strong it is. A part without any
    triangle (a chain, a tree) has nothing to compare against and stays whole.

    ponytail: a true member attached to a clique by one scored edge (a
    pendant) is split off -- recoverable, and the average-linkage cut it
    replaces split it too. Upgrade: support counted over 2-hop paths."""
    inside = set(members)
    internal = [k for k in edges if k[0] in inside and k[1] in inside]
    neighbours: dict[str, set[str]] = {m: set() for m in members}
    for a, b in support:
        if a in inside and b in inside:
            neighbours[a].add(b)
            neighbours[b].add(a)
    supported = {(a, b): 0 for a, b in internal if neighbours[a] & neighbours[b]}
    if not supported or len(supported) == len(internal):
        return [members]
    return _components(members, supported)


def _one_to_one_parts(
    members: list[str],
    edges: dict[tuple[str, str], float],
    records_by_id: dict[str, dict[str, Any]],
) -> list[list[str]]:
    """Maximum-weight 1:1 assignment inside a part spanning two sources (an LP,
    solved exactly by ``scipy.optimize.linear_sum_assignment``); unmatched
    members become singletons. A part with one source per member, or with more
    than two sources, is returned as is.

    ponytail: more than two sources is not reduced (logged); scipy comes with
    the optional extras -- without it the part is left unreduced (logged)."""
    source = {m: str(records_by_id[m].get("_origin_id")) for m in members}
    sides = sorted(set(source.values()))
    if len(sides) != 2 or len(sides) == len(members):
        if len(sides) > 2:
            logger.warning("1:1 reduction skipped: %d sources in one cluster", len(sides))
        return [members]
    try:
        from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("1:1 reduction skipped: scipy is not installed")
        return [members]
    left = [m for m in members if source[m] == sides[0]]
    right = [m for m in members if source[m] == sides[1]]
    col = {m: j for j, m in enumerate(right)}
    weight: list[list[float]] = [[0] * len(right) for _ in left]
    for i, a in enumerate(left):
        for b in right:
            weight[i][col[b]] = edges.get((a, b) if a < b else (b, a), 0)
    rows, cols = linear_sum_assignment(weight, maximize=True)
    pairs = {(left[i], right[j]): 1 for i, j in zip(rows, cols, strict=True) if weight[i][j] > 0}
    return _components(members, pairs)


def _min_internal_confidence(
    members: list[str], edge_weight: dict[tuple[str, str], float]
) -> float:
    """Weakest edge inside a cluster, or 1.0 for a singleton."""
    if len(members) < 2:
        return 1.0
    weights = [
        edge_weight[(a, b) if a < b else (b, a)]
        for i, a in enumerate(members)
        for b in members[i + 1 :]
        if ((a, b) if a < b else (b, a)) in edge_weight
    ]
    return min(weights) if weights else 1.0


def _evidence_for_members(
    members: list[str],
    edge_weight: dict[tuple[str, str], float],
    pair_evidence: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every internal edge of a finished cluster with recorded evidence.

    Same pairwise enumeration as ``_average_linkage``/``_min_internal_confidence``
    (an edge exists here only when ``edge_weight`` has it — i.e. the pair was
    actually scored, not merely co-members via a chain). A pair scored but with
    no ``pair_evidence`` entry (heuristic-only, no probabilistic model fit) is
    silently skipped rather than padded with an empty placeholder — nothing
    false to show beats a confident-looking blank.
    """
    result: list[dict[str, Any]] = []
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            key = (a, b) if a < b else (b, a)
            if key in edge_weight and key in pair_evidence:
                result.append(pair_evidence[key])
    return result


def _partition_component(
    members: list[str],
    edge_weight: dict[tuple[str, str], float],
    records_by_id: dict[str, dict[str, Any]],
    min_cohesion: float = _MIN_COHESION,
    corpus: CorpusEvidence | None = None,
) -> tuple[list[list[str]], int, int]:
    """Split one connected component into defensible clusters.

    ``corpus`` (P6): when given, a conflict is one on a measured exclusive
    field with non-generic values (``_conflicting_exclusives``), the same
    test scoring used; without it, the identity hints decide (P15's gate
    rebuild sees only the band's records, too few to measure sharing).

    ``min_cohesion`` is ``_MIN_COHESION`` unless the match bar sits below it
    (P15: an operator-committed bar): a pair the bar itself admits must not be
    cut again by a stricter cohesion bar.

    Returns (partitions, repaired_flag, cut_flag).
    """
    # ── Repair: conflicting unique identifiers can't share a cluster ──
    # BUG D.2: `{k: v for k, v in rec.items() if not k.startswith("_")}` used
    # to be rebuilt for every (a, b) pair inside the O(n^2) loop below — for
    # member `a`, that is once per every other member in the component
    # instead of once total. Hoisted to a single pass over `members` first;
    # semantics are unchanged, this is a pure constant-factor win.
    fields_by_id = {
        m: {k: v for k, v in records_by_id.get(m, {}).items() if not k.startswith("_")}
        for m in members
    }
    conflict = False
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            if (
                _conflicting_exclusives(records_by_id.get(a, {}), records_by_id.get(b, {}), corpus)
                if corpus is not None
                else _has_conflicting_identity(fields_by_id[a], fields_by_id[b])
            ):
                conflict = True
                break
        if conflict:
            break

    if conflict:
        # The component provably mixes distinct entities. Rather than guess
        # the correct partition, separate every member — leaving duplicates
        # is recoverable, and the matches gate can re-merge deliberately.
        return [[m] for m in members], 1, 0

    # ── Cohesion cut: break weak chains ──────────────────────────────
    if min_cohesion <= 0 or _average_linkage(members, edge_weight) >= min_cohesion:
        return [members], 0, 0

    return _split_weakest_link(members, edge_weight, min_cohesion), 0, 1


def _average_linkage(members: list[str], edge_weight: dict[tuple[str, str], float]) -> float:
    """Mean similarity over all member pairs, missing edges counting as 0.

    A chain A-B-C has no A-C edge, so its average is dragged down — which is
    exactly the signal that it is a chain rather than a genuine group.
    """
    if len(members) < 2:
        return 1.0
    total = 0.0
    count = 0
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            total += edge_weight.get((a, b) if a < b else (b, a), 0.0)
            count += 1
    return total / count if count else 0.0


def _split_weakest_link(
    members: list[str],
    edge_weight: dict[tuple[str, str], float],
    min_cohesion: float = _MIN_COHESION,
) -> list[list[str]]:
    """Drop the weakest edges and re-derive components, recursing as needed."""
    edges = sorted(
        (
            (edge_weight.get((a, b) if a < b else (b, a), 0.0), a, b)
            for i, a in enumerate(members)
            for b in members[i + 1 :]
            if ((a, b) if a < b else (b, a)) in edge_weight
        ),
        reverse=True,
    )
    if not edges:
        return [[m] for m in members]

    # Keep only the strongest edges, then see what components remain.
    strongest = edges[0][0]
    keep = [e for e in edges if e[0] >= strongest - 1e-9]

    parent = {m: m for m in members}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _w, a, b in keep:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    groups: dict[str, list[str]] = {}
    for m in members:
        groups.setdefault(find(m), []).append(m)

    parts = list(groups.values())
    if len(parts) == 1:
        # Every edge was equally strong yet cohesion is still too low —
        # a fully-connected-but-weak group. Separate them.
        return [[m] for m in members]

    result: list[list[str]] = []
    for part in parts:
        if len(part) > 1 and _average_linkage(part, edge_weight) < min_cohesion:
            result.extend(_split_weakest_link(part, edge_weight, min_cohesion))
        else:
            result.append(part)
    return result


def _record_id(rec: dict[str, Any]) -> str:
    """Extract a unique record identifier from a record dict."""
    origin = rec.get("_origin_id", rec.get("origin_id", "unknown"))
    table = rec.get("_origin_table", rec.get("table", "unknown"))
    row_id = rec.get("_origin_id_row", rec.get("id", str(id(rec))))
    return f"{origin}:{table}:{row_id}"
