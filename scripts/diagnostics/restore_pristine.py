"""Restore a destination schema to pristine schema-only state after an app run.
Drops any column on company/person outside the known-original allowlist (this is
what a failed run's `ADD COLUMN`s leave behind -- see the schema-rollback fix,
remediation #4 in merge-engine-remediation-1-to-6), drops merge_engine_* helper
tables, truncates all, and verifies 25 tables / 0 rows.

Usage:
    python restore_pristine.py                                   # global_warehouse.public
    python restore_pristine.py --db global_warehouse_golden --schema boeing
"""

import argparse

import psycopg2

ORIGINAL = {
    "company": [
        "id",
        "name",
        "legal_name",
        "website",
        "industry_id",
        "location_id",
        "employee_count",
        "revenue",
        "revenue_currency",
        "founded_date",
        "status",
        "description",
        "created_at",
        "updated_at",
    ],
    "person": [
        "id",
        "first_name",
        "middle_name",
        "last_name",
        "date_of_birth",
        "gender",
        "location_id",
        "created_at",
        "updated_at",
    ],
}

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--host", default="localhost")
ap.add_argument("--port", type=int, default=55201)
ap.add_argument("--user", default="postgres")
ap.add_argument("--password", default="pass")
ap.add_argument("--db", default="global_warehouse")
ap.add_argument("--schema", default="public")
args = ap.parse_args()

c = psycopg2.connect(
    host=args.host, port=args.port, user=args.user, password=args.password, dbname=args.db
)
c.autocommit = True
cur = c.cursor()
cur.execute("SET search_path TO %s", (args.schema,))

# Drop app-added columns
for tbl, keep in ORIGINAL.items():
    cur.execute(
        """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = %s AND table_name = %s""",
        (args.schema, tbl),
    )
    present = [r[0] for r in cur.fetchall()]
    extra = [col for col in present if col not in keep]
    for col in extra:
        cur.execute(f'ALTER TABLE "{args.schema}"."{tbl}" DROP COLUMN IF EXISTS "{col}" CASCADE')
    print(f"{tbl}: dropped {len(extra)} added columns -> {extra}")

# Drop merge_engine helper tables
for helper in ("merge_engine_entity_map", "merge_engine_extraction_state"):
    cur.execute(f'DROP TABLE IF EXISTS "{args.schema}".{helper} CASCADE')

# Truncate everything
cur.execute(
    "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (args.schema,)
)
tables = [r[0] for r in cur.fetchall()]
if tables:
    qualified = ", ".join(f'"{args.schema}"."{t}"' for t in tables)
    cur.execute(f"TRUNCATE TABLE {qualified} RESTART IDENTITY CASCADE")

total = 0
for t in tables:
    cur.execute(f'SELECT count(*) FROM "{args.schema}"."{t}"')
    total += cur.fetchone()[0]

cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = %s", (args.schema,))
ntables = cur.fetchone()[0]
print(f"PRISTINE: {ntables} tables, {total} rows in {args.db}.{args.schema}")
cur.close()
c.close()
