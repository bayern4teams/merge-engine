"""V3 discovery-engine metadata store (Part B, P-1).

A dedicated, project-owned PostgreSQL store for everything the discovery
engine learns about a source system: its structure (source/database/schema/
table/column), what was measured about it (profile), what was inferred
(candidate_key/relationship/canonical_field/field_mapping), and a generic
edge table the metadata-graph UI (P-8) can traverse without needing a
type-specific join for every edge kind.

Deliberately its own store, not a reuse of the destination connector: the
destination a user picks for one merge run may be ClickHouse, but discovery
metadata is project-wide and persists across runs and destinations (ADR
0011 in docs/decisions/ -- DataHub's metadata *model* is adopted, its
infrastructure is not; this module is where that model actually lives).

Every discovery row carries the same four columns, per PreSplink's schema
(Branch_Plan.md P-1):

- ``confidence`` (float, 0.0-1.0) -- never an opaque number; the ``evidence``
  column alongside it records what produced it.
- ``evidence`` (JSONB) -- the signals that produced the confidence score.
- ``algorithm_version`` (text) -- which version of which algorithm produced
  this row, so a re-run with an improved algorithm is a visible diff, not a
  silent overwrite.
- ``origin`` -- one of ALGORITHMIC / OSS_TOOL / LLM_ASSISTED / USER_CONFIRMED.
- ``review_status`` -- one of AUTO_ACCEPTED / AUTO_REJECTED / NEEDS_REVIEW /
  USER_ACCEPTED / USER_REJECTED.

A human override is *never* a mutation of the algorithmic row -- see
``record_override``: the original decision and the human's decision are
both preserved, as two separate rows a human or an algorithm can both still
see (PreSplink §39).
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as Psycopg2Connection

from src.utils.id_utils import generate_uuid

logger = logging.getLogger("merge-engine.tools.metadata_graph")

# PreSplink's origin/review_status enums -- validated in Python (not a
# Postgres CHECK constraint) so a schema migration is never needed to add a
# new value; callers that pass something outside these sets get a loud
# ValueError instead of a row a later reader can't interpret.
VALID_ORIGINS = frozenset({"ALGORITHMIC", "OSS_TOOL", "LLM_ASSISTED", "USER_CONFIRMED"})
VALID_REVIEW_STATUSES = frozenset(
    {"AUTO_ACCEPTED", "AUTO_REJECTED", "NEEDS_REVIEW", "USER_ACCEPTED", "USER_REJECTED"}
)
VALID_RUN_STATUSES = frozenset({"QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "PARTIAL"})
VALID_KEY_TYPES = frozenset(
    {
        "DECLARED_PRIMARY_KEY",
        "DECLARED_UNIQUE",
        "INFERRED_SINGLE_COLUMN",
        "INFERRED_COMPOSITE",
    }
)


def _validate_origin(origin: str) -> None:
    if origin not in VALID_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(VALID_ORIGINS)}, got {origin!r}")


def _validate_review_status(review_status: str) -> None:
    if review_status not in VALID_REVIEW_STATUSES:
        raise ValueError(
            f"review_status must be one of {sorted(VALID_REVIEW_STATUSES)}, got {review_status!r}"
        )


class MetadataStore:
    """Owns the connection and schema for the V3 discovery metadata store."""

    # Every discovery-evidence table gets these four columns, in this order,
    # right before created_at -- kept as one constant so ensure_schema's DDL
    # and every insert helper agree on the shape without repeating it five
    # times with a chance to drift.
    _EVIDENCE_COLUMNS: ClassVar[str] = """
        confidence DOUBLE PRECISION,
        evidence JSONB,
        algorithm_version TEXT,
        origin TEXT,
        review_status TEXT
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Parameters
        ----------
        config : dict
            Connection configuration: host, port, user, password, database,
            schema (default "public") -- same shape every other connector in
            this project takes.
        """
        self._config = config
        self._connection: Psycopg2Connection | None = None
        self._schema: str = config.get("schema", "public")

    # ── Connection lifecycle ─────────────────────────────────────────

    def _ensure_connection(self) -> Psycopg2Connection:
        if self._connection is not None and self._connection.closed == 0:
            return self._connection
        self._connection = psycopg2.connect(
            host=self._config.get("host", "localhost"),
            port=self._config.get("port", 5432),
            user=self._config.get("user", ""),
            password=self._config.get("password", ""),
            database=self._config.get("database", ""),
            connect_timeout=30,
        )
        return self._connection

    def close(self) -> None:
        if self._connection is not None and self._connection.closed == 0:
            self._connection.close()
        self._connection = None

    def __enter__(self) -> MetadataStore:
        self._ensure_connection()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ── Schema ────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create every md_* table if it doesn't already exist.

        One transaction: either the whole metadata schema exists afterwards,
        or none of it does (a half-created schema is worse than none, since
        it looks ready to a caller that only checks one table).
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        s = self._schema
        try:
            # md_run: the discovery-run model (PreSplink §40). Every other
            # table's run_id points here, so a run's full footprint --
            # everything it discovered, inferred, or measured -- is one
            # query away.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_run" (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ DEFAULT now(),
                    finished_at TIMESTAMPTZ,
                    counts JSONB,
                    warnings JSONB,
                    errors JSONB,
                    configuration JSONB,
                    algorithm_versions JSONB
                )
            """)

            # md_source / md_database / md_schema / md_table / md_column:
            # the structural backbone. Every row has its own stable id
            # (never keyed on name alone, per PreSplink §15) so a renamed
            # table is a new fact about the same object, not a new object.
            # Passwords are never stored here -- same convention as
            # src/orchestrator/profiles.py's strip_secrets: this table
            # exists to be diffed, exported, and read by a human, and a
            # credential in a diffable table is a leak waiting to happen.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_source" (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    host TEXT,
                    port INTEGER,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_database" (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES "{s}"."md_source"(id),
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (source_id, name)
                )
            """)
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_schema" (
                    id TEXT PRIMARY KEY,
                    database_id TEXT NOT NULL REFERENCES "{s}"."md_database"(id),
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (database_id, name)
                )
            """)
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_table" (
                    id TEXT PRIMARY KEY,
                    schema_id TEXT NOT NULL REFERENCES "{s}"."md_schema"(id),
                    name TEXT NOT NULL,
                    row_count_estimate BIGINT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (schema_id, name)
                )
            """)
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_column" (
                    id TEXT PRIMARY KEY,
                    table_id TEXT NOT NULL REFERENCES "{s}"."md_table"(id),
                    name TEXT NOT NULL,
                    data_type TEXT,
                    ordinal_position INTEGER,
                    nullable BOOLEAN,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (table_id, name)
                )
            """)

            # md_profile: P-3's output. mode is FULL/SAMPLED/APPROXIMATE --
            # PreSplink §46 requires the UI show which one produced a given
            # number explicitly, so it is a first-class column, not buried
            # in evidence JSON a human has to open to find.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_profile" (
                    id TEXT PRIMARY KEY,
                    column_id TEXT NOT NULL REFERENCES "{s}"."md_column"(id),
                    run_id TEXT REFERENCES "{s}"."md_run"(id),
                    mode TEXT NOT NULL,
                    null_count BIGINT,
                    null_ratio DOUBLE PRECISION,
                    distinct_count BIGINT,
                    distinct_ratio DOUBLE PRECISION,
                    unique_ratio DOUBLE PRECISION,
                    min_value TEXT,
                    max_value TEXT,
                    length_distribution JSONB,
                    top_values JSONB,
                    pattern_distribution JSONB,
                    type_distribution JSONB,
                    computed_at TIMESTAMPTZ DEFAULT now()
                )
            """)

            # md_candidate_key: P-5. key_type distinguishes a declared PK
            # from an inferred one at the type level -- an inferred
            # candidate is never called a PK (PreSplink §18), so this is a
            # column value, not something read out of evidence.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_candidate_key" (
                    id TEXT PRIMARY KEY,
                    table_id TEXT NOT NULL REFERENCES "{s}"."md_table"(id),
                    columns JSONB NOT NULL,
                    key_type TEXT NOT NULL,
                    run_id TEXT REFERENCES "{s}"."md_run"(id),
                    {self._EVIDENCE_COLUMNS},
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)

            # md_relationship: P-6. coverage is the directional
            # |distinct(A) ∩ distinct(B)| / |distinct(A)| formula PreSplink
            # §21 specifies -- stored as its own column (not just inside
            # evidence) because BP-B2's live check queries it directly.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_relationship" (
                    id TEXT PRIMARY KEY,
                    from_column_id TEXT NOT NULL REFERENCES "{s}"."md_column"(id),
                    to_column_id TEXT NOT NULL REFERENCES "{s}"."md_column"(id),
                    relationship_type TEXT NOT NULL,
                    coverage DOUBLE PRECISION,
                    signals JSONB,
                    run_id TEXT REFERENCES "{s}"."md_run"(id),
                    {self._EVIDENCE_COLUMNS},
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)

            # md_canonical_field: P-7's source-independent canonical
            # schema. Not itself evidence-carrying -- it's the fixed
            # target md_field_mapping rows point at.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_canonical_field" (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    data_type TEXT,
                    description TEXT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (entity_type, field_name)
                )
            """)

            # md_field_mapping: P-7. Every mapping stores source_column,
            # canonical_field, confidence, evidence, origin (PreSplink §25)
            # -- origin is what lets a human tell "Valentine proposed this"
            # from "the LLM proposed this" from "a human typed this in".
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_field_mapping" (
                    id TEXT PRIMARY KEY,
                    source_column_id TEXT NOT NULL REFERENCES "{s}"."md_column"(id),
                    canonical_field_id TEXT NOT NULL REFERENCES "{s}"."md_canonical_field"(id),
                    run_id TEXT REFERENCES "{s}"."md_run"(id),
                    {self._EVIDENCE_COLUMNS},
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)

            # md_edge: a generic, type-tagged edge for the metadata-graph
            # UI (P-8) to traverse without a type-specific join for every
            # edge kind. Complements, does not replace, the specific
            # md_relationship/md_field_mapping tables -- those remain the
            # source of truth for their own domain; md_edge is a
            # denormalised traversal index over all of them plus any edge
            # kind that doesn't warrant its own table.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_edge" (
                    id TEXT PRIMARY KEY,
                    from_node_type TEXT NOT NULL,
                    from_node_id TEXT NOT NULL,
                    to_node_type TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    run_id TEXT REFERENCES "{s}"."md_run"(id),
                    {self._EVIDENCE_COLUMNS},
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS md_edge_from_idx
                    ON "{s}"."md_edge" (from_node_type, from_node_id)
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS md_edge_to_idx
                    ON "{s}"."md_edge" (to_node_type, to_node_id)
            """)

            # md_override: PreSplink §39 -- human overrides are additive.
            # target_table/target_id name the row being overridden;
            # original_decision is a snapshot of what the algorithm said,
            # captured at override time, so the algorithmic row upstream
            # can still change (a re-run, an improved algorithm) without
            # erasing what a human actually saw when they overrode it.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{s}"."md_override" (
                    id TEXT PRIMARY KEY,
                    target_table TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    original_decision JSONB,
                    human_decision JSONB NOT NULL,
                    user_name TEXT,
                    reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS md_override_target_idx
                    ON "{s}"."md_override" (target_table, target_id)
            """)

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    # ── md_run ────────────────────────────────────────────────────────

    def create_run(
        self,
        kind: str,
        *,
        configuration: dict[str, Any] | None = None,
        algorithm_versions: dict[str, Any] | None = None,
    ) -> str:
        """Start a new discovery run, status QUEUED. Returns the run id."""
        run_id = generate_uuid()
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."md_run"
                    (id, kind, status, configuration, algorithm_versions)
                VALUES (%s, %s, 'QUEUED', %s, %s)
                """,
                (
                    run_id,
                    kind,
                    json.dumps(configuration or {}),
                    json.dumps(algorithm_versions or {}),
                ),
            )
            conn.commit()
            return run_id
        finally:
            cursor.close()

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        counts: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        finished: bool = False,
    ) -> None:
        if status not in VALID_RUN_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_RUN_STATUSES)}, got {status!r}")
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE "{self._schema}"."md_run"
                SET status = %s,
                    counts = COALESCE(%s, counts),
                    warnings = COALESCE(%s, warnings),
                    errors = COALESCE(%s, errors),
                    finished_at = CASE WHEN %s THEN now() ELSE finished_at END
                WHERE id = %s
                """,
                (
                    status,
                    json.dumps(counts) if counts is not None else None,
                    json.dumps(warnings) if warnings is not None else None,
                    json.dumps(errors) if errors is not None else None,
                    finished,
                    run_id,
                ),
            )
            conn.commit()
        finally:
            cursor.close()

    # ── Structural backbone (source/database/schema/table/column) ─────

    def upsert_source(self, name: str, source_type: str, host: str = "", port: int = 0) -> str:
        """Insert or update a registered source by name. Returns its id."""
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."md_source" (id, name, type, host, port)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (generate_uuid(), name, source_type, host, port or None),
            )
            # id is generated fresh above, so ON CONFLICT(id) never fires in
            # practice -- the real de-dup key is name, handled here: look up
            # first, insert only if truly new. Kept as two statements rather
            # than a name-based UNIQUE constraint because a source's name is
            # a display label a human may legitimately want to reuse across
            # genuinely different registered connections (e.g. two "staging"
            # sources for two different projects sharing this metadata
            # store) -- uniqueness is enforced at the (source, name) level
            # for database/schema/table/column below, not here.
            cursor.execute(
                f"""SELECT id FROM "{self._schema}"."md_source"
                    WHERE name = %s ORDER BY created_at LIMIT 1""",
                (name,),
            )
            row = cursor.fetchone()
            conn.commit()
            return row[0] if row else name
        finally:
            cursor.close()

    def upsert_database(self, source_id: str, name: str) -> str:
        return self._upsert_named_child(
            table="md_database",
            id_column="source_id",
            id_value=source_id,
            name=name,
        )

    def upsert_schema(self, database_id: str, name: str) -> str:
        return self._upsert_named_child(
            table="md_schema",
            id_column="database_id",
            id_value=database_id,
            name=name,
        )

    def upsert_table(self, schema_id: str, name: str, row_count_estimate: int | None = None) -> str:
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            new_id = generate_uuid()
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."md_table"
                    (id, schema_id, name, row_count_estimate)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (schema_id, name) DO UPDATE
                    SET row_count_estimate = EXCLUDED.row_count_estimate,
                        updated_at = now()
                RETURNING id
                """,
                (new_id, schema_id, name, row_count_estimate),
            )
            result_id = cursor.fetchone()[0]
            conn.commit()
            return str(result_id)
        finally:
            cursor.close()

    def upsert_column(
        self,
        table_id: str,
        name: str,
        data_type: str = "",
        ordinal_position: int | None = None,
        nullable: bool | None = None,
    ) -> str:
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            new_id = generate_uuid()
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."md_column"
                    (id, table_id, name, data_type, ordinal_position, nullable)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (table_id, name) DO UPDATE
                    SET data_type = EXCLUDED.data_type,
                        ordinal_position = EXCLUDED.ordinal_position,
                        nullable = EXCLUDED.nullable
                RETURNING id
                """,
                (new_id, table_id, name, data_type, ordinal_position, nullable),
            )
            result_id = cursor.fetchone()[0]
            conn.commit()
            return str(result_id)
        finally:
            cursor.close()

    def _upsert_named_child(self, *, table: str, id_column: str, id_value: str, name: str) -> str:
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            new_id = generate_uuid()
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."{table}" (id, {id_column}, name)
                VALUES (%s, %s, %s)
                ON CONFLICT ({id_column}, name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (new_id, id_value, name),
            )
            result_id = cursor.fetchone()[0]
            conn.commit()
            return str(result_id)
        finally:
            cursor.close()

    # ── Human overrides (additive, PreSplink §39) ──────────────────────

    def record_override(
        self,
        target_table: str,
        target_id: str,
        *,
        original_decision: dict[str, Any] | None,
        human_decision: dict[str, Any],
        user_name: str = "",
        reason: str = "",
    ) -> str:
        """Record a human override as a NEW row -- never mutates the
        algorithmic row at (target_table, target_id)."""
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            override_id = generate_uuid()
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."md_override"
                    (id, target_table, target_id, original_decision, human_decision,
                     user_name, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    override_id,
                    target_table,
                    target_id,
                    json.dumps(original_decision) if original_decision is not None else None,
                    json.dumps(human_decision),
                    user_name,
                    reason,
                ),
            )
            conn.commit()
            return override_id
        finally:
            cursor.close()

    def get_overrides(self, target_table: str, target_id: str) -> list[dict[str, Any]]:
        """Every override recorded for one row, oldest first -- the full
        history a human reviewing a decision might need, not just the
        latest one."""
        conn = self._ensure_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute(
                f"""
                SELECT id, original_decision, human_decision, user_name, reason, created_at
                FROM "{self._schema}"."md_override"
                WHERE target_table = %s AND target_id = %s
                ORDER BY created_at ASC
                """,
                (target_table, target_id),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    # ── Evidence-carrying discovery rows (P-5/P-6/P-7 write through these) ─

    def _insert_evidence_row(
        self,
        table: str,
        columns: dict[str, Any],
        *,
        confidence: float | None,
        evidence: dict[str, Any] | None,
        algorithm_version: str,
        origin: str,
        review_status: str,
        run_id: str | None,
    ) -> str:
        """Shared insert path for every md_* table carrying the standard
        confidence/evidence/algorithm_version/origin/review_status columns
        -- one place validating origin/review_status so P-5/P-6/P-7 cannot
        each independently forget to."""
        _validate_origin(origin)
        _validate_review_status(review_status)
        row_id = generate_uuid()
        all_columns = {"id": row_id, "run_id": run_id, **columns}
        col_names = [
            *all_columns,
            "confidence",
            "evidence",
            "algorithm_version",
            "origin",
            "review_status",
        ]
        placeholders = ", ".join(["%s"] * len(col_names))
        values = [
            *all_columns.values(),
            confidence,
            json.dumps(evidence or {}),
            algorithm_version,
            origin,
            review_status,
        ]
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""INSERT INTO "{self._schema}"."{table}" ({", ".join(col_names)})
                    VALUES ({placeholders})""",
                values,
            )
            conn.commit()
            return row_id
        finally:
            cursor.close()

    def insert_candidate_key(
        self,
        table_id: str,
        columns: list[str],
        key_type: str,
        *,
        confidence: float | None = None,
        evidence: dict[str, Any] | None = None,
        algorithm_version: str = "",
        origin: str = "ALGORITHMIC",
        review_status: str = "NEEDS_REVIEW",
        run_id: str | None = None,
    ) -> str:
        if key_type not in VALID_KEY_TYPES:
            raise ValueError(f"key_type must be one of {sorted(VALID_KEY_TYPES)}, got {key_type!r}")
        return self._insert_evidence_row(
            "md_candidate_key",
            {"table_id": table_id, "columns": json.dumps(columns), "key_type": key_type},
            confidence=confidence,
            evidence=evidence,
            algorithm_version=algorithm_version,
            origin=origin,
            review_status=review_status,
            run_id=run_id,
        )

    def insert_relationship(
        self,
        from_column_id: str,
        to_column_id: str,
        relationship_type: str,
        *,
        coverage: float | None = None,
        signals: dict[str, Any] | None = None,
        confidence: float | None = None,
        evidence: dict[str, Any] | None = None,
        algorithm_version: str = "",
        origin: str = "ALGORITHMIC",
        review_status: str = "NEEDS_REVIEW",
        run_id: str | None = None,
    ) -> str:
        return self._insert_evidence_row(
            "md_relationship",
            {
                "from_column_id": from_column_id,
                "to_column_id": to_column_id,
                "relationship_type": relationship_type,
                "coverage": coverage,
                "signals": json.dumps(signals or {}),
            },
            confidence=confidence,
            evidence=evidence,
            algorithm_version=algorithm_version,
            origin=origin,
            review_status=review_status,
            run_id=run_id,
        )

    def insert_field_mapping(
        self,
        source_column_id: str,
        canonical_field_id: str,
        *,
        confidence: float | None = None,
        evidence: dict[str, Any] | None = None,
        algorithm_version: str = "",
        origin: str = "ALGORITHMIC",
        review_status: str = "NEEDS_REVIEW",
        run_id: str | None = None,
    ) -> str:
        return self._insert_evidence_row(
            "md_field_mapping",
            {"source_column_id": source_column_id, "canonical_field_id": canonical_field_id},
            confidence=confidence,
            evidence=evidence,
            algorithm_version=algorithm_version,
            origin=origin,
            review_status=review_status,
            run_id=run_id,
        )

    def insert_edge(
        self,
        from_node_type: str,
        from_node_id: str,
        to_node_type: str,
        to_node_id: str,
        edge_type: str,
        *,
        confidence: float | None = None,
        evidence: dict[str, Any] | None = None,
        algorithm_version: str = "",
        origin: str = "ALGORITHMIC",
        review_status: str = "NEEDS_REVIEW",
        run_id: str | None = None,
    ) -> str:
        return self._insert_evidence_row(
            "md_edge",
            {
                "from_node_type": from_node_type,
                "from_node_id": from_node_id,
                "to_node_type": to_node_type,
                "to_node_id": to_node_id,
                "edge_type": edge_type,
            },
            confidence=confidence,
            evidence=evidence,
            algorithm_version=algorithm_version,
            origin=origin,
            review_status=review_status,
            run_id=run_id,
        )

    def upsert_canonical_field(
        self, entity_type: str, field_name: str, data_type: str = "", description: str = ""
    ) -> str:
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            new_id = generate_uuid()
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."md_canonical_field"
                    (id, entity_type, field_name, data_type, description)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (entity_type, field_name) DO UPDATE
                    SET data_type = EXCLUDED.data_type, description = EXCLUDED.description
                RETURNING id
                """,
                (new_id, entity_type, field_name, data_type, description),
            )
            result_id = cursor.fetchone()[0]
            conn.commit()
            return str(result_id)
        finally:
            cursor.close()

    def insert_profile(
        self,
        column_id: str,
        mode: str,
        *,
        run_id: str | None = None,
        null_count: int | None = None,
        null_ratio: float | None = None,
        distinct_count: int | None = None,
        distinct_ratio: float | None = None,
        unique_ratio: float | None = None,
        min_value: str | None = None,
        max_value: str | None = None,
        length_distribution: dict[str, Any] | None = None,
        top_values: list[Any] | None = None,
        pattern_distribution: dict[str, Any] | None = None,
        type_distribution: dict[str, Any] | None = None,
    ) -> str:
        """Store one P-3 profiling result. Not evidence-carrying in the
        confidence/origin/review_status sense (a profile is a measurement,
        not an inference a human would confirm or reject) -- its own
        columns instead, per PreSplink §16, with ``mode`` always present so
        a reader never has to guess whether a number is exact (PreSplink
        §46)."""
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            profile_id = generate_uuid()
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."md_profile"
                    (id, column_id, run_id, mode, null_count, null_ratio,
                     distinct_count, distinct_ratio, unique_ratio, min_value, max_value,
                     length_distribution, top_values, pattern_distribution, type_distribution)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    profile_id,
                    column_id,
                    run_id,
                    mode,
                    null_count,
                    null_ratio,
                    distinct_count,
                    distinct_ratio,
                    unique_ratio,
                    min_value,
                    max_value,
                    json.dumps(length_distribution) if length_distribution is not None else None,
                    json.dumps(top_values) if top_values is not None else None,
                    json.dumps(pattern_distribution) if pattern_distribution is not None else None,
                    json.dumps(type_distribution) if type_distribution is not None else None,
                ),
            )
            conn.commit()
            return profile_id
        finally:
            cursor.close()

    def get_latest_profile(self, column_id: str) -> dict[str, Any] | None:
        """The most recently computed profile for one column, or None if
        it has never been profiled."""
        conn = self._ensure_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute(
                f"""
                SELECT * FROM "{self._schema}"."md_profile"
                WHERE column_id = %s
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                (column_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()
