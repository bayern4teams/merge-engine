"""plan_scope tool: turn a subject ("Nova") into a per-table extraction plan.

When a request is scoped to one organisation, the pipeline must extract only
that organisation's data — its own record, its people, its products — and
nothing about any other. This stage decides, for every target entity and every
source table, HOW to restrict extraction to the subject:

- ``match``     — keep rows whose text column contains the subject value
                  (the company's own name; a denormalised ``company_name``).
- ``link_path`` — keep rows reachable from the subject's matched rows by
                  walking 1..N real join hops in the same origin.

Before Phase 4.3, a dependent table's link to the subject could only be
expressed as a single column (``link``) or a single junction table
(``link_via``, two hops) — ``PersonContact``, three hops from ``Company`` via
``Experience`` then ``Person``, was structurally unrepresentable. ``link_path``
generalises this to any number of hops, found by ``join_graph.py``'s
``find_paths`` BFS over the real, value-verified join graph rather than a flat
scan capped at one junction.

An LLM may still propose ``match``/``link``/``link_via`` in its familiar
shape — that contract is kept for backward compatibility — but it can no
longer invent a join: ``_sanitize_plan`` validates every hop of every
LLM-proposed entry against the real join graph (``JoinGraph.find_edge``) and
normalises whatever survives into the single ``link_path`` shape, so
extraction (Phase 4.4) only ever has one execution path to walk, not three.
The heuristic floor — always computed, and what runs when the LLM is
unavailable — is built directly from ``find_paths`` and needs no separate
link-column or junction-table heuristic of its own; BFS over a real graph
subsumes both.

Cost is O(schema): one call per run, never per row. Building the join graph
adds bounded per-column sampling (Phase 4.1), not a full-table read.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.mcp_server.tools.join_graph import (
    _DEFAULT_MAX_HOPS,
    _DEFAULT_SAMPLE_SIZE,
    JoinEdge,
    JoinGraph,
    JoinPath,
    Node,
    PathHop,
    build_join_graph,
    find_paths,
)
from src.mcp_server.tools.select_entity_tables import (
    _known_tables,
    _resolve_table,
    build_table_catalog,
)
from src.orchestrator.utils.entity_aliases import concept_matches

logger = logging.getLogger("merge-engine.tools.plan_scope")

# Column names that, absent a declared FK, mark a text column as carrying an
# organisation's own identifying value — used by the heuristic floor. Bare
# "org" is deliberately excluded: it matches "org_id", an id column, not a name.
_NAME_COLUMN_HINTS = ("name", "title", "company", "organization", "organisation")


def _is_name_column(col: str) -> bool:
    """A text column that carries the subject's own identifying value.

    Excludes id columns explicitly: ``org_id`` contains the "org" family but is
    a key, not a name, and matching a subject name against it never works.
    """
    lc = col.lower()
    if lc == "id" or lc.endswith("_id"):
        return False
    return any(h in lc for h in _NAME_COLUMN_HINTS)


def _columns_of(table: dict[str, Any]) -> list[str]:
    return [str(c.get("name", "")) for c in table.get("columns", []) if c.get("name")]


def _iter_tables(schemas: dict[str, Any]):
    """Yield (origin_id, table_dict, full_name) across every origin."""
    for origin_id, schema_data in (schemas or {}).items():
        for table in schema_data.get("tables", []):
            full = table.get("full_name") or table.get("name") or ""
            if full:
                yield origin_id, table, full


def _bare_table_name(schemas: dict[str, Any], origin_id: str, full_or_bare: str) -> str | None:
    """The join-graph node name (``table.get("name")``) for a table given
    its resolved full name — the join graph identifies tables by their bare
    ``name``, never the qualified ``full_name`` a connector needs
    (``public.companies`` vs ``companies``), so anything that looks the
    table up in the graph (hop validation) needs this translation back.
    """
    for table in (schemas or {}).get(origin_id, {}).get("tables", []):
        full = table.get("full_name") or table.get("name")
        name = table.get("name")
        if full == full_or_bare or name == full_or_bare:
            return str(name) if name else None
    return None


def _wire_hops(hops: list[PathHop]) -> list[dict[str, str]]:
    return [
        {
            "from_table": h.from_table,
            "from_column": h.from_column,
            "to_table": h.to_table,
            "to_column": h.to_column,
        }
        for h in hops
    ]


def _nodes_from_hops(origin_id: str, hops: list[PathHop]) -> tuple[Node, ...]:
    if not hops:
        return ()
    nodes: list[Node] = [(origin_id, hops[0].from_table)]
    nodes.extend((origin_id, h.to_table) for h in hops)
    return tuple(nodes)


def _entry_from_hops(
    origin_id: str,
    full_table: str,
    hops: list[PathHop],
    subject_entity: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the one join-based wire shape every dependent-entity entry uses,
    whether the hops came from the heuristic floor's own `find_paths` call or
    from `_sanitize_plan` validating an LLM's match/link/link_via/link_path
    proposal against the real graph — a single execution path downstream
    (Phase 4.4's extract_data) has to handle exactly one shape, not three.
    """
    nodes = _nodes_from_hops(origin_id, hops)
    path = JoinPath(nodes=nodes, hops=tuple(hops))
    wire = _wire_hops(hops)
    chain = " -> ".join(n[1] for n in nodes) if nodes else full_table
    return {
        "origin_id": origin_id,
        "table": full_table,
        "mode": "link_path",
        "path": wire,
        "hops": path.hop_count,
        "confidence": path.confidence,
        "enabled": not path.high_fanout,
        "high_fanout": path.high_fanout,
        "link_entity": subject_entity,
        "columns": [wire[-1]["to_column"]] if wire else [],
        "reason": reason or f"linked to the subject via a {path.hop_count}-hop join ({chain})",
    }


def _heuristic_plan(
    subject_entity: str,
    target_entities: list[str],
    schemas: dict[str, Any],
    graph: JoinGraph,
    *,
    max_hops: int = _DEFAULT_MAX_HOPS,
) -> dict[str, list[dict[str, Any]]]:
    """A best-effort plan when the LLM is unavailable.

    Subject-entity tables are matched on a name-like column. Dependent-entity
    tables are linked via the shortest verified route `find_paths` finds from
    any of the origin's subject tables — one call now covers what used to be
    three separate cases (direct FK, name-hinted id column, single-junction
    table) because BFS over a real graph subsumes all three, plus chains
    longer than the old two-hop ceiling. A dependent table with no route is
    left out rather than extracted in full, because extracting it whole would
    pull in every other organisation's rows, which is the one thing a scoped
    run must not do.
    """
    plan: dict[str, list[dict[str, Any]]] = {e: [] for e in target_entities}

    # Real (origin, table) NODES for the subject entity, per origin — the
    # join graph identifies tables by their bare name, not full_name.
    subject_nodes: dict[str, list[Node]] = {}
    subject_tables: dict[str, list[dict[str, Any]]] = {}
    for origin_id, table, full in _iter_tables(schemas):
        name = table.get("name", "")
        if concept_matches(subject_entity, name) or concept_matches(subject_entity, full):
            subject_nodes.setdefault(origin_id, []).append((origin_id, name))
            subject_tables.setdefault(origin_id, []).append(table)

    for entity in target_entities:
        for origin_id, table, full in _iter_tables(schemas):
            name = table.get("name", "")
            cols = _columns_of(table)

            is_subject_table = concept_matches(entity, name) or concept_matches(entity, full)
            if not is_subject_table:
                continue

            if entity == subject_entity:
                match_cols = [c for c in cols if _is_name_column(c)] or (
                    [cols[1]] if len(cols) > 1 else cols[:1]
                )
                if match_cols:
                    plan[entity].append(
                        {
                            "origin_id": origin_id,
                            "table": full,
                            "mode": "match",
                            "columns": match_cols[:2],
                            "enabled": True,
                            "reason": "subject entity's own record (heuristic)",
                        }
                    )
                continue

            # Dependent entity: shortest verified route from any of this
            # origin's subject tables to this table.
            target_node: Node = (origin_id, name)
            best: JoinPath | None = None
            for subj_node in subject_nodes.get(origin_id, []):
                if subj_node == target_node:
                    continue
                for candidate in find_paths(graph, subj_node, target_node, max_hops=max_hops):
                    if best is None or candidate.rank_key() < best.rank_key():
                        best = candidate
            if best is not None and best.hop_count >= 1:
                plan[entity].append(
                    _entry_from_hops(
                        origin_id,
                        full,
                        list(best.hops),
                        subject_entity,
                        reason=(
                            f"linked to the subject via a {best.hop_count}-hop verified join "
                            f"(heuristic; {' -> '.join(n[1] for n in best.nodes)})"
                        ),
                    )
                )
                continue

            # No structural route at all: the join graph's name-heuristic
            # tier only matches a column derived from the OTHER table's own
            # name (e.g. "companyID", "company_id"), so an abbreviation like
            # "org_id" for an "organizations" table never becomes a graph
            # candidate in the first place. concept_matches already treats
            # such abbreviations as the same concept everywhere else in this
            # pipeline (Phase 3's entity grouping, table selection) -- reuse
            # it here as a last-resort single-hop guess rather than leaving
            # the entity out entirely.
            fallback_hop = _concept_link_fallback(
                origin_id, table, subject_entity, subject_tables.get(origin_id, [])
            )
            if fallback_hop is not None:
                plan[entity].append(
                    _entry_from_hops(
                        origin_id,
                        full,
                        [fallback_hop],
                        subject_entity,
                        reason=(
                            f"{fallback_hop.from_column} plausibly links to the subject "
                            "(heuristic name guess, unverified)"
                        ),
                    )
                )

    return plan


def _concept_link_fallback(
    origin_id: str,
    table: dict[str, Any],
    subject_entity: str,
    subject_tables_for_origin: list[dict[str, Any]],
) -> PathHop | None:
    """Last-resort single-hop guess when `find_paths` found no route at all:
    scan this table's own id-like columns for one whose stripped prefix
    concept-matches the subject entity. Bypasses graph verification
    entirely (there is no real `JoinEdge` behind it — the resulting
    `PathHop` wraps a synthetic one), so it is always tagged
    `confidence="heuristic"` and never promoted beyond it.
    """
    if not subject_tables_for_origin:
        return None
    subj_table = subject_tables_for_origin[0]
    subj_name = str(subj_table.get("name", ""))
    subj_pk_list = subj_table.get("primary_key") or []
    if subj_pk_list:
        subj_pk = str(subj_pk_list[0])
    else:
        subj_cols = {str(c.get("name", "")) for c in subj_table.get("columns", []) or []}
        subj_pk = "_id" if "_id" in subj_cols else ("id" if "id" in subj_cols else "")
    if not subj_name or not subj_pk:
        return None

    entity_table_name = str(table.get("name", ""))
    for col in table.get("columns", []) or []:
        col_name = str(col.get("name", ""))
        low = col_name.lower()
        if low.endswith("_id") and len(low) > 3:
            fragment = low[:-3]
        elif low.endswith("id") and len(low) > 2:
            fragment = low[:-2]
        else:
            continue
        if not fragment or not concept_matches(subject_entity, fragment):
            continue
        edge = JoinEdge(
            child_origin=origin_id,
            child_table=entity_table_name,
            child_column=col_name,
            parent_origin=origin_id,
            parent_table=subj_name,
            parent_column=subj_pk,
            confidence="heuristic",
            overlap_ratio=None,
            rationale=(
                f"{entity_table_name}.{col_name} name plausibly references the "
                f"subject entity (unverified name guess)"
            ),
        )
        return PathHop(edge=edge, direction="fanout")
    return None


def _find_link_hop(
    graph: JoinGraph,
    origin_id: str,
    entity_table: str,
    link_column: str,
    subject_entity: str,
) -> PathHop | None:
    """Validate an LLM-proposed `link` column against the real graph: an edge
    must connect `entity_table.link_column` to a table whose name
    concept-matches the subject entity. This is what stops the LLM inventing
    a link column that isn't a real relationship — a hallucinated join is as
    dangerous here as a hallucinated column name, since it either crashes
    extraction or, worse, silently pulls in unrelated organisations' rows.
    """
    for edge in graph.neighbors((origin_id, entity_table)):
        if (
            edge.child_table.lower() == entity_table.lower()
            and edge.child_column.lower() == link_column.lower()
        ):
            other_table, direction = edge.parent_table, "fanout"
        elif (
            edge.parent_table.lower() == entity_table.lower()
            and edge.parent_column.lower() == link_column.lower()
        ):
            other_table, direction = edge.child_table, "narrow"
        else:
            continue
        if concept_matches(subject_entity, other_table):
            return PathHop(edge=edge, direction=direction)
    return None


def _find_via_hops(
    graph: JoinGraph,
    origin_id: str,
    entity_table: str,
    entity_key_column: str,
    via_table: str,
    via_from_column: str,
    via_to_column: str,
    subject_entity: str,
) -> list[PathHop] | None:
    """Validate an LLM-proposed `link_via` junction hop by hop: hop 1 must
    connect the junction to a subject-concept table, hop 2 must connect the
    junction to this entity's table on the named columns. Either hop failing
    means the claimed junction isn't real — drop the whole entry rather than
    keep a half-verified chain.
    """
    hop1 = _find_link_hop(graph, origin_id, via_table, via_from_column, subject_entity)
    if hop1 is None:
        return None
    hop2_edge = graph.find_edge(
        origin_id, via_table, via_to_column, entity_table, entity_key_column
    )
    if hop2_edge is None:
        return None
    direction = "fanout" if hop2_edge.parent_table.lower() == via_table.lower() else "narrow"
    return [hop1, PathHop(edge=hop2_edge, direction=direction)]


def _find_explicit_path_hops(
    graph: JoinGraph, origin_id: str, raw_path: Any
) -> list[PathHop] | None:
    """Validate an explicit `link_path` hop list: every hop must name a real
    edge in the graph (`JoinGraph.find_edge`), and consecutive hops must
    actually chain (each hop's `to_table` is the next hop's `from_table`).
    Returns `None` on any break in the chain — a partially-real path is not
    usable, since a filter built from it would silently stop narrowing
    midway and extract too much.
    """
    if not isinstance(raw_path, list) or not raw_path:
        return None
    hops: list[PathHop] = []
    for i, raw_hop in enumerate(raw_path):
        if not isinstance(raw_hop, dict):
            return None
        ft = str(raw_hop.get("from_table") or "").strip()
        fc = str(raw_hop.get("from_column") or "").strip()
        tt = str(raw_hop.get("to_table") or "").strip()
        tc = str(raw_hop.get("to_column") or "").strip()
        if not (ft and fc and tt and tc):
            return None
        if i > 0 and hops[-1].to_table.lower() != ft.lower():
            return None
        edge = graph.find_edge(origin_id, ft, fc, tt, tc)
        if edge is None:
            return None
        if edge.child_table.lower() == ft.lower() and edge.child_column.lower() == fc.lower():
            direction = "narrow"
        else:
            direction = "fanout"
        hops.append(PathHop(edge=edge, direction=direction))
    return hops


def _sanitize_plan(
    proposed: dict[str, Any],
    target_entities: list[str],
    subject_entity: str,
    schemas: dict[str, Any],
    graph: JoinGraph,
) -> dict[str, list[dict[str, Any]]]:
    """Keep only well-formed entries naming real (origin, table) pairs, with
    every join-based entry's hops validated against the real join graph —
    the LLM can still describe a join in the familiar match/link/link_via/
    link_path shapes, but every hop it names must correspond to a real edge
    or the whole entry is dropped, never silently trusted.
    """
    known = _known_tables(schemas)
    clean: dict[str, list[dict[str, Any]]] = {e: [] for e in target_entities}
    for entity, items in (proposed or {}).items():
        if entity not in clean:
            continue
        for item in items or []:
            if not isinstance(item, dict):
                continue
            origin_id = str(item.get("origin_id") or "").strip()
            table = str(item.get("table") or "").strip()
            resolved = _resolve_table(origin_id, table, known)
            if resolved is None:
                logger.warning(
                    "Scope plan named a table not in any schema: %s.%s — dropped.",
                    origin_id,
                    table,
                )
                continue
            resolved_origin, resolved_table = resolved
            mode = str(item.get("mode") or "").strip().lower()
            reason = str(item.get("reason", ""))[:200] or None

            if mode == "match":
                columns = [str(c) for c in (item.get("columns") or []) if str(c)]
                if not columns:
                    logger.warning(
                        "Scope plan match entry for %s has no columns — dropped.", resolved
                    )
                    continue
                clean[entity].append(
                    {
                        "origin_id": resolved_origin,
                        "table": resolved_table,
                        "mode": "match",
                        "columns": columns[:3],
                        "enabled": True,
                        "reason": reason or "",
                    }
                )
                continue

            bare_table = _bare_table_name(schemas, resolved_origin, resolved_table)
            if bare_table is None:
                logger.warning(
                    "Scope plan entry for %s resolved to a table not found in the "
                    "discovered schema — dropped.",
                    resolved,
                )
                continue

            hops: list[PathHop] | None = None
            if mode == "link":
                columns = [str(c) for c in (item.get("columns") or []) if str(c)]
                if columns:
                    hop = _find_link_hop(
                        graph, resolved_origin, bare_table, columns[0], subject_entity
                    )
                    hops = [hop] if hop else None
            elif mode == "link_via":
                columns = [str(c) for c in (item.get("columns") or []) if str(c)]
                via_table = str(item.get("via_table") or "").strip()
                from_col = str(item.get("via_from_column") or "").strip()
                to_col = str(item.get("via_to_column") or "").strip()
                if columns and via_table and from_col and to_col:
                    via_resolved = _resolve_table(resolved_origin, via_table, known)
                    bare_via = (
                        _bare_table_name(schemas, resolved_origin, via_resolved[1])
                        if via_resolved
                        else _bare_table_name(schemas, resolved_origin, via_table)
                    )
                    if bare_via:
                        hops = _find_via_hops(
                            graph,
                            resolved_origin,
                            bare_table,
                            columns[0],
                            bare_via,
                            from_col,
                            to_col,
                            subject_entity,
                        )
            elif mode == "link_path":
                hops = _find_explicit_path_hops(graph, resolved_origin, item.get("path"))
                if hops and hops[-1].to_table.lower() != bare_table.lower():
                    hops = None
            else:
                logger.warning(
                    "Scope plan entry for %s has unknown mode %r — dropped.", resolved, mode
                )
                continue

            if not hops:
                logger.warning(
                    "Scope plan %s entry for %s does not correspond to a real relationship "
                    "in the join graph — dropped.",
                    mode,
                    resolved,
                )
                continue

            clean[entity].append(
                _entry_from_hops(
                    resolved_origin, resolved_table, hops, subject_entity, reason=reason
                )
            )
    return clean


async def plan_scope_impl(
    subject: str,
    subject_entity: str,
    target_entities: list[str],
    schemas: dict[str, Any],
    llm_config: dict[str, Any] | None = None,
    origins: list[dict[str, Any]] | None = None,
    scope_config: dict[str, Any] | None = None,
    cached_graph: JoinGraph | None = None,
) -> dict[str, Any]:
    """Build the per-table extraction plan for a scoped ("Nova") merge.

    Returns ``{"scope_plan": {...}, "notes": str, "llm_used": bool,
    "join_graph": JoinGraph}`` where ``scope_plan`` is ``{subject,
    subject_entity, entities: {entity: [filters]}}``. The heuristic floor is
    always computed; the LLM plan replaces it when available and
    well-formed, else the floor stands.

    ``origins`` supplies connection details for the join graph's
    inclusion-dependency verification tier (Phase 4.1); when absent (or an
    origin is missing from it), name-heuristic candidates for that origin
    still enter the graph, tagged ``confidence="heuristic"`` rather than lost.

    ``scope_config`` carries ``max_hops``/``join_graph_sample_size`` from
    config.yaml's ``scope:`` section (state's ``scope_config`` key); absent
    or missing keys fall back to join_graph.py's own module defaults.

    ``cached_graph``: a previously built ``JoinGraph`` (state's
    ``join_graph`` key, or a saved profile's) to reuse instead of rebuilding
    — value-overlap sampling is one connector round-trip per candidate edge,
    so skipping it on a node retry or a profile-matched repeat run is a real
    saving, not a cosmetic one. Only reused when the schema/origin/
    sample-size inputs it would otherwise be rebuilt from are unchanged;
    callers that can't easily prove that should just leave this ``None``.
    """
    subject = str(subject or "").strip()
    subject_entity = str(subject_entity or "company").strip() or "company"
    scope_config = scope_config or {}
    max_hops = int(scope_config.get("max_hops") or _DEFAULT_MAX_HOPS)
    sample_size = int(scope_config.get("join_graph_sample_size") or _DEFAULT_SAMPLE_SIZE)
    graph = cached_graph
    if graph is None:
        graph = await build_join_graph(schemas, origins or [], sample_size=sample_size)
    floor = _heuristic_plan(subject_entity, target_entities, schemas, graph, max_hops=max_hops)

    def _wrap(entities: dict[str, list[dict[str, Any]]], notes: str, llm_used: bool):
        return {
            "scope_plan": {
                "subject": subject,
                "subject_entity": subject_entity,
                "entities": entities,
            },
            "notes": notes,
            "llm_used": llm_used,
            "join_graph": graph,
        }

    def _ensure_complete_origin_coverage(entities: dict[str, list[dict[str, Any]]]):
        all_origins = list((schemas or {}).keys())
        for entity in target_entities:
            existing_entries = entities.setdefault(entity, [])
            if not existing_entries:
                for oid in all_origins:
                    existing_entries.append(
                        {
                            "origin_id": oid,
                            "table": "—",
                            "mode": "not_found",
                            "columns": [],
                            "reason": f"No matching or linked {entity} table found in {oid}",
                            "est_rows": None,
                        }
                    )

    from src.mcp_server.tools.extract_data import extract_data_impl

    by_origin_id = {str(o.get("id")): o for o in (origins or [])}
    _est_rows_cache: dict[tuple[str, str], int | None] = {}

    async def _annotate_est_rows(entities: dict[str, list[dict[str, Any]]]) -> None:
        """Best-effort, coarse row-count annotation for the scope gate.

        Deliberately NOT a count of rows the join filter would actually
        match — the subject's own ids aren't known yet at scope-plan time
        (extract_data, which resolves them, runs after this stage), so a
        true filtered estimate would mean pre-running the join walk before
        a human has even seen the plan. Reports each table's own
        unfiltered row count instead (`extract_data_impl` already computes
        this as `total_count` on any query, so this adds no new connector
        capability) — still enough to distinguish "a 40-row lookup table"
        from "a 43M-row event table" without pretending false precision.
        `None` (never a guess) when no origin connection is available or
        the probe fails; the gate renders that as "—".
        """
        for entries in entities.values():
            for entry in entries:
                if entry.get("mode") == "not_found":
                    continue
                oid, table = str(entry.get("origin_id")), str(entry.get("table"))
                key = (oid, table)
                if key not in _est_rows_cache:
                    origin = by_origin_id.get(oid)
                    count: int | None = None
                    if origin and origin.get("connection"):
                        try:
                            result = await extract_data_impl(
                                origin_config=origin,
                                table_name=table,
                                columns=["*"],
                                batch_size=1,
                            )
                            count = result.get("total_count")
                        except Exception as exc:
                            logger.debug("est_rows probe failed for %s.%s: %s", oid, table, exc)
                    _est_rows_cache[key] = count
                entry["est_rows"] = _est_rows_cache[key]

    llm_enabled = bool(llm_config) and llm_config.get("scope_planning_enabled", True)
    if not subject or not target_entities or not schemas or not llm_enabled:
        _ensure_complete_origin_coverage(floor)
        await _annotate_est_rows(floor)
        return _wrap(floor, "", False)

    from src.llm import PROMPT_TEMPLATES, LLMClient

    prompts = PROMPT_TEMPLATES.get("scope_planning", {})
    system_prompt = prompts.get("system", "")
    human_template = prompts.get("human", "")
    if not system_prompt or not human_template:
        logger.warning("scope_planning prompts missing; using heuristic scope plan")
        _ensure_complete_origin_coverage(floor)
        await _annotate_est_rows(floor)
        return _wrap(floor, "", False)

    catalog = build_table_catalog(schemas, include_foreign_keys=True)
    user_prompt = (
        human_template.replace("{subject}", subject)
        .replace("{subject_entity}", subject_entity)
        .replace("{target_entities}", json.dumps(target_entities))
        .replace("{table_catalog}", catalog)
    )

    try:
        client = LLMClient(llm_config)
        result = await client.call_json(system_prompt=system_prompt, user_prompt=user_prompt)
        proposed = result.get("entities", {}) if isinstance(result, dict) else {}
        notes = str(result.get("notes", "")) if isinstance(result, dict) else ""
        clean = _sanitize_plan(proposed, target_entities, subject_entity, schemas, graph)

        # If the model returned nothing usable for any entity, the heuristic
        # floor is a better answer than an empty plan that extracts nothing.
        if not any(clean.values()):
            logger.warning("LLM scope plan had no usable entries; falling back to heuristic.")
            _ensure_complete_origin_coverage(floor)
            await _annotate_est_rows(floor)
            return _wrap(floor, notes or "LLM plan empty; used heuristic.", False)

        # Merge in floor entries the model didn't cover, per (origin, table)
        # -- not per entity. The scope-planning prompt deliberately tells the
        # model it MAY omit a deep join-path table and let "the automatic
        # path search find and verify the full chain" (see
        # scope_planning.system.txt), which is exactly what the floor exists
        # to backfill. An entity-level merge (`if not clean.get(entity)`)
        # discarded the ENTIRE floor for an entity the moment the model
        # returned even one entry for it — so a model answer of
        # `person: [origin_1.Person]` silently dropped the floor's
        # `origin_2.PersonContact` deep-chain entry outright. Live-observed
        # on the Airbus run: seven "does not correspond to a real
        # relationship" drops and three zero-row entities behind a passing
        # scorecard. Merging per (origin_id, table) means a partial LLM
        # answer for an entity can only ADD tables beyond what the model
        # named, never remove one the floor already covers.
        for entity, entries in floor.items():
            have = {(e.get("origin_id"), e.get("table")) for e in clean.get(entity, [])}
            for e in entries:
                key = (e.get("origin_id"), e.get("table"))
                if key not in have:
                    clean.setdefault(entity, []).append(e)
                    have.add(key)

        _ensure_complete_origin_coverage(clean)
        await _annotate_est_rows(clean)
        logger.info(
            "Scope plan for %r: %s",
            subject,
            {e: len(v) for e, v in clean.items()},
        )
        return _wrap(clean, notes, True)

    except Exception as exc:
        logger.warning(
            "LLM scope planning failed (%s); using the heuristic scope plan.",
            exc,
        )
        _ensure_complete_origin_coverage(floor)
        await _annotate_est_rows(floor)
        return _wrap(floor, f"LLM scope planning unavailable: {exc}", False)


# How many subject rows to show at the confirmation gate, and per table, so the
# probe is a preview ("which Nova did you mean") rather than a real extraction.
_PROBE_TOTAL = 40
_PROBE_PER_TABLE = 20


async def probe_subject_candidates(
    subject: str,
    scope_plan: dict[str, Any],
    origins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fetch a few subject rows so a human can confirm *which* subject is meant.

    Reads only the subject entity's ``match`` tables, with the same ILIKE
    predicate extraction will use, capped hard. Best-effort: any origin that is
    unreachable or misconfigured is skipped, because a preview must never be
    what stops a run — the gate can still show the plan without it.
    """
    from src.mcp_server.tools.extract_data import extract_data_impl

    subject = str(subject or "").strip()
    subject_entity = scope_plan.get("subject_entity", "company")
    plans = (scope_plan.get("entities") or {}).get(subject_entity, [])
    by_id = {str(o.get("id")): o for o in (origins or [])}

    candidates: list[dict[str, Any]] = []
    for entry in plans:
        if entry.get("mode") != "match" or len(candidates) >= _PROBE_TOTAL:
            continue
        origin = by_id.get(str(entry.get("origin_id")))
        if not origin or not origin.get("connection"):
            continue
        columns = entry.get("columns") or []
        if not columns:
            continue
        # OR across the match columns is expressed as one ILIKE here (the first
        # column); the gate is a preview, not the authoritative filter.
        filt = {columns[0]: {"op": "ilike", "value": subject}}
        try:
            result = await extract_data_impl(
                origin_config=origin,
                table_name=entry["table"],
                columns=["*"],
                batch_size=_PROBE_PER_TABLE,
                filter=filt,
            )
        except Exception as exc:
            logger.warning(
                "Scope probe skipped %s.%s: %s",
                entry.get("origin_id"),
                entry.get("table"),
                exc,
            )
            continue
        for row in (result.get("rows") or [])[:_PROBE_PER_TABLE]:
            candidates.append(
                {
                    "origin_id": entry.get("origin_id"),
                    "table": entry.get("table"),
                    "fields": {
                        k: v
                        for k, v in row.items()
                        if not str(k).startswith("_") and v not in (None, "")
                    },
                }
            )
            if len(candidates) >= _PROBE_TOTAL:
                break
    logger.info("Scope probe found %d candidate subject row(s)", len(candidates))
    return candidates


async def plan_scope_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler entry point."""
    return await plan_scope_impl(**arguments)
