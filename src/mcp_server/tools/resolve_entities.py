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
from functools import lru_cache
from typing import Any

from src.utils.id_utils import deterministic_uuid

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
    "name", "title", "full_name", "name_full", "company", "organization",
    "org", "label", "email",
)

# Field-name hints for identifier-like fields used as a second blocking pass
# (catches cross-origin pairs whose names differ but share a business ref).
# Deliberately excludes "number": it matches phone fields (tel_number), whose
# first token (country code) lumps entire origins into one giant block.
_REF_FIELD_HINTS = (
    "ref", "code", "external", "uid", "guid", "registration",
)

# Fields holding only *part* of a person's name. They match the "name" hint by
# substring, but two people sharing a first name are not the same person, so
# they must never drive the deterministic (auto-merge) name path.
_PARTIAL_NAME_FIELDS = frozenset({
    "first_name", "last_name", "given_name", "family_name",
    "surname", "middle_name", "username", "nickname",
})

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
    "website", "domain", "url", "homepage",
    "ticker", "stock_symbol", "registration_number", "tax_id",
)

# The subset of the above that holds a URL/hostname rather than a bare code —
# these need registrable-domain extraction before comparison, or
# "https://www.boeing.com/careers" and "boeing.com" never agree.
_DOMAIN_FIELD_HINTS = ("website", "domain", "url", "homepage")

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

# Blocking outcome from the most recent call, surfaced in the resolution
# stats so a truncated comparison budget is a reported number rather than a
# warning buried in a log file.
_LAST_BLOCKING_STATS: dict[str, int] = {}


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
            strictness, sorted(_STRICTNESS),
        )
        auto_match = _AUTO_MATCH
    if auto_match <= auto_exclude:
        logger.warning(
            "auto_match (%.2f) must sit above auto_exclude (%.2f); "
            "falling back to the defaults.", auto_match, auto_exclude,
        )
        return _AUTO_MATCH, _AUTO_EXCLUDE
    return auto_match, auto_exclude


async def resolve_entities_impl(
    entity_type: str,
    records: list[dict[str, Any]],
    blocking_keys: list[str] | None = None,
    llm_config: dict[str, Any] | None = None,
    known_cluster_ids: dict[str, str] | None = None,
    matching_rules: dict[str, Any] | None = None,
    subject: str | None = None,
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
    logger.debug("ENTER resolve_entities_impl: entity_type=%s, records_count=%d",
                  entity_type, len(records))

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
            "clusters": [{
                "cluster_id": _assign_cluster_id(
                    entity_type, [rid], known_cluster_ids
                ),
                "record_ids": [rid],
                "match_confidence": 1.0,
                "required_human_review": False,
            }],
            "total_records": 1,
            "auto_matched": 1,
            "auto_excluded": 0,
            "llm_scored": 0,
        }

    logger.info(
        "Resolving entity type '%s': %d records", entity_type, len(records),
    )

    # Per-entity rules (Phase O.2). An unconfigured entity keeps exactly the
    # behaviour it had before: auto-detected blocking, hint-derived unique
    # identifiers, and the standard thresholds.
    rules = matching_rules or {}
    blocking_keys = blocking_keys or rules.get("blocking_keys") or None
    auto_match, auto_exclude = _thresholds_for(rules)
    unique_ids = {str(f) for f in (rules.get("unique_id_fields") or []) if f}
    deterministic_fields = _deterministic_id_fields(
        records, rules.get("deterministic_id_fields")
    )
    normalise = _subject_stripped_normalize(_subject_tokens(subject))
    if subject:
        logger.info(
            "Entity '%s': scoped merge for subject %r — term frequencies and "
            "discriminating-agreement checks use the subject-stripped "
            "residual, not the raw value.", entity_type, subject,
        )
    if rules:
        logger.info(
            "Entity '%s' uses saved matching rules: blocking=%s, "
            "unique_ids=%s, strictness=%s (auto_match=%.2f)",
            entity_type, blocking_keys or "auto",
            sorted(unique_ids) or "auto", rules.get("strictness", "normal"),
            auto_match,
        )

    # ── Step 1: Blocking + candidate pair generation ──────────────────
    candidate_pairs = _generate_candidate_pairs(records, blocking_keys)
    logger.info(
        "Generated %d candidate pairs for entity '%s' (blocked from %d possible)",
        len(candidate_pairs), entity_type,
        len(records) * (len(records) - 1) // 2,
    )

    # ── Step 2: Probabilistic scoring (Fellegi-Sunter + term frequency) ─
    # An exact agreement on a rare value is far stronger evidence than on a
    # common one — the distinction the old averaged-similarity score could
    # not express, and the reason people sharing a first name were merged.
    match_model = _fit_match_model(records, candidate_pairs, unique_ids, normalise)

    scored_pairs: list[tuple[str, str, float]] = []
    borderline: list[tuple[str, str, dict, dict, float]] = []
    for pair_idx, (id1, id2, rec1, rec2) in enumerate(candidate_pairs):
        conf = _score_pair(
            rec1, rec2, match_model, unique_ids, deterministic_fields, normalise
        )
        if auto_exclude < conf < auto_match:
            borderline.append((id1, id2, rec1, rec2, conf))
        else:
            scored_pairs.append((id1, id2, conf))
        # This loop is pure CPU-bound Python with no natural await point, and
        # up to _MAX_COMPARISONS (250k) pairs can take minutes on a large
        # entity. A live Boeing run (34,782 'experience' records) went long
        # enough on this single synchronous stretch that the API's HTTP
        # status-poll requests started timing out — yielding here periodically
        # gives the event loop (and other threads sharing the GIL) a chance
        # to run in between, without meaningfully slowing the scoring itself.
        if pair_idx % 1000 == 999:
            await asyncio.sleep(0)

    # ── Step 3: LLM judgment for the borderline band ──────────────────
    llm_scored = 0
    llm_enabled = bool(llm_config) and llm_config.get(
        "entity_resolution_enabled", True
    )
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
                "pairs", len(borderline) + len(overflow), entity_type,
                _MAX_LLM_PAIRS, len(overflow),
            )
            scored_pairs.extend(
                (i1, i2, c) for i1, i2, _r1, _r2, c in overflow
            )
        logger.info(
            "Sending %d borderline pairs for entity '%s' to LLM judgment",
            len(borderline), entity_type,
        )
        llm_results = await _judge_pairs_with_llm(
            entity_type, borderline, llm_config
        )
        llm_scored = len(llm_results)
        scored_pairs.extend(llm_results)
    else:
        if borderline and not llm_enabled:
            logger.info(
                "LLM entity resolution disabled; keeping heuristic scores "
                "for %d borderline pairs", len(borderline),
            )
        scored_pairs.extend((i1, i2, c) for i1, i2, _r1, _r2, c in borderline)

    # ── Step 4: Connected Components (Union-Find) ─────────────────────
    clusters, auto_matched, auto_excluded = _build_clusters(
        records, scored_pairs, entity_type, known_cluster_ids,
        auto_match=auto_match, auto_exclude=auto_exclude,
    )
    logger.debug("Built %d clusters: matched=%d, excluded=%d",
                  len(clusters), auto_matched, auto_excluded)

    logger.debug(
        "EXIT resolve_entities_impl: clusters=%d, matched=%d, excluded=%d, llm=%d",
        len(clusters), auto_matched, auto_excluded, llm_scored,
    )
    return {
        "entity_type": entity_type,
        "clusters": clusters,
        "total_records": len(records),
        "auto_matched": auto_matched,
        "auto_excluded": auto_excluded,
        "llm_scored": llm_scored,
        # Pairs the comparison budget could not reach. A human reviewing the
        # final gate must be able to see "N pairs were never compared" rather
        # than have it hidden in a log line.
        "uncompared_pairs": _LAST_BLOCKING_STATS.get("uncompared_pairs", 0),
        "candidate_pairs": _LAST_BLOCKING_STATS.get("candidate_pairs", 0),
        "blocks_purged": _LAST_BLOCKING_STATS.get("blocks_purged", 0),
        # Pairs that scored in the uncertain band and were therefore NOT
        # merged. Without surfacing these the human never learns they
        # existed: they form no multi-record cluster, so the matches gate
        # would have nothing to show and would silently auto-skip.
        "pending_merge_candidates": _pending_merge_candidates(
            scored_pairs, auto_match, auto_exclude
        ),
    }


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
    """String fields worth blocking on: names and identifiers."""
    hints = _NAME_FIELD_HINTS + _REF_FIELD_HINTS + _UNIQUE_IDENTITY_HINTS
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
) -> list[tuple[str, str, dict, dict]]:
    """Generate candidate record pairs via multi-pass blocking.

    Pass 1 blocks on the normalized first token of each record's own best
    name-ish field (falling back to explicit ``blocking_keys`` when given);
    pass 2 blocks on identifier-like fields (``*_ref``, ``*code*``, ...) so
    records whose names differ but share a business reference are still
    compared. The union of intra-block pairs is scored. Records with no
    usable key land in a shared fallback block so missing data never
    silently excludes a record. Enforces ``_MAX_COMPARISONS``.
    """
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

    # Block purging, for approximate keys only. A phonetic code, a first
    # token, or a reference prefix shared by dozens of records is not
    # evidence of anything — it just floods the comparison budget. An exact
    # match on the COMPLETE value ("full:") is real evidence at any block
    # size, so those blocks are never purged; the budget below handles them.
    usable: dict[str, list[dict[str, Any]]] = {}
    purged: list[tuple[str, int]] = []
    for key, members in blocks.items():
        if len(members) < 2:
            continue
        # "full:" is an exact match on a complete identifying value. Every
        # other key — phonetic, first token, partial names ("part:...") and
        # identifier prefixes ("ref:...") — is approximate.
        approximate = not key.startswith("full:")
        if approximate and len(members) > _MAX_REF_BLOCK:
            purged.append((key, len(members)))
            continue
        usable[key] = members
    if purged:
        purged.sort(key=lambda kv: -kv[1])
        logger.info(
            "Blocking: purged %d non-discriminative block(s) over %d records "
            "(largest: %s). These are approximate keys — phonetic codes, "
            "first tokens, partial names, identifier prefixes — that group "
            "too many records to be evidence. Those records are still "
            "compared via any exact-value key they share.",
            len(purged), _MAX_REF_BLOCK,
            ", ".join(f"{k}={n}" for k, n in purged[:3]),
        )

    logger.debug(
        "Blocking on %s: %d records -> %d usable blocks (of %d)",
        fields, len(records), len(usable), len(blocks),
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
    for _key, block_records in sorted(
        usable.items(), key=lambda kv: (len(kv[1]), kv[0])
    ):
        n = len(block_records)
        if budget_reached:
            overflow_pairs_estimate += n * (n - 1) // 2
            continue
        for i in range(n):
            for j in range(i + 1, n):
                id1 = _record_id(block_records[i])
                id2 = _record_id(block_records[j])
                if id1 == id2:
                    continue
                pair_key = (id1, id2) if id1 < id2 else (id2, id1)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                pairs.append((id1, id2, block_records[i], block_records[j]))
        if len(pairs) >= _MAX_COMPARISONS:
            budget_reached = True

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
            len(pairs) + overflow_pairs_estimate, _MAX_COMPARISONS,
            _MAX_COMPARISONS, uncompared,
        )
        _LAST_BLOCKING_STATS["uncompared_pairs"] = uncompared
        pairs = pairs[:_MAX_COMPARISONS]
    else:
        _LAST_BLOCKING_STATS["uncompared_pairs"] = 0
    _LAST_BLOCKING_STATS["candidate_pairs"] = len(pairs)
    _LAST_BLOCKING_STATS["blocks_used"] = len(usable)
    _LAST_BLOCKING_STATS["blocks_purged"] = len(purged)

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
    tokens1 = _tokenize(s1)
    tokens2 = _tokenize(s2)
    jaccard = 0.0
    if tokens1 and tokens2:
        matched = 0
        unused = list(tokens2)
        for t in tokens1:
            for u in unused:
                if _tokens_match(*_ordered(t, u)):
                    matched += 1
                    unused.remove(u)
                    break
        denom = len(tokens1) + len(tokens2) - matched
        jaccard = matched / denom if denom else 0.0
    seq = _seq_ratio(s1, s2)
    return max(jaccard, seq)


def _fit_match_model(
    records: list[dict[str, Any]],
    candidate_pairs: list[tuple[str, str, dict, dict]],
    extra_unique_ids: set[str] | None = None,
    normalise: Any = _normalize,
) -> Any:
    """Estimate the probabilistic match model for this entity type.

    Unsupervised: term frequencies come from all records, m/u probabilities
    from an EM pass over the candidate pairs. Returns None if the model can't
    be built, in which case scoring falls back to the heuristic alone.

    ``normalise`` is the value-normalisation function used both to build the
    term-frequency table and to render each pair's comparison vector — they
    must be the SAME function or a frequency lookup silently misses (see
    ``_subject_stripped_normalize`` for the scoped-merge case).
    """
    if len(candidate_pairs) < _MIN_PAIRS_FOR_MODEL:
        # EM has nothing to learn from a handful of pairs: the priors
        # dominate and a genuine match can score near zero. Measured on a
        # single pair, the model returned 0.002 where the heuristic
        # correctly said 0.82. Below this size the heuristic is simply the
        # better estimator.
        logger.debug(
            "Only %d candidate pair(s) — too few to estimate a match model; "
            "using heuristic scoring.", len(candidate_pairs),
        )
        return None

    try:
        from src.mcp_server.tools.fellegi_sunter import (
            build_term_frequencies, estimate_model,
        )

        vectors = [
            _build_comparison_vector(rec1, rec2, normalise)
            for _id1, _id2, rec1, rec2 in candidate_pairs
        ]
        model = estimate_model(
            vectors,
            build_term_frequencies(records, normalise),
            unique_id_fields=_unique_id_fields(records) | (extra_unique_ids or set()),
        )
        logger.debug(
            "Match model fitted over %d candidate pairs (match_prior=%.3f)",
            len(vectors), model.match_prior,
        )
        return model
    except Exception as exc:
        logger.warning(
            "Could not fit the probabilistic match model (%s); scoring falls "
            "back to the heuristic.", exc,
        )
        return None


def _score_pair(
    rec1: dict[str, Any],
    rec2: dict[str, Any],
    model: Any,
    extra_unique_ids: set[str] | None = None,
    deterministic_fields: set[str] | None = None,
    normalise: Any = _normalize,
) -> float:
    """Confidence that two records are the same entity.

    The probabilistic model decides, but the heuristic guards still apply:
    contradicting unique identifiers and partial-name-only evidence can never
    reach the auto-merge threshold, however the model scores them. A shared
    deterministic identity key (website/domain/ticker/...) settles the pair
    directly, ahead of every other signal, unless it conflicts with a
    contradicting unique identifier.
    """
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}
    conflicting = _has_conflicting_identity(fields1, fields2, extra_unique_ids) or (
        bool(deterministic_fields)
        and _deterministic_conflict(rec1, rec2, deterministic_fields)
    )

    if deterministic_fields and not conflicting and _deterministic_match(
        rec1, rec2, deterministic_fields
    ):
        return 1.0

    heuristic = _compute_heuristic_confidence(rec1, rec2, extra_unique_ids)
    if conflicting:
        # A disagreeing deterministic field (different domain, different
        # ticker, ...) is proof of non-identity even when the names look
        # alike — never let a same-name auto-merge (_cross_name_similarity)
        # override it.
        heuristic = min(heuristic, 0.93)
    if model is None:
        return heuristic

    try:
        probability = model.probability(_build_comparison_vector(rec1, rec2, normalise))
    except Exception:
        return heuristic

    if conflicting:
        return min(probability, 0.93)

    # The heuristic already refuses to auto-merge on a shared partial name;
    # keep that ceiling rather than letting the model override it.
    if heuristic <= 0.93 and probability >= _AUTO_MATCH:
        if _only_partial_name_evidence(rec1, rec2, normalise):
            return 0.93

    # An auto-merge needs agreement on something that actually distinguishes
    # this pair from every other pair. Without this, two records agree on a
    # first name and on a field that is identical across the whole table
    # (Pagila stamps every actor row with the same last_update), and the
    # accumulated weight crosses the threshold on evidence that identifies
    # nobody. Observed live: 25 pairs of different actors merged this way.
    if probability >= _AUTO_MATCH and not _has_discriminating_agreement(
        rec1, rec2, model, normalise
    ):
        return 0.93

    return round(probability, 4)


def _has_discriminating_agreement(
    rec1: dict[str, Any], rec2: dict[str, Any], model: Any, normalise: Any = _normalize
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
    from src.mcp_server.tools.fellegi_sunter import LEVEL_EXACT, LEVEL_STRONG

    fields = getattr(model, "fields", {})

    def rarity(field: str, value: str | None) -> float:
        """How common the agreed value is; 0.0 when unknown (treat as rare)."""
        field_model = fields.get(field)
        if field_model is None or value is None:
            return 0.0
        return field_model.term_frequency.get(value, 0.0)

    name_parts: list[float] = []
    for field, (level, value) in _build_comparison_vector(rec1, rec2, normalise).items():
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
    rec1: dict[str, Any], rec2: dict[str, Any], normalise: Any = _normalize
) -> bool:
    """True when the sole agreement between two records is a partial name."""
    vector = _build_comparison_vector(rec1, rec2, normalise)
    from src.mcp_server.tools.fellegi_sunter import LEVEL_EXACT, LEVEL_STRONG

    agreeing = [
        f for f, (level, _v) in vector.items()
        if level in (LEVEL_EXACT, LEVEL_STRONG)
    ]
    if not agreeing:
        return False
    return all(f.lower() in _PARTIAL_NAME_FIELDS for f in agreeing)


def _comparison_level(
    v1: Any, v2: Any, normalise: Any = _normalize
) -> tuple[str, str | None]:
    """Classify how two field values agree, for the probabilistic model.

    Returns (level, normalised_value). The value is carried so an exact
    agreement can be weighted by how rare that value actually is — using
    whichever ``normalise`` function fed the term-frequency table, so the
    two line up (see ``_subject_stripped_normalize`` for the scoped case).
    """
    from src.mcp_server.tools.fellegi_sunter import (
        LEVEL_DISAGREE, LEVEL_EXACT, LEVEL_NULL, LEVEL_STRONG, LEVEL_WEAK,
    )

    if v1 is None or v2 is None:
        return LEVEL_NULL, None

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
    rec1: dict[str, Any], rec2: dict[str, Any], normalise: Any = _normalize
) -> dict[str, tuple[str, str | None]]:
    """Per-field comparison outcomes for one candidate pair."""
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}
    return {
        field: _comparison_level(fields1.get(field), fields2.get(field), normalise)
        for field in set(fields1) & set(fields2)
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


def _deterministic_match(
    rec1: dict[str, Any], rec2: dict[str, Any], fields: set[str]
) -> bool:
    """True when both records agree on at least one deterministic id field."""
    return any(n1 == n2 for n1, n2 in _deterministic_values(rec1, rec2, fields))


def _deterministic_conflict(
    rec1: dict[str, Any], rec2: dict[str, Any], fields: set[str]
) -> bool:
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
) -> float:
    """Compute a match confidence based on field comparison.

    Returns confidence between 0.0 and 1.0.
    """
    fields1 = {k: v for k, v in rec1.items() if not k.startswith("_")}
    fields2 = {k: v for k, v in rec2.items() if not k.startswith("_")}

    common_fields = set(fields1.keys()) & set(fields2.keys())

    score = 0.0
    total = 0

    for field in common_fields:
        v1 = fields1[field]
        v2 = fields2[field]

        if v1 is None or v2 is None:
            continue

        total += 1

        n1, n2 = _normalize(v1), _normalize(v2)
        if n1 == n2:
            score += 1.0
        elif isinstance(v1, str) and isinstance(v2, str):
            s1 = str(v1).strip().lower()
            s2 = str(v2).strip().lower()
            if len(s1) > 2 and len(s2) > 2:
                sim = _string_similarity(s1, s2)
                if sim > 0.5:
                    score += sim

    field_score = score / total if total > 0 else 0.0
    confidence = max(field_score, _cross_name_similarity(rec1, rec2))

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
        if key1 not in declared and not any(
            hint in kl for hint in _UNIQUE_IDENTITY_HINTS
        ):
            continue
        val2 = fields2.get(key1)
        if val1 is None or val2 is None:
            continue
        if _normalize(val1) != _normalize(val2):
            return True
    return False


def _cross_name_similarity(rec1: dict[str, Any], rec2: dict[str, Any]) -> float:
    """Similarity of the records' best name fields, across field names.

    Origins name the same concept differently (``company_name`` vs
    ``org_title``), so common-field comparison alone can never match their
    records. An exact normalized name match scores just above _AUTO_MATCH
    (deterministic cross-origin merge); fuzzy name-only evidence is capped
    below it so it lands in the LLM/human review band instead.
    """
    f1, f2 = _best_name_field(rec1), _best_name_field(rec2)
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
    from src.llm import LLMClient, PROMPT_TEMPLATES
    from src.orchestrator.utils.retry_hints import apply_retry_hint

    hint = str((llm_config or {}).get("retry_hint") or "").strip()
    prompts = PROMPT_TEMPLATES.get("entity_resolution", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")
    if not system_prompt or not human_template:
        logger.warning(
            "entity_resolution prompts missing; keeping heuristic scores"
        )
        return [(i1, i2, c) for i1, i2, _r1, _r2, c in borderline]

    client = LLMClient(llm_config)
    chunks = [
        borderline[start:start + _LLM_CHUNK_SIZE]
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
                human_template
                .replace("{entity_type}", entity_type)
                .replace("{pair_count}", str(len(chunk)))
                .replace("{pairs_block}", _format_pairs_block(chunk))
            )
            # Set when a reviewer sent the matches gate back for rework.
            user_prompt = apply_retry_hint(
                user_prompt, {"retry_hints": {"resolve_entities": hint}},
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
                    offset + n, id1, id2, j.get("match"), llm_conf, score,
                    str(j.get("reason", ""))[:120],
                )
                chunk_results.append((id1, id2, round(score, 3)))

        except Exception as exc:
            logger.warning(
                "LLM entity-resolution chunk failed (%s); keeping heuristic "
                "scores for %d pairs in this chunk", exc, len(chunk),
            )
            chunk_results = [(i1, i2, c) for i1, i2, _r1, _r2, c in chunk]
        return chunk_results, chunk_judged

    chunk_outputs = await asyncio.gather(*(
        judge_chunk(chunk, i * _LLM_CHUNK_SIZE)
        for i, chunk in enumerate(chunks)
    ))

    results: list[tuple[str, str, float]] = []
    judged = 0
    for chunk_results, chunk_judged in chunk_outputs:
        results.extend(chunk_results)
        judged += chunk_judged

    logger.info(
        "LLM judged %d/%d borderline pairs for entity '%s'",
        judged, len(borderline), entity_type,
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
        seen = [
            known_cluster_ids[rid] for rid in member_ids
            if rid in known_cluster_ids
        ]
        if seen:
            # Deterministic tie-break when a cluster now spans records that
            # previously belonged to different entities: the smallest id
            # wins, so both runs agree regardless of iteration order.
            return min(seen)
    return deterministic_uuid(entity_type, *member_ids)


def _build_clusters(
    records: list[dict[str, Any]],
    scored_pairs: list[tuple[str, str, float]],
    entity_type: str = "",
    known_cluster_ids: dict[str, str] | None = None,
    auto_match: float = _AUTO_MATCH,
    auto_exclude: float = _AUTO_EXCLUDE,
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

    Returns (clusters, auto_matched_count, auto_excluded_count).
    """
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
            borderline_count, entity_type,
        )

    clusters: list[dict[str, Any]] = []
    repaired = 0
    cut = 0
    for rids in component_map.values():
        members = sorted(set(rids))
        if len(members) > 1:
            parts, was_repaired, was_cut = _partition_component(
                members, edge_weight, records_by_id
            )
            repaired += was_repaired
            cut += was_cut
        else:
            parts = [members]

        for part in parts:
            part_ids = sorted(part)
            min_conf = _min_internal_confidence(part_ids, edge_weight)
            clusters.append({
                # Stable across runs: a previously-assigned id is reused, and
                # only a genuinely new entity derives one from its members.
                # The id is the destination primary key, so instability here
                # means duplicate rows.
                "cluster_id": _assign_cluster_id(
                    entity_type, part_ids, known_cluster_ids
                ),
                "record_ids": part_ids,
                "match_confidence": round(min_conf, 3),
                "required_human_review": len(part_ids) > 1 and min_conf < 0.85,
            })

    if repaired or cut:
        logger.info(
            "Cluster repair for entity '%s': %d component(s) split on "
            "conflicting identifiers, %d split by cohesion cut.",
            entity_type, repaired, cut,
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
        for b in members[i + 1:]
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
    conflict = False
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            rec_a = records_by_id.get(a, {})
            rec_b = records_by_id.get(b, {})
            fields_a = {k: v for k, v in rec_a.items() if not k.startswith("_")}
            fields_b = {k: v for k, v in rec_b.items() if not k.startswith("_")}
            if _has_conflicting_identity(fields_a, fields_b):
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


def _average_linkage(
    members: list[str], edge_weight: dict[tuple[str, str], float]
) -> float:
    """Mean similarity over all member pairs, missing edges counting as 0.

    A chain A-B-C has no A-C edge, so its average is dragged down — which is
    exactly the signal that it is a chain rather than a genuine group.
    """
    if len(members) < 2:
        return 1.0
    total = 0.0
    count = 0
    for i, a in enumerate(members):
        for b in members[i + 1:]:
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
            for b in members[i + 1:]
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
