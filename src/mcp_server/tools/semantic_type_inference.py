"""V3 semantic type inference (Part B, P-4).

Evidence-carrying inference over a fixed type set, from column name +
declared datatype + P-3's measured profile (pattern distribution, length
distribution, uniqueness). No LLM in the first pass -- the heuristic below
runs on name + measured evidence alone. The LLM is a fallback only for
columns the heuristic could not confidently place (UNKNOWN or below
``_HEURISTIC_MIN_CONFIDENCE``), and its output is always tagged
LLM_ASSISTED, never treated as more authoritative than a human override.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.utils.column_naming import normalize_column_name as _normalize_column_name

logger = logging.getLogger("merge-engine.tools.semantic_type_inference")

SEMANTIC_TYPES = frozenset(
    {
        "PERSON_NAME",
        "ORGANIZATION_NAME",
        "EMAIL",
        "PHONE_NUMBER",
        "POSTAL_ADDRESS",
        "CITY",
        "STATE_REGION",
        "COUNTRY",
        "POSTAL_CODE",
        "DATE",
        "DATETIME",
        "IDENTIFIER",
        "FOREIGN_KEY",
        "CURRENCY_AMOUNT",
        "PERCENTAGE",
        "BOOLEAN",
        "URL",
        "LATITUDE",
        "LONGITUDE",
        "DESCRIPTION_TEXT",
        "CATEGORICAL_CODE",
        "NUMERIC_MEASURE",
        "UNKNOWN",
    }
)

# Below this, the heuristic result is not confident enough to stand alone --
# eligible for the LLM fallback (if configured) rather than stored as-is.
_HEURISTIC_MIN_CONFIDENCE = 0.6

# Column-NAME signals: (semantic_type, compiled regex over the normalized
# name). Order matters where names overlap (e.g. "email" before the looser
# "name" check) -- first match wins.
_NAME_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"e[-_]?mail")),
    ("PHONE_NUMBER", re.compile(r"phone|telephone|mobile|fax")),
    ("URL", re.compile(r"^url$|website|homepage|web_?site")),
    ("POSTAL_CODE", re.compile(r"post(al)?[-_]?code|zip")),
    ("COUNTRY", re.compile(r"^country")),
    ("STATE_REGION", re.compile(r"^state$|^region$|^province")),
    ("CITY", re.compile(r"^city$|^town$")),
    ("POSTAL_ADDRESS", re.compile(r"address|street")),
    ("LATITUDE", re.compile(r"^lat(itude)?$")),
    ("LONGITUDE", re.compile(r"^lon(g|gitude)?$")),
    ("DATETIME", re.compile(r"(created|updated|modified|deleted)[-_]?at$|timestamp")),
    ("DATE", re.compile(r"date|_dob$|^dob$|birthday")),
    ("CURRENCY_AMOUNT", re.compile(r"price|amount|revenue|salary|cost|budget|investment")),
    ("PERCENTAGE", re.compile(r"percent|_pct$|^pct")),
    ("PERSON_NAME", re.compile(r"^(first|last|middle|full|given|family)[-_]?name$|^name$")),
    ("ORGANIZATION_NAME", re.compile(r"company[-_]?name|org(anization)?[-_]?name|legal[-_]?name")),
    ("FOREIGN_KEY", re.compile(r"_id$|^fk_")),
    ("IDENTIFIER", re.compile(r"^id$|^uuid$|_uuid$|^guid$")),
    ("BOOLEAN", re.compile(r"^is_|^has_|_flag$")),
    ("DESCRIPTION_TEXT", re.compile(r"description|summary|notes?$|comment")),
    ("CATEGORICAL_CODE", re.compile(r"^type$|^status$|^category$|^kind$|^role$")),
]

# Which P-3 pattern_distribution bucket corroborates which semantic type --
# used to raise or lower confidence once a NAME match already proposed a
# specific type. Deliberately many-to-one (e.g. both IDENTIFIER and
# FOREIGN_KEY corroborate on "uuid" -- a uuid-shaped value is consistent
# with either, and only the column NAME can tell them apart).
_PATTERN_CORROBORATION = {
    "EMAIL": "email",
    "IDENTIFIER": "uuid",
    "FOREIGN_KEY": "uuid",
    "DATE": "date_iso",
    "DATETIME": "date_iso",
    "CURRENCY_AMOUNT": "numeric",
    "PERCENTAGE": "numeric",
    "NUMERIC_MEASURE": "numeric",
    "LATITUDE": "numeric",
    "LONGITUDE": "numeric",
    "PERSON_NAME": "alpha",
    "ORGANIZATION_NAME": "alpha",
    "CITY": "alpha",
    "COUNTRY": "alpha",
    "STATE_REGION": "alpha",
}

# For the OPPOSITE direction -- no name signal at all, pattern is all we
# have -- a many-to-one bucket like "uuid" or "alpha" is too ambiguous to
# commit to any of its several corroborating types (a uuid could be an
# IDENTIFIER or a FOREIGN_KEY; alpha text could be a person, an
# organization, a city...). Only buckets with exactly one plausible,
# genuinely safe default get an entry here; everything else stays UNKNOWN
# rather than silently picking whichever type happened to be defined last
# in _PATTERN_CORROBORATION above.
_PATTERN_ONLY_FALLBACK = {
    "email": "EMAIL",
    "uuid": "IDENTIFIER",
    "numeric": "NUMERIC_MEASURE",
}

# Patterns more than one semantic type corroborates on (uuid, numeric,
# alpha, date_iso all are). Live-caught: a bare "name" column measured as
# ~100% alpha-shaped text was boosted to 0.93 confidence for PERSON_NAME --
# but PERSON_NAME and ORGANIZATION_NAME corroborate on the exact same
# "alpha" bucket, so that measurement cannot actually distinguish between
# them. The corroboration BOOST is suppressed for any name match whose
# pattern falls in this set (the name match itself still stands at its
# base, uncorroborated confidence) -- only a genuinely distinguishing
# pattern (email, date_iso alone would be if only one type used it) may
# raise confidence above the name-alone baseline.
_AMBIGUOUS_PATTERNS = frozenset(
    pattern
    for pattern in set(_PATTERN_CORROBORATION.values())
    if list(_PATTERN_CORROBORATION.values()).count(pattern) > 1
)


def infer_semantic_type_heuristic(
    column_name: str,
    data_type: str,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evidence-carrying heuristic inference. Never calls the LLM.

    Returns ``{"semantic_type", "confidence", "signals"}`` -- ``signals``
    records exactly what fed the decision, so a human reviewing a
    NEEDS_REVIEW row can see the reasoning without re-running anything.
    """
    normalized = _normalize_column_name(column_name)
    profile = profile or {}
    pattern_dist: dict[str, int] = profile.get("pattern_distribution") or {}
    total_sampled = sum(pattern_dist.values()) or 0

    name_match: str | None = None
    for semantic_type, rule in _NAME_RULES:
        if rule.search(normalized):
            name_match = semantic_type
            break

    if name_match is None:
        # No name signal at all -- fall back to whichever pattern bucket
        # dominates the sample, if any single one clearly does AND that
        # bucket has an unambiguous default (_PATTERN_ONLY_FALLBACK) --
        # never guess among several equally-plausible types just because
        # one of them happens to iterate last in a dict.
        if total_sampled:
            dominant_pattern, dominant_count = max(pattern_dist.items(), key=lambda kv: kv[1])
            dominant_ratio = dominant_count / total_sampled
            if dominant_pattern in _PATTERN_ONLY_FALLBACK and dominant_ratio >= 0.8:
                return {
                    "semantic_type": _PATTERN_ONLY_FALLBACK[dominant_pattern],
                    "confidence": round(0.5 * dominant_ratio, 3),
                    "signals": {
                        "name_match": None,
                        "dominant_pattern": dominant_pattern,
                        "dominant_pattern_ratio": round(dominant_ratio, 3),
                    },
                }
        return {
            "semantic_type": "UNKNOWN",
            "confidence": 0.0,
            "signals": {"name_match": None, "pattern_distribution": pattern_dist},
        }

    # A name match exists -- corroborate (or contradict) it against the
    # measured pattern distribution, when that type has a defined
    # corroborating pattern and there is enough sample to judge by.
    expected_pattern = _PATTERN_CORROBORATION.get(name_match)
    confidence = 0.75  # name alone, uncorroborated (no pattern to check against)
    corroboration_ratio = None
    if expected_pattern and total_sampled:
        corroboration_ratio = pattern_dist.get(expected_pattern, 0) / total_sampled
        # A pattern more than one semantic type corroborates on cannot
        # discriminate between them -- e.g. PERSON_NAME and
        # ORGANIZATION_NAME both corroborate on "alpha", so measuring 100%
        # alpha-shaped values is equally consistent with either and must
        # not push confidence in ONE of them's favor. Contradiction still
        # counts against the name match either way -- an ambiguous pattern
        # can rule a type OUT (near-zero corroboration) even though it
        # cannot rule one IN.
        if expected_pattern in _AMBIGUOUS_PATTERNS:
            if corroboration_ratio <= 0.1:
                confidence = 0.3
        # Name says X, measured values agree -> high confidence. Name says
        # X, values disagree entirely -> the name is misleading; trust the
        # measurement over the label and drop to UNKNOWN-adjacent territory
        # rather than assert a type the data doesn't support.
        elif corroboration_ratio >= 0.7:
            confidence = min(0.95, 0.75 + 0.2 * corroboration_ratio)
        elif corroboration_ratio <= 0.1:
            confidence = 0.3

    return {
        "semantic_type": name_match if confidence > 0.3 else "UNKNOWN",
        "confidence": round(confidence, 3) if confidence > 0.3 else 0.0,
        "signals": {
            "name_match": name_match,
            "normalized_column_name": normalized,
            "expected_pattern": expected_pattern,
            "corroboration_ratio": (
                round(corroboration_ratio, 3) if corroboration_ratio is not None else None
            ),
        },
    }


def _format_evidence_block(profile: dict[str, Any] | None) -> str:
    profile = profile or {}
    lines = [
        f"- null_ratio: {profile.get('null_ratio')}",
        f"- distinct_ratio: {profile.get('distinct_ratio')}",
        f"- length_distribution: {profile.get('length_distribution')}",
        f"- pattern_distribution: {profile.get('pattern_distribution')}",
        f"- example values: {[v.get('value') for v in (profile.get('top_values') or [])[:5]]}",
    ]
    return "\n".join(lines)


async def _infer_with_llm(
    column_name: str,
    data_type: str,
    profile: dict[str, Any] | None,
    heuristic_result: dict[str, Any],
    llm_config: dict[str, Any],
) -> dict[str, Any] | None:
    """One LLM call proposing a label for a column the heuristic could not
    confidently place. Returns None on any failure -- the caller keeps the
    heuristic's own (low-confidence) result rather than blocking on the LLM."""
    from src.llm import PROMPT_TEMPLATES, LLMClient

    try:
        prompts = PROMPT_TEMPLATES.get("semantic_type_inference", {})
        system_prompt = prompts.get("system", "")
        human_template = prompts.get("human", "")
        if not system_prompt or not human_template:
            logger.warning("semantic_type_inference prompts missing; keeping heuristic result")
            return None

        user_prompt = (
            human_template.replace("{column_name}", column_name)
            .replace("{data_type}", data_type or "unknown")
            .replace("{evidence_block}", _format_evidence_block(profile))
            .replace(
                "{heuristic_summary}",
                f"heuristic proposed {heuristic_result['semantic_type']} at confidence "
                f"{heuristic_result['confidence']} ({heuristic_result['signals']})",
            )
        )
        client = LLMClient(llm_config)
        result = await client.call_json(system_prompt=system_prompt, user_prompt=user_prompt)
        if not isinstance(result, dict):
            return None
        semantic_type = result.get("semantic_type")
        if semantic_type not in SEMANTIC_TYPES:
            logger.warning("LLM proposed unknown semantic type %r; ignoring", semantic_type)
            return None
        confidence = result.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        return {
            "semantic_type": semantic_type,
            "confidence": float(confidence),
            "signals": {"reason": result.get("reason", "")},
        }
    except Exception as exc:
        logger.warning("LLM semantic-type inference failed for %s: %s", column_name, exc)
        return None


async def infer_semantic_type(
    column_name: str,
    data_type: str,
    profile: dict[str, Any] | None = None,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full P-4 inference: heuristic first, LLM fallback only when the
    heuristic is not confident and an LLM is configured.

    Returns ``{"semantic_type", "confidence", "origin", "signals"}`` --
    ``origin`` is ``"ALGORITHMIC"`` for the heuristic path,
    ``"LLM_ASSISTED"`` when the LLM's answer was used, matching P-1's
    origin taxonomy directly so the caller can pass this straight into
    ``MetadataStore.insert_semantic_type``.
    """
    heuristic = infer_semantic_type_heuristic(column_name, data_type, profile)
    if heuristic["confidence"] >= _HEURISTIC_MIN_CONFIDENCE:
        return {**heuristic, "origin": "ALGORITHMIC"}

    if llm_config is not None and llm_config.get("field_mapping_enabled", True):
        llm_result = await _infer_with_llm(column_name, data_type, profile, heuristic, llm_config)
        if llm_result is not None:
            return {**llm_result, "origin": "LLM_ASSISTED"}

    return {**heuristic, "origin": "ALGORITHMIC"}
