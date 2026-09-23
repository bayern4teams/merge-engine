"""
PostgreSQL destination connector for data loading and schema inspection.

Uses psycopg2-binary for connection and cursor management.
This connector is used for the destination PostgreSQL database where
merged/unified data is loaded.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager, suppress
from typing import Any, ClassVar

import psycopg2
import psycopg2.extras
from psycopg2 import OperationalError as Psycopg2OperationalError
from psycopg2.extensions import connection as Psycopg2Connection

from src.orchestrator.utils.retry import retry_on_transient
from src.utils.value_coercion import coerce_value

logger = logging.getLogger("merge-engine.connectors.postgresql_destination")


def _coerce_to_column_type(
    value: Any, column_type: str, stats: dict[str, int] | None = None
) -> Any:
    """Adapt a value to the destination column's declared type.

    Different origins legitimately model the same concept with different
    types — Pagila stores ``staff.active`` as BOOLEAN and ``customer.active``
    as INTEGER, and both feed the same merged ``person.active`` column.
    Postgres will not implicitly cast between them, so an entire entity's
    load fails on the first mismatched row unless the value is adapted here.
    Delegates to ``value_coercion.coerce_value``, the same logic
    ``map_fields`` uses to judge a mapping compatible in the first place, so
    the two never disagree about what "convertible" means.

    ``stats``, when given, is incremented in place: ``attempted`` for every
    non-trivial coercion tried and ``failed`` for every one that could not be
    converted (Fix #3, Phase 4.4) — surfaced later in the reconciliation
    report so a load that silently passed a bad value through as-is is a
    visible, counted event, not something only a query against the
    destination would reveal.

    Anything that can't be converted is passed through untouched, so the
    database still gets the chance to reject genuinely bad data rather than
    this function silently inventing a value.
    """
    if value is None or not column_type:
        return value

    coerced, ok, reason = coerce_value(value, column_type)
    if coerced == value and ok:
        # No actual conversion happened (already the right shape) — not
        # worth counting as an "attempt".
        return coerced
    if stats is not None:
        stats["attempted"] = stats.get("attempted", 0) + 1
        if not ok:
            stats["failed"] = stats.get("failed", 0) + 1
    if not ok:
        logger.warning(
            "Could not coerce value %r to column type %r: %s",
            value,
            column_type,
            reason,
        )
        return value
    return coerced


class PostgreSQLDestinationConnector:
    """PostgreSQL connector for destination writes.

    Handles upserts, schema inspection, and schema extension for the
    destination PostgreSQL database.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the connector with configuration.

        Parameters
        ----------
        config : dict
            Destination connection configuration with keys:
            - host (str): PostgreSQL hostname or IP
            - port (int): Port number (default 5432)
            - user (str): Username
            - password (str): Password
            - database (str): Database name
            - schema (str): Schema name (default "public")
        """
        self._config = config
        self._connection: Psycopg2Connection | None = None
        self._schema: str = config.get("schema", "public")
        # Set inside an `atomic()` block: individual writes stop committing
        # so the whole block lands or none of it does.
        self._in_transaction: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────

    def _commit(self, conn: Psycopg2Connection) -> None:
        """Commit, unless an enclosing `atomic()` block owns the commit."""
        if not self._in_transaction:
            conn.commit()

    def _rollback(self, conn: Psycopg2Connection) -> None:
        """Roll back, unless an enclosing `atomic()` block owns the rollback.

        Inside a block the exception propagates to `atomic()`, which undoes
        everything — rolling back here would discard the earlier entities
        too but leave the caller believing only this one failed.
        """
        if not self._in_transaction:
            conn.rollback()

    @contextmanager
    def atomic(self, dry_run: bool = False):
        """Make every write inside the block land together, or not at all.

        Without this, a merge writing three entities that fails on the third
        leaves the destination holding the first two — a half-merged
        database that no longer matches any origin and that a re-run has to
        reconcile against. PostgreSQL DDL is transactional too, so table
        creation and ALTERs inside the block roll back with the data.

        With ``dry_run`` the block always rolls back, so the caller can
        report exactly what *would* have been written without writing it.
        """
        conn = self._ensure_connection()
        if self._in_transaction:
            # Nested: the outermost block owns the outcome.
            yield self
            return

        self._in_transaction = True
        try:
            yield self
        except Exception:
            conn.rollback()
            logger.error("Transaction rolled back — the destination is unchanged.")
            raise
        else:
            if dry_run:
                conn.rollback()
                logger.info("Dry run: transaction rolled back, destination unchanged.")
            else:
                conn.commit()
                logger.info("Transaction committed.")
        finally:
            self._in_transaction = False

    def _ensure_connection(self) -> Psycopg2Connection:
        """Create or return the PostgreSQL connection."""
        if self._connection is not None and self._connection.closed == 0:
            return self._connection

        host = self._config.get("host", "localhost")
        port = self._config.get("port", 5432)
        user = self._config.get("user", "")
        password = self._config.get("password", "")
        database = self._config.get("database", "")

        self._connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=30,
        )
        return self._connection

    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None and self._connection.closed == 0:
            self._connection.close()
        self._connection = None

    def __enter__(self):
        self._ensure_connection()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ── Schema Inspection ─────────────────────────────────────────────

    def get_table_schema(self, table_name: str) -> dict[str, Any]:
        """Inspect the schema of an existing PostgreSQL table.

        Parameters
        ----------
        table_name : str
            Table name (may include schema prefix like "public.my_table").

        Returns
        -------
        dict
            {
                "columns": [
                    {"name": ..., "type": ..., "nullable": ...}
                ],
                "primary_key": str | None
            }
        """
        conn = self._ensure_connection()

        # Check if table exists
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                );
            """,
                (self._schema, table_name),
            )
            exists = cursor.fetchone()[0]

            if not exists:
                return {"columns": [], "primary_key": None}

            # Get column info
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position;
            """,
                (self._schema, table_name),
            )
            columns = [
                {
                    "name": row[0],
                    "type": self._normalize_type(row[1]),
                    "nullable": row[2] == "YES",
                }
                for row in cursor.fetchall()
            ]

            # Get primary key
            cursor.execute(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s;
            """,
                (self._schema, table_name),
            )
            pk_row = cursor.fetchone()
            primary_key = pk_row[0] if pk_row else None

            return {"columns": columns, "primary_key": primary_key}
        finally:
            cursor.close()

    def get_foreign_keys(self, table_name: str) -> dict[str, str]:
        """Column -> referenced table for every FK constraint on this table.

        Used at load time to translate a foreign-key column's raw source-record
        id into the referenced entity's actual destination id (a deterministic
        hash of its cluster membership, not the source id) before insert —
        without this a load_batch insert into e.g. company_relation fails with
        "Key (company_a_id)=(<raw source id>) is not present in table company".
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT kcu.column_name, ccu.table_name AS referenced_table
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                    AND tc.table_schema = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s;
            """,
                (self._schema, table_name),
            )
            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            cursor.close()

    def _fk_targets_for_column(self, table_name: str, column: str) -> list[str]:
        """Every table a single column has an FK constraint against — unlike
        ``get_foreign_keys`` (one row per column, last-wins), this keeps all
        of them. Needed because this project's entity/knowledge-graph schema
        deliberately gives one column two FKs at once (``company.company_id``
        references both ``organization`` and ``entity`` directly), and
        collapsing that to a single target would silently skip the
        intermediate ``organization`` row on the way to satisfying the chain.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT ccu.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                    AND tc.table_schema = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s
                    AND kcu.column_name = %s;
            """,
                (self._schema, table_name, column),
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_entity_supertype_chain(self, table_name: str) -> list[str] | None:
        """Ancestor tables (top-down, ``entity`` first) whose row must exist
        before ``table_name``'s own row can be inserted — ``[]`` when
        ``table_name`` IS ``entity``, ``None`` when it isn't part of an
        entity/knowledge-graph hierarchy at all (every other destination
        shape in this project is a normal flat table with no such chain, so
        this is a no-op for them).

        Walks PK -> FK -> referenced table's own PK -> FK -> ... preferring
        any target other than ``entity`` at each hop (see
        ``_fk_targets_for_column``) until it reaches ``entity`` or runs out
        of FKs to follow.
        """
        if table_name == "entity":
            return []
        chain: list[str] = []
        current = table_name
        seen = {table_name}
        while True:
            pk = self.get_table_schema(current).get("primary_key")
            if not pk:
                return None
            targets = self._fk_targets_for_column(current, pk)
            if not targets:
                return None
            parent = next((t for t in targets if t != "entity"), None) or (
                "entity" if "entity" in targets else None
            )
            if parent is None:
                return None
            if parent == "entity":
                chain.append("entity")
                chain.reverse()
                return chain
            if parent in seen:
                return None  # cycle guard — not a valid entity chain
            seen.add(parent)
            chain.append(parent)
            current = parent

    def ensure_entity_chain(self, entity_id: str, leaf_table: str, chain: list[str]) -> None:
        """Idempotently insert the ``entity`` row (and any intermediate
        supertype row, e.g. ``organization``) a leaf table's own insert
        requires — both to satisfy its FK constraints and the
        ``assert_entity_type()`` trigger. ``chain`` is ``get_entity_supertype_
        chain(leaf_table)``'s return value (top-down, ``entity`` first).
        ``entity_type`` is derived from ``leaf_table``'s own name
        (``company`` -> ``COMPANY``) — this schema's ``entity_type`` CHECK
        enum matches its subtype table names exactly, by convention.
        """
        if not chain:
            return
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            entity_type = leaf_table.upper()
            for table in chain:
                if table == "entity":
                    cursor.execute(
                        f'INSERT INTO "{self._schema}"."entity" (entity_id, entity_type) '
                        f"VALUES (%s, %s) ON CONFLICT (entity_id) DO NOTHING",
                        (entity_id, entity_type),
                    )
                else:
                    pk = self.get_table_schema(table).get("primary_key")
                    if not pk:
                        continue
                    cursor.execute(
                        f'INSERT INTO "{self._schema}"."{table}" ("{pk}") '
                        f'VALUES (%s) ON CONFLICT ("{pk}") DO NOTHING',
                        (entity_id,),
                    )
        finally:
            cursor.close()

    def get_or_create_taxonomy_term(
        self, taxonomy_code: str, term_code: str, term_name: str
    ) -> int:
        """Resolve a controlled-vocabulary term, creating its taxonomy and
        the term itself on first use — this schema's own design intent
        (``taxonomy.is_extensible`` defaults to true) for values discovered
        at load time rather than pre-seeded.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'SELECT taxonomy_id FROM "{self._schema}"."taxonomy" WHERE code = %s',
                (taxonomy_code,),
            )
            row = cursor.fetchone()
            if row:
                taxonomy_id = row[0]
            else:
                cursor.execute(
                    f'INSERT INTO "{self._schema}"."taxonomy" (code, name) '
                    f"VALUES (%s, %s) ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code "
                    f"RETURNING taxonomy_id",
                    (taxonomy_code, taxonomy_code.replace("_", " ").title()),
                )
                taxonomy_id = cursor.fetchone()[0]

            cursor.execute(
                f'SELECT taxonomy_term_id FROM "{self._schema}"."taxonomy_term" '
                f"WHERE taxonomy_id = %s AND code = %s",
                (taxonomy_id, term_code),
            )
            row = cursor.fetchone()
            if row:
                return row[0]
            cursor.execute(
                f'INSERT INTO "{self._schema}"."taxonomy_term" (taxonomy_id, code, name) '
                f"VALUES (%s, %s, %s) ON CONFLICT (taxonomy_id, code) DO UPDATE SET "
                f"code = EXCLUDED.code RETURNING taxonomy_term_id",
                (taxonomy_id, term_code, term_name),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def get_or_create_source(self, uri: str, title: str) -> int:
        """Resolve the shared provenance ``source`` row for one load run
        (one per ``load_data_impl`` call, reused across all its records —
        not one per fact; that's what ``source`` vs. ``knowledge_item``
        each represent)."""
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'SELECT source_id FROM "{self._schema}"."source" WHERE uri = %s', (uri,)
            )
            row = cursor.fetchone()
            if row:
                return row[0]
            source_type_id = self.get_or_create_taxonomy_term("source_type", "DATABASE", "Database")
            cursor.execute(
                f'INSERT INTO "{self._schema}"."source" (source_type_term_id, uri, title) '
                f"VALUES (%s, %s, %s) RETURNING source_id",
                (source_type_id, uri, title),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def get_or_create_fact_predicate(
        self, code: str, name: str, subject_entity_type: str, value_kind: str
    ) -> int:
        """Resolve a fact predicate, creating it on first use. ``code`` is
        namespaced per entity type by the caller (e.g. ``company_website``)
        — ``fact_predicate.code`` is globally unique, and the same source
        field name can legitimately mean different things for different
        entity types.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'SELECT predicate_id FROM "{self._schema}"."fact_predicate" WHERE code = %s',
                (code,),
            )
            row = cursor.fetchone()
            if row:
                return row[0]
            cursor.execute(
                f'INSERT INTO "{self._schema}"."fact_predicate" '
                f"(code, name, subject_entity_type, value_kind) "
                f"VALUES (%s, %s, %s, %s) ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code "
                f"RETURNING predicate_id",
                (code, name, subject_entity_type, value_kind),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def write_entity_name(
        self,
        item_id: str,
        entity_id: str,
        name: str,
        name_type_term_id: int,
        language_code: str = "en",
    ) -> None:
        """Insert one ``entity_name`` row. ``item_id`` is caller-supplied and
        deterministic (derived from ``entity_id``) so a re-run of the same
        merge doesn't insert a duplicate name every time."""
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'INSERT INTO "{self._schema}"."entity_name" '
                f"(item_id, entity_id, name, normalized_name, language_code, name_type_term_id) "
                f"VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (item_id) DO NOTHING",
                (item_id, entity_id, name, name.strip().lower(), language_code, name_type_term_id),
            )
        finally:
            cursor.close()

    def write_knowledge_fact(
        self,
        item_id: str,
        entity_id: str,
        predicate_id: int,
        value_kind: str,
        value: Any,
        source_id: int,
    ) -> None:
        """Insert one ``knowledge_item`` + ``knowledge_fact`` pair.
        ``item_id`` is caller-supplied and deterministic (derived from
        ``entity_id`` + the predicate code) so a re-run of the same merge
        doesn't duplicate historical facts. Only TEXT/NUMERIC/DATE/BOOLEAN
        are supported.
        # ponytail: ENTITY/CODE/MONEY value kinds need extra reference data
        # (a controlled code taxonomy, currency + exchange-rate lookups) this
        # writer doesn't resolve yet — a value that would need one of those
        # is coerced to TEXT by the caller instead of raising.
        """
        value_column = {
            "TEXT": "value_text",
            "NUMERIC": "value_numeric",
            "DATE": "value_date",
            "BOOLEAN": "value_boolean",
        }[value_kind]
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'INSERT INTO "{self._schema}"."knowledge_item" (item_id, item_type, source_id) '
                f"VALUES (%s, 'FACT', %s) ON CONFLICT (item_id) DO NOTHING",
                (item_id, source_id),
            )
            cursor.execute(
                f'INSERT INTO "{self._schema}"."knowledge_fact" '
                f'(item_id, subject_entity_id, predicate_id, value_kind, "{value_column}") '
                f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT (item_id) DO NOTHING",
                (item_id, entity_id, predicate_id, value_kind, value),
            )
        finally:
            cursor.close()

    def list_tables(self) -> list[str]:
        """List all tables in the target schema.

        Returns
        -------
        list[str]
            Table names.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE';
            """,
                (self._schema,),
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()

    def read_entity_rows(
        self,
        table_name: str,
        limit: int = 1000,
        name_columns: list[str] | None = None,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read existing destination rows, optionally narrowed to a subject.

        Used for destination dedup: a scoped merge pulls the destination rows
        that might already be the subject (matched on a name column) so the
        matcher can recognise an incoming record as one already present and
        update it in place instead of inserting a duplicate. Best-effort and
        bounded — a missing table or column yields an empty list, never an error.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            where = ""
            params: list[Any] = []
            cols = [c for c in (name_columns or []) if c]
            if subject and cols:
                clauses = []
                for c in cols:
                    clauses.append(f'"{c}"::text ILIKE %s')
                    params.append(f"%{subject}%")
                where = " WHERE " + " OR ".join(clauses)
            sql = f'SELECT * FROM "{self._schema}"."{table_name}"{where} LIMIT {int(limit)}'
            cursor.execute(sql, params)
            colnames = [d[0] for d in cursor.description]
            return [dict(zip(colnames, row, strict=True)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("Could not read destination rows from %s: %s", table_name, exc)
            # A read failure must not stop a merge — dedup is an enhancement,
            # not a prerequisite. Roll back so the aborted SELECT does not
            # leave the shared connection in a failed-transaction state.
            with suppress(Exception):
                conn.rollback()
            return []
        finally:
            cursor.close()

    async def sample_distinct_values(
        self, table_name: str, column: str, limit: int = 500
    ) -> list[Any]:
        """Bounded random DISTINCT sample from a destination column
        (map_fields' Valentine Phase 0, B.3.1).

        Same duck-typed name/shape as the origin connectors'
        ``sample_distinct_values`` (e.g. ``PostgresConnector``) — join_graph's
        inclusion-dependency check and map_fields' Phase 0 can treat either
        kind of connector identically. Declared ``async def`` to match that
        shape; the work itself is synchronous psycopg2, same as every other
        method on this class. Best-effort: a missing table/column degrades
        to ``[]``, never an error — this is an enhancement over the
        heuristic tiers, not a prerequisite for mapping to work at all.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'SELECT "{column}" FROM ('
                f'SELECT DISTINCT "{column}" FROM "{self._schema}"."{table_name}" '
                f'WHERE "{column}" IS NOT NULL LIMIT %s'
                f") t ORDER BY random() LIMIT %s",
                (limit * 5, limit),
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as exc:
            logger.debug("sample_distinct_values failed for %s.%s: %s", table_name, column, exc)
            with suppress(Exception):
                conn.rollback()
            return []
        finally:
            cursor.close()

    def column_exists(self, table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table.

        Parameters
        ----------
        table_name : str
            Table name.
        column_name : str
            Column name.

        Returns
        -------
        bool
            True if column exists.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s AND column_name = %s
                );
            """,
                (self._schema, table_name, column_name),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def execute_sql(self, sql: str, params: tuple[Any, ...] | None = None) -> list[Any]:
        """Run one ad hoc SQL statement, returning its rows (or ``[]`` for
        a statement with no result set, e.g. DDL).

        A minimal, generic escape hatch for callers that need destination
        SQL with no dedicated typed method (e.g. resolve_entities.py's
        pg_trgm trigram-blocking pass, B.1.3) — every upsert/write still
        goes through its own typed method above; this is for one-off
        reads/DDL only. Same cursor/commit/rollback idiom as
        ``read_entity_rows``/``column_exists`` above.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            if cursor.description is None:
                self._commit(conn)
                return []
            rows: list[Any] = cursor.fetchall()
            self._commit(conn)
            return rows
        except Exception:
            self._rollback(conn)
            raise
        finally:
            cursor.close()

    # ── Data Loading (Upsert) ─────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def load_batch(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        mapping: dict[str, Any] | None = None,
        upsert_key: str = "id",
    ) -> dict[str, int]:
        """Upsert records into a PostgreSQL table.

        Uses INSERT ... ON CONFLICT DO UPDATE for upsert behavior.

        Parameters
        ----------
        table_name : str
            Destination table name.
        records : list[dict]
            Unified records to upsert.
        mapping : dict, optional
            Field mapping used to transform record keys before writing.
        upsert_key : str
            Column to use as the conflict key (default "id").

        Returns
        -------
        dict
            {"inserted": int, "updated": int}
        """
        conn = self._ensure_connection()

        if not records:
            return {"inserted": 0, "updated": 0}

        # Only write columns that actually exist in the destination table —
        # records may carry provenance fields (_loaded_at, _source_count, …)
        # or unmapped source fields the table doesn't have.
        dest_columns = self.get_table_schema(table_name).get("columns", [])
        existing_columns = {c["name"] for c in dest_columns}
        # Declared type per column, so values from origins that disagree about
        # a shared column's type can be coerced. Real case: merging Pagila's
        # staff (active BOOLEAN) with its customer (active INTEGER) into one
        # person table — without coercion the whole entity load fails on
        # "column active is of type boolean but expression is of type integer".
        column_types = {c["name"]: str(c.get("type", "")).lower() for c in dest_columns}
        record_columns = list(records[0].keys())
        if existing_columns:
            target_columns = [c for c in record_columns if c in existing_columns]
        else:
            target_columns = record_columns
        if upsert_key not in target_columns:
            raise ValueError(
                f"Upsert key '{upsert_key}' not available for table "
                f"'{table_name}' (columns: {sorted(target_columns)})"
            )

        quoted_columns = ", ".join(f'"{c}"' for c in target_columns)
        placeholders = ", ".join(f"%({c})s" for c in target_columns)
        updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in target_columns if c != upsert_key)
        conflict_action = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"

        # xmax = 0 → freshly inserted row; xmax != 0 → updated row.
        sql = f"""
            INSERT INTO "{self._schema}"."{table_name}" ({quoted_columns})
            VALUES ({placeholders})
            ON CONFLICT ("{upsert_key}") {conflict_action}
            RETURNING (xmax = 0) AS was_inserted
        """

        total_inserted = 0
        total_updated = 0
        coercion_stats: dict[str, int] = {}

        cursor = conn.cursor()
        try:
            for record in records:
                processed = {}
                for key in target_columns:
                    value = record.get(key)
                    if isinstance(value, (dict, list)):
                        processed[key] = json.dumps(value)
                    else:
                        processed[key] = _coerce_to_column_type(
                            value, column_types.get(key, ""), stats=coercion_stats
                        )

                cursor.execute(sql, processed)
                row = cursor.fetchone()
                if row is None:
                    total_updated += 1  # DO NOTHING conflict
                elif row[0]:
                    total_inserted += 1
                else:
                    total_updated += 1

            self._commit(conn)

        except Exception:
            self._rollback(conn)
            raise
        finally:
            cursor.close()

        return {
            "inserted": total_inserted,
            "updated": total_updated,
            # Fix #3 (Phase 4.4): how many values needed adapting to the
            # destination column's type, and how many of those could not be —
            # a scorecard-visible signal (Phase 6) instead of a value that
            # silently passed through unconverted.
            "type_coercions_attempted": coercion_stats.get("attempted", 0),
            "type_coercion_failures": coercion_stats.get("failed", 0),
        }

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def load_batch_bulk(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        upsert_key: str = "id",
    ) -> dict[str, int]:
        """Bulk load records using INSERT ... ON CONFLICT.

        Uses executemany for better performance with large batches.

        Parameters
        ----------
        table_name : str
            Destination table name.
        records : list[dict]
            Unified records to insert.
        upsert_key : str
            Column to use as the conflict key.

        Returns
        -------
        dict
            {"inserted": int, "updated": int}
        """
        conn = self._ensure_connection()

        if not records:
            return {"inserted": 0, "updated": 0}

        target_columns = list(records[0].keys())
        quoted_columns = ", ".join(f'"{c}"' for c in target_columns)
        placeholders = ", ".join(f":{c}" for c in target_columns)
        updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in target_columns if c != upsert_key)

        sql = f"""
            INSERT INTO "{self._schema}"."{table_name}" ({quoted_columns})
            VALUES ({placeholders})
            ON CONFLICT ("{upsert_key}") DO UPDATE SET {updates}
        """

        # Process records in micro-batches of 1000
        batch_size = 1000
        total_rows = 0

        cursor = conn.cursor()
        try:
            for i in range(0, len(records), batch_size):
                batch = records[i : i + batch_size]
                processed_batch = []
                for record in batch:
                    processed = {}
                    for key, value in record.items():
                        if isinstance(value, (dict, list)):
                            processed[key] = json.dumps(value)
                        else:
                            processed[key] = value
                    processed_batch.append(processed)

                # Use execute_values for bulk insert
                from psycopg2.extras import execute_values

                execute_values(
                    cursor,
                    sql.replace("VALUES (%s)", "VALUES %(col1)s").replace("%(col1)s", ""),
                    processed_batch,
                    template=f"({', '.join(['%s'] * len(target_columns))})",
                )
                total_rows += cursor.rowcount

            self._commit(conn)
        except Exception:
            self._rollback(conn)
            raise
        finally:
            cursor.close()

        return {"inserted": total_rows, "updated": 0}

    # ── Schema Extension ──────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def add_column(
        self,
        table_name: str,
        column_name: str,
        data_type: str,
    ) -> bool:
        """Add a column to an existing table.

        Parameters
        ----------
        table_name : str
            Destination table name.
        column_name : str
            New column name.
        data_type : str
            PostgreSQL-compatible data type (e.g. "TEXT", "INTEGER", "JSONB").

        Returns
        -------
        bool
            True on success, False if column already exists.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            sql = (
                f'ALTER TABLE "{self._schema}"."{table_name}" '
                f'ADD COLUMN IF NOT EXISTS "{column_name}" {self._pg_ddl_type(data_type)}'
            )
            cursor.execute(sql)
            self._commit(conn)
            return True
        except Psycopg2OperationalError as exc:
            if "already exists" in str(exc).lower():
                return True  # Idempotent
            logger.warning("Failed to add column %s.%s: %s", table_name, column_name, exc)
            return False
        finally:
            cursor.close()

    @retry_on_transient(max_attempts=3, backoff="exponential")
    def ensure_table(self, table_name: str) -> bool:
        """Create the destination table with just an ``id`` primary key if absent.

        Every other column arrives through extend_schema_batch, so a bare
        keyed table is enough to bootstrap a previously empty destination.
        Returns True when a table was created.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'CREATE TABLE IF NOT EXISTS "{self._schema}"."{table_name}" (id TEXT PRIMARY KEY)'
            )
            created = cursor.rowcount != 0 or True
            self._commit(conn)
            logger.info("Ensured destination table %s.%s exists", self._schema, table_name)
            return created
        except Exception:
            self._rollback(conn)
            raise
        finally:
            cursor.close()

    def extend_schema_batch(
        self,
        changes: list[dict[str, str]],
    ) -> dict[str, int]:
        """Apply multiple ALTER TABLE ADD COLUMN statements.

        Parameters
        ----------
        changes : list[dict]
            [{table_name, column_name, data_type}, ...]

        Returns
        -------
        dict
            {"added": int, "failed": int}
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        added = 0
        failed = 0

        try:
            for change in changes:
                table = change["table"]
                col = change["column"]
                dtype = change["data_type"]

                try:
                    sql = (
                        f'ALTER TABLE "{self._schema}"."{table}" '
                        f'ADD COLUMN IF NOT EXISTS "{col}" {self._pg_ddl_type(dtype)}'
                    )
                    cursor.execute(sql)
                    added += 1
                except Psycopg2OperationalError as exc:
                    if "already exists" in str(exc).lower():
                        added += 1
                    else:
                        logger.warning("Failed to add column %s.%s: %s", table, col, exc)
                        failed += 1
            self._commit(conn)
        except Exception:
            self._rollback(conn)
            raise
        finally:
            cursor.close()

        return {"added": added, "failed": failed}

    # ── Helper ────────────────────────────────────────────────────────

    # Foreign/generic type names (ClickHouse, LLM-suggested) → valid Postgres DDL types
    _DDL_TYPE_MAP: ClassVar[dict[str, str]] = {
        "string": "TEXT",
        "str": "TEXT",
        "fixedstring": "TEXT",
        "int8": "SMALLINT",
        "int16": "SMALLINT",
        "int32": "INTEGER",
        "int64": "BIGINT",
        "uint8": "SMALLINT",
        "uint16": "INTEGER",
        "uint32": "BIGINT",
        "uint64": "NUMERIC",
        "int": "INTEGER",
        "float": "DOUBLE PRECISION",
        "float32": "REAL",
        "float64": "DOUBLE PRECISION",
        "bool": "BOOLEAN",
        "datetime": "TIMESTAMP",
        "datetime64": "TIMESTAMP",
        "date32": "DATE",
        "number": "NUMERIC",
        "object": "JSONB",
        "array": "JSONB",
        "map": "JSONB",
    }

    # Postgres types that may legitimately appear in DDL requests (pass through)
    _VALID_PG_TYPES: ClassVar[set[str]] = {
        "text",
        "varchar",
        "character varying",
        "char",
        "uuid",
        "boolean",
        "smallint",
        "integer",
        "bigint",
        "numeric",
        "decimal",
        "real",
        "double precision",
        "date",
        "time",
        "timestamp",
        "timestamptz",
        "timestamp with time zone",
        "timestamp without time zone",
        "json",
        "jsonb",
        "bytea",
        "serial",
        "bigserial",
        "interval",
    }

    @classmethod
    def _pg_ddl_type(cls, data_type: str) -> str:
        """Translate a requested column type into a valid Postgres DDL type.

        Schema-change requests carry type names from the origin (e.g.
        ClickHouse ``String``/``Nullable(Int64)``) or from LLM output;
        anything unrecognized falls back to TEXT rather than producing
        invalid DDL.
        """
        dt = (data_type or "").strip()
        # Unwrap ClickHouse wrappers like Nullable(String) / LowCardinality(String)
        low = dt.lower()
        for wrapper in ("nullable(", "lowcardinality("):
            if low.startswith(wrapper) and low.endswith(")"):
                dt = dt[len(wrapper) : -1].strip()
                low = dt.lower()
        # Strip length/precision qualifiers for lookup, e.g. varchar(255)
        base = low.split("(", 1)[0].strip()
        if base in cls._DDL_TYPE_MAP:
            return cls._DDL_TYPE_MAP[base]
        if base in cls._VALID_PG_TYPES:
            return dt.upper() if "(" not in dt else dt
        return "TEXT"

    # ── Extraction State Tracking (incremental watermark extraction) ──

    def ensure_extraction_state(self) -> None:
        """Create the extraction-state tracking table if it doesn't exist.

        One row per (origin_id, table_name) — O(number of tables), not
        O(number of rows) — since origins are read-only and never carry
        per-row import markers. See src/mcp_server/connectors/watermark.py.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{self._schema}"."merge_engine_extraction_state" (
                    origin_id TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    watermark_column TEXT,
                    watermark_kind TEXT,
                    last_value TEXT,
                    last_run_id TEXT,
                    last_extracted_at TIMESTAMPTZ,
                    rows_extracted_last_run INTEGER,
                    PRIMARY KEY (origin_id, table_name)
                )
            """)
            self._commit(conn)
        finally:
            cursor.close()

    def ensure_entity_map(self) -> None:
        """Create the source-record -> cluster-id map if it doesn't exist.

        Deriving a cluster id from its members is only stable while the
        membership is. Under incremental extraction it isn't: a later run
        sees a subset of the records (only the origins without a usable
        watermark are re-read), so a record that previously clustered with
        others now stands alone, derives a different id, and is inserted as
        a NEW destination row instead of updating the existing one.

        Persisting the assignment fixes that: once a source record has been
        given an entity id, it keeps it.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{self._schema}"."merge_engine_entity_map" (
                    entity_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    first_seen_run TEXT,
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    PRIMARY KEY (entity_type, record_id)
                )
            """)
            self._commit(conn)
        finally:
            cursor.close()

    def get_entity_map(self, entity_type: str) -> dict[str, str]:
        """Previously assigned cluster ids for this entity type."""
        self.ensure_entity_map()
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT record_id, cluster_id
                FROM "{self._schema}"."merge_engine_entity_map"
                WHERE entity_type = %s
            """,
                (entity_type,),
            )
            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            cursor.close()

    def save_entity_map(
        self,
        entity_type: str,
        assignments: dict[str, str],
        run_id: str = "",
    ) -> int:
        """Record which cluster each source record was assigned to."""
        if not assignments:
            return 0
        self.ensure_entity_map()
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            psycopg2.extras.execute_batch(
                cursor,
                f"""
                INSERT INTO "{self._schema}"."merge_engine_entity_map"
                    (entity_type, record_id, cluster_id, first_seen_run, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (entity_type, record_id) DO UPDATE
                    SET cluster_id = EXCLUDED.cluster_id, updated_at = now()
            """,
                [
                    (entity_type, record_id, cluster_id, run_id)
                    for record_id, cluster_id in assignments.items()
                ],
            )
            self._commit(conn)
            return len(assignments)
        finally:
            cursor.close()

    def ensure_merge_audit(self) -> None:
        """Create the merge-lineage audit table if it doesn't exist.

        Issue 7: per-pair match evidence (Phase 7.1) was computed and then
        thrown away the moment clustering finished — a reviewer, or anyone
        auditing a merge after the fact, had no way to see *why* two records
        were judged the same entity. This is the durable half of that fix:
        one row per matched pair, queryable long after the run's own spool
        (and DuckDB working data) is cleaned up.
        """
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{self._schema}"."merge_engine_merge_audit" (
                    run_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    record_a TEXT NOT NULL,
                    record_b TEXT NOT NULL,
                    confidence DOUBLE PRECISION,
                    method TEXT,
                    evidence JSONB,
                    decided_by TEXT,
                    human_override TEXT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    PRIMARY KEY (run_id, entity_type, record_a, record_b)
                )
            """)
            self._commit(conn)
        finally:
            cursor.close()

    def save_merge_audit(
        self,
        entity_type: str,
        run_id: str,
        rows: list[dict[str, Any]],
    ) -> int:
        """Persist per-pair merge evidence rows.

        ``rows``: ``{cluster_id, record_a, record_b, confidence, method,
        evidence, decided_by, human_override}`` — ``record_a``/``record_b``
        are ordered the same way ``resolve_entities.py`` orders every other
        pair key (``a < b``) so a later query for one pair only ever needs
        to check one row, not two.  ``ON CONFLICT DO UPDATE`` so a human
        override recorded after the original auto/LLM decision updates the
        SAME row (visible history of the original decision plus the
        override) rather than duplicating it.
        """
        if not rows:
            return 0
        self.ensure_merge_audit()
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            psycopg2.extras.execute_batch(
                cursor,
                f"""
                INSERT INTO "{self._schema}"."merge_engine_merge_audit"
                    (run_id, entity_type, cluster_id, record_a, record_b,
                     confidence, method, evidence, decided_by, human_override, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (run_id, entity_type, record_a, record_b) DO UPDATE
                    SET confidence = EXCLUDED.confidence,
                        method = EXCLUDED.method,
                        evidence = EXCLUDED.evidence,
                        decided_by = EXCLUDED.decided_by,
                        human_override = COALESCE(EXCLUDED.human_override,
                            "{self._schema}"."merge_engine_merge_audit".human_override)
            """,
                [
                    (
                        run_id,
                        entity_type,
                        row.get("cluster_id", ""),
                        row["record_a"],
                        row["record_b"],
                        row.get("confidence"),
                        row.get("method"),
                        json.dumps(row.get("evidence") or {}, default=str),
                        row.get("decided_by"),
                        row.get("human_override"),
                    )
                    for row in rows
                ],
            )
            self._commit(conn)
            return len(rows)
        finally:
            cursor.close()

    def get_extraction_state(self, origin_id: str, table_name: str) -> dict[str, Any] | None:
        """Fetch the tracked extraction state for one (origin, table), or None."""
        self.ensure_extraction_state()
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT watermark_column, watermark_kind, last_value, last_run_id, rows_extracted_last_run
                FROM "{self._schema}"."merge_engine_extraction_state"
                WHERE origin_id = %s AND table_name = %s
            """,
                (origin_id, table_name),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "watermark_column": row[0],
                "watermark_kind": row[1],
                "last_value": row[2],
                "last_run_id": row[3],
                "rows_extracted_last_run": row[4],
            }
        finally:
            cursor.close()

    def update_extraction_state(
        self,
        origin_id: str,
        table_name: str,
        watermark_column: str | None,
        watermark_kind: str | None,
        last_value: str | None,
        run_id: str,
        rows_extracted: int,
    ) -> None:
        """Upsert the extraction state after a successful load for one table."""
        self.ensure_extraction_state()
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                INSERT INTO "{self._schema}"."merge_engine_extraction_state"
                    (origin_id, table_name, watermark_column, watermark_kind, last_value, last_run_id, last_extracted_at, rows_extracted_last_run)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (origin_id, table_name) DO UPDATE SET
                    watermark_column = EXCLUDED.watermark_column,
                    watermark_kind = EXCLUDED.watermark_kind,
                    last_value = EXCLUDED.last_value,
                    last_run_id = EXCLUDED.last_run_id,
                    last_extracted_at = EXCLUDED.last_extracted_at,
                    rows_extracted_last_run = EXCLUDED.rows_extracted_last_run
            """,
                (
                    origin_id,
                    table_name,
                    watermark_column,
                    watermark_kind,
                    last_value,
                    run_id,
                    rows_extracted,
                ),
            )
            self._commit(conn)
        finally:
            cursor.close()

    def clear_extraction_state(
        self, origin_id: str | None = None, table_name: str | None = None
    ) -> int:
        """Forget tracked watermarks so the next run re-extracts in full.

        A full re-merge is the only honest answer to "I deleted rows from the
        destination and want them back": the watermark lives here, not in the
        entity tables, so deleting a row from `companies` leaves the mark that
        says "already extracted up to X" untouched and the re-run reads nothing.

        With no arguments, clears every table's mark; narrow with origin_id
        and/or table_name. Returns how many rows were removed.
        """
        self.ensure_extraction_state()
        conn = self._ensure_connection()
        cursor = conn.cursor()
        try:
            clauses: list[str] = []
            params: list[Any] = []
            if origin_id is not None:
                clauses.append("origin_id = %s")
                params.append(origin_id)
            if table_name is not None:
                clauses.append("table_name = %s")
                params.append(table_name)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            cursor.execute(
                f'DELETE FROM "{self._schema}"."merge_engine_extraction_state"{where}',
                params,
            )
            removed = cursor.rowcount or 0
            self._commit(conn)
            logger.info(
                "Cleared %d extraction-state row(s) for a full re-merge (origin=%s, table=%s)",
                removed,
                origin_id,
                table_name,
            )
            return removed
        finally:
            cursor.close()

    @staticmethod
    def _normalize_type(data_type: str) -> str:
        """Normalize PostgreSQL data types to canonical form."""
        mapping = {
            "character varying": "VARCHAR",
            "uuid": "UUID",
            "timestamp without time zone": "TIMESTAMP",
            "timestamp with time zone": "TIMESTAMPTZ",
            "boolean": "BOOLEAN",
            "integer": "INTEGER",
            "bigint": "BIGINT",
            "smallint": "SMALLINT",
            "numeric": "NUMERIC",
            "real": "REAL",
            "double precision": "DOUBLE PRECISION",
            "text": "TEXT",
            "json": "JSON",
            "jsonb": "JSONB",
            "bytea": "BYTEA",
            "date": "DATE",
            "time without time zone": "TIME",
        }
        return mapping.get(data_type.lower(), data_type.upper())
