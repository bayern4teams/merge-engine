"""Column-level SQL lineage (ADR 0009, fix-v3 B.1.5).

Thin wrapper over ``sqlglot.lineage.lineage`` for the field mappings
``map_fields``/``md_field_mapping`` already record: given the mapping
rendered as SQL (``mapping_to_sql``) and a destination column name, trace
it back to the real origin table/column(s) it came from.
"""

from __future__ import annotations

from typing import Any

from sqlglot import expressions as exp
from sqlglot.lineage import lineage


def column_lineage(
    mapping_sql: str, target_column: str, schema: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Trace ``target_column`` back through ``mapping_sql`` to its base
    table(s)/column(s).

    Returns ``[{"node": str, "source_table": str | None,
    "source_column": str | None, "kind": str}, ...]`` — one entry per leaf
    of the lineage graph (a node with no further upstream — i.e. it
    resolved to a real table rather than another expression). A column
    unknown to ``mapping_sql`` raises (``sqlglot.errors.SqlglotError``) —
    a lineage caller needs to know the mapping was wrong, not silently get
    nothing back.
    """
    root = lineage(target_column, mapping_sql, schema=schema)
    entries: list[dict[str, Any]] = []
    for node in root.walk():
        if node.downstream:
            # Not a leaf -- an intermediate expression/subquery, not a
            # real source. Only leaves name an actual base table.
            continue
        source = node.source
        if isinstance(source, exp.Table):
            table = source.name
            db = source.db
            source_table = f"{db}.{table}" if db else table
            source_column = node.name.rsplit(".", 1)[-1]
            kind = "column"
        else:
            source_table = None
            source_column = None
            kind = type(source).__name__.lower() if source is not None else "unknown"
        entries.append(
            {
                "node": node.name,
                "source_table": source_table,
                "source_column": source_column,
                "kind": kind,
            }
        )
    return entries


def mapping_to_sql(field_mapping: dict[str, dict[str, Any]], dest_table: str) -> str:
    """Render a ``map_fields`` ``field_mapping`` dict as parseable SQL so
    ``column_lineage`` has something to trace. Pure string-building, no DB
    — a naive comma-join across every distinct source table referenced;
    lineage only needs a parseable FROM clause, never a runnable query.
    """
    by_table: dict[str, list[tuple[str, str]]] = {}
    for field, entry in field_mapping.items():
        src_col = entry.get("source_field") or field
        src_table = entry.get("table") or dest_table
        dest_col = entry.get("dest_column") or field
        by_table.setdefault(src_table, []).append((src_col, dest_col))

    if not by_table:
        return f"SELECT * FROM {dest_table}"

    select_parts: list[str] = []
    from_parts: list[str] = []
    for i, (table, cols) in enumerate(by_table.items()):
        alias = f"t{i}"
        for src_col, dest_col in cols:
            select_parts.append(f"{alias}.{src_col} AS {dest_col}")
        from_parts.append(f"{table} AS {alias}")
    return f"SELECT {', '.join(select_parts)} FROM {', '.join(from_parts)}"
