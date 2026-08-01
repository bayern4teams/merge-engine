"""Reset a destination schema to a true blank slate: truncate all data tables + any
merge-engine watermark/entity-map helper tables. Verifies 0 rows afterward.

Run before every app diagnostic run against `global_warehouse` (see
docs/*_APP_VS_MANUAL_DIAGNOSIS.md and merge-engine-remediation-1-to-6 in project
memory). Defaults match the real destination; pass --schema to target a different
schema/db (e.g. re-running a golden merge).

Usage:
    python reset_destination.py                                   # global_warehouse.public
    python reset_destination.py --db global_warehouse_golden --schema boeing
"""
import argparse

import psycopg2

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--host", default="localhost")
ap.add_argument("--port", type=int, default=55201)
ap.add_argument("--user", default="postgres")
ap.add_argument("--password", default="pass")
ap.add_argument("--db", default="global_warehouse")
ap.add_argument("--schema", default="public")
args = ap.parse_args()

conn = psycopg2.connect(host=args.host, port=args.port, user=args.user,
                         password=args.password, dbname=args.db)
conn.autocommit = True
cur = conn.cursor()
cur.execute('SET search_path TO %s', (args.schema,))

# All base tables in this schema
cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (args.schema,))
tables = [r[0] for r in cur.fetchall()]
print(f"Found {len(tables)} tables in schema '{args.schema}': {tables}")

if tables:
    # Truncate everything in one shot (CASCADE handles FKs, RESTART IDENTITY resets PKs)
    qualified = ", ".join(f'"{args.schema}"."{t}"' for t in tables)
    cur.execute(f"TRUNCATE TABLE {qualified} RESTART IDENTITY CASCADE")
    print("TRUNCATE ... RESTART IDENTITY CASCADE done.")

# Verify 0 rows everywhere
total = 0
nonzero = []
for t in tables:
    cur.execute(f'SELECT count(*) FROM "{args.schema}"."{t}"')
    c = cur.fetchone()[0]
    total += c
    if c:
        nonzero.append((t, c))
print(f"Total rows across all {len(tables)} tables after reset: {total}")
if nonzero:
    print("!!! NON-ZERO TABLES:", nonzero)
else:
    print(f"OK - {args.db}.{args.schema} is completely empty (schema-only).")

cur.close()
conn.close()
