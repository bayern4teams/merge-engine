"""
Create the golden-baseline database used to verify remediations 1-6 against the
hand-merged references (see docs/diagnostics/: BOEING/AIRBUS/LOCKHEED_APP_VS_MANUAL_DIAGNOSIS.md,
and the plan at merge-engine-remediation-1-to-6 in project memory).

This provisions ONE new PostgreSQL database, `global_warehouse_golden`, with THREE
schemas (`boeing`, `airbus`, `lockheed`) — each an exact clone of the 25-table Global
Warehouse DDL in sample_databases/destination/sql/*.sql. It is never configured as an
app destination, so the standing "exactly one destination at a time" rule on
`global_warehouse` itself is untouched.

Live-caught (BP-B, 2026-08-19, while populating the golden DB for P-9's regression
gate): the DDL declares every `id` column as `TEXT PRIMARY KEY` with NO database-side
default -- confirmed the same is true of the real `global_warehouse` destination, so
this isn't a stale-DDL mismatch. `boeing_manual_merge.py`/`airbus_manual_merge.py`
(and, presumably, the real app's own load path) rely on `INSERT ... RETURNING id`
without ever supplying an id, which fails outright against a freshly-provisioned
golden DB (`NotNullViolation` on the very first row of the very first table). Fixed
here, not in the manual-merge scripts, since the golden DB is the one thing this
script fully controls and re-provisions from scratch every run: after building each
schema, every `id` column gets `DEFAULT gen_random_uuid()::text` (PostgreSQL 13+
built-in, no extension needed) so the golden generators can actually complete.

Usage:
    python setup_golden_db.py [--host localhost] [--port 55201] [--user postgres]
                               [--password pass] [--admin-db postgres]
                               [--golden-db global_warehouse_golden]
"""

import argparse
import pathlib
import sys

import psycopg2

SCHEMAS = ("boeing", "airbus", "lockheed")
DDL_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "sample_databases"
    / "destination"
    / "sql"
)
DDL_FILES = sorted(DDL_DIR.glob("*.sql"))  # 01_company.sql ... 07_technographic.sql


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=55201)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--password", default="pass")
    ap.add_argument("--admin-db", default="postgres")
    ap.add_argument("--golden-db", default="global_warehouse_golden")
    args = ap.parse_args()

    if not DDL_FILES:
        log(f"!! No DDL files found under {DDL_DIR}")
        return 1
    log(f"Using {len(DDL_FILES)} DDL files: {[f.name for f in DDL_FILES]}")

    # 1. Create the golden database if it doesn't exist yet.
    admin_conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        dbname=args.admin_db,
    )
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.golden_db,))
        exists = cur.fetchone() is not None
        if exists:
            log(f"Database '{args.golden_db}' already exists — reusing it.")
        else:
            cur.execute(f'CREATE DATABASE "{args.golden_db}"')
            log(f"Created database '{args.golden_db}'.")
    admin_conn.close()

    # 2. Build each schema from the same DDL used for global_warehouse's `public`.
    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        dbname=args.golden_db,
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        for schema in SCHEMAS:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            for ddl_file in DDL_FILES:
                sql = ddl_file.read_text(encoding="utf-8")
                cur.execute(sql)
            cur.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s", (schema,)
            )
            n_tables = cur.fetchone()[0]
            log(f"  schema '{schema}': {n_tables} tables created")
            if n_tables != 25:
                log(f"  !! expected 25 tables in '{schema}', got {n_tables}")

            cur.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = %s AND column_name = 'id'",
                (schema,),
            )
            id_tables = [row[0] for row in cur.fetchall()]
            for table in id_tables:
                cur.execute(
                    f'ALTER TABLE "{schema}"."{table}" '
                    "ALTER COLUMN id SET DEFAULT gen_random_uuid()::text"
                )
            log(f"  schema '{schema}': id default added to {len(id_tables)} tables")
    conn.close()

    log("\nDone. Golden DB ready:")
    log(f"  host={args.host} port={args.port} dbname={args.golden_db}")
    log(f"  schemas: {', '.join(SCHEMAS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
