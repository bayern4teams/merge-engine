"""
parse_request node: Groups discovered tables into entities and extracts
merge scope from the user's natural language request.

Entity GROUPING is deterministic (Phase 3.1's group_tables_into_entities) —
every table discover_schemas found is assigned to exactly one group before
the LLM is ever called, so a schema-grounded entity can no longer be
silently dropped the way 7 of 20 tables were in the 4th live run (Bugs/Logs/
4th_try_05_18_local_enrichprompt.txt). The LLM's remaining job is narrower
and lower-stakes: pick a readable name for each group, say which groups the
user's text explicitly named (vs. everything else, included by design so
"all information about X" and "just company records" both reach the same
completeness — see plan Context/Phase 3 for why), and extract the merge
subject/scope for a scoped merge. It cannot add, remove, or move a table
between groups; a mismatched or missing name falls back to the deterministic
key, never to fewer tables.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm import PROMPT_TEMPLATES, LLMClient
from src.orchestrator.fingerprint import origins_fingerprint, stage_fingerprint
from src.orchestrator.profiles import stage_cache_get
from src.orchestrator.utils.entity_grouping import group_tables_into_entities
from src.orchestrator.utils.retry import classify_exception

logger = logging.getLogger("merge-engine.orchestrator.parse_request")

_INACTIVE_SCOPE: dict[str, Any] = {"active": False, "subject": "", "scope_type": "none"}


def _discovered_table_pairs(schemas: dict[str, Any]) -> set[tuple[str, str]]:
    """Every (origin_id, table_name) discover_schemas actually found —
    the ground truth the post-condition below checks entity_tables against."""
    pairs: set[tuple[str, str]] = set()
    for origin_id, schema_data in (schemas or {}).items():
        for table in (schema_data or {}).get("tables", []) or []:
            name = (table or {}).get("name")
            if name:
                pairs.add((origin_id, name))
    return pairs


def _build_group_catalog(groups: dict[str, list[dict[str, str]]], schemas: dict[str, Any]) -> str:
    """Render each deterministic group with its member tables and a
    handful of columns per table, as the LLM's naming/classification input.

    Column lookup is by (origin_id, table name) against the full schema —
    entity_grouping.py only returns origin_id/table, not columns, since
    grouping itself never needs them.
    """
    columns_by_table: dict[tuple[str, str], list[str]] = {}
    for origin_id, schema_data in (schemas or {}).items():
        for table in (schema_data or {}).get("tables", []) or []:
            name = (table or {}).get("name")
            if not name:
                continue
            cols = [c.get("name", "") for c in table.get("columns", []) if c.get("name")]
            columns_by_table[(origin_id, name)] = cols

    lines: list[str] = []
    for group_key in sorted(groups):
        members = groups[group_key]
        lines.append(f"\nGroup: {group_key}")
        for m in members:
            oid, tbl = m["origin_id"], m["table"]
            cols = columns_by_table.get((oid, tbl), [])
            col_str = ", ".join(cols[:8])
            lines.append(f"  - {oid}.{tbl} ({col_str})" if col_str else f"  - {oid}.{tbl}")
    return "\n".join(lines) if lines else "(No discovered tables)"


async def parse_request(state: dict[str, Any]) -> dict[str, Any]:
    """Group discovered tables into entities and extract merge scope.

    Parameters
    ----------
    state : dict
        MergeState with ``user_request`` and ``schemas`` populated.

    Returns
    -------
    dict
        Update dict with ``target_entities``, ``explicit_target_entities``,
        ``merge_scope``, ``entity_tables``, and ``table_selection_notes``.
    """
    logger.debug("ENTER parse_request")
    logger.debug("State keys: %s", list(state.keys()))
    logger.debug("user_request (truncated): %.200s", state.get("user_request", ""))
    schemas = state.get("schemas", {})
    logger.debug("schemas count: %d", len(schemas))

    user_request: str = state["user_request"]
    llm_config: dict[str, Any] = state["llm_config"]

    # Subject-first mode (Phase 3.3): get_initial_state already set an
    # active merge_scope directly from the UI's company-name box. That is
    # authoritative — the LLM's own subject/scope_type guess (still asked
    # for, since the same call also does naming/classification) must not
    # override it, and a naming-call failure must not silently drop it back
    # to inactive either.
    pre_set_scope_raw = state.get("merge_scope")
    pre_set_scope: dict[str, Any] | None = (
        dict(pre_set_scope_raw)
        if isinstance(pre_set_scope_raw, dict) and pre_set_scope_raw.get("active")
        else None
    )
    if pre_set_scope is not None:
        logger.debug("Pre-set merge_scope from subject-first mode: %s", pre_set_scope)

    # Phase A4: reuse a profile-pinned decision if the origins' schema
    # shape AND the request text match exactly what it was computed from.
    # Request text is part of the fingerprint (not just the schema) because
    # explicit_target_entities/merge_scope depend on it as much as
    # entity_tables depends on the schema -- reusing them for a DIFFERENT
    # request would silently answer the wrong question. This is what fixes
    # entity_tables being "pinned then immediately overwritten" (O.1 was
    # inert): the pin is now actually consulted before any work happens,
    # not just seeded into state and ignored.
    origins = state.get("origins", []) or []
    profile_cache = state.get("_profile_cache") or {}
    entity_tables_fp = stage_fingerprint(
        "entity_tables",
        origins_fp=origins_fingerprint(origins, schemas),
        user_request=user_request,
        subject=(pre_set_scope or {}).get("subject", ""),
    )
    cached = stage_cache_get(profile_cache, "entity_tables", entity_tables_fp)
    if cached and cached.get("entity_tables"):
        logger.info(
            "Reusing entity_tables/target_entities from profile %r (fingerprint match)",
            state.get("profile_name"),
        )
        logger.debug("EXIT parse_request (reused from profile)")
        return {
            "target_entities": cached.get("target_entities", []),
            "explicit_target_entities": cached.get("explicit_target_entities", []),
            "merge_scope": pre_set_scope or cached.get("merge_scope", dict(_INACTIVE_SCOPE)),
            "entity_tables": cached["entity_tables"],
            "table_selection_notes": cached.get("table_selection_notes", ""),
            "interrupt_data": None,
            "reused_stages": ["entity_tables"],
            "_entity_tables_fingerprint": entity_tables_fp,
        }

    # group_tables_into_entities raises ValueError on any unassigned table —
    # deliberately NOT caught by the `except Exception` below. A grouping
    # failure is a bug in discover_schemas' output (an unnamed table), not
    # something the LLM-naming fallback can meaningfully paper over, and it
    # must not be misreported as "LLM call failed" the way it was before
    # this fix (the failure was previously an AssertionError raised *inside*
    # `_finish`, which `_finish` being called from within the `try` caused
    # to be caught, logged with the wrong diagnosis, and re-raised).
    deterministic_groups, _unassigned = group_tables_into_entities(schemas)
    logger.debug(
        "Deterministic groups: %d covering %d tables",
        len(deterministic_groups),
        sum(len(v) for v in deterministic_groups.values()),
    )

    if not deterministic_groups:
        logger.warning("No discovered schemas to group; parse_request has nothing to work with")
        logger.debug("EXIT parse_request (no schemas)")
        return {
            "target_entities": [],
            "explicit_target_entities": [],
            "merge_scope": pre_set_scope or dict(_INACTIVE_SCOPE),
            "entity_tables": {},
            "table_selection_notes": "No discovered schemas — nothing to group.",
            "interrupt_data": None,
        }

    client = LLMClient(llm_config)
    prompts = PROMPT_TEMPLATES.get("request_parsing", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")

    if not system_prompt or not human_template:
        logger.warning("request_parsing prompt not found, using fallback")
        system_prompt = (
            system_prompt
            or "You name entity groups and extract merge scope from user requests. "
            "Return only JSON."
        )
        human_template = (
            human_template
            or "Request: {user_request}\n\nDiscovered table groups:\n{group_catalog}\n\nOutput:"
        )

    group_catalog = _build_group_catalog(deterministic_groups, schemas)
    # CLAUDE.md hard constraint: prompt substitution is literal str.replace(), never .format()
    user_prompt = human_template.replace("{user_request}", user_request).replace(
        "{group_catalog}", group_catalog
    )
    logger.debug(
        "Prompt loaded (%d chars, truncated to %d)", len(user_prompt), min(len(user_prompt), 500)
    )
    logger.debug("User prompt (first 500 chars): %s", user_prompt[:500])

    def _finish(
        name_map: dict[str, str],
        explicit_keys: set[str],
        merge_scope: dict[str, Any],
        table_selection_notes: str,
    ) -> dict[str, Any]:
        target_entities = [name_map[g] for g in deterministic_groups]
        entity_tables = {name_map[g]: deterministic_groups[g] for g in deterministic_groups}
        explicit_target_entities = [name_map[g] for g in explicit_keys]

        # The hard post-condition: every table discover_schemas found must
        # still be reachable through entity_tables. Naming and explicit-vs-
        # inferred classification are the LLM's only degrees of freedom
        # here — membership itself is fixed the moment deterministic_groups
        # was built, so this can only fail if this function has a bug, not
        # because of anything the LLM said.
        discovered = _discovered_table_pairs(schemas)
        mapped = {(e["origin_id"], e["table"]) for lst in entity_tables.values() for e in lst}
        orphaned = discovered - mapped
        if orphaned:
            # Not an `assert` (silently vanishes under `python -O`) and
            # deliberately raised from OUTSIDE the LLM `try/except` below —
            # see the call site. A failure here must surface as exactly what
            # it is: a bug in this function or in group_tables_into_entities,
            # never as "the LLM call failed".
            raise ValueError(f"Orphaned source tables in parse_request: {orphaned}")

        logger.debug("Extracted target_entities: %s", target_entities)
        logger.debug("Extracted explicit_target_entities: %s", explicit_target_entities)
        logger.info(
            "Parsed request: %d entities (%d explicit), scope = %s, tables = %d",
            len(target_entities),
            len(explicit_target_entities),
            f"{merge_scope['scope_type']}:{merge_scope['subject']!r}"
            if merge_scope["active"]
            else "none",
            sum(len(v) for v in entity_tables.values()),
        )
        return {
            "target_entities": target_entities,
            "explicit_target_entities": explicit_target_entities,
            "merge_scope": merge_scope,
            "entity_tables": entity_tables,
            "table_selection_notes": table_selection_notes,
            "interrupt_data": None,
            # Recorded even on a normal (non-degraded) computation so a
            # completed run's capture_from_state can cache this decision for
            # the NEXT run against the same schema/request -- the cache
            # check above is what actually consumes it.
            "_entity_tables_fingerprint": entity_tables_fp,
        }

    # Only the LLM call and the parsing of its response are caught here.
    # `_finish` (called exactly once, below, outside this block) is
    # deliberately NOT inside the try — its post-condition raising must
    # never be caught and re-logged as "the LLM call failed", which is what
    # happened when `_finish` used to be called from inside this block: the
    # AssertionError it raised was swallowed by `except Exception`, logged
    # with a false diagnosis, and `_finish` was then called a second time
    # from the handler, raising the same error uncaught anyway.
    degraded_errors: list[str] | None = None
    try:
        logger.debug("Calling LLMClient.call_json()")
        result = await client.call_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        logger.debug(
            "LLM response received: type=%s, repr=%.300s", type(result).__name__, repr(result)
        )
        if not isinstance(result, dict):
            result = {}

        raw_names = result.get("group_names", {})
        name_map: dict[str, str] = {}
        for group_key in deterministic_groups:
            candidate = raw_names.get(group_key) if isinstance(raw_names, dict) else None
            name_map[group_key] = (
                candidate.strip() if isinstance(candidate, str) and candidate.strip() else group_key
            )

        raw_explicit = result.get("explicit_groups", [])
        explicit_keys = (
            {g for g in raw_explicit if g in deterministic_groups}
            if isinstance(raw_explicit, list)
            else set()
        )

        if pre_set_scope is not None:
            merge_scope = pre_set_scope
        else:
            subject = str(result.get("subject", "")).strip()
            scope_type = str(result.get("scope_type", "none")).strip().lower()
            merge_scope = {
                "active": bool(subject) and scope_type not in ("", "none"),
                "subject": subject,
                "scope_type": scope_type or "none",
            }
        table_selection_notes = str(result.get("notes", "")).strip()

    except Exception as exc:
        # Grouping never depended on the LLM (Phase 3.1) — only naming,
        # explicit/inferred classification, and subject extraction did. A
        # failure here degrades to the deterministic keys as their own
        # names and an inactive scope, rather than the empty-entities
        # silent-continue this project has already been burned by twice
        # (Phase 1.4, Phase 1.5): the run can meaningfully proceed with
        # ugly-but-complete entity names, so it does, and says so loudly
        # via table_selection_notes rather than either hiding the failure
        # or stopping a run that doesn't need to stop.
        logger.error(
            "Naming/scope LLM call failed, falling back to deterministic keys: %s",
            exc,
            exc_info=True,
        )
        classified = classify_exception(exc)
        name_map = {g: g for g in deterministic_groups}
        explicit_keys = set()
        merge_scope = pre_set_scope or dict(_INACTIVE_SCOPE)
        table_selection_notes = (
            f"LLM naming/scope extraction failed ({classify_exception(exc).__class__.__name__}: "
            f"{exc}); entity groups use their deterministic keys as-is and no merge scope was "
            f"extracted from the request text."
        )
        degraded_errors = [f"Request parsing degraded: {exc}"]
        # Not a hard failure (state["failure"]) — the run has usable,
        # complete entity_tables and can continue. classified.disposition
        # is still logged so the pattern (transient vs permanent) is
        # visible without escalating a recoverable degradation to a stop.
        logger.debug(
            "Classified as %s (disposition=%s)", type(classified).__name__, classified.disposition
        )

    response = _finish(name_map, explicit_keys, merge_scope, table_selection_notes)
    if degraded_errors is not None:
        response["errors"] = degraded_errors
    logger.debug("EXIT parse_request%s", " (degraded)" if degraded_errors is not None else "")
    return response
