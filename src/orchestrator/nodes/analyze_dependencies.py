"""
analyze_dependencies node: Uses LLM and programmatic coverage matrix to build entity dependency tree from schemas.

Parses the collected schemas and schema samples to identify FK relationships
and semantic dependencies between entity types.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.llm import PROMPT_TEMPLATES, LLMClient
from src.orchestrator.utils.dependency_sort import topological_sort
from src.orchestrator.utils.entity_aliases import ALIAS_GROUPS, concept_matches
from src.orchestrator.utils.retry_hints import apply_retry_hint

logger = logging.getLogger("merge-engine.orchestrator.analyze_dependencies")


async def analyze_dependencies(state: dict[str, Any]) -> dict[str, Any]:
    """Analyze schemas to determine entity dependencies.

    Parameters
    ----------
    state : dict
        MergeState with ``schemas``, ``target_entities``, and ``entity_tables`` populated.

    Returns
    -------
    dict
        Update dict with ``dependency_tree`` and ``extraction_order`` keys.
    """
    logger.debug("ENTER analyze_dependencies")
    logger.debug("State keys: %s", list(state.keys()))
    target_entities = state.get("target_entities", [])
    schemas = state.get("schemas", {})
    llm_config: dict[str, Any] = state["llm_config"]
    errors: list[str] = []

    logger.debug("target_entities: %s", target_entities)
    logger.debug("schemas keys: %s", list(schemas.keys()))
    for origin_id, schema_data in schemas.items():
        table_count = len(schema_data.get("tables", []))
        logger.debug("Schema %s: %d tables", origin_id, table_count)

    if not target_entities:
        # Phase 3's deterministic entity grouping should make this
        # unreachable, but as a defensive backstop: returning {} here
        # silently used to let the run sail on to human_review_deps with an
        # empty tree, looking like a normal (if boring) merge instead of the
        # broken one it is. Set failure so _fallible_router stops the run at
        # the error gate — same disposition discover_schemas uses for "we
        # have less data than we need to proceed safely" — rather than
        # forwarding through as if nothing were wrong.
        msg = "No target entities parsed; skipping dependency analysis."
        logger.warning(msg)
        logger.debug("EXIT analyze_dependencies (no entities)")
        return {
            "dependency_tree": {},
            "extraction_order": [],
            "errors": [*errors, msg],
            "failure": {
                "node": "analyze_dependencies",
                "error": (
                    f"{msg} Merging now would produce an empty extraction "
                    "plan and silently drop every entity."
                ),
                "error_class": "PermanentError",
                "disposition": "escalate",
                "context": {"target_entities": target_entities},
            },
        }

    # Retrieve entity_tables from state (populated by parse_request or pinned profile)
    entity_tables: dict[str, Any] = state.get("entity_tables") or {}
    table_selection_notes = state.get("table_selection_notes") or ""

    # Build programmatic coverage matrix from schemas & entity tables
    matrix_entries = _build_coverage_matrix(schemas, entity_tables, target_entities)
    coverage_matrix_text = _format_coverage_matrix(matrix_entries)
    logger.debug("Coverage matrix built: %d entries", len(matrix_entries))

    # Build schema summary for LLM prompt
    schema_summary = _build_schema_summary(schemas, target_entities)
    logger.debug("Schema summary generated (%d chars)", len(schema_summary))

    client = LLMClient(llm_config)
    prompts = PROMPT_TEMPLATES.get("dependency_analysis", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")

    if not system_prompt or not human_template:
        system_prompt = system_prompt or (
            "You analyze database schemas to determine which entities must be "
            "extracted before others. Return only JSON."
        )
        human_template = human_template or (
            "Analyze these schemas and determine dependencies for entities: {target_entities}\n\n{schema_info}\n\nCoverage Matrix:\n{coverage_matrix}"
        )

    # Substitute placeholders using literal str.replace()
    user_prompt = (
        human_template.replace("{target_entities}", json.dumps(target_entities))
        .replace("{schema_info}", schema_summary)
        .replace("{coverage_matrix}", coverage_matrix_text)
    )
    user_prompt = apply_retry_hint(user_prompt, state, "analyze_dependencies")
    logger.debug("LLM prompt prepared (first 300 chars): %s", user_prompt[:300])

    dependency_tree: dict[str, list[str]] = {}
    dependency_analysis_warning = ""

    def _parse_llm_tree(res: Any) -> dict[str, list[str]] | None:
        if not isinstance(res, dict) or not res:
            return None
        raw_tree = res.get("dependency_tree")
        if not isinstance(raw_tree, dict) or not raw_tree:
            return None
        # Reject raw_tree if the LLM omitted any target entity key
        if any(e not in raw_tree for e in target_entities):
            return None
        tree = {}
        for e in target_entities:
            deps = raw_tree.get(e, [])
            tree[e] = list(deps) if isinstance(deps, list) else []
        return tree

    try:
        logger.debug("Calling LLMClient.call_json for dependency analysis")
        result = await client.call_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        logger.debug("LLM response received: %s", json.dumps(result, default=str)[:500])

        parsed_tree = _parse_llm_tree(result)

        # Validation: ensure response is not empty and covers target_entities
        if parsed_tree is None:
            logger.warning(
                "LLM dependency analysis returned empty or invalid response %s; retrying once with explicit prompt",
                result,
            )
            retry_prompt = (
                user_prompt
                + "\n\nIMPORTANT: Your previous output was empty or invalid. "
                + "You MUST return a JSON dict with key 'dependency_tree' containing EVERY target entity!"
            )
            retry_result = await client.call_json(
                system_prompt=system_prompt,
                user_prompt=retry_prompt,
            )
            parsed_tree = _parse_llm_tree(retry_result)

            if parsed_tree is None:
                logger.warning(
                    "LLM retry still returned invalid dependency tree; fallback to programmatic coverage matrix",
                )
                dependency_tree = _matrix_to_dependency_tree(matrix_entries, target_entities)
                dependency_analysis_warning = "LLM dependency analysis returned incomplete results; fallback to programmatic FK coverage matrix."
            else:
                dependency_tree = parsed_tree
        else:
            dependency_tree = parsed_tree

        # Ensure all target entities are keys in dependency_tree
        for e in target_entities:
            if e not in dependency_tree or not isinstance(dependency_tree[e], list):
                dependency_tree[e] = []

        # Extract column-level dependencies (declared FKs + matrix entries + LLM rationales)
        column_dependencies = _extract_declared_column_dependencies(schemas)
        for m in matrix_entries:
            column_dependencies.append(
                {
                    "child_origin": m["child_origin"],
                    "child_table": m["child_table"],
                    "child_column": m["child_column"],
                    "parent_origin": m["parent_origin"],
                    "parent_table": m["parent_table"],
                    "parent_column": m["parent_column"],
                    "rationale": m["rationale"],
                }
            )

        if isinstance(result, dict):
            llm_col_deps = result.get("column_dependencies", [])
            for item in llm_col_deps:
                if isinstance(item, dict) and item.get("child_table") and item.get("parent_table"):
                    column_dependencies.append(item)

        # Compute extraction order via topological sort
        extraction_order = topological_sort(dependency_tree)
        logger.debug("Computed extraction_order: %s", extraction_order)

        logger.info(
            "Dependency analysis complete: tree=%s, order=%s, column_deps=%d",
            dependency_tree,
            extraction_order,
            len(column_dependencies),
        )

        return {
            "dependency_tree": dependency_tree,
            "column_dependencies": column_dependencies,
            "dependency_analysis_warning": dependency_analysis_warning,
            "extraction_order": extraction_order,
            "entity_tables": entity_tables,
            "table_selection_notes": table_selection_notes,
            "interrupt_data": None,
        }

    except Exception as exc:
        msg = f"Dependency analysis failed: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
        fallback_tree = _matrix_to_dependency_tree(matrix_entries, target_entities)
        warning = (
            "Dependency order is UNVERIFIED — the LLM analysis failed, so order fallback "
            "to programmatic FK coverage matrix heuristics. Review carefully before approving."
        )
        return {
            "dependency_tree": fallback_tree,
            "column_dependencies": _extract_declared_column_dependencies(schemas),
            "dependency_analysis_warning": warning,
            "extraction_order": topological_sort(fallback_tree) or list(target_entities),
            "entity_tables": entity_tables,
            "table_selection_notes": table_selection_notes,
            "errors": errors,
            "interrupt_data": None,
        }
    finally:
        logger.debug("EXIT analyze_dependencies")


def _build_coverage_matrix(
    schemas: dict[str, Any], entity_tables: dict[str, Any], target_entities: list[str]
) -> list[dict[str, str]]:
    """Scan database schemas for foreign key column patterns and map to entity relationships."""
    entries: list[dict[str, str]] = []

    # Map table_name -> entity_name
    table_to_entity: dict[str, str] = {}
    for ent, tbls in (entity_tables or {}).items():
        if isinstance(tbls, list):
            for t in tbls:
                if isinstance(t, dict) and "table" in t:
                    table_to_entity[t["table"].lower()] = ent

    # Helper to find entity for a table name
    def find_entity_for_table(t_name: str) -> str | None:
        t_low = t_name.lower()
        if t_low in table_to_entity:
            return table_to_entity[t_low]
        for e in target_entities:
            if e.lower() in t_low or t_low in e.lower():
                return e
        return None

    # Process origins
    for origin_id, schema_data in (schemas or {}).items():
        tables = schema_data.get("tables", [])
        table_dict = {t.get("name", ""): t for t in tables if t.get("name")}

        for table_name, table_info in table_dict.items():
            child_entity = find_entity_for_table(table_name)

            # 1. Declared foreign keys
            for fk in table_info.get("foreign_keys") or []:
                ref_table = fk.get("ref_table", "")
                parent_entity = find_entity_for_table(ref_table)
                entries.append(
                    {
                        "child_origin": origin_id,
                        "child_table": table_name,
                        "child_column": fk.get("column", ""),
                        "parent_origin": origin_id,
                        "parent_table": ref_table,
                        "parent_column": fk.get("ref_column", ""),
                        "child_entity": child_entity or table_name,
                        "parent_entity": parent_entity or ref_table,
                        "rationale": f"Declared FK: {table_name}.{fk.get('column')} -> {ref_table}.{fk.get('ref_column')}",
                    }
                )

            # 2. Heuristic ID column matching (e.g. companyID, personID, hospital_id)
            cols = [c.get("name", "") for c in table_info.get("columns", []) if c.get("name")]
            for col in cols:
                col_low = col.lower()
                # Skip primary key of the same table
                if (
                    col_low == "id"
                    or col_low == f"{table_name.lower()}id"
                    or col_low == f"{table_name.lower()}_id"
                ):
                    continue

                # Check if column matches any other table in schema
                for other_table in table_dict:
                    if other_table == table_name:
                        continue
                    ot_low = other_table.lower()
                    # Phase 4.1 of the fix-bugs follow-up plan: the
                    # `or col_low.startswith(ot_low)` clause this used to
                    # have fabricated an edge for any column merely
                    # starting with another table's name -- e.g.
                    # "Person.company_name" -> "Company.id", a real column,
                    # a real table, and nothing to do with each other.
                    # src/mcp_server/tools/join_graph.py is the module that
                    # now does this matching for real (with an
                    # inclusion-dependency check against sampled data, not
                    # just a name guess) — this coverage matrix stays a
                    # lighter-weight, unverified heuristic. NOT prompt-only,
                    # despite an earlier version of this comment claiming
                    # so: the node below appends every one of these entries
                    # straight into state["column_dependencies"], which
                    # pre-populates the deps gate's UI. A wrong parent_column
                    # here isn't just a worse LLM hint, it's a dependency-gate
                    # row that fails validation and vanishes silently the
                    # moment a human approves without editing it.
                    if col_low in (
                        f"{ot_low}id",
                        f"{ot_low}_id",
                        f"{ot_low}key",
                        f"{ot_low}_key",
                    ):
                        parent_column = _resolve_parent_key_column(table_dict[other_table])
                        if parent_column is None:
                            # Every table this heuristic has ever guessed
                            # "id" for regardless of the table's real
                            # columns -- live-observed as 14 rejected
                            # dependency-gate entries in one run, all
                            # "unknown parent column: <origin>.<table>.id",
                            # because the destination's own tables are
                            # rarely literally named "id" (companyID,
                            # entityId, etc.). Omit the entry entirely
                            # rather than fabricate a column this table
                            # doesn't have -- an honest gap in the coverage
                            # matrix, not a guaranteed-wrong dependency-gate
                            # row a human has to notice and delete.
                            continue
                        parent_entity = find_entity_for_table(other_table)
                        entries.append(
                            {
                                "child_origin": origin_id,
                                "child_table": table_name,
                                "child_column": col,
                                "parent_origin": origin_id,
                                "parent_table": other_table,
                                "parent_column": parent_column,
                                "child_entity": child_entity or table_name,
                                "parent_entity": parent_entity or other_table,
                                "rationale": f"Column match heuristic: {table_name}.{col} references {other_table}",
                            }
                        )
    return entries


def _resolve_parent_key_column(table_info: dict[str, Any]) -> str | None:
    """The real column on ``table_info`` the heuristic ID-column match
    (above) should point a guessed FK at, instead of always assuming "id".

    Prefers the table's own declared primary key (what a real FK would
    reference); falls back to a literal "id" column only if one actually
    exists; returns None — never a guess — otherwise.
    """
    pk = table_info.get("primary_key") or []
    if isinstance(pk, list) and pk and pk[0]:
        return str(pk[0])
    for col in table_info.get("columns", []) or []:
        name = col.get("name")
        if name and str(name).lower() == "id":
            return str(name)
    return None


def _format_coverage_matrix(matrix_entries: list[dict[str, str]]) -> str:
    """Format coverage matrix entries for prompt inclusion."""
    if not matrix_entries:
        return "No clear foreign-key patterns or column references detected."
    lines: list[str] = []
    seen = set()
    for m in matrix_entries:
        key = (m["child_table"], m["child_column"], m["parent_table"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"- [{m['child_origin']}] {m['child_table']}.{m['child_column']} -> "
            f"{m['parent_table']}.{m['parent_column']} "
            f"({m['child_entity']} depends on {m['parent_entity']}) — {m['rationale']}"
        )
    return "\n".join(lines)


def _matrix_to_dependency_tree(
    matrix_entries: list[dict[str, str]], target_entities: list[str]
) -> dict[str, list[str]]:
    """Convert coverage matrix entries to an entity-level dependency tree dict."""
    dep_tree: dict[str, list[str]] = {e: [] for e in target_entities}
    for m in matrix_entries:
        child = m.get("child_entity")
        parent = m.get("parent_entity")
        if (
            child
            and parent
            and child in dep_tree
            and parent in target_entities
            and child != parent
            and parent not in dep_tree[child]
        ):
            dep_tree[child].append(parent)
    return dep_tree


def _extract_declared_column_dependencies(schemas: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract declared foreign keys across origins as column dependencies."""
    col_deps: list[dict[str, Any]] = []
    for origin_id, schema_data in (schemas or {}).items():
        for table in schema_data.get("tables", []):
            table_name = table.get("name", "")
            for fk in table.get("foreign_keys") or []:
                col_deps.append(
                    {
                        "child_origin": origin_id,
                        "child_table": table_name,
                        "child_column": fk.get("column", ""),
                        "parent_origin": origin_id,
                        "parent_table": fk.get("ref_table", ""),
                        "parent_column": fk.get("ref_column", ""),
                        "rationale": f"Declared FK: {table_name}.{fk.get('column')} -> {fk.get('ref_table')}.{fk.get('ref_column')}",
                    }
                )
    return col_deps


def _build_schema_summary(schemas: dict[str, Any], target_entities: list[str]) -> str:
    """Build a compact schema summary string for the LLM prompt."""
    lines: list[str] = []
    for origin_id, schema_data in schemas.items():
        lines.append(f"\n=== Origin: {origin_id} ({schema_data.get('origin_type', '?')}) ===")
        for table in schema_data.get("tables", []):
            table_name = table.get("name", "?")
            if not _table_may_be_related(table_name, target_entities):
                continue
            lines.append(f"\n  Table: {table_name}")
            lines.append(f"    Columns: {', '.join(c['name'] for c in table.get('columns', []))}")

            # Foreign keys
            fks = table.get("foreign_keys", [])
            if fks:
                for fk in fks:
                    lines.append(
                        f"    FK: {table_name}.{fk.get('column', '?')} -> "
                        f"{fk.get('ref_table', '?')}.{fk.get('ref_column', '?')}"
                    )

            # Sample row (first 3 fields)
            sample = table.get("sample_row", {})
            if sample:
                sample_str = ", ".join(f"{k}={v!r}" for k, v in list(sample.items())[:3])
                lines.append(f"    Sample: {{{sample_str}}}")

    return "\n".join(lines)


def _table_may_be_related(table_name: str, target_entities: list[str]) -> bool:
    """Check if a table name matches a target entity or a recognised
    business concept.

    Broad recall for the dependency-analysis LLM prompt's schema summary,
    not a precision gate — Phase 3.1's entity_grouping.py is what actually
    guarantees every discovered table reaches an entity; this only decides
    what schema context the LLM sees when reasoning about relationships.

    Delegates to entity_aliases' ALIAS_GROUPS / concept_matches — the
    single vocabulary source the pipeline is meant to consult — instead of
    maintaining a second, hand-curated relational-terms list and a third
    copy of the irregular-plurals table (this module used to carry its
    own `_RELATIONAL_TERMS` and `_IRREGULAR_PLURALS`, alongside
    entity_grouping.py's and join_graph.py's own copies of the latter).
    """
    if not target_entities:
        return True
    name_lower = table_name.lower().replace("_", " ").replace("-", " ")
    for entity in target_entities:
        if concept_matches(entity, table_name):
            return True
    # A table naming ANY recognised business concept -- not just one of
    # today's target entities -- is plausibly a junction/relational table
    # worth showing the LLM (e.g. a "person" merge still benefits from
    # seeing an "employment_history" table's foreign keys).
    return any(term in name_lower for group in ALIAS_GROUPS for term in group)
