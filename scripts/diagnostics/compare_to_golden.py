"""
Row-level diff of an app run's output (`global_warehouse`.public) against a
hand-merged golden baseline (`global_warehouse_golden`.<subject>). This is the
verification instrument for remediations 1-6 (see merge-engine-remediation-1-to-6 in
project memory, and Phase 7 of the plan) -- count-matching alone was explicitly
rejected as insufficient evidence.

Row identity can't rely on surrogate ids (they're independently assigned by each
run), so every table is compared on a NATURAL KEY built from its own content, with
FK columns resolved to the natural key of the row they point to (e.g. a `finance`
row's `company_id` is resolved to that company's normalized name before comparing,
so a `finance` row in the app DB can be matched against a `finance` row in the
golden DB even though their `company_id` integers are unrelated).

Usage:
    python compare_to_golden.py boeing     # compares global_warehouse.public
    python compare_to_golden.py airbus     #   against global_warehouse_golden.airbus
    python compare_to_golden.py lockheed
"""
import argparse
import json
import pathlib
import re
import sys

import psycopg2
import psycopg2.extras

TABLES = [
    "industry", "location", "company", "company_relation", "person",
    "experience", "finance", "technologic_category", "technologic",
    "contact_phone", "contact_email",
]

GOLDEN_COMPANY_NAME = {"boeing": "Boeing", "airbus": "Airbus", "lockheed": "Lockheed Martin"}


def norm(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def fetch_all(conn, schema, table):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f'SELECT * FROM "{schema}"."{table}"')
        return [dict(r) for r in cur.fetchall()]


class SideData:
    """One DB side (either the app's public schema or the golden's subject schema)."""

    def __init__(self, conn, schema):
        self.schema = schema
        self.rows = {t: fetch_all(conn, schema, t) for t in TABLES}
        self.company_name_by_id = {r["id"]: norm(r.get("name")) for r in self.rows["company"]}
        self.person_key_by_id = {
            r["id"]: (norm(r.get("first_name")), norm(r.get("last_name")))
            for r in self.rows["person"]
        }
        self.location_key_by_id = {
            r["id"]: (norm(r.get("city_name")), norm(r.get("country_name")), norm(r.get("address_line1")))
            for r in self.rows["location"]
        }
        self.industry_name_by_id = {r["id"]: norm(r.get("name")) for r in self.rows["industry"]}
        self.tech_cat_name_by_id = {r["id"]: norm(r.get("name")) for r in self.rows["technologic_category"]}

    def company_name(self, cid):
        return self.company_name_by_id.get(cid, f"<missing:{cid}>")

    def person_key(self, pid):
        return self.person_key_by_id.get(pid, (f"<missing:{pid}>", ""))


def natural_key(table, row, side: SideData):
    """Build a natural key for `row` in `table`, resolving FKs via `side`'s lookups."""
    if table == "company":
        return ("name", norm(row.get("name")))
    if table == "person":
        return ("name", norm(row.get("first_name")), norm(row.get("last_name")))
    if table == "industry":
        return ("name", norm(row.get("name")))
    if table == "technologic_category":
        return ("name", norm(row.get("name")))
    if table == "location":
        return ("loc", norm(row.get("city_name")), norm(row.get("country_name")), norm(row.get("address_line1")))
    if table == "finance":
        return ("fin", side.company_name(row.get("company_id")), str(row.get("period_start")), str(row.get("period_end")))
    if table == "technologic":
        return ("tech", side.company_name(row.get("vendor_company_id")), norm(row.get("name")))
    if table == "experience":
        return ("exp", side.person_key(row.get("person_id")), side.company_name(row.get("company_id")), norm(row.get("job_title")))
    if table == "contact_email":
        return ("email", side.company_name(row.get("company_id")) if row.get("company_id") else side.person_key(row.get("person_id")), norm(row.get("email")))
    if table == "contact_phone":
        return ("phone", side.company_name(row.get("company_id")) if row.get("company_id") else side.person_key(row.get("person_id")), norm(row.get("phone")))
    if table == "company_relation":
        return ("rel", side.company_name(row.get("company_a_id")), side.company_name(row.get("company_b_id")), norm(row.get("relation_type")))
    return ("raw", json.dumps(row, default=str, sort_keys=True))


def compare_table(table, golden: SideData, app: SideData):
    g_keys = {natural_key(table, r, golden): r for r in golden.rows[table]}
    a_keys = {natural_key(table, r, app): r for r in app.rows[table]}
    matched = set(g_keys) & set(a_keys)
    missing = set(g_keys) - set(a_keys)
    extra = set(a_keys) - set(g_keys)
    golden_n, app_n = len(golden.rows[table]), len(app.rows[table])
    parity_pct = round(100.0 * len(matched) / golden_n, 1) if golden_n else (100.0 if app_n == 0 else 0.0)
    return {
        "table": table,
        "golden_rows": golden_n,
        "app_rows": app_n,
        "matched": len(matched),
        "missing_from_app": len(missing),
        "extra_in_app": len(extra),
        "parity_pct": parity_pct,
        "sample_missing": [g_keys[k] for k in list(missing)[:5]],
        "sample_extra": [a_keys[k] for k in list(extra)[:5]],
    }


def golden_record_diff(subject, golden: SideData, app: SideData):
    name = GOLDEN_COMPANY_NAME[subject]
    fields = ["name", "website", "founded_date", "status", "revenue", "employee_count", "revenue_currency", "legal_name"]

    def find(side):
        for r in side.rows["company"]:
            if norm(r.get("name")) == norm(name):
                return r
        return None

    g = find(golden)
    a = find(app)
    diff = {}
    for f in fields:
        gv = g.get(f) if g else None
        av = a.get(f) if a else None
        diff[f] = {"golden": str(gv), "app": str(av), "match": str(gv) == str(av)}
    return {"golden_found": g is not None, "app_found": a is not None, "fields": diff}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("subject", choices=["boeing", "airbus", "lockheed"])
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=55201)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--password", default="pass")
    ap.add_argument("--app-db", default="global_warehouse")
    ap.add_argument("--app-schema", default="public")
    ap.add_argument("--golden-db", default="global_warehouse_golden")
    args = ap.parse_args()

    golden_conn = psycopg2.connect(host=args.host, port=args.port, user=args.user,
                                    password=args.password, dbname=args.golden_db)
    app_conn = psycopg2.connect(host=args.host, port=args.port, user=args.user,
                                 password=args.password, dbname=args.app_db)

    golden = SideData(golden_conn, args.subject)
    app = SideData(app_conn, args.app_schema)

    table_diffs = [compare_table(t, golden, app) for t in TABLES]
    total_golden = sum(d["golden_rows"] for d in table_diffs)
    total_app = sum(d["app_rows"] for d in table_diffs)
    total_matched = sum(d["matched"] for d in table_diffs)
    overall_parity = round(100.0 * total_matched / total_golden, 1) if total_golden else 0.0

    families_golden = {t for t, d in zip(TABLES, table_diffs) if d["golden_rows"] > 0}
    families_app = {t for t, d in zip(TABLES, table_diffs) if d["app_rows"] > 0}

    result = {
        "subject": args.subject,
        "totals": {
            "golden_rows": total_golden,
            "app_rows": total_app,
            "matched_rows": total_matched,
            "overall_parity_pct": overall_parity,
        },
        "families": {
            "golden_family_count": len(families_golden),
            "app_family_count": len(families_app),
            "golden_families": sorted(families_golden),
            "app_families": sorted(families_app),
            "missing_families": sorted(families_golden - families_app),
        },
        "tables": table_diffs,
        "golden_record": golden_record_diff(args.subject, golden, app),
    }

    print(json.dumps(result, indent=2, default=str))

    out_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "docs" / "verification"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.subject}_diff.json"
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nWritten to {out_path}")

    golden_conn.close()
    app_conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
