"""V3 canonical matching dataset (Part B, P-10; PreSplink §38, §66, §67).

The Splink handoff artefact: assembles what P-6 (relationships), P-7
(schema matching / canonical fields), and P-9 (anchors, traversal,
extraction) already computed into ONE materialized, SQL-queryable table.
BP-B3's own bar -- "every canonical record answers 'where did this come
from?' by SQL alone" -- is a property of this table's own columns
(``source_id``, ``source_table``, ``source_record_id``, ``graph_path``,
``provenance``, ``discovery_evidence``), not something a human has to
reconstruct by re-running the app.

Materialised into the run's own DuckDB spool -- the same file
``src/orchestrator/spool.py``'s ``RunSpool`` already writes extracted rows
into. This module never imports ``spool.py`` directly: ``src/orchestrator``
sits above ``src/mcp_server/tools`` in CLAUDE.md's layering, so the caller
(the orchestrator-layer flag branch) passes the spool's own file path in,
the same way P-9's modules stay entity-type-agnostic rather than importing
upward. Registered directly as a Splink input when B.4 wires that in -- no
copy, no second table living somewhere else.

``canonical_entity_id`` is deliberately ``NULL`` at this stage: it is what
Splink (B.4, not yet reached) fills in once matching runs. A row without it
is not incomplete -- it is exactly what "handoff artefact" means, a record
ready to be matched, not already-matched output.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.mcp_server.tools.join_graph import PathHop

logger = logging.getLogger("merge-engine.tools.canonical_matching_dataset")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS canonical_matching_dataset (
    record_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_anchor_id TEXT,
    canonical_entity_id TEXT,
    source_table TEXT NOT NULL,
    -- Nullable, not NOT NULL: build_dataset_rows() legitimately produces
    -- source_record_id=NULL for a table with no known (or no genuinely
    -- unique) primary-key column -- live-caught, BP-B, P-10, 2026-08-19,
    -- the very first real materialize() call after adding the
    -- non-unique-pk_column fallback above hit this constraint.
    source_record_id TEXT,
    canonical_fields JSON,
    original_fields JSON,
    context_score DOUBLE,
    graph_path JSON,
    provenance JSON,
    discovery_evidence JSON,
    quality_diagnostics JSON,
    run_id TEXT,
    created_at TIMESTAMP DEFAULT current_timestamp
)
"""

_INSERT_SQL = """
INSERT INTO canonical_matching_dataset (
    record_id, source_id, entity_type, entity_anchor_id,
    canonical_entity_id, source_table, source_record_id,
    canonical_fields, original_fields, context_score,
    graph_path, provenance, discovery_evidence,
    quality_diagnostics, run_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (record_id) DO UPDATE SET
    canonical_entity_id = excluded.canonical_entity_id,
    context_score = excluded.context_score,
    graph_path = excluded.graph_path,
    provenance = excluded.provenance
"""


def serialize_graph_path(path: list[PathHop]) -> list[dict[str, Any]]:
    """A ``PathHop`` chain (P-9's ``NodeProvenance.path``) into plain,
    JSON-serializable dicts -- the exact route from the entity anchor to
    this record's own table, one hop per element."""
    return [
        {
            "from_table": hop.from_table,
            "from_column": hop.from_column,
            "to_table": hop.to_table,
            "to_column": hop.to_column,
            "direction": hop.direction,
            "confidence": hop.edge.confidence,
            "rationale": hop.edge.rationale,
        }
        for hop in path
    ]


def build_dataset_rows(
    extraction_results: dict[tuple[str, str], list[dict[str, Any]]],
    *,
    entity_type: str,
    entity_anchor_id: str | None,
    field_mappings_by_table: dict[tuple[str, str], dict[str, dict[str, Any]]],
    context_scores_by_table: dict[tuple[str, str], float],
    graph_paths_by_table: dict[tuple[str, str], list[dict[str, Any]]],
    record_id_column_by_table: dict[tuple[str, str], str],
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """One row per extracted source record, every lineage field filled in
    from what an earlier phase already computed -- never re-derived, never
    guessed.

    ``extraction_results``: ``{(origin_id, table): [row, ...]}`` -- P-9's
    ``execute_extraction`` output, or any source with the same shape.
    ``field_mappings_by_table``: ``{(origin_id, table): {source_column:
    {"canonical_field": str, "confidence": float, "origin": str}}}`` -- P-7's
    field mappings, keyed for a direct per-column lookup so a source column
    with no mapping is simply absent from ``canonical_fields`` (never
    invented -- CLAUDE.md's "no new destination columns" applies here too:
    an unmapped source field stays in ``original_fields``, never promoted).
    ``record_id_column_by_table``: ``{(origin_id, table): column_name}`` --
    which column is that table's own primary key, for ``source_record_id``.
    A table with no known PK column still produces rows (``source_record_id``
    is ``None``), since a record missing its own id is still worth keeping
    for lineage -- just not addressable by id alone.
    """
    rows: list[dict[str, Any]] = []
    for node, records in extraction_results.items():
        origin_id, table_name = node
        mapping = field_mappings_by_table.get(node, {})
        pk_column = record_id_column_by_table.get(node)
        context_score = context_scores_by_table.get(node)
        graph_path = graph_paths_by_table.get(node, [])

        # A caller-supplied pk_column that turns out not to be unique within
        # this batch is worse than no pk_column at all: every colliding row
        # gets the SAME record_id, and materialize()'s upsert then silently
        # overwrites all but the last one -- exactly the kind of quiet data
        # loss this project treats as a real bug (live-caught, BP-B, P-10,
        # 2026-08-19: a caller passed "companyID" -- a foreign key, not
        # CompanyExperience's own primary key -- collapsing every
        # experience row for one company into a single overwritten record).
        # Verified once per table, not trusted blindly.
        if pk_column:
            values = [
                record.get(pk_column) for record in records if record.get(pk_column) is not None
            ]
            if len(values) != len(set(values)):
                logger.warning(
                    "record_id_column %r for %s.%s is not unique across %d row(s); "
                    "falling back to positional record ids for this table.",
                    pk_column,
                    origin_id,
                    table_name,
                    len(records),
                )
                pk_column = None

        for index, record in enumerate(records):
            source_record_id = str(record.get(pk_column)) if pk_column else None
            canonical_fields: dict[str, Any] = {}
            discovery_evidence: dict[str, Any] = {}
            for source_column, value in record.items():
                field_info = mapping.get(source_column)
                if field_info is None:
                    continue
                canonical_fields[field_info["canonical_field"]] = value
                discovery_evidence[source_column] = {
                    "canonical_field": field_info["canonical_field"],
                    "confidence": field_info.get("confidence"),
                    "origin": field_info.get("origin"),
                }

            # A missing PK still gets a stable, reproducible record_id (the
            # row's own position in this extraction) rather than a fresh
            # random uuid every run, which would make record_id useless as
            # a lineage anchor across re-extractions of the same data.
            record_id = f"{origin_id}:{table_name}:{source_record_id or index}"
            rows.append(
                {
                    "record_id": record_id,
                    "source_id": origin_id,
                    "entity_type": entity_type,
                    "entity_anchor_id": entity_anchor_id,
                    "canonical_entity_id": None,
                    "source_table": table_name,
                    "source_record_id": source_record_id,
                    "canonical_fields": canonical_fields,
                    "original_fields": record,
                    "context_score": context_score,
                    "graph_path": graph_path,
                    "provenance": {
                        "origin_id": origin_id,
                        "table": table_name,
                        "record_id_column": pk_column,
                    },
                    "discovery_evidence": discovery_evidence,
                    "quality_diagnostics": {},
                    "run_id": run_id,
                }
            )
    return rows


def materialize(duckdb_path: str, rows: list[dict[str, Any]]) -> int:
    """Write ``rows`` into the run's own DuckDB spool file, as the
    ``canonical_matching_dataset`` table (created if absent). An existing
    ``record_id`` is updated in place (``canonical_entity_id``/
    ``context_score``/``graph_path``/``provenance`` only -- lineage that can
    legitimately change on a re-run without discarding the row's history),
    never duplicated. Returns the number of rows written."""
    import duckdb

    if not rows:
        return 0
    con = duckdb.connect(duckdb_path)
    try:
        con.execute(_SCHEMA_SQL)
        con.executemany(
            _INSERT_SQL,
            [
                (
                    r["record_id"],
                    r["source_id"],
                    r["entity_type"],
                    r["entity_anchor_id"],
                    r["canonical_entity_id"],
                    r["source_table"],
                    r["source_record_id"],
                    json.dumps(r["canonical_fields"], default=str),
                    json.dumps(r["original_fields"], default=str),
                    r["context_score"],
                    json.dumps(r["graph_path"], default=str),
                    json.dumps(r["provenance"], default=str),
                    json.dumps(r["discovery_evidence"], default=str),
                    json.dumps(r["quality_diagnostics"], default=str),
                    r["run_id"],
                )
                for r in rows
            ],
        )
        logger.info(
            "Materialized %d canonical_matching_dataset row(s) into %s", len(rows), duckdb_path
        )
        return len(rows)
    finally:
        con.close()
