"""
unify_entity_group tool implementation.

Merges attributes within a cluster: complement nulls, flag conflicts,
produce unified record with conflict details.

Called both by MCP server dispatch and directly by orchestrator nodes.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from src.mcp_server.tools.survivorship import measure_field_reliability
from src.utils.value_coercion import parse_magnitude

logger = logging.getLogger("merge-engine.tools.unify_entity_group")

_RECENCY_FIELDS = (
    "updated_at",
    "modified_at",
    "last_modified",
    "updated_on",
    "last_updated",
    "modified_on",
)


def _completeness(value: Any) -> int:
    """String length of a value (0 for None). Used by ``longest`` and ``shortest``."""
    if value is None:
        return 0
    return len(str(value).strip())


def _recency_key(rec: dict[str, Any]) -> str:
    """Comparable recency stamp from an updated_at-like field, '' if none.

    ISO-ish timestamps compare correctly as strings; heterogeneous or
    missing stamps simply lose the tiebreak.
    """
    for f in _RECENCY_FIELDS:
        v = rec.get(f)
        if v is not None and str(v).strip():
            return str(v)
    return ""


# The order applied when a field matches none of _DEFAULT_FIELD_POLICIES
# below and no per-field/`_default` override was configured. Kept as the
# last-resort fallback because it is what every run before Phase O used.
DEFAULT_POLICY = ("priority", "longest", "recency")

# P18-3: ``completeness`` was raw string length, which no surveyed vendor
# ships under that name (Profisee's "most complete" is fewest NULLs across a
# record; only Data Ladder has a plain "longest"). Renamed, and the old name
# kept as a config alias so saved profiles still load with the same meaning on
# text. Chose the rename over a record-level fewest-NULLs rule: it needs no new
# record-level machinery and cannot change any stored config's winners.
_RULE_ALIASES = {"completeness": "longest"}

# ponytail: these five repair the rules, they do not make survivorship
# non-destructive -- the losing values are still gone at the end of the run.
# Deferred: read-time survivorship over a lossless corpus (report section 3.4;
# trigger: Part 3, where "the loser is still retrievable" becomes true).
_KNOWN_RULES = {
    "priority",
    "longest",
    "recency",
    "shortest",
    "first_seen",
    "numeric_max",
    "numeric_min",
    "non_null",
    "measured_reliability",
}

# Smart built-in defaults by field-name pattern (fnmatch glob syntax),
# checked in order — first match wins. Before this table every field used
# DEFAULT_POLICY, which is why a longer numeric-looking string ("longest"
# is raw string length) could out-rank the actual larger number for fields
# like revenue, and why an identity field like `name`/`website` could be
# decided by which value happened to be a longer string rather than by which
# origin is actually more authoritative.
#
# "measured_reliability" (Phase A6) sits between "priority" and "non_null"
# for identity fields: an explicit, user-set priority still wins outright
# when configured, but "priority" is a no-op tiebreak when every origin
# defaults to 0 (the common case — most users never set it), and used to
# fall straight through to "non_null" (also a no-op here) then "recency"
# (only useful if a timestamp field exists). measured_reliability replaces
# that blind fallthrough with each origin's OBSERVED completeness for this
# specific field, so "which origin actually has better data for `name`" is
# answered from the data rather than left to whichever record happened to
# come first.
_DEFAULT_FIELD_POLICIES: list[tuple[str, tuple[str, ...]]] = [
    ("revenue", ("numeric_max", "priority")),
    ("employee_count", ("numeric_max", "priority")),
    ("*_count", ("numeric_max", "priority")),
    ("*_amount", ("numeric_max", "priority")),
    ("total_*", ("numeric_max", "priority")),
    ("name", ("priority", "measured_reliability", "non_null", "recency")),
    ("legal_name", ("priority", "measured_reliability", "non_null", "recency")),
    ("website", ("priority", "measured_reliability", "non_null", "recency")),
    ("domain", ("priority", "measured_reliability", "non_null", "recency")),
    ("ticker", ("priority", "measured_reliability", "non_null", "recency")),
    ("founded_date", ("priority", "measured_reliability", "non_null", "recency")),
    ("*_id", ("priority", "measured_reliability", "non_null", "recency")),
    ("description", ("longest", "priority")),
    ("summary", ("longest", "priority")),
    ("*_notes", ("longest", "priority")),
    ("created_at", ("recency",)),
    ("updated_at", ("recency",)),
    ("*_at", ("recency",)),
]


def _default_policy_for_field(field: str) -> tuple[str, ...]:
    """The smart built-in chain for a field nothing more specific covers."""
    lname = field.lower()
    for pattern, chain in _DEFAULT_FIELD_POLICIES:
        if fnmatch.fnmatch(lname, pattern):
            return chain
    return DEFAULT_POLICY


def resolve_policy(field: str, conflict_policy: dict[str, Any] | None) -> tuple[str, ...]:
    """The rule chain to apply to one field.

    A policy maps a field name to an ordered list of rules, with ``_default``
    covering everything not named by the caller. Absent either, the field
    falls to the smart built-in per-field-class default
    (``_default_policy_for_field``), and only if nothing matches that does it
    fall to the historical ``DEFAULT_POLICY``. An unknown rule name is
    dropped with a warning rather than silently treated as "first_seen" — a
    typo in a survivorship rule would otherwise quietly pick arbitrary
    values.
    """
    policy = conflict_policy or {}
    chain = policy.get(field, policy.get("_default", _default_policy_for_field(field)))
    if isinstance(chain, str):
        chain = (chain,)
    chain = [_RULE_ALIASES.get(r, r) for r in chain]
    usable = tuple(r for r in chain if r in _KNOWN_RULES)
    unknown = [r for r in chain if r not in _KNOWN_RULES]
    fallback = _default_policy_for_field(field)
    if unknown:
        logger.warning(
            "Ignoring unknown conflict rule(s) %s for field '%s'; using %s",
            unknown,
            field,
            list(usable) or list(fallback),
        )
    return usable or fallback


def _numeric_value(value: Any) -> float | None:
    """A field value as a plain number, or None if it isn't one.

    Reuses the Phase 4 money/magnitude parser (`parse_magnitude`) so a
    conflict between "$6.1 Million" and "$6.7 Billion" compares on actual
    magnitude, not on which string happens to be longer.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_magnitude(value)


def _apply_rule(
    rule: str,
    candidates: list[tuple[dict[str, Any], str]],
    field: str,
    origin_priorities: dict[Any, int],
    field_reliability: dict[str, dict[str, float]] | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """Narrow the candidates by one rule. Never returns an empty list."""
    if rule == "priority":
        # Higher priority number wins (diffbot: 1 vs zoominfo: 2 -> zoominfo).
        # Do not reverse this: "1 is highest" is the common convention, but
        # flipping the direction would silently change every saved config.
        # A (origin, field) entry overrides the origin-level value (P18-2).
        def rank(o: str) -> int:
            return origin_priorities.get((o, field), origin_priorities.get(o, 0))

        best = max(rank(o) for _, o in candidates)
        return [(r, o) for r, o in candidates if rank(o) == best]
    if rule == "measured_reliability":
        # Phase A6: each origin's OBSERVED completeness for this field
        # (survivorship.measure_field_reliability), not an asserted number.
        # A score of 0.0 means "no measured data for this origin/field" --
        # never treated as a real signal, so an unmeasured run (no
        # field_reliability supplied at all) degrades to a no-op exactly
        # like "priority" does when every origin defaults to 0.
        scores = field_reliability or {}
        best_score = max(scores.get(o, {}).get(field, 0.0) for _, o in candidates)
        if best_score <= 0.0:
            return candidates
        return [(r, o) for r, o in candidates if scores.get(o, {}).get(field, 0.0) == best_score]
    if rule == "longest":
        # Text only: string length proxies information for prose, not for a
        # number (9.5 is "longer" than 10). Any non-text candidate -> no-op.
        if not all(isinstance(r.get(field), str) for r, _ in candidates):
            return candidates
        best = max(_completeness(r.get(field)) for r, _ in candidates)
        return [(r, o) for r, o in candidates if _completeness(r.get(field)) == best]
    if rule == "shortest":
        # For fields where the terse value is the canonical one — a bare
        # phone number over the same number wrapped in formatting.
        best = min(
            _completeness(r.get(field)) for r, _ in candidates if _completeness(r.get(field))
        )
        return [(r, o) for r, o in candidates if _completeness(r.get(field)) == best]
    if rule == "recency":
        stamps = [_recency_key(r) for r, _ in candidates]
        best_stamp = max(stamps)
        if not best_stamp:
            return candidates
        return [(r, o) for (r, o), s in zip(candidates, stamps, strict=True) if s == best_stamp]
    if rule in ("numeric_max", "numeric_min"):
        scored = [(r, o, _numeric_value(r.get(field))) for r, o in candidates]
        numeric = [(r, o, v) for r, o, v in scored if v is not None]
        if not numeric:
            # Nothing parses as a number (e.g. a hex id) — this rule has
            # nothing to discriminate on, so let the next rule in the chain
            # decide instead of arbitrarily picking one.
            return candidates
        target = (
            max(v for _, _, v in numeric)
            if rule == "numeric_max"
            else min(v for _, _, v in numeric)
        )
        return [(r, o) for r, o, v in numeric if v == target]
    if rule == "non_null":
        # A no-op by construction: _resolve_conflict is only ever called on
        # already-non-null values, so every remaining candidate already
        # satisfies this. Listed as a real, known rule (rather than silently
        # dropped as unknown) so a policy chain that spells it out — e.g.
        # identity fields' "priority -> non_null -> recency" — is honest
        # about what each step actually does.
        return candidates
    if rule == "first_seen":
        # Total order, independent of extraction order: origin ASC, then source
        # table, then source row id ASC (Semarchy's last-resort ranking; Reltio
        # patched this exact bug). Ties that reach this rule are, by definition,
        # indistinguishable on every rule tried before it.
        return [
            min(
                candidates,
                key=lambda c: (
                    str(c[1]),
                    str(c[0].get("_origin_table", "")),
                    str(c[0].get("_origin_id_row", "")),
                ),
            )
        ]
    return candidates


def _resolve_conflict(
    non_null_values: list[tuple[dict[str, Any], str]],
    field: str,
    origin_priorities: dict[Any, int],
    conflict_policy: dict[str, Any] | None = None,
    field_reliability: dict[str, dict[str, float]] | None = None,
) -> tuple[tuple[dict[str, Any], str], str]:
    """Pick the winning (record, origin) by this field's configured rules.

    Returns the winner and the name of the rule that decided it, so the
    reconciliation report can say *why* a value won rather than only which
    one did.
    """
    candidates = list(non_null_values)
    for rule in resolve_policy(field, conflict_policy):
        candidates = _apply_rule(rule, candidates, field, origin_priorities, field_reliability)
        if len(candidates) == 1:
            return candidates[0], rule

    return _apply_rule("first_seen", candidates, field, origin_priorities)[0], "first_seen"


async def unify_entity_group_impl(
    cluster_id: str,
    records: list[dict[str, Any]],
    origins: list[dict[str, Any]] | None = None,
    conflict_policy: dict[str, Any] | None = None,
    field_reliability: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Unify a cluster of matching records into one record.

    Parameters
    ----------
    cluster_id : str
        Unique identifier for the cluster.
    records : list[dict]
        All records in this cluster.
    origins : list[dict], optional
        Origin configs (each with "id" and "priority") used to resolve
        conflicts in favor of the higher-priority origin. The HIGHER priority
        NUMBER wins (do not reverse; see ``_apply_rule``). An optional
        ``"field_priorities": {field: int}`` overrides ``priority`` for that
        one field of that origin.
    field_reliability : dict, optional
        ``{origin_id: {field: completeness_ratio}}`` (Phase A6,
        ``survivorship.measure_field_reliability``), measured once per
        entity type across ALL of that origin's records — a single
        cluster is usually too small a sample to measure anything
        meaningful from on its own. Feeds the ``"measured_reliability"``
        conflict rule. When omitted (the MCP-handler path) it is measured from
        this cluster's own records, so the rule is never a silent no-op.

    Returns
    -------
    dict
        {
            "cluster_id": str,
            "unified_record": dict,
            "conflicts": list[dict],
            "null_filled_fields": list[str],
            "source_count": int,
        }
    """
    logger.debug(
        "ENTER unify_entity_group_impl: cluster_id=%s, records_count=%d", cluster_id, len(records)
    )

    if not records:
        logger.debug("No records to unify, raising ValueError")
        raise ValueError("Cannot unify empty record list")

    conflicts: list[dict[str, Any]] = []
    null_filled: list[str] = []
    unified: dict[str, Any] = {}
    sources: set[str] = set()

    # Collect all field names across all records
    all_fields: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for rec in records:
        origin = rec.get("_origin_id", rec.get("origin_id", "unknown"))
        sources.add(origin)
        for key in rec:
            if key.startswith("_"):
                continue
            all_fields.setdefault(key, []).append((rec, origin))

    logger.debug(
        "Collected %d fields from %d records across %d sources",
        len(all_fields),
        len(records),
        len(sources),
    )

    # Priority order: the higher priority NUMBER wins (do not reverse).
    # (origin, field) entries override the origin-level value (P18-2).
    origin_priorities: dict[Any, int] = {o.get("id"): o.get("priority", 0) for o in (origins or [])}
    for o in origins or []:
        for f, p in (o.get("field_priorities") or {}).items():
            origin_priorities[(o.get("id"), f)] = p
    if field_reliability is None:
        # P18-2: with no precomputed table the rule was a silent no-op. A
        # cluster is a small sample, but it is the only evidence on this path.
        field_reliability = measure_field_reliability(records)
    logger.debug("Origin priorities: %s", origin_priorities)

    fields_processed = 0
    fields_with_conflicts = 0
    fields_complemented = 0
    for field, origin_values in all_fields.items():
        non_null_values = [(rec, orig) for rec, orig in origin_values if rec.get(field) is not None]
        null_count = len(origin_values) - len(non_null_values)
        fields_processed += 1

        if len(non_null_values) == 1:
            # Only one non-null value — complement: fill null with this
            rec, orig = non_null_values[0]
            unified[field] = rec[field]
            if null_count > 0:
                null_filled.append(field)
                fields_complemented += 1
            logger.debug("Field '%s': single non-null value from %s", field, orig)
        elif len(non_null_values) == 0:
            # All null — skip this field
            logger.debug("Field '%s': all null, skipping", field)
            continue
        else:
            # Multiple non-null values
            values = [v[0][field] for v in non_null_values]

            # Check if all values agree
            if all(v == values[0] for v in values):
                unified[field] = values[0]
                logger.debug(
                    "Field '%s': all %d values agree on '%s'", field, len(values), values[0]
                )
            else:
                # Conflict! priority → completeness → recency tiebreaks
                fields_with_conflicts += 1
                best, rule = _resolve_conflict(
                    non_null_values, field, origin_priorities, conflict_policy, field_reliability
                )
                unified[field] = best[0][field]

                conflict_record: dict[str, Any] = {
                    "field": field,
                    "values": [
                        {"origin": orig, "value": rec[field]} for rec, orig in non_null_values
                    ],
                    "selected_origin": best[1],
                    "selected_value": best[0][field],
                    "winning_rule": rule,
                }
                if rule in ("numeric_max", "numeric_min"):
                    # P18-5: still resolved above, but two sources disagreeing
                    # on a number is a data-quality alert, not just a tiebreak.
                    nums = [
                        v
                        for v in (_numeric_value(r[field]) for r, _ in non_null_values)
                        if v is not None
                    ]
                    spread = max(nums) - min(nums)
                    peak = max(abs(v) for v in nums)
                    conflict_record["numeric_disagreement"] = {
                        "min": min(nums),
                        "max": max(nums),
                        "spread": spread,
                        "relative_spread": spread / peak if peak else 0.0,
                    }
                if rule == "measured_reliability" and field_reliability:
                    # The actual evidence, not just the rule name -- what
                    # makes "name taken from origin_1" into "name taken
                    # from origin_1 (98% complete vs 41%)" at the gate.
                    conflict_record["reliability_scores"] = {
                        orig: field_reliability.get(orig, {}).get(field, 0.0)
                        for _, orig in non_null_values
                    }
                conflicts.append(conflict_record)
                logger.debug(
                    "Field '%s': CONFLICT! %d values, selected %s from %s (rule=%s)",
                    field,
                    len(values),
                    best[0][field],
                    best[1],
                    rule,
                )

    # Preserve cluster-level metadata
    unified["_cluster_id"] = cluster_id
    unified["_source_count"] = len(sources)

    logger.debug(
        "EXIT unify_entity_group_impl: cluster_id=%s, unified_fields=%d, conflicts=%d, null_filled=%d, sources=%d",
        cluster_id,
        len(unified),
        len(conflicts),
        len(null_filled),
        len(sources),
    )
    return {
        "cluster_id": cluster_id,
        "unified_record": unified,
        "conflicts": conflicts,
        "null_filled_fields": null_filled,
        "source_count": len(sources),
    }


async def unify_entity_group_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    logger.debug("ENTER unify_entity_group_handler: arguments_keys=%s", list(arguments.keys()))
    return await unify_entity_group_impl(**arguments)
