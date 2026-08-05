"""
resolve_entities tool implementation.

Given records from multiple sources for a given entity type, clusters
matching ones with a hybrid pipeline:

1. Blocking (cheap): candidate pairs are only generated within blocks
   sharing a normalized first name-token, so comparisons stay far below
   the O(n^2) all-pairs count. ``_MAX_COMPARISONS`` is enforced.
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
import re
import time
from functools import lru_cache
from typing import Any

from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools.fellegi_sunter import (
    LEVEL_DISAGREE,
    LEVEL_EXACT,
    LEVEL_NULL,
    LEVEL_STRONG,
    LEVEL_WEAK,
)
from src.utils.id_utils import deterministic_uuid
from src.utils.tracing import span

logger = logging.getLogger("merge-engine.tools.resolve_entities")


# Maximum pairwise comparisons per call (safety limit)
# How many candidate pairs may be scored for one entity. This is a runaway
# guard, not a tuning knob: every pair it excludes is evidence the matcher
# never saw, so it should bind rarely.
#
# It was 10,000, which a 2,484-record merge already exceeded (28,170 pairs) —
# so ordinary runs were silently dropping two thirds of their evidence, and
# two runs of identical data could disagree. That budget existed because
# scoring cost ~2ms/pair; memoising the string comparators took it to
# ~0.085ms, so 250,000 pairs now costs about 20 seconds rather than eight
# minutes. Overridable per run via extraction_config["max_comparisons"].
_MAX_COMPARISONS = 250_000

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

# A ref block bigger than this is a non-discriminative key (e.g. a shared
# prefix across a whole origin) — skip it rather than flood comparisons.
_MAX_REF_BLOCK = 25

# At most this many borderline pairs are LLM-judged per entity type (closest
# to auto-match first); the rest keep their heuristic score.
_MAX_LLM_PAIRS = 200

# Pairs per LLM call — keeps each prompt well inside the context window.
_LLM_CHUNK_SIZE = 40

# Concurrent LLM judgment calls (chunks run in parallel up to this limit).
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
) -> dict[str, Any]:
    """The CPU-bound heart of resolve_entities_impl: blocking, model fit, and
    scoring — no LLM calls, no clustering. Synchronous and module-level, and
    every argument and return value is plain data (str/list/dict/tuple), so
    it can run either inline or dispatched to a ProcessPoolExecutor worker
    (Part 1.5 B.3) with no other change.

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

    # Part 1.5 B.3: blocking/fit_model/scoring used to be 3 separate async
    # Langfuse spans (C.4); collapsed into this one synchronous function,
    # they can only ever be one span at the call site. Timed here instead,
    # so the phase breakdown is still visible as that span's attributes
    # rather than as separate child-span bars.
    t_blocking_start = time.monotonic()
    blocking_stats: dict[str, int] = {}
    candidate_pairs = _generate_candidate_pairs(records, blocking_keys, stats=blocking_stats)
    t_fit_start = time.monotonic()

    record_cache: RecordCache = {}
    match_model, fitted_vectors = _fit_match_model(
        records, candidate_pairs, unique_ids, normalise, record_cache
    )
    t_scoring_start = time.monotonic()

    scored_pairs: list[tuple[str, str, float]] = []
    borderline: list[tuple[str, str, dict, dict, float]] = []
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
        )
        if auto_exclude < conf < auto_match:
            borderline.append((id1, id2, rec1, rec2, conf))
        else:
            scored_pairs.append((id1, id2, conf))
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
                process_pool, _resolve_core, entity_type, records, blocking_keys, rules, subject
            )
        else:
            core = _resolve_core(entity_type, records, blocking_keys, rules, subject)
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
            # Judge the pairs closest to auto-match; the rest keep their
            # heuristic score (they stay in the review band).
            borderline.sort(key=lambda p: p[4], reverse=True)
            overflow = borderline[_MAX_LLM_PAIRS:]
            borderline = borderline[:_MAX_LLM_PAIRS]
            logger.warning(
                "%d borderline pairs for entity '%s' exceed the LLM budget "
                "(%d); keeping heuristic scores for the %d lowest-confidence "
                "pairs",
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
        llm_results = await _judge_pairs_with_llm(entity_type, borderline, llm_config)
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
    }
    # Part 1.5 D.7: cached cluster_id values are meaningless (they were
    # derived from THIS run's known_cluster_ids) — apply_known_cluster_ids
    # re-derives them from scratch on every read, so caching them verbatim
    # here is harmless, not just convenient.
    resolve_cache.put(cache_key, entity_type, result)
    return result


def _pending_merge_candidates(
    scored_pairs: list[tuple[str, str, float]],
    auto_match: float = _AUTO_MATCH,
    auto_exclude: float = _AUTO_EXCLUDE,
) -> list[dict[str, Any]]:
    """Uncertain pairs the system declined to merge, strongest first."""
    undecided = [
        {"record_ids": [id1, id2], "confidence": round(conf, 3)}
        for id1, id2, conf in scored_pairs
        if auto_exclude < conf < auto_match
    ]
    undecided.sort(key=lambda p: -p["confidence"])
    return undecided[:_MAX_PENDING_CANDIDATES]


# ── Blocking ─────────────────────────────────────────────────────────


def _pick_blocking_field(records: list[dict[str, Any]]) -> str | None:
    """Pick the best name-ish field present in most records."""
    field_counts: dict[str, int] = {}
    for rec in records:
        for k, v in rec.items():
            if k.startswith("_") or v is None:
                continue
            if isinstance(v, str) and v.strip():
                field_counts[k] = field_counts.get(k, 0) + 1

    if not field_counts:
        return None

    def rank(field: str) -> tuple[int, int]:
        f = field.lower()
        # Lower hint index = better; unmatched fields rank last
        for i, hint in enumerate(_NAME_FIELD_HINTS):
            if hint in f:
                return (i, -field_counts[field])
        return (len(_NAME_FIELD_HINTS), -field_counts[field])

    return min(field_counts, key=rank)


def _block_key(rec: dict[str, Any], field: str) -> str:
    """Normalized first token of the blocking field ('' if unavailable)."""
    value = rec.get(field)
    if value is None:
        return ""
    tokens = re.findall(r"[a-z0-9]+", str(value).lower())
    return tokens[0] if tokens else ""


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


def _ref_fields(records: list[dict[str, Any]]) -> list[str]:
    """Identifier-like string fields present anywhere in the record set."""
    seen: set[str] = set()
    for rec in records:
        for k, v in rec.items():
            if k.startswith("_") or not isinstance(v, str) or not v.strip():
                continue
            kl = k.lower()
            if any(h in kl for h in _REF_FIELD_HINTS):
                seen.add(k)
    return sorted(seen)


def _phonetic_key(value: str) -> str:
    """A rough phonetic code, so spelling variants land in the same block.

    A compact Soundex: keep the first letter, map the rest to consonant
    classes, drop vowels and repeats. Catches Smith/Smyth and Katherine/
    Catherine — the variants exact-match blocking misses entirely. Not as
    accurate as Double Metaphone, but it needs no extra dependency and
    blocking only has to be generous, not correct: scoring decides.
    """
    text = re.sub(r"[^a-z]", "", str(value).lower())
    if not text:
        return ""

    codes = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        **dict.fromkeys("l", "4"),
        **dict.fromkeys("mn", "5"),
        **dict.fromkeys("r", "6"),
    }
    first = text[0]
    encoded = []
    previous = codes.get(first, "")
    for char in text[1:]:
        code = codes.get(char, "")
        if code and code != previous:
            encoded.append(code)
        if char not in "hw":
            previous = code
    return (first + "".join(encoded) + "000")[:4]


def _blocking_keys_for(rec: dict[str, Any], fields: list[str]) -> set[str]:
    """Every block a record belongs to, across all blocking rules.

    Multiple independent rules, unioned. One rule missing a pair (a typo in
    the surname, say) no longer means the pair is never compared — another
    rule still catches it. This replaces single-key blocking, where one bad
    character silently excluded a record from consideration entirely.

    Keys deliberately do NOT include the field name: origins name the same
    concept differently (``company_name`` vs ``org_title``), and a key tied
    to the field name would drop every cross-origin pair. Value collisions
    across unrelated fields are harmless — blocking only proposes pairs,
    scoring decides.

    Keys derived from identifier-like fields are prefixed ``ref:`` so the
    caller can purge over-large identifier blocks (a code shared by hundreds
    of records is not evidence) without touching name blocks.
    """
    keys: set[str] = set()
    for field_name in fields:
        value = rec.get(field_name)
        if value is None or not str(value).strip():
            continue
        text = str(value).strip().lower()
        norm = re.sub(r"[^a-z0-9]", "", text)
        if not norm:
            continue

        field_lower = field_name.lower()

        # Rule 0 (BUG C / C2): registrable-domain key for website/URL-shaped
        # fields. `_domain_of` already normalises "https://www.boeing.com/
        # careers" and "boeing.com" to the same value — the deterministic
        # match path (`_deterministic_match`) already trusts it completely.
        # Without this, two records sharing a domain never even get proposed
        # as a candidate pair unless their (possibly differently-spelled)
        # name fields also co-block. "full:" marks it a real identity key,
        # never subject to size-based deprioritisation.
        if any(h in field_lower for h in _DOMAIN_FIELD_HINTS):
            dom = _domain_of(value)
            if dom:
                keys.add(f"full:dom:{dom}")
            continue

        is_ref = any(h in field_lower for h in _REF_FIELD_HINTS)
        # An exact match on a COMPLETE value (full name, email, tax id)
        # identifies a record. An exact match on PART of a name does not —
        # a shared surname is a hint, and a block of every Smith in a
        # million-row table is 50 billion pairs of almost no value. Partial
        # names therefore get approximate keys, subject to purging.
        is_partial_name = field_lower in _PARTIAL_NAME_FIELDS
        prefix = "ref:" if is_ref else ("part:" if is_partial_name else "")

        # Rule 1: whole normalised value (exact agreement).
        keys.add(f"{prefix}full:{norm}")
        # Rule 3: first token (the original single-key behaviour).
        tokens = re.findall(r"[a-z0-9]+", text)
        if tokens:
            keys.add(f"{prefix}tok:{tokens[0]}")

        if is_ref:
            continue

        # Rule 2: phonetic code — catches Smith/Smyth, Katherine/Catherine,
        # which exact-match blocking misses entirely.
        phonetic = _phonetic_key(text)
        if phonetic:
            keys.add(f"snd:{phonetic}")
        # Rule 4: for emails, the local part alone — the same mailbox
        # recorded against different domains still points at one person.
        if "@" in text:
            local = re.sub(r"[^a-z0-9]", "", text.split("@", 1)[0])
            if local:
                keys.add(f"eml:{local}")
    return keys


def _blockable_fields(records: list[dict[str, Any]]) -> list[str]:
    """String fields worth blocking on: names, identifiers, and domains."""
    hints = _NAME_FIELD_HINTS + _REF_FIELD_HINTS + _UNIQUE_IDENTITY_HINTS + _DOMAIN_FIELD_HINTS
    found: set[str] = set()
    for rec in records[:200]:
        for key, value in rec.items():
            if key.startswith("_") or not isinstance(value, str) or not value.strip():
                continue
            kl = key.lower()
            if any(h in kl for h in hints) or kl in _PARTIAL_NAME_FIELDS:
                found.add(key)
    return sorted(found)


def _generate_candidate_pairs(
    records: list[dict[str, Any]],
    blocking_keys: list[str] | None = None,
    stats: dict[str, int] | None = None,
) -> list[tuple[str, str, dict, dict]]:
    """Generate candidate record pairs via multi-pass blocking.

    Pass 1 blocks on the normalized first token of each record's own best
    name-ish field (falling back to explicit ``blocking_keys`` when given);
    pass 2 blocks on identifier-like fields (``*_ref``, ``*code*``, ...) so
    records whose names differ but share a business reference are still
    compared. The union of intra-block pairs is scored. Records with no
    usable key land in a shared fallback block so missing data never
    silently excludes a record. Enforces ``_MAX_COMPARISONS``.

    ``stats``, when given, is filled with this call's blocking outcome
    (``uncompared_pairs``, ``candidate_pairs``, ``blocks_used``,
    ``blocks_purged``). It belongs to the caller: these counts used to live in
    a module-level dict, so two concurrent entity resolutions each reported
    the other's numbers (Phase 0 Sec 0.8).
    """
    if stats is None:
        stats = {}
    explicit = [f for f in (blocking_keys or []) if f]
    fields = explicit or _blockable_fields(records)
    if not fields:
        fields = [f for f in (_best_name_field(records[0]) or "",) if f]

    # Build every block, from every rule, in one pass.
    blocks: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        keys = _blocking_keys_for(rec, fields)
        if not keys:
            # No usable key at all — a shared fallback block, so missing data
            # never silently excludes a record from consideration entirely.
            keys = {"__nokey__"}
        for key in keys:
            blocks.setdefault(key, []).append(rec)

    # Non-discriminative blocks (BUG C / C1): a phonetic code, a first token,
    # or a reference prefix shared by dozens of records is weak evidence —
    # but a purge must degrade, never annihilate. These blocks used to be
    # dropped from `usable` entirely, which is how a scoped merge (every
    # record shares the subject token, so the one block that holds
    # everything exceeds the old threshold) went from 465 candidate pairs to
    # exactly zero while still reporting `uncompared_pairs=0` — the one
    # honesty signal the whole design depends on, silently lying. Now every
    # block — however non-discriminative — is admitted into `usable` and
    # left to the size-sorted budget below: smaller, more discriminating
    # blocks are scored first (see the sort key a few lines down), and only
    # once the budget is exhausted does a large block fall back to the
    # cheap overflow *estimate* instead of being scored — the same honest
    # degradation `_MAX_COMPARISONS` already provides. "full:" (an exact
    # match on a complete identifying value) is real evidence at any size
    # and was never purged; everything else now shares its fate instead of
    # vanishing.
    usable: dict[str, list[dict[str, Any]]] = {}
    purged: list[tuple[str, int]] = []
    for key, members in blocks.items():
        if len(members) < 2:
            continue
        approximate = not key.startswith("full:")
        if approximate and len(members) > _MAX_REF_BLOCK:
            purged.append((key, len(members)))
        usable[key] = members
    if purged:
        purged.sort(key=lambda kv: -kv[1])
        logger.info(
            "Blocking: %d non-discriminative block(s) over %d records "
            "(largest: %s) — still admitted for scoring, budget-limited "
            "rather than dropped.",
            len(purged),
            _MAX_REF_BLOCK,
            ", ".join(f"{k}={n}" for k, n in purged[:3]),
        )

    logger.debug(
        "Blocking on %s: %d records -> %d usable blocks (of %d)",
        fields,
        len(records),
        len(usable),
        len(blocks),
    )

    pairs: list[tuple[str, str, dict, dict]] = []
    seen_pairs: set[tuple[str, str]] = set()
    # Smallest blocks first: they are the most discriminating, so if the
    # budget does run out it is the weakest evidence that gets dropped.
    # The block key breaks size ties, so two runs over the same records
    # always compare the same pairs in the same order — without it, equal
    # sized blocks fell back on dict insertion order and a truncated run
    # was not reproducible.
    #
    # Once the budget is already met from smaller blocks, a later (larger)
    # block is *never* going to be scored — but a "full:" exact-match block
    # is never purged regardless of size (see above), so a single block of
    # thousands of records sharing one value (e.g. many "experience" rows
    # with the same company+title) used to still get fully expanded into
    # O(n^2) (id, id, dict, dict) tuples just to be sliced off at the end.
    # On a live 34,782-row run this alone took the process from a ~11-minute
    # entity to one that never finished materializing pairs. Once the budget
    # is met, remaining blocks are only counted (cheap combinatorics), never
    # expanded.
    overflow_pairs_estimate = 0
    budget_reached = False
    for _key, block_records in sorted(usable.items(), key=lambda kv: (len(kv[1]), kv[0])):
        n = len(block_records)
        if budget_reached:
            overflow_pairs_estimate += n * (n - 1) // 2
            continue
        # Part 1.5 A.4: _record_id(rec) is a pure function of one record (3
        # dict.get calls + an f-string build) but was being recomputed on
        # every (i, j) pair — id1 alone was rebuilt n-i-1 times for a fixed
        # i. Hoisted to one pass over the block before the O(n^2) loop; this
        # is the exact same defect BUG D.2 already fixed in
        # _partition_component, just never applied here.
        ids = [_record_id(r) for r in block_records]
        pairs_before_this_block = len(pairs)
        for i in range(n):
            if budget_reached:
                break
            for j in range(i + 1, n):
                id1 = ids[i]
                id2 = ids[j]
                if id1 == id2:
                    continue
                pair_key = (id1, id2) if id1 < id2 else (id2, id1)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                pairs.append((id1, id2, block_records[i], block_records[j]))
                if len(pairs) >= _MAX_COMPARISONS:
                    # Part 1.5 A.6: this check used to sit AFTER the whole
                    # block finished expanding (outside both loops), so the
                    # ONE block being processed when the budget was hit
                    # could still grow unbounded before ever being trimmed —
                    # a purge-exempt "full:dom:" block covering a whole
                    # scoped corpus (e.g. 34,782 records sharing one
                    # domain) would fully materialize n(n-1)/2 four-tuples
                    # first. Checked here instead, so a block is cut off
                    # mid-expansion the moment the budget is met.
                    #
                    # This provably selects the SAME pairs as before: the
                    # final result was always `pairs[:_MAX_COMPARISONS]`,
                    # a positional slice in block-then-(i,j) insertion
                    # order — stopping insertion at that exact position
                    # instead of over-inserting-then-slicing produces an
                    # identical list, just without the wasted allocation.
                    budget_reached = True
                    break
        if budget_reached:
            # The block's remaining un-enumerated pairs, folded into the
            # same honest overflow estimate a block skipped entirely above
            # already uses (same "lower bound, not exact" caveat below —
            # this doesn't dedupe against `seen_pairs` either).
            expanded_this_block = len(pairs) - pairs_before_this_block
            overflow_pairs_estimate += max(n * (n - 1) // 2 - expanded_this_block, 0)

    if len(pairs) > _MAX_COMPARISONS or overflow_pairs_estimate:
        # Previously this silently dropped records mid-loop and logged a
        # warning nobody saw. Now it is a reported, quantified outcome —
        # see _uncompared_pairs in the reconciliation report. Once overflow
        # blocks are only estimated (not deduped against what was already
        # scored), the count is a lower bound, not exact — worth knowing
        # either way, so still reported.
        uncompared = max(len(pairs) - _MAX_COMPARISONS, 0) + overflow_pairs_estimate
        logger.warning(
            "Blocking produced at least %d candidate pairs, above the %d "
            "comparison budget. Scoring the %d most discriminating; at "
            "least %d pair(s) were NOT compared and are reported in the "
            "reconciliation report.",
            len(pairs) + overflow_pairs_estimate,
            _MAX_COMPARISONS,
            _MAX_COMPARISONS,
            uncompared,
        )
        stats["uncompared_pairs"] = uncompared
        pairs = pairs[:_MAX_COMPARISONS]
    else:
        stats["uncompared_pairs"] = 0
    stats["candidate_pairs"] = len(pairs)
    stats["blocks_used"] = len(usable)
    stats["blocks_purged"] = len(purged)

    logger.debug("_generate_candidate_pairs: generated %d pairs", len(pairs))
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
            build_term_frequencies,
            estimate_model,
        )

        vectors = [
            _build_comparison_vector(rec1, rec2, normalise, record_cache)
            for _id1, _id2, rec1, rec2 in candidate_pairs
        ]
        model = estimate_model(
            vectors,
            build_term_frequencies(records, normalise),
            unique_id_fields=_unique_id_fields(records) | (extra_unique_ids or set()),
        )
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
    """
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}
    conflicting = _has_conflicting_identity(fields1, fields2, extra_unique_ids) or (
        bool(deterministic_fields) and _deterministic_conflict(rec1, rec2, deterministic_fields)
    )

    if (
        deterministic_fields
        and not conflicting
        and _deterministic_match(rec1, rec2, deterministic_fields)
    ):
        return 1.0

    heuristic = _compute_heuristic_confidence(rec1, rec2, extra_unique_ids, record_cache)
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
            else _build_comparison_vector(rec1, rec2, normalise, record_cache)
        )
        probability = model.probability(vector)
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
    vector = _build_comparison_vector(rec1, rec2, normalise, record_cache)
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
    v1: Any, v2: Any, normalise: Any = _normalize, field_name: str = ""
) -> tuple[str, str | None]:
    """Classify how two field values agree, for the probabilistic model.

    Returns (level, normalised_value). The value is carried so an exact
    agreement can be weighted by how rare that value actually is — using
    whichever ``normalise`` function fed the term-frequency table, so the
    two line up (see ``_subject_stripped_normalize`` for the scoped case).

    Type-aware (BUG C / C0c): domain-shaped fields compare on registrable
    domain via ``_domain_of``, so ``https://www.boeing.com/careers`` and
    ``boeing.com`` register as LEVEL_EXACT instead of LEVEL_WEAK.
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
    return {
        field: _comparison_level(proj1.get(field), proj2.get(field), normalise, field_name=field)
        for field in set(proj1) & set(proj2)
    }


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


def _compute_heuristic_confidence(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    extra_unique_ids: set[str] | None = None,
    record_cache: RecordCache | None = None,
) -> float:
    """Compute a match confidence based on field comparison.

    Projects both records onto the canonical schema first (BUG B / C0a),
    so cross-origin pairs with different raw field names still share a
    comparison intersection. Surrogate keys are excluded (C0b). Domain
    fields compare on registrable domain (C0c).

    ``record_cache`` (Part 1.5 A.1) memoizes the per-record projection —
    see ``_cached_projection``.

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
                sim = _string_similarity(s1, s2)
                if sim > 0.5:
                    if name_needs_cap:
                        sim = min(sim, _FUZZY_NAME_CAP)
                    score += sim

    field_score = score / total if total > 0 else 0.0
    confidence = max(field_score, _cross_name_similarity(rec1, rec2, record_cache))

    # Use original records (not projections) for identity conflict check,
    # since _has_conflicting_identity needs the raw fields.
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}
    if _has_conflicting_identity(fields1, fields2, extra_unique_ids):
        # Never auto-merge over a contradicting unique id — route it to review.
        return min(confidence, 0.93)
    return confidence


def _has_conflicting_identity(
    fields1: dict[str, Any],
    fields2: dict[str, Any],
    extra_unique_ids: set[str] | None = None,
) -> bool:
    """True when both records carry a unique identifier and they disagree.

    ``extra_unique_ids`` names columns the operator declared unique for this
    entity (Phase O.2) — a customer_no or member_id that no generic hint
    would recognise, but which settles identity just as firmly as an email.
    """
    declared = extra_unique_ids or set()
    for key1, val1 in fields1.items():
        kl = key1.lower()
        if key1 not in declared and not any(hint in kl for hint in _UNIQUE_IDENTITY_HINTS):
            continue
        val2 = fields2.get(key1)
        if val1 is None or val2 is None:
            continue
        if _normalize(val1) != _normalize(val2):
            return True
    return False


def _cross_name_similarity(
    rec1: dict[str, Any], rec2: dict[str, Any], record_cache: RecordCache | None = None
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
    sim = _string_similarity(s1, s2)
    return min(sim, 0.93) if sim > 0.5 else 0.0


# ── LLM judgment for borderline pairs ────────────────────────────────


def _format_pairs_block(
    borderline: list[tuple[str, str, dict, dict, float]],
) -> str:
    """Format numbered pairs (non-null public fields only) for the prompt."""
    lines: list[str] = []
    for n, (_id1, _id2, rec1, rec2, _conf) in enumerate(borderline, 1):
        f1 = {k: v for k, v in rec1.items() if not k.startswith("_") and v is not None}
        f2 = {k: v for k, v in rec2.items() if not k.startswith("_") and v is not None}
        lines.append(f"Pair {n}:")
        lines.append(f"  Record A: {json.dumps(f1, ensure_ascii=False, default=str)}")
        lines.append(f"  Record B: {json.dumps(f2, ensure_ascii=False, default=str)}")
    return "\n".join(lines)


async def _judge_pairs_with_llm(
    entity_type: str,
    borderline: list[tuple[str, str, dict, dict, float]],
    llm_config: dict[str, Any],
) -> list[tuple[str, str, float]]:
    """Judge borderline pairs in chunked batched LLM calls.

    Pairs are sent ``_LLM_CHUNK_SIZE`` at a time so each prompt stays well
    inside the model's context window. Returns (id1, id2, confidence) per
    pair; LLM match confidence maps to a clustering score (match ->
    confidence, non-match -> 1 - confidence capped below the union
    threshold). On any failure the affected chunk keeps its heuristic
    scores so the pipeline never blocks on the LLM.
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
    chunks = [
        borderline[start : start + _LLM_CHUNK_SIZE]
        for start in range(0, len(borderline), _LLM_CHUNK_SIZE)
    ]
    semaphore = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def judge_chunk(
        chunk: list[tuple[str, str, dict, dict, float]], offset: int
    ) -> tuple[list[tuple[str, str, float]], int]:
        chunk_results: list[tuple[str, str, float]] = []
        chunk_judged = 0
        try:
            user_prompt = (
                human_template.replace("{entity_type}", entity_type)
                .replace("{pair_count}", str(len(chunk)))
                .replace("{pairs_block}", _format_pairs_block(chunk))
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
            judgments = result.get("judgments", []) if isinstance(result, dict) else []

            by_pair_id: dict[int, dict[str, Any]] = {}
            for j in judgments:
                if isinstance(j, dict) and isinstance(j.get("pair_id"), int):
                    by_pair_id[j["pair_id"]] = j

            for n, (id1, id2, _r1, _r2, heuristic_conf) in enumerate(chunk, 1):
                j = by_pair_id.get(n)
                if j is None:
                    chunk_results.append((id1, id2, heuristic_conf))
                    continue
                chunk_judged += 1
                try:
                    llm_conf = float(j.get("confidence", 0.5))
                except (TypeError, ValueError):
                    llm_conf = 0.5
                llm_conf = max(0.0, min(1.0, llm_conf))
                if j.get("match"):
                    # Confident LLM "match" can auto-union; unsure ones stay
                    # in the review band.
                    score = max(heuristic_conf, llm_conf)
                else:
                    # "Not a match": push below the union threshold, more
                    # decisively the more confident the LLM is.
                    score = min(heuristic_conf, 1.0 - llm_conf, _AUTO_EXCLUDE)
                logger.debug(
                    "LLM judgment pair %d (%s vs %s): match=%s conf=%.2f -> score=%.2f (%s)",
                    offset + n,
                    id1,
                    id2,
                    j.get("match"),
                    llm_conf,
                    score,
                    str(j.get("reason", ""))[:120],
                )
                chunk_results.append((id1, id2, round(score, 3)))

        except Exception as exc:
            logger.warning(
                "LLM entity-resolution chunk failed (%s); keeping heuristic "
                "scores for %d pairs in this chunk",
                exc,
                len(chunk),
            )
            chunk_results = [(i1, i2, c) for i1, i2, _r1, _r2, c in chunk]
        return chunk_results, chunk_judged

    chunk_outputs = await asyncio.gather(
        *(judge_chunk(chunk, i * _LLM_CHUNK_SIZE) for i, chunk in enumerate(chunks))
    )

    results: list[tuple[str, str, float]] = []
    judged = 0
    for chunk_results, chunk_judged in chunk_outputs:
        results.extend(chunk_results)
        judged += chunk_judged

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
) -> tuple[list[dict[str, Any]], int, int]:
    """Build entity clusters from scored pairs.

    Three stages, because connected components alone over-merges badly:

    1. **Union only confident edges.** Previously any pair above the exclude
       threshold was unioned, so a borderline pair — evidence the system had
       explicitly flagged as "not sure" — merged with no review anyway.
       Measured cost on real data: 26 of 400 distinct people destroyed.
    2. **Conflict repair.** Split any component containing two records whose
       unique identifiers (email/tax_id/...) disagree. They cannot be the same
       entity however similar their names.
    3. **Cohesion cut.** Average-linkage inside each surviving component, cut
       below ``_MIN_COHESION``, so a weak chain A-B-C breaks instead of
       merging three entities on two mediocre edges.

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
    """
    if stats is None:
        stats = {}
    all_record_ids = {_record_id(r) for r in records}
    records_by_id = {_record_id(r): r for r in records}
    parent: dict[str, str] = {rid: rid for rid in all_record_ids}
    rank: dict[str, int] = {rid: 0 for rid in all_record_ids}

    auto_matched_count = 0
    auto_excluded_count = 0
    borderline_count = 0

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # Path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    # Edge weights, needed by the cohesion cut below.
    edge_weight: dict[tuple[str, str], float] = {}
    for id1, id2, conf in scored_pairs:
        key = (id1, id2) if id1 < id2 else (id2, id1)
        edge_weight[key] = max(edge_weight.get(key, 0.0), conf)

    for id1, id2, conf in scored_pairs:
        if conf >= auto_match:
            union(id1, id2)
            auto_matched_count += 1
        elif conf <= auto_exclude:
            auto_excluded_count += 1
        else:
            # Borderline: the system is NOT confident. Merging here is what
            # silently destroyed records; leave them separate and let the
            # review gate decide. Under-merging is recoverable, over-merging
            # is not.
            borderline_count += 1

    component_map: dict[str, list[str]] = {}
    for rid in all_record_ids:
        component_map.setdefault(find(rid), []).append(rid)

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
            parts, was_repaired, was_cut = _partition_component(members, edge_weight, records_by_id)
            repaired += was_repaired
            cut += was_cut
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
            clusters.append(
                {
                    # Stable across runs: a previously-assigned id is reused, and
                    # only a genuinely new entity derives one from its members.
                    # The id is the destination primary key, so instability here
                    # means duplicate rows.
                    "cluster_id": _assign_cluster_id(entity_type, part_ids, known_cluster_ids),
                    "record_ids": part_ids,
                    "match_confidence": round(min_conf, 3),
                    "required_human_review": len(part_ids) > 1 and min_conf < 0.85,
                }
            )

    if repaired or cut:
        logger.info(
            "Cluster repair for entity '%s': %d component(s) split on "
            "conflicting identifiers, %d split by cohesion cut.",
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


def _partition_component(
    members: list[str],
    edge_weight: dict[tuple[str, str], float],
    records_by_id: dict[str, dict[str, Any]],
) -> tuple[list[list[str]], int, int]:
    """Split one connected component into defensible clusters.

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
            if _has_conflicting_identity(fields_by_id[a], fields_by_id[b]):
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
    if _average_linkage(members, edge_weight) >= _MIN_COHESION:
        return [members], 0, 0

    return _split_weakest_link(members, edge_weight), 0, 1


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
    members: list[str], edge_weight: dict[tuple[str, str], float]
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
        if len(part) > 1 and _average_linkage(part, edge_weight) < _MIN_COHESION:
            result.extend(_split_weakest_link(part, edge_weight))
        else:
            result.append(part)
    return result


def _record_id(rec: dict[str, Any]) -> str:
    """Extract a unique record identifier from a record dict."""
    origin = rec.get("_origin_id", rec.get("origin_id", "unknown"))
    table = rec.get("_origin_table", rec.get("table", "unknown"))
    row_id = rec.get("_origin_id_row", rec.get("id", str(id(rec))))
    return f"{origin}:{table}:{row_id}"
