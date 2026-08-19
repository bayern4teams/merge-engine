"""V3 relationship discovery & scoring (Part B, P-6).

Extends ``join_graph.py`` rather than replacing it: the exact,
database-verified inclusion-coverage computation this module uses is the
same formula ``join_graph._verify_inclusion`` already established (coverage
= |distinct(child) intersect distinct(parent)| / |distinct(child)|,
computed by filtering the parent to the child's own sampled values, never
two independent samples that coincidentally miss each other on a small
table). ``join_graph.py`` stays the primary path for building the graph
V2's pipeline actually joins on; this module adds the full ten-signal,
evidence-carrying score PreSplink §21 asks for, on top of it.

Ten signals, each returned as its own named entry -- never collapsed into
one opaque number (PreSplink §22): datatype, semantic_type, name_similarity,
value_overlap, inclusion_coverage, target_uniqueness, cardinality,
null_behaviour, table_semantics, identifier_likelihood.

``value_overlap`` and ``inclusion_coverage`` are deliberately two different
signals, not the same computation twice: ``value_overlap`` is a cheap,
approximate check over whatever sample is already at hand (the same kind of
job datasketch's MinHashLSHEnsemble does at larger scale, per ADR 0007 --
this module's own set-based version is what actually runs today);
``inclusion_coverage`` is the exact, database-verified figure from a
targeted query, expensive enough that it is only worth computing once
``value_overlap`` has already made the pair look plausible.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from src.utils.column_naming import normalize_column_name

logger = logging.getLogger("merge-engine.tools.relationship_scoring")

# The plan's own list, PreSplink §21, in this fixed order so a caller can
# always tell which of the ten was never computed (missing key) versus
# computed as zero.
SIGNAL_NAMES = (
    "datatype",
    "semantic_type",
    "name_similarity",
    "value_overlap",
    "inclusion_coverage",
    "target_uniqueness",
    "cardinality",
    "null_behaviour",
    "table_semantics",
    "identifier_likelihood",
)

VALID_RELATIONSHIP_TYPES = frozenset({"FOREIGN_KEY", "SHARED_LOOKUP", "POSSIBLE_JOIN", "UNRELATED"})

_IRREGULAR_PLURALS = {"people": "person", "categories": "category"}


def _singularise(name: str) -> str:
    """Local copy of join_graph.py's own singulariser -- not imported, to
    keep this module free to be called from either the same or a different
    layer without creating a dependency on join_graph.py's private names."""
    n = name.lower()
    if n in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[n]
    if n.endswith("ies") and len(n) > 3:
        return n[:-3] + "y"
    if n.endswith("es") and len(n) > 2 and n[-3] in "sxzh":
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss") and len(n) > 1:
        return n[:-1]
    return n


def _normalize_type(data_type: str) -> str:
    """Coarse type family, so ClickHouse's `String` and PostgreSQL's
    `character varying` are recognized as the same family instead of
    scoring a false type mismatch just because the two engines spell it
    differently."""
    t = (data_type or "").lower()
    if any(s in t for s in ("int", "serial", "numeric", "decimal", "float", "double")):
        return "numeric"
    if any(s in t for s in ("char", "text", "string", "uuid")):
        return "text"
    if "date" in t or "time" in t:
        return "temporal"
    if "bool" in t:
        return "boolean"
    return "other"


def score_datatype(child_data_type: str, parent_data_type: str) -> dict[str, Any]:
    child_family = _normalize_type(child_data_type)
    parent_family = _normalize_type(parent_data_type)
    match = child_family == parent_family
    return {
        "score": 1.0 if match else 0.0,
        "evidence": {"child_family": child_family, "parent_family": parent_family},
    }


def score_semantic_type(
    child_semantic_type: str | None, parent_semantic_type: str | None
) -> dict[str, Any]:
    """A child FK-shaped column pointing at a parent that P-4 also
    classified as IDENTIFIER/FOREIGN_KEY is corroborating; two columns
    with unrelated semantic types (e.g. EMAIL -> CURRENCY_AMOUNT) is
    strong evidence AGAINST the relationship."""
    compatible_targets = {"IDENTIFIER", "FOREIGN_KEY"}
    if child_semantic_type is None or parent_semantic_type is None:
        return {"score": 0.5, "evidence": {"reason": "one or both columns not yet classified"}}
    if parent_semantic_type in compatible_targets and child_semantic_type in (
        compatible_targets | {"UNKNOWN"}
    ):
        return {
            "score": 1.0,
            "evidence": {"child": child_semantic_type, "parent": parent_semantic_type},
        }
    if child_semantic_type == parent_semantic_type:
        return {
            "score": 0.6,
            "evidence": {"child": child_semantic_type, "parent": parent_semantic_type},
        }
    return {
        "score": 0.1,
        "evidence": {"child": child_semantic_type, "parent": parent_semantic_type},
    }


def score_name_similarity(
    child_table: str, child_column: str, parent_table: str, parent_column: str
) -> dict[str, Any]:
    """Two checks, either of which is enough to count as a strong match:
    the columns share a name (companyID == companyID), or the child column
    follows the `<singular parent table>_id`/`<ParentTable>ID` convention
    this project's own schemas already use (Pagila-style)."""
    child_norm = child_column.lower().replace("_", "")
    parent_norm = parent_column.lower().replace("_", "")
    exact_column_match = child_norm == parent_norm

    singular_parent = _singularise(parent_table).replace("_", "")
    convention_match = child_norm in (
        f"{singular_parent}id",
        f"{singular_parent}_id",
        f"{singular_parent}uuid",
    )

    ratio = SequenceMatcher(None, child_norm, parent_norm).ratio()
    score = 1.0 if (exact_column_match or convention_match) else round(ratio, 3)
    return {
        "score": score,
        "evidence": {
            "exact_column_match": exact_column_match,
            "convention_match": convention_match,
            "string_similarity_ratio": round(ratio, 3),
        },
    }


def score_value_overlap(child_sample: list[Any], parent_sample: list[Any]) -> dict[str, Any]:
    """Cheap, approximate overlap over two ALREADY-fetched samples -- no
    query of its own. Distinct from ``inclusion_coverage``, which targets
    the parent query at the child's exact values instead of hoping two
    independent samples happen to intersect."""
    child_set = {str(v) for v in child_sample if v is not None}
    parent_set = {str(v) for v in parent_sample if v is not None}
    if not child_set:
        return {"score": 0.0, "evidence": {"reason": "empty child sample"}}
    overlap = len(child_set & parent_set) / len(child_set)
    return {
        "score": round(overlap, 3),
        "evidence": {"child_sample_size": len(child_set), "parent_sample_size": len(parent_set)},
    }


async def score_inclusion_coverage(
    connector: Any,
    child_table: str,
    child_column: str,
    parent_table: str,
    parent_column: str,
    sample_size: int = 500,
) -> dict[str, Any]:
    """The exact, database-verified coverage(child->parent) = |distinct
    (child) intersect distinct(parent)| / |distinct(child)| -- the same
    formula and the same targeted-filter technique join_graph.py's
    ``_verify_inclusion`` already established (independent sampling
    live-verified to undercount badly on a small-parent/large-child pair;
    filtering the parent to the child's own values answers the real
    question directly)."""
    try:
        child_vals = await connector.sample_distinct_values(
            child_table, child_column, limit=sample_size
        )
    except Exception as exc:
        logger.debug("inclusion_coverage: child sampling failed: %s", exc)
        return {"score": None, "evidence": {"error": str(exc)}}

    child_norm = {str(v) for v in child_vals if v is not None}
    if not child_norm:
        return {"score": None, "evidence": {"reason": "no usable child values"}}

    try:
        parent_matches = await connector.sample_distinct_values(
            parent_table,
            parent_column,
            limit=sample_size,
            filter_values=[v for v in child_vals if v is not None],
        )
    except Exception as exc:
        logger.debug("inclusion_coverage: parent lookup failed: %s", exc)
        return {"score": None, "evidence": {"error": str(exc)}}

    parent_norm = {str(v) for v in parent_matches}
    coverage = len(child_norm & parent_norm) / len(child_norm)
    return {
        "score": round(coverage, 3),
        "evidence": {
            "child_distinct_sampled": len(child_norm),
            "matched_in_parent": len(parent_norm & child_norm),
        },
    }


def score_target_uniqueness(parent_key_types: list[str]) -> dict[str, Any]:
    """A parent column backed by a declared or inferred candidate key
    (P-5) is a plausible FK target; one with none is not -- a relationship
    pointing at a non-key column is at best a SHARED_LOOKUP, never a real
    foreign key."""
    if any(t.startswith("DECLARED_") for t in parent_key_types):
        return {"score": 1.0, "evidence": {"parent_key_types": parent_key_types}}
    if parent_key_types:
        return {"score": 0.6, "evidence": {"parent_key_types": parent_key_types}}
    return {"score": 0.0, "evidence": {"parent_key_types": []}}


def score_cardinality(
    child_unique_ratio: float | None, parent_unique_ratio: float | None
) -> dict[str, Any]:
    """The classic FK shape: parent side near-unique (it's the one side),
    child side lower (the many side). Both near-unique suggests a 1:1
    relationship instead -- still plausible, just a different
    ``relationship_type``, decided by the caller from this evidence."""
    if child_unique_ratio is None or parent_unique_ratio is None:
        return {"score": 0.5, "evidence": {"reason": "uniqueness not yet measured on one side"}}
    if parent_unique_ratio >= 0.95 and child_unique_ratio < 0.95:
        inferred = "many_to_one"
        score = 1.0
    elif parent_unique_ratio >= 0.95 and child_unique_ratio >= 0.95:
        inferred = "one_to_one"
        score = 0.9
    else:
        inferred = "many_to_many_or_unclear"
        score = 0.3
    return {
        "score": score,
        "evidence": {
            "inferred_cardinality": inferred,
            "child_unique_ratio": child_unique_ratio,
            "parent_unique_ratio": parent_unique_ratio,
        },
    }


def score_null_behaviour(child_null_ratio: float | None) -> dict[str, Any]:
    """A heavily-null FK-candidate column is weaker evidence of a real,
    consistently-populated relationship -- an optional FK is still a real
    relationship type, just a less certain one from null ratio alone."""
    if child_null_ratio is None:
        return {"score": 0.5, "evidence": {"reason": "not yet profiled"}}
    return {
        "score": round(max(0.0, 1.0 - child_null_ratio), 3),
        "evidence": {"child_null_ratio": child_null_ratio},
    }


def score_table_semantics(child_table: str, parent_table: str) -> dict[str, Any]:
    """The child table's name plausibly refers to the parent table
    (`CompanyFinancials` -> `Company`, `experience` -> the singular
    `person`/`company` it belongs to) -- a coarse but real signal that
    costs nothing beyond string comparison."""
    child_norm = child_table.lower().replace("_", "")
    parent_singular = _singularise(parent_table).replace("_", "")
    plausible = parent_singular in child_norm
    return {
        "score": 1.0 if plausible else 0.3,
        "evidence": {"child_table": child_table, "parent_table": parent_table},
    }


def score_identifier_likelihood(
    child_semantic_type: str | None, child_column: str
) -> dict[str, Any]:
    """Reuses P-4's classification when available; falls back to the same
    generic id-shape check P-5 uses when P-4 hasn't run for this column
    yet, so this signal never silently returns nothing."""
    if child_semantic_type in ("FOREIGN_KEY", "IDENTIFIER"):
        return {"score": 1.0, "evidence": {"source": "semantic_type", "value": child_semantic_type}}
    if re.search(r"_id$|^id$|_uuid$|^uuid$", normalize_column_name(child_column)):
        return {"score": 0.7, "evidence": {"source": "name_shape"}}
    return {"score": 0.1, "evidence": {"source": "no signal"}}


def _classify_relationship_type(signals: dict[str, dict[str, Any]], overall: float) -> str:
    if overall < 0.4:
        return "UNRELATED"
    target_uniqueness = signals["target_uniqueness"]["score"]
    cardinality_evidence = signals["cardinality"]["evidence"]
    if (
        overall >= 0.7
        and target_uniqueness >= 0.6
        and cardinality_evidence.get("inferred_cardinality") in ("many_to_one", "one_to_one")
    ):
        return "FOREIGN_KEY"
    if target_uniqueness < 0.6 and signals["value_overlap"]["score"] >= 0.5:
        return "SHARED_LOOKUP"
    return "POSSIBLE_JOIN"


def score_relationship(signals: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Combine the ten already-computed signals into an overall confidence
    and a relationship_type -- never the sole thing stored (every
    individual signal is kept in ``signals`` for the UI/human review,
    PreSplink §22's "no opaque score"), just the summary a caller needing
    one number can use."""
    scored = [v["score"] for v in signals.values() if v.get("score") is not None]
    overall = round(sum(scored) / len(scored), 3) if scored else 0.0
    return {
        "confidence": overall,
        "relationship_type": _classify_relationship_type(signals, overall),
        "signals": signals,
    }
