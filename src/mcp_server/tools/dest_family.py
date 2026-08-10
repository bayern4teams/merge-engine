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

    logger.info(
        "Destination family map built: %d table(s), %d relationship(s)",
        len(family),
        sum(len(v) for v in family.values()) // 2,
    )
    return family
