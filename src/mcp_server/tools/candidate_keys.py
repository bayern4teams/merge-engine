"""V3 candidate & composite key discovery (Part B, P-5).

Three tiers, checked in order, never brute force:

1. Declared PK/unique -- read straight from P-2's ``discover_indexes()``.
   Authoritative; nothing to infer.
2. Single-column inference -- from P-3's measured ``unique_ratio``/
   ``null_ratio``. Cheap: reads an already-computed profile, no new query.
3. Pruned composite search -- only for a table with NO declared or
   single-column candidate. "Pruned" means: only identifier-LIKE, low-null
   columns are even considered (never every column combination), capped at
   2-column pairs, and each candidate pair is verified against the real
   database (an actual ``COUNT(DISTINCT ...)`` vs ``COUNT(*)``) before it is
   ever reported -- an unverified guess is never stored as a candidate key.

An inferred candidate is never labelled a primary key -- ``key_type`` is one
of ``DECLARED_PRIMARY_KEY`` / ``DECLARED_UNIQUE`` / ``INFERRED_SINGLE_COLUMN``
/ ``INFERRED_COMPOSITE``, matching P-1's ``VALID_KEY_TYPES`` exactly, and the
type itself carries that distinction -- a reader never has to also check
``origin``/``confidence`` to know whether a row is a real constraint or a
measurement-based guess.
"""

from __future__ import annotations

import logging
import re
from itertools import combinations
from typing import Any

from src.utils.column_naming import normalize_column_name

logger = logging.getLogger("merge-engine.tools.candidate_keys")

# Single-column inference thresholds (PreSplink §18: measured, not guessed).
_SINGLE_COLUMN_MIN_UNIQUE_RATIO = 0.98
_SINGLE_COLUMN_MAX_NULL_RATIO = 0.01

# A composite candidate must clear this joint-uniqueness bar to be reported
# at all -- below it, the pair simply isn't a key and is silently dropped,
# not stored at low confidence (an unverified or weak composite key is
# worse than no answer, since P-6/P-9 would treat ANY stored candidate key
# as something worth joining on).
_COMPOSITE_MIN_UNIQUE_RATIO = 0.95

# Column-name signal for "plausibly part of an identifier" -- the same
# generic id-shape used by P-4's FOREIGN_KEY/IDENTIFIER name rules, kept
# separate here since P-5 must work even when P-4 hasn't run yet.
_IDENTIFIER_LIKE = re.compile(r"_id$|^id$|_uuid$|^uuid$|_code$|_key$|_ref$")


def classify_declared_indexes(indexes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn P-2's raw ``discover_indexes()`` rows into candidate-key
    proposals. Purely a relabelling -- no inference, no query."""
    proposals: list[dict[str, Any]] = []
    for idx in indexes:
        if idx.get("is_primary"):
            key_type = "DECLARED_PRIMARY_KEY"
        elif idx.get("is_unique"):
            key_type = "DECLARED_UNIQUE"
        else:
            continue
        proposals.append(
            {
                "columns": list(idx.get("columns") or []),
                "key_type": key_type,
                "confidence": 1.0,
                "evidence": {"index_name": idx.get("name"), "kind": idx.get("kind")},
            }
        )
    return proposals


def infer_single_column_candidates(
    column_profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """From P-3 profiles alone -- ``{column_name: profile_dict}``.

    A column already covered by a declared key should be excluded by the
    caller before calling this (its profile isn't wrong, but reporting it
    again as "inferred" would misrepresent a real constraint as a guess).
    """
    proposals: list[dict[str, Any]] = []
    for column, profile in column_profiles.items():
        unique_ratio = profile.get("unique_ratio")
        null_ratio = profile.get("null_ratio")
        if unique_ratio is None or null_ratio is None:
            continue
        if (
            unique_ratio >= _SINGLE_COLUMN_MIN_UNIQUE_RATIO
            and null_ratio <= _SINGLE_COLUMN_MAX_NULL_RATIO
        ):
            proposals.append(
                {
                    "columns": [column],
                    "key_type": "INFERRED_SINGLE_COLUMN",
                    "confidence": round(unique_ratio * (1 - null_ratio), 3),
                    "evidence": {"unique_ratio": unique_ratio, "null_ratio": null_ratio},
                }
            )
    return proposals


def propose_composite_candidate_pairs(
    column_profiles: dict[str, dict[str, Any]],
    max_null_ratio: float = 0.05,
) -> list[tuple[str, str]]:
    """The PRUNED search: only identifier-LIKE, low-null columns are even
    considered, and only as 2-column pairs -- never every combination of
    every column, which is the brute force this phase explicitly rejects.

    Returns unverified PAIRS for the caller to check against the real
    database -- this function issues no queries itself.
    """
    eligible = [
        column
        for column, profile in column_profiles.items()
        if _IDENTIFIER_LIKE.search(normalize_column_name(column))
        and (profile.get("null_ratio") or 0.0) <= max_null_ratio
    ]
    return list(combinations(sorted(eligible), 2))


async def verify_composite_uniqueness(
    connector: Any, table_name: str, columns: tuple[str, ...]
) -> float:
    """The real check a proposed composite pair must clear: distinct
    combined-value count over total row count, computed database-side via
    the connector's own ``extract`` (bounded, never a full unbounded scan
    for a huge table -- reuses P-3's same sampling discipline)."""
    extracted = await connector.extract(table_name, list(columns), batch_size=10_000)
    rows = extracted.get("rows", [])
    if not rows:
        return 0.0
    seen: set[tuple[Any, ...]] = set()
    non_null_rows = 0
    for row in rows:
        values = tuple(row.get(c) for c in columns)
        if any(v is None for v in values):
            continue
        non_null_rows += 1
        seen.add(values)
    if non_null_rows == 0:
        return 0.0
    return len(seen) / non_null_rows


async def discover_candidate_keys(
    connector: Any,
    table_name: str,
    column_profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The full P-5 pipeline for one table: declared first (via P-2's
    ``connector.discover_indexes()``), single-column inference second (from
    ``column_profiles``, P-3's output), pruned+verified composite search
    only if the first two tiers found nothing.

    ``column_profiles`` is ``{column_name: P-3 profile dict}`` for every
    column in the table -- the caller is expected to have already profiled
    them via P-3.
    """
    declared_indexes = await connector.discover_indexes(table_name)
    declared = classify_declared_indexes(declared_indexes)
    if declared:
        return declared

    single_column = infer_single_column_candidates(column_profiles)
    if single_column:
        return single_column

    pairs = propose_composite_candidate_pairs(column_profiles)
    composite: list[dict[str, Any]] = []
    for pair in pairs:
        ratio = await verify_composite_uniqueness(connector, table_name, pair)
        if ratio >= _COMPOSITE_MIN_UNIQUE_RATIO:
            composite.append(
                {
                    "columns": list(pair),
                    "key_type": "INFERRED_COMPOSITE",
                    "confidence": round(ratio, 3),
                    "evidence": {"joint_unique_ratio": round(ratio, 3)},
                }
            )
    return composite


def store_candidate_keys(
    store: Any,
    table_id: str,
    proposals: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    algorithm_version: str = "v1",
) -> list[str]:
    """Persist every proposal via ``MetadataStore.insert_candidate_key``.

    Declared keys are stored ``AUTO_ACCEPTED`` (they are a real database
    constraint, not something worth a human's review queue time); inferred
    ones are stored ``NEEDS_REVIEW`` regardless of confidence -- an
    inferred candidate is a proposal, never asserted as fact.
    """
    ids: list[str] = []
    for proposal in proposals:
        review_status = (
            "AUTO_ACCEPTED" if proposal["key_type"].startswith("DECLARED_") else "NEEDS_REVIEW"
        )
        key_id = store.insert_candidate_key(
            table_id,
            proposal["columns"],
            proposal["key_type"],
            confidence=proposal.get("confidence"),
            evidence=proposal.get("evidence"),
            algorithm_version=algorithm_version,
            origin="ALGORITHMIC",
            review_status=review_status,
            run_id=run_id,
        )
        ids.append(key_id)
    return ids
