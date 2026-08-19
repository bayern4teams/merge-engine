"""V3 schema matching & canonical schema (Part B, P-7; PreSplink §25, §27).

Three tiers, in order (ADR 0005, docs/decisions/0005-adopt-valentine-schema-matching.md):

1. Valentine (Coma + DistributionBased + JaccardDistanceMatcher) over sampled
   frames -- ranked candidates, merged across matchers by max score, each
   matcher's own vote kept as evidence (never collapsed into one opaque
   number, per PreSplink §22).
2. The deterministic exact/heuristic pass -- reuses map_fields.py's own
   ``_heuristic_match`` rather than a second, drifting copy of the same
   name-similarity logic.
3. The LLM sees only what remains ambiguous after both.

Every mapping stores ``source_column``, ``canonical_field``, ``confidence``,
``evidence``, ``origin`` (PreSplink §25) via ``MetadataStore.insert_field_mapping``.

The canonical schema here is *discovered*, not preset: unlike V2's
``map_fields.py`` (which maps onto the destination's fixed 14-43 column
contract), P-7 has no fixed target -- the first table seen for a given
entity type seeds the canonical fields 1:1 (normalized through
``column_naming.normalize_column_name``), and every subsequent table is
matched against that seed; a column with no acceptable candidate becomes a
NEW canonical field rather than being dropped, since there is no destination
schema authority here to refuse against.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from valentine import valentine_match
from valentine.algorithms import Coma, DistributionBased, JaccardDistanceMatcher

from src.mcp_server.tools.map_fields import _heuristic_match
from src.utils.column_naming import normalize_column_name

logger = logging.getLogger("merge-engine.tools.schema_matching")

# A candidate at or above this score is accepted without needing the LLM --
# mirrors map_fields.py's own high-confidence bar (_apply_heuristic_tier's
# `best_score >= 0.5`) rounded up slightly since P-7 has no destination
# schema authority to catch a wrong accept later.
_ACCEPT_THRESHOLD = 0.75

# Below this, there is no real signal at all -- the column is a new
# canonical field candidate, not even worth sending to the LLM as
# "ambiguous" (matches map_fields.py's own `best_score >= 0.3` floor for
# "worth a human/LLM look" vs. "no match found").
_AMBIGUOUS_FLOOR = 0.3

_MATCHER_CLASSES: list[tuple[str, type]] = [
    ("coma", Coma),
    ("distribution_based", DistributionBased),
    ("jaccard", JaccardDistanceMatcher),
]

_SOURCE_NAME = "source"
_TARGET_NAME = "canonical"


def run_valentine_candidates(
    source_df: pd.DataFrame, target_df: pd.DataFrame
) -> dict[str, list[dict[str, Any]]]:
    """Run all three ADR-0005 matchers and merge their results.

    Returns ``{source_column: [{"target_column", "score", "matchers"}, ...]}``,
    each source column's candidates sorted by score descending. A pair found
    by more than one matcher keeps its MAX score and lists every matcher
    that voted for it, so a reader can see whether a candidate is backed by
    one algorithm's opinion or a consensus.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for matcher_name, matcher_cls in _MATCHER_CLASSES:
        try:
            result = valentine_match(
                [source_df, target_df], matcher_cls(), [_SOURCE_NAME, _TARGET_NAME]
            )
        except Exception as exc:
            logger.debug("Valentine matcher %s failed: %s", matcher_name, exc)
            continue
        for pair, score in result.items():
            if pair.source_table != _SOURCE_NAME or pair.target_table != _TARGET_NAME:
                continue
            key = (pair.source_column, pair.target_column)
            entry = merged.setdefault(key, {"score": 0.0, "matchers": []})
            entry["matchers"].append(matcher_name)
            entry["score"] = max(entry["score"], float(score))

    by_source: dict[str, list[dict[str, Any]]] = {}
    for (source_column, target_column), entry in merged.items():
        by_source.setdefault(source_column, []).append(
            {
                "target_column": target_column,
                "score": round(entry["score"], 4),
                "matchers": entry["matchers"],
            }
        )
    for candidates in by_source.values():
        candidates.sort(key=lambda c: c["score"], reverse=True)
    return by_source


def heuristic_candidates_for_column(
    source_column: str, target_columns: list[str]
) -> list[dict[str, Any]]:
    """The deterministic exact/heuristic pass, reusing map_fields.py's own
    ``_heuristic_match`` -- ranked, non-zero candidates only."""
    scored = [
        {"target_column": t, "score": _heuristic_match(source_column, t)} for t in target_columns
    ]
    scored = [c for c in scored if c["score"] > 0.0]
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored


def _best_candidate(
    valentine_ranked: list[dict[str, Any]], heuristic_ranked: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Merge one source column's two candidate lists into a single best
    pick: Valentine's top candidate and the heuristic's top candidate are
    compared by score, and whichever is higher wins -- both are kept in the
    returned evidence regardless of which one won."""
    best: dict[str, Any] | None = None
    if valentine_ranked:
        best = {**valentine_ranked[0], "source": "valentine"}
    if heuristic_ranked:
        top_heuristic = heuristic_ranked[0]
        if best is None or top_heuristic["score"] > best["score"]:
            best = {**top_heuristic, "source": "heuristic"}
    return best


def match_columns_to_canonical_schema(
    source_columns: list[str],
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    """Tiers 1+2 for every source column against the current canonical
    schema's own reference table. Returns one entry per source column:
    ``{"decision": "accept" | "ambiguous" | "new_field", "best": {...} | None,
    "valentine_candidates": [...], "heuristic_candidates": [...]}``.

    ``target_df`` is empty (or has no columns) the first time an entity type
    is seen -- every source column is then a ``new_field`` by construction,
    seeding the canonical schema 1:1.
    """
    target_columns = list(target_df.columns)
    valentine_by_source = run_valentine_candidates(source_df, target_df) if target_columns else {}

    results: dict[str, dict[str, Any]] = {}
    taken: set[str] = set()
    for column in source_columns:
        valentine_ranked = valentine_by_source.get(column, [])
        heuristic_ranked = (
            heuristic_candidates_for_column(column, target_columns) if target_columns else []
        )
        best = _best_candidate(valentine_ranked, heuristic_ranked)

        if best is None or best["score"] < _AMBIGUOUS_FLOOR:
            decision = "new_field"
        elif best["target_column"] in taken and best["score"] < 1.0:
            # A different source column already claimed this canonical
            # field in this same table -- live-caught (BP-B, P-7,
            # 2026-08-19): real origin2.Person's `firstName`/`lastName`/
            # `middleName` each independently scored 0.8 against a bare
            # `name` seed column (all contain "name" as a substring), so
            # without this guard all three would silently "accept" onto
            # the SAME canonical field -- whichever is stored last wins in
            # any consumer that treats a canonical field as single-valued,
            # scrambling the person's name exactly like the ceoName bug
            # this module's `_heuristic_match` fix addressed, but for
            # decomposed-part columns rather than role-qualified ones. Only
            # an exact (1.0) match may still claim an already-taken field.
            decision = "ambiguous"
        elif best["score"] >= _ACCEPT_THRESHOLD:
            decision = "accept"
            taken.add(best["target_column"])
        else:
            decision = "ambiguous"

        results[column] = {
            "decision": decision,
            "best": best,
            "valentine_candidates": valentine_ranked,
            "heuristic_candidates": heuristic_ranked,
        }
    return results


async def _decide_ambiguous_with_llm(
    source_column: str,
    candidates: list[dict[str, Any]],
    llm_config: dict[str, Any],
) -> dict[str, Any] | None:
    """One LLM call for a column tier 1+2 could not confidently place.
    Returns ``None`` on any failure -- the caller falls back to the best
    algorithmic candidate (or `new_field`) rather than blocking on the LLM."""
    from src.llm import PROMPT_TEMPLATES, LLMClient

    try:
        prompts = PROMPT_TEMPLATES.get("schema_matching", {})
        system_prompt = prompts.get("system", "")
        human_template = prompts.get("human", "")
        if not system_prompt or not human_template:
            logger.warning("schema_matching prompts missing; keeping algorithmic result")
            return None

        candidates_block = "\n".join(
            f"- {c['target_column']} (score={c['score']}, source={c.get('source', 'merged')})"
            for c in candidates
        )
        user_prompt = human_template.replace("{source_column}", source_column).replace(
            "{candidates_block}", candidates_block or "(no candidates found)"
        )
        client = LLMClient(llm_config)
        result = await client.call_json(system_prompt=system_prompt, user_prompt=user_prompt)
        if not isinstance(result, dict):
            return None
        decision = result.get("decision")
        if decision not in ("accept", "new_field"):
            logger.warning("LLM proposed unknown schema_matching decision %r; ignoring", decision)
            return None
        confidence = result.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        return {
            "decision": decision,
            "target_column": result.get("target_column"),
            "confidence": float(confidence),
            "reason": result.get("reason", ""),
        }
    except Exception as exc:
        logger.warning("schema_matching LLM call failed for %r: %s", source_column, exc)
        return None


async def resolve_ambiguous_columns(
    ambiguous: dict[str, dict[str, Any]],
    *,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Tier 3: the LLM sees only what tiers 1+2 left ambiguous. Returns
    updated entries with a resolved ``decision`` -- unresolved (LLM
    unavailable or failed) columns keep the tier-1+2 candidate as
    ``NEEDS_REVIEW`` rather than silently accepting or dropping them."""
    resolved: dict[str, dict[str, Any]] = {}
    for column, entry in ambiguous.items():
        if llm_config is None or not llm_config.get("field_mapping_enabled", True):
            resolved[column] = {**entry, "origin": "ALGORITHMIC", "review_status": "NEEDS_REVIEW"}
            continue

        all_candidates = entry["valentine_candidates"] + entry["heuristic_candidates"]
        llm_result = await _decide_ambiguous_with_llm(column, all_candidates, llm_config)
        if llm_result is None:
            resolved[column] = {**entry, "origin": "ALGORITHMIC", "review_status": "NEEDS_REVIEW"}
            continue

        resolved[column] = {
            **entry,
            "decision": llm_result["decision"],
            "best": {
                "target_column": llm_result["target_column"],
                "score": llm_result["confidence"],
                "reason": llm_result["reason"],
            },
            "origin": "LLM_ASSISTED",
            "review_status": "NEEDS_REVIEW",
        }
    return resolved


def store_mappings(
    store: Any,
    entity_type: str,
    table_id: str,
    column_ids: dict[str, str],
    match_results: dict[str, dict[str, Any]],
    *,
    run_id: str | None = None,
    algorithm_version: str = "v1",
) -> dict[str, str]:
    """Persist every column's decision: ``accept``/LLM-``accept`` becomes a
    ``md_field_mapping`` row against an existing (or newly upserted, for a
    seed table) ``md_canonical_field``; ``new_field`` upserts a fresh
    canonical field named after the (normalized) source column and maps
    onto it 1:1 at confidence 1.0 (it did not need matching -- it defines
    itself); ``ambiguous`` stores whatever tier 3 left, ``NEEDS_REVIEW``.

    Returns ``{source_column: canonical_field_id}`` for the caller to reuse
    as the growing canonical schema's own reference for the next table.
    """
    canonical_field_ids: dict[str, str] = {}
    for column, entry in match_results.items():
        column_id = column_ids[column]
        decision = entry["decision"]

        if decision == "new_field":
            canonical_name = normalize_column_name(column)
            field_id = store.upsert_canonical_field(entity_type, canonical_name)
            store.insert_field_mapping(
                column_id,
                field_id,
                confidence=1.0,
                evidence={"reason": "seeded new canonical field", "source_column": column},
                algorithm_version=algorithm_version,
                origin="ALGORITHMIC",
                review_status="AUTO_ACCEPTED",
                run_id=run_id,
            )
            canonical_field_ids[column] = field_id
            continue

        best = entry.get("best") or {}
        target_column = best.get("target_column")
        canonical_name = normalize_column_name(target_column or column)
        field_id = store.upsert_canonical_field(entity_type, canonical_name)

        origin = entry.get("origin", "ALGORITHMIC")
        review_status = (
            "AUTO_ACCEPTED"
            if decision == "accept" and origin == "ALGORITHMIC"
            else entry.get("review_status", "NEEDS_REVIEW")
        )
        store.insert_field_mapping(
            column_id,
            field_id,
            confidence=best.get("score"),
            evidence={
                "valentine_candidates": entry.get("valentine_candidates", []),
                "heuristic_candidates": entry.get("heuristic_candidates", []),
                "chosen": best,
            },
            algorithm_version=algorithm_version,
            origin=origin,
            review_status=review_status,
            run_id=run_id,
        )
        canonical_field_ids[column] = field_id
    return canonical_field_ids
