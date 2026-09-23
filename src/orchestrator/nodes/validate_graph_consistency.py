"""validate_graph_consistency node: post-merge consistency rules over
just-written destination facts (audit §13 #9, fix-v3 B.4.2).

Runs after ``load_data``, before ``update_extraction_state``. Rules-as-data
(``config/consistency_rules.yaml``), evaluated by plain-Python clause
matching — no rule-engine dependency (ADR 0021: ``semantica`` rejected as
immature at install time for this narrow a job).

Scope, adapted to the REAL destination schema (verified live, not
guessed): every fact in the ``kg`` schema is bitemporal
(``entity``/``knowledge_fact``/``fact_predicate``), including ``company``
and ``investment_round`` themselves — there is no flat ``company.status``
*column* to compare, and the plan's own worked example
("investment_round.valid_from") does not correspond to a real
subject-entity-typed predicate (``investment_round`` is not one of
``fact_predicate``'s allowed ``subject_entity_type`` values). Rules here
therefore compare two facts recorded on the SAME subject entity — the
narrowest shape that still covers the audit's own worked example ("an
ACQUIRED company's investment shouldn't postdate its acquisition"), just
modeled as two facts on the company rather than a cross-entity join.

ponytail: only one comparison op (``after_when_valid_from``) and same-
entity facts are implemented. Upgrade path: add more ``compare_op``
values, or a ``related_via`` cross-entity link predicate, when a rule
genuinely needs one — the pure evaluator (``check_consistency_rules``)
and the rule file are both additive, no rule-engine dependency to swap.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("merge-engine.orchestrator.validate_graph_consistency")

_RULES_PATH = Path("config") / "consistency_rules.yaml"


def _load_consistency_rules() -> list[dict[str, Any]]:
    """Rules-as-data from ``config/consistency_rules.yaml``. Missing file
    or empty ``rules:`` -> ``[]`` (fail-open on ABSENT config only — a
    rule that parses and evaluates true is never swallowed)."""
    if not _RULES_PATH.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning(
            "Could not parse %s (%s); consistency checks skipped this run.", _RULES_PATH, exc
        )
        return []
    rules = data.get("rules") or []
    return [r for r in rules if isinstance(r, dict) and r.get("id")]


def check_consistency_rules(
    facts_by_entity: dict[str, dict[str, dict[str, Any]]],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pure rule evaluator — no I/O, easily faked in tests.

    ``facts_by_entity``: ``{entity_id: {predicate_code: {"value": ...,
    "valid_from": date|None}}}`` — already-fetched facts, one dict per
    entity. Returns ``[{"rule_id", "entity_id", "severity", "detail"}, ...]``.
    """
    violations: list[dict[str, Any]] = []
    for rule in rules:
        when_pred = rule.get("when_predicate")
        when_value = rule.get("when_value")
        compare_pred = rule.get("compare_predicate")
        op = rule.get("compare_op")
        if not (when_pred and compare_pred and op):
            continue
        for entity_id, facts in facts_by_entity.items():
            when_fact = facts.get(when_pred)
            if not when_fact or when_fact.get("value") != when_value:
                continue
            compare_fact = facts.get(compare_pred)
            if not compare_fact:
                continue
            if op == "after_when_valid_from":
                when_valid_from = when_fact.get("valid_from")
                compare_value = compare_fact.get("value")
                if not (when_valid_from and compare_value and compare_value > when_valid_from):
                    continue
                detail = (
                    f"{when_pred}={when_value!r} (effective {when_valid_from}) but "
                    f"{compare_pred}={compare_value!r} is later"
                )
            else:
                # Unknown compare_op -- a config typo, not a violation.
                logger.warning("Rule %r: unknown compare_op %r, skipped.", rule.get("id"), op)
                continue
            violations.append(
                {
                    "rule_id": rule.get("id"),
                    "entity_id": entity_id,
                    "severity": rule.get("severity", "warn"),
                    "detail": detail,
                }
            )
    return violations


def _fetch_facts(
    connector: Any, predicate_codes: list[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    """Real facts for the given predicate codes, across every entity that
    has one, via the destination connector's generic ``execute_sql``
    escape hatch (B.1.3). Grouped ``{entity_id: {predicate_code: {value,
    valid_from}}}``."""
    if not predicate_codes:
        return {}
    rows = connector.execute_sql(
        "SELECT e.entity_id::text, fp.code, kf.value_text, kf.value_code, "
        "kf.value_date, kf.value_numeric, kf.value_boolean, kf.valid_from "
        "FROM kg.entity e "
        "JOIN kg.knowledge_fact kf ON kf.subject_entity_id = e.entity_id "
        "JOIN kg.fact_predicate fp ON fp.predicate_id = kf.predicate_id "
        "WHERE fp.code = ANY(%s)",
        (predicate_codes,),
    )
    facts: dict[str, dict[str, dict[str, Any]]] = {}
    for (
        entity_id,
        code,
        value_text,
        value_code,
        value_date,
        value_numeric,
        value_boolean,
        valid_from,
    ) in rows:
        value: Any = (
            value_boolean
            if value_boolean is not None
            else (value_code or value_text or value_date or value_numeric)
        )
        facts.setdefault(str(entity_id), {})[code] = {"value": value, "valid_from": valid_from}
    return facts


async def validate_graph_consistency(state: dict[str, Any]) -> dict[str, Any]:
    """Evaluate configured consistency rules against just-written
    destination facts, folding violations into the existing
    ``reconciliation_report.data_quality.red_flags`` — one report, not two
    (same fold-in discipline as B.1.4's GX pass).

    Best-effort throughout: an absent rule file, an unreadable
    destination, or a missing connector capability all degrade to a no-op
    update (``{}``) rather than failing the run — this gate exists to
    surface problems to a human reviewer, never to abort a merge by
    itself.
    """
    logger.debug("ENTER validate_graph_consistency")
    rules = _load_consistency_rules()
    if not rules:
        logger.info("No consistency rules configured (%s absent or empty); skipping.", _RULES_PATH)
        logger.debug("EXIT validate_graph_consistency (no rules)")
        return {}

    destination = state.get("destination")
    if not isinstance(destination, dict):
        logger.debug("EXIT validate_graph_consistency (no destination)")
        return {}

    try:
        from src.mcp_server.tools.load_data import _get_destination_connector

        connector = _get_destination_connector(destination)
        if not hasattr(connector, "execute_sql"):
            logger.debug("Destination connector has no execute_sql; consistency checks skipped.")
            logger.debug("EXIT validate_graph_consistency (no execute_sql)")
            return {}
        predicate_codes = sorted(
            {r["when_predicate"] for r in rules if r.get("when_predicate")}
            | {r["compare_predicate"] for r in rules if r.get("compare_predicate")}
        )
        facts = _fetch_facts(connector, predicate_codes)
    except Exception as exc:
        logger.warning(
            "Consistency check skipped (%s) — a destination read failure must never "
            "be what stops a merge.",
            exc,
        )
        logger.debug("EXIT validate_graph_consistency (read failed)")
        return {}

    violations = check_consistency_rules(facts, rules)
    if not violations:
        logger.info("Consistency checks: 0 violation(s) across %d rule(s).", len(rules))
        logger.debug("EXIT validate_graph_consistency (clean)")
        return {}

    reconciliation_report = dict(state.get("reconciliation_report") or {})
    data_quality = dict(reconciliation_report.get("data_quality") or {})
    red_flags = list(data_quality.get("red_flags") or [])
    for v in violations:
        red_flags.append(
            f"consistency rule '{v['rule_id']}' violated for entity {v['entity_id']}: {v['detail']}"
        )
    data_quality["red_flags"] = red_flags
    if data_quality.get("grade") in (None, "A"):
        data_quality["grade"] = "B"
    reconciliation_report["data_quality"] = data_quality

    logger.warning("Consistency checks: %d violation(s) found.", len(violations))

    # load_data already wrote logs/run_reports/<run_id>.json before this
    # node ran -- re-write it so the flag is actually visible on disk,
    # not just in in-memory state a later node might not persist.
    try:
        from src.orchestrator.nodes.load_data import _write_run_report

        _write_run_report(reconciliation_report)
    except Exception as exc:
        logger.warning("Could not re-write run report with consistency flags: %s", exc)

    logger.debug("EXIT validate_graph_consistency (%d violation(s))", len(violations))
    return {"reconciliation_report": reconciliation_report}
