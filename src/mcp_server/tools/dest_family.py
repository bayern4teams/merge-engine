"""dest_family tool: derive the destination's real table-relationship graph.

Before this, field mapping only ever considered one destination table per
entity (``map_fields.py``'s ``destination_schema`` was a single ``{table,
columns}`` dict) — a source field with no home on that ONE table was refused
even when a sibling destination table existed for it. The live run's log is
explicit: ``Company.totalInvestment`` was dropped with *"no destination
column for investment amount"* while ``finance`` sat right there, unused, and
a ``contact`` entity could never fan out into ``contact_email``/
``contact_phone``/``contact_social`` no matter how the source data was shaped.

This derives which destination tables are related to which, from the
destination's REAL foreign-key constraints, so Phase 6.2's widened mapping
search knows the full candidate set for an entity — not a hand-written list
that would need updating every time the destination schema changes.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("merge-engine.tools.dest_family")


def build_family_map(dest_connector: Any) -> dict[str, set[str]]:
    """entity table -> {tables with an FK to it} u {tables it has an FK to}.

    One-hop only, undirected: a table is in another's family if either one
    declares a foreign key to the other. For ``company`` this yields
    something like ``{finance, industry, experience, company_relation,
    contact_email, contact_phone, contact_social, location, ...}`` —
    whatever the real schema's FK constraints say, never hand-written, so
    it generalises to any destination without code changes when a new
    table is added.

    Only PostgreSQL exposes ``get_foreign_keys`` today (the ClickHouse
    destination has no FK constraints at all to read). A connector without
    it degrades to an empty family map — the same graceful-degradation
    shape as every other schema-introspection call site in this codebase
    (`map_fields_node.py`'s own ``try/except`` around
    ``dest_connector.list_tables()``, for one) — never an error that would
    abort mapping over what is, at worst, a missing enhancement.
    """
    get_fks = getattr(dest_connector, "get_foreign_keys", None)
    list_tables = getattr(dest_connector, "list_tables", None)
    if get_fks is None or list_tables is None:
        logger.debug(
            "%s does not support get_foreign_keys/list_tables -- cross-table "
            "mapping family graph will be empty.",
            type(dest_connector).__name__,
        )
        return {}

    try:
        tables = list_tables()
    except Exception as exc:
        logger.warning("Could not list destination tables for family map: %s", exc)
        return {}

    family: dict[str, set[str]] = {t: set() for t in tables}
    for table in tables:
        try:
            fks = get_fks(table)
        except Exception as exc:
            logger.warning("Could not read foreign keys for destination table '%s': %s", table, exc)
            continue
        for _column, ref_table in fks.items():
            if not ref_table:
                continue
            # A referenced table this list_tables() call didn't surface
            # (schema drift mid-read, or a system table excluded from
            # list_tables' own filter) -- still record the relationship
            # rather than silently dropping it.
            family.setdefault(ref_table, set())
            family[table].add(ref_table)
            family[ref_table].add(table)

    # Two shapes of table the one-hop FK walk above correctly finds but a
    # plain column-mapping search must never target, live-reproduced both
    # (Boeing, this session):
    #
    #  1. A table's own entity-supertype ancestors ("entity"/"organization"
    #     for "company") -- populated automatically by the entity/
    #     knowledge-graph writer (ensure_entity_chain) once a leaf row is
    #     loaded, never by mapping an arbitrary source field onto one of
    #     their columns. A "created_at" field auto-mapped onto
    #     entity.created_at purely on column-name coincidence.
    #  2. A "relationship"/fact-shaped table -- one whose own primary key
    #     references knowledge_item (knowledge_fact itself, and every
    #     *_relationship table: company_company_relationship,
    #     company_offering_relationship, ...). These need a resolved
    #     SECOND entity, a relationship_type, and their own knowledge_item
    #     row -- a write path this project doesn't have yet (see the
    #     forensic audit) -- not a simple one-parent child row. A
    #     "description" field auto-mapped onto
    #     company_company_relationship.description failed for lacking a
    #     second company id the mapper has no way to supply.
    get_chain = getattr(dest_connector, "get_entity_supertype_chain", None)
    if get_chain is not None:
        for table in list(family):
            try:
                chain = get_chain(table)
            except Exception as exc:
                logger.debug("Could not resolve entity-supertype chain for '%s': %s", table, exc)
                continue
            if chain:
                # Symmetric: an ancestor is exactly as invalid a mapping
                # target for its descendant as the descendant is for it —
                # both relationships exist only for ensure_entity_chain,
                # never for column mapping.
                for ancestor in chain:
                    family[table].discard(ancestor)
                    family.get(ancestor, set()).discard(table)

    get_schema = getattr(dest_connector, "get_table_schema", None)
    if get_schema is not None:
        item_based_tables = set()
        for table in list(family):
            try:
                pk = get_schema(table).get("primary_key")
                if pk and get_fks(table).get(pk) == "knowledge_item":
                    item_based_tables.add(table)
            except Exception as exc:
                logger.debug("Could not check '%s' for an item-based primary key: %s", table, exc)
        for table in item_based_tables:
            for neighbor in family.pop(table, ()):
                family[neighbor].discard(table)
        if item_based_tables:
            logger.info(
                "Excluded %d relationship/fact-shaped table(s) from the family map "
                "(need the dedicated relationship writer, not column mapping): %s",
                len(item_based_tables),
                sorted(item_based_tables),
            )

    logger.info(
        "Destination family map built: %d table(s), %d relationship(s)",
        len(family),
        sum(len(v) for v in family.values()) // 2,
    )
    return family
