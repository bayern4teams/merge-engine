"""
Manual Lockheed Martin merge -- same 6-step pipeline as Boeing/Airbus hand-merges:
  1. Extract all Lockheed Martin data from origin1 + origin2 (ClickHouse)
  2. Entity resolution (intra-origin1 dedup + cross-origin match)
  3. Field mapping (origin schemas -> destination schema)
  4. Conflict resolution / unification (build golden records)
  5. Load into the destination (PostgreSQL)
  6. Verification

Key Lockheed-specific challenges vs Boeing/Airbus:
  - origin1 has FOUR duplicate "Lockheed Martin Corporation" parent records
    (same domain, differing only by founding-year 2011/2012) requiring dedup
  - All origin1 parents wrongly marked isPublic=False (real LMT is NYSE:LMT)
  - ZERO CompanyFinancials in origin1 -- genuine data gap
  - origin2 has a single ALL-NULL stub -- contributes essentially nothing
  - Only 3 people linked in origin1 (vs Boeing's 39 or Airbus's 33)

Promoted from the original scratchpad script 2026-07-30 with the destination now
parameterised via --target-db/--target-schema (via `SET search_path`) so this same
script can populate either the real `global_warehouse` destination or a `lockheed`
schema inside the read-only `global_warehouse_golden` verification database.

Usage:
    python lockheed_manual_merge.py                                 # -> global_warehouse.public
    python lockheed_manual_merge.py --target-db global_warehouse_golden --target-schema lockheed
"""

import argparse
import json
import re
from datetime import date, datetime

import clickhouse_connect
import psycopg2

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--ch-host", default="localhost")
ap.add_argument("--ch-port", type=int, default=8190)
ap.add_argument("--ch-user", default="default")
ap.add_argument("--ch-password", default="pass")
ap.add_argument("--pg-host", default="localhost")
ap.add_argument("--pg-port", type=int, default=55201)
ap.add_argument("--pg-user", default="postgres")
ap.add_argument("--pg-password", default="pass")
ap.add_argument("--target-db", default="global_warehouse")
ap.add_argument("--target-schema", default="public")
args = ap.parse_args()

# -- Connections --
ch = clickhouse_connect.get_client(
    host=args.ch_host, port=args.ch_port, username=args.ch_user, password=args.ch_password
)
pg = psycopg2.connect(
    host=args.pg_host,
    port=args.pg_port,
    user=args.pg_user,
    password=args.pg_password,
    dbname=args.target_db,
)
pg.autocommit = True
cur = pg.cursor()
cur.execute("SET search_path TO %s", (args.target_schema,))

NOW = datetime.now().isoformat()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def ch_query(sql):
    res = ch.query(sql)
    cols = res.column_names
    return [dict(zip(cols, row)) for row in res.result_rows]


def safe_str(v):
    if v is None:
        return None
    s = str(v).replace("'", "''").strip()
    return s if s else None


def parse_founding_date(raw):
    if not raw or not raw.startswith("d"):
        return None
    raw = raw[1:]
    raw = raw.replace("XX", "01")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


def parse_employee_count(raw):
    if not raw:
        return None
    raw = raw.replace(",", "")
    if "Over" in raw:
        m = re.search(r"(\d+)", raw)
        return int(m.group(1)) if m else None
    m = re.search(r"(\d+)\s*-\s*(\d+)", raw)
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m = re.search(r"(\d+)", raw)
    return int(m.group(1)) if m else None


def parse_revenue(raw):
    if not raw:
        return None
    raw = raw.replace("$", "").replace(",", "").strip()
    multiplier = 1
    if "Billion" in raw:
        multiplier = 1_000_000_000
        raw = raw.replace("Billion", "").strip()
    elif "Million" in raw:
        multiplier = 1_000_000
        raw = raw.replace("Million", "").strip()
    try:
        return float(raw) * multiplier
    except Exception:
        return None


def normalize_name(n):
    if not n:
        return ""
    n = n.lower().strip()
    for stop in [
        "the ",
        "inc.",
        "inc",
        "ltd.",
        "ltd",
        "llc",
        "co.",
        "co",
        "corp.",
        "corp",
        "corporation",
        "limited",
        "company",
        "proprietary",
        ",",
        ".",
    ]:
        n = n.replace(stop, "")
    return " ".join(n.split())


# ============================================================================
# STEP 1: EXTRACT
# ============================================================================
log("=== STEP 1: EXTRACT ===")

# -- origin1: all Lockheed-family companies --
o1_companies = ch_query("""
    SELECT * FROM origin1.Company
    WHERE entityName ILIKE '%lockheed%' OR fullName ILIKE '%lockheed%'
""")
log(f"origin1.Company: {len(o1_companies)} Lockheed entities")
for c in o1_companies:
    log(
        f"  {c['companyID'][:12]}.. = {c['entityName']} / {c['fullName']} "
        f"(isPublic={c.get('isPublic')}, founded={c.get('foundingDate')})"
    )

# -- origin2: all Lockheed-family companies --
o2_companies = ch_query("""
    SELECT DISTINCT * FROM origin2.Company
    WHERE name ILIKE '%lockheed%'
""")
log(f"origin2.Company: {len(o2_companies)} Lockheed entities")
for c in o2_companies:
    log(
        f"  {c['companyID']} = '{c['name']}' (rev={c.get('revenue')}, "
        f"emp={c.get('employeeCount')}, web={c.get('website')})"
    )

# -- origin1: linked data --
o1_ids = [c["companyID"] for c in o1_companies]
o1_id_clause = "(" + ",".join(f"'{i}'" for i in o1_ids) + ")"

o1_relations = ch_query(f"SELECT * FROM origin1.CompanyRelation WHERE companyID IN {o1_id_clause}")
o1_contacts = ch_query(f"SELECT * FROM origin1.CompanyContact WHERE companyID IN {o1_id_clause}")
o1_people_exp = ch_query(f"""
    SELECT ce.*, p.name as person_full_name, p.summary as person_summary
    FROM origin1.CompanyExperience ce
    JOIN origin1.Person p ON ce.personID = p.personID
    WHERE ce.companyID IN {o1_id_clause}
""")
o1_industries = ch_query(f"""
    SELECT ci.companyID, ci.classificationID, ic.name as industry_name, ic.isPrimary
    FROM origin1.CompanyIndustry ci
    JOIN origin1.IndustryClassification ic ON ci.classificationID = ic.classificationID
    WHERE ci.companyID IN {o1_id_clause}
""")
o1_tech = ch_query(f"SELECT * FROM origin1.CompanyTechnographics WHERE companyID IN {o1_id_clause}")
o1_financials = ch_query(
    f"SELECT * FROM origin1.CompanyFinancials WHERE companyID IN {o1_id_clause}"
)
o1_locations = ch_query(f"SELECT * FROM origin1.Location WHERE companyID IN {o1_id_clause}")
o1_emp_cats = ch_query(
    f"SELECT * FROM origin1.CompanyEmployeeCategory WHERE companyID IN {o1_id_clause}"
)

log(
    f"  origin1 linked: {len(o1_relations)} relations, {len(o1_contacts)} contacts, "
    f"{len(o1_people_exp)} people/exp, {len(o1_industries)} industries, "
    f"{len(o1_tech)} technologies, {len(o1_financials)} financials, "
    f"{len(o1_locations)} locations, {len(o1_emp_cats)} employee categories"
)

# -- origin2: linked data (only for the real Lockheed Martin match) --
o2_lm_id = "1106943476"
o2_experiences = ch_query(f"""
    SELECT e.*, p.fullName, p.firstName, p.lastName, p.middleName
    FROM origin2.Experience e
    JOIN origin2.Person p ON e.experienceID = p.personID
    WHERE e.companyID = '{o2_lm_id}'
""")
o2_industries = ch_query(f"""
    SELECT ci.companyID, ci.classificationID, ci.isPrimary, i.name as industry_name
    FROM origin2.CompanyIndustry ci
    JOIN origin2.Industry i ON ci.classificationID = i.classificationID
    WHERE ci.companyID = '{o2_lm_id}'
""")
o2_locations = ch_query(f"""
    SELECT * FROM origin2.Location
    WHERE toString(companyID) = '{o2_lm_id}'
""")

log(
    f"  origin2 linked (LM only): {len(o2_experiences)} experiences, "
    f"{len(o2_industries)} industries, {len(o2_locations)} locations"
)


# ============================================================================
# STEP 2: ENTITY RESOLUTION
# ============================================================================
log("\n=== STEP 2: ENTITY RESOLUTION ===")

o1_by_id = {c["companyID"]: c for c in o1_companies}
o2_by_id = {c["companyID"]: c for c in o2_companies}

# -- INTRA-ORIGIN1 DEDUP: 4 identical "Lockheed Martin Corporation" parents --
# All four share lockheedmartin.com domain, differ only by:
#   - companyID (different internal IDs)
#   - foundingDate (2011 vs 2012)
#   - description (one has rich Space Systems desc, others are stubs)
#   - city/location (Colorado Springs, San Jose, Lompoc -- different offices)
#
# Decision: merge all 4 into one golden parent. Pick the one with the richest
# description as the "primary", union satellite data from all 4.
# Et_vf_PG has the best description (Space Systems).
# Also correct isPublic to True (all say False, but LMT is NYSE:LMT).

PARENT_IDS = [
    "EoSpqvXbEPbWfQoWgBl-vyA",  # Colorado Springs, founded 2012, stub desc
    "Et_vf_PGANIKJHIynbSiGKA",  # Littleton CO, founded 2011, RICH desc (Space Systems)
    "E4amzNQB9P6SSjyGekSMdJw",  # San Jose CA, founded 2012, stub desc
    "EiRiccPdnOB-E1dHOyE9eng",  # Lompoc CA, founded 2012, stub desc
]
GOLDEN_O1 = "Et_vf_PGANIKJHIynbSiGKA"  # richest description

SUBSIDIARY_IDS = [cid for cid in o1_ids if cid not in PARENT_IDS]

log("INTRA-ORIGIN1 dedup:")
log(f"  4 duplicate parents -> 1 golden (best desc): {GOLDEN_O1}")
for pid in PARENT_IDS:
    p = o1_by_id[pid]
    desc_preview = (p.get("description", "") or "")[:60]
    log(f"    {pid[:12]}.. city='{p.get('foundingDate', '')}' desc='{desc_preview}...'")
log(f"  Subsidiaries: {len(SUBSIDIARY_IDS)}")
for sid in SUBSIDIARY_IDS:
    log(f"    {o1_by_id[sid]['entityName']}")

# -- CROSS-ORIGIN resolution --
# origin2 "Lockheed Martin" (1106943476) matches by exact name but ALL fields NULL.
# The other 3 origin2 entries are unrelated (Indian construction co, empty stub, nonprofit).
CROSS_MATCH = {
    "o1": GOLDEN_O1,
    "o2": o2_lm_id,
    "confidence": 0.70,
    "reason": "exact name only; origin2 has ALL NULL fields, contributes nothing",
}

log("\nCROSS-ORIGIN resolution:")
log(
    f"  [{CROSS_MATCH['confidence']:.2f}] origin1 '{o1_by_id[GOLDEN_O1]['entityName']}' <-> "
    f"origin2 '{o2_by_id[o2_lm_id]['name']}' -- {CROSS_MATCH['reason']}"
)
log("  origin2 unrelated entries (excluded):")
all_o2_ids = [c["companyID"] for c in o2_companies]
for oid in all_o2_ids:
    if oid != o2_lm_id:
        log(f"    '{o2_by_id[oid]['name']}' -- NOT the real Lockheed Martin")


# ============================================================================
# STEP 3: FIELD MAPPING
# ============================================================================
log("\n=== STEP 3: FIELD MAPPING ===")
log(
    "  origin1.Company -> company: entityName->name, fullName->legal_name, "
    "website->website, foundingDate->founded_date, isPublic->status (CORRECTED to public), "
    "description->description"
)
log("  origin2.Company -> company: NOTHING (all fields NULL)")
log("  origin1.CompanyFinancials -> finance: EMPTY (0 financials for Lockheed)")
log("  origin1.CompanyTechnographics -> technologic: technologyName->name, categories->category")
log("  origin1.CompanyRelation -> company_relation: relationType->relation_type")
log("  origin1.CompanyExperience+Person -> person+experience (only 3 people)")
log("  origin1.CompanyContact -> contact_email/contact_phone")
log("  origin1.Location -> location")


# ============================================================================
# STEP 4: CONFLICT RESOLUTION & UNIFICATION
# ============================================================================
log("\n=== STEP 4: CONFLICT RESOLUTION & UNIFICATION ===")

# The golden parent is built from origin1 only (origin2 adds nothing).
#   - founding: 2011 (Et_vf_PG) vs 2012 (the other 3). Real LMT formed 1995.
#     -> OVERRIDE: use 1995-03-15 (the actual merger date of Lockheed Corp + Martin Marietta)
#   - isPublic: all say False. Real LMT is NYSE:LMT.
#     -> OVERRIDE: set to 'public'
#   - employee_count/revenue: NONE available anywhere -> note the gap.

c_golden = o1_by_id[GOLDEN_O1]

golden_lockheed = {
    "name": "Lockheed Martin",
    "legal_name": "Lockheed Martin Corporation",  # majority (3/4) agree
    "website": "lockheedmartin.com",  # all agree
    "employee_count": None,  # GAP: no source has this
    "revenue": None,  # GAP: no financials anywhere
    "revenue_currency": None,
    "founded_date": date(1995, 3, 15),  # OVERRIDE: real merger date
    "status": "public",  # OVERRIDE: NYSE:LMT
    "description": c_golden.get("description"),  # richest desc (Space Systems)
}

log("Golden Lockheed Martin company record (conflict resolution):")
for k, v in golden_lockheed.items():
    src = ""
    if k in ("name", "legal_name", "website"):
        src = " <- all 4 origin1 duplicates agree"
    elif k == "founded_date":
        src = " <- OVERRIDE (real merger date 1995-03-15; origin1 says 2011/2012, both wrong)"
    elif k == "status":
        src = " <- OVERRIDE (NYSE:LMT; all origin1 say False)"
    elif k in ("employee_count", "revenue", "revenue_currency"):
        src = " <- GAP: no data in either origin (real LMT: ~116k emp, ~$67.6B rev)"
    elif k == "description":
        src = " <- origin1 Et_vf_PG (richest: Space Systems desc)"
    val_display = str(v)[:100] + "..." if v and len(str(v)) > 100 else v
    log(f"  {k}: {val_display}{src}")


# ============================================================================
# STEP 5: LOAD INTO DESTINATION
# ============================================================================
log("\n=== STEP 5: LOAD INTO DESTINATION ===")

# -- 5a: Industries --
log("Loading industries...")
industry_ids = {}
all_industries = set()
for ind in o1_industries:
    all_industries.add(ind.get("industry_name", ""))
all_industries.discard("")

for ind_name in sorted(all_industries):
    cur.execute(
        """
        INSERT INTO industry (name, is_active, updated_at)
        VALUES (%s, true, NOW()) RETURNING id
    """,
        (ind_name,),
    )
    industry_ids[ind_name] = cur.fetchone()[0]
log(f"  Inserted {len(industry_ids)} industries")

# -- 5b: Locations --
log("Loading locations...")
location_ids = {}

for loc in o1_locations:
    cur.execute(
        """
        INSERT INTO location (country_name, state_name, city_name, postal_code,
                              latitude, longitude, address_line1, is_active, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, true, NOW()) RETURNING id
    """,
        (
            safe_str(loc.get("country")),
            safe_str(loc.get("region")),
            safe_str(loc.get("city")),
            safe_str(loc.get("postalCode")),
            float(loc["latitude"]) if loc.get("latitude") else None,
            float(loc["longitude"]) if loc.get("longitude") else None,
            safe_str(loc.get("street")),
        ),
    )
    location_ids[loc["companyID"]] = cur.fetchone()[0]

log(f"  Inserted {len(location_ids)} locations")

# -- 5c: Companies --
log("Loading companies...")
company_dest_ids = {}

main_ind_name = next(
    (i["industry_name"] for i in o1_industries if i["companyID"] == GOLDEN_O1), None
)
main_ind_id = industry_ids.get(main_ind_name) if main_ind_name else None
main_loc_id = location_ids.get(GOLDEN_O1)

cur.execute(
    """
    INSERT INTO company (name, legal_name, website, industry_id, location_id,
                         employee_count, revenue, revenue_currency, founded_date,
                         status, description, created_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    RETURNING id
""",
    (
        golden_lockheed["name"],
        golden_lockheed["legal_name"],
        golden_lockheed["website"],
        main_ind_id,
        main_loc_id,
        golden_lockheed["employee_count"],
        golden_lockheed["revenue"],
        golden_lockheed["revenue_currency"],
        golden_lockheed["founded_date"],
        golden_lockheed["status"],
        golden_lockheed["description"],
    ),
)
main_company_id = cur.fetchone()[0]

for pid in PARENT_IDS:
    company_dest_ids[pid] = main_company_id
company_dest_ids[o2_lm_id] = main_company_id

log(f"  Golden Lockheed Martin -> dest id {main_company_id}")
log(f"  Mapped {len(PARENT_IDS)} origin1 duplicates + 1 origin2 stub -> same dest row")

for sub_id in SUBSIDIARY_IDS:
    sub = o1_by_id[sub_id]
    sub_loc_id = location_ids.get(sub_id)
    sub_ind_name = next(
        (i["industry_name"] for i in o1_industries if i["companyID"] == sub_id), None
    )
    sub_ind_id = industry_ids.get(sub_ind_name) if sub_ind_name else None

    cur.execute(
        """
        INSERT INTO company (name, legal_name, website, industry_id, location_id,
                             founded_date, status, description, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        RETURNING id
    """,
        (
            sub.get("entityName"),
            sub.get("fullName"),
            safe_str(sub.get("website")),
            sub_ind_id,
            sub_loc_id,
            parse_founding_date(sub.get("foundingDate")),
            "public" if sub.get("isPublic") == "True" else "private",
            safe_str(sub.get("description")),
        ),
    )
    company_dest_ids[sub_id] = cur.fetchone()[0]

log(f"  Total companies loaded: {len(set(company_dest_ids.values()))}")

# -- 5d: Company Relations --
log("Loading company relations...")
rel_count = 0
for rel in o1_relations:
    a_id = company_dest_ids.get(rel["companyID"])
    if not a_id:
        continue
    cur.execute(
        """
        INSERT INTO company_relation (company_a_id, company_b_id, relation_type,
                                      description, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, true, NOW(), NOW())
    """,
        (
            a_id,
            a_id,
            safe_str(rel.get("relationType")),
            safe_str(f"{rel.get('surfaceForm', '')} -- {rel.get('summary', '')}"),
        ),
    )
    rel_count += 1
log(f"  Inserted {rel_count} company relations")

# -- 5e: Persons --
log("Loading persons...")
person_dest_ids = {}

for p in o1_people_exp:
    pid = p["personID"]
    if pid in person_dest_ids:
        continue
    name = p.get("person_full_name", "")
    parts = name.split()
    first = parts[0] if parts else None
    last = parts[-1] if len(parts) > 1 else None
    middle = " ".join(parts[1:-1]) if len(parts) > 2 else None

    cur.execute(
        """
        INSERT INTO person (first_name, middle_name, last_name, created_at, updated_at)
        VALUES (%s, %s, %s, NOW(), NOW()) RETURNING id
    """,
        (safe_str(first), safe_str(middle), safe_str(last)),
    )
    person_dest_ids[pid] = cur.fetchone()[0]

log(f"  origin1 persons: {len(person_dest_ids)}")

# origin2: no experiences linked to Lockheed Martin, so 0 persons from origin2
log("  origin2 persons: 0 (origin2 LM has no linked experiences)")
log(f"  Total persons loaded: {len(person_dest_ids)}")

# -- 5f: Experiences --
log("Loading experiences...")
exp_count = 0
for p in o1_people_exp:
    p_id = person_dest_ids.get(p["personID"])
    c_id = company_dest_ids.get(p["companyID"])
    if not p_id or not c_id:
        continue
    cur.execute(
        """
        INSERT INTO experience (person_id, company_id, job_title, role, is_current, is_active,
                                source, created_at, updated_at)
        VALUES (%s, %s, %s, %s, true, true, %s, NOW(), NOW())
    """,
        (p_id, c_id, safe_str(p.get("role")), safe_str(p.get("role")), "origin1"),
    )
    exp_count += 1
log(f"  Inserted {exp_count} experiences")

# -- 5g: Financials --
log("Loading financials...")
log("  ZERO financials (no CompanyFinancials for any Lockheed entity in origin1)")

# -- 5h: Technologies --
log("Loading technologies...")
tech_cat_ids = {}
all_cats = set()
for t in o1_tech:
    cats_raw = t.get("categories", "[]")
    try:
        cats = json.loads(cats_raw.replace("'", '"')) if cats_raw else []
    except Exception:
        cats = []
    for c in cats:
        all_cats.add(c)

for cat in sorted(all_cats):
    cur.execute(
        """
        INSERT INTO technologic_category (name, is_active, created_at)
        VALUES (%s, true, NOW()) RETURNING id
    """,
        (cat,),
    )
    tech_cat_ids[cat] = cur.fetchone()[0]
log(f"  Inserted {len(tech_cat_ids)} tech categories")

tech_count = 0
seen_tech = set()
for t in o1_tech:
    c_id = company_dest_ids.get(t["companyID"])
    if not c_id:
        continue
    tech_name = t.get("technologyName", "")
    if not tech_name or tech_name in seen_tech:
        continue
    seen_tech.add(tech_name)

    cats_raw = t.get("categories", "[]")
    try:
        cats = json.loads(cats_raw.replace("'", '"')) if cats_raw else []
    except Exception:
        cats = []
    cat_id = tech_cat_ids.get(cats[0]) if cats else None

    cur.execute(
        """
        INSERT INTO technologic (name, category_id, vendor_company_id, product_type,
                                 source, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, true, NOW(), NOW())
    """,
        (
            tech_name,
            cat_id,
            c_id,
            safe_str(t.get("technologyType")),
            "origin1",
        ),
    )
    tech_count += 1
log(f"  Inserted {tech_count} technologies")

# -- 5i: Contacts --
log("Loading contacts...")
phone_count = 0
email_count = 0

for cc in o1_contacts:
    c_id = company_dest_ids.get(cc["companyID"])
    if not c_id:
        continue
    contact_str = cc.get("contactString", "")
    contact_type = cc.get("contactType", "")

    if "@" in contact_str or contact_type == "PROFESSIONAL":
        cur.execute(
            """
            INSERT INTO contact_email (company_id, email, email_type, is_primary, created_at)
            VALUES (%s, %s, %s, false, NOW())
        """,
            (c_id, contact_str, contact_type or "PROFESSIONAL"),
        )
        email_count += 1
    else:
        cur.execute(
            """
            INSERT INTO contact_phone (company_id, phone, phone_type, is_primary, created_at)
            VALUES (%s, %s, %s, false, NOW())
        """,
            (c_id, contact_str, contact_type or "OFFICE"),
        )
        phone_count += 1

log(f"  Inserted {phone_count} phone contacts, {email_count} email contacts")


# ============================================================================
# STEP 6: VERIFICATION
# ============================================================================
log("\n=== STEP 6: LIVE VERIFICATION ===")

tables_to_check = [
    "industry",
    "location",
    "company",
    "company_relation",
    "person",
    "experience",
    "finance",
    "technologic_category",
    "technologic",
    "contact_phone",
    "contact_email",
]

total_rows = 0
for tbl in tables_to_check:
    cur.execute(f"SELECT count(*) FROM {tbl}")
    cnt = cur.fetchone()[0]
    total_rows += cnt
    log(f"  {tbl}: {cnt} rows")

log(f"\n  TOTAL rows loaded: {total_rows}")

cur.execute("""SELECT id, name, legal_name, employee_count, revenue, founded_date, status
               FROM company WHERE name = 'Lockheed Martin' AND legal_name IS NOT NULL""")
row = cur.fetchone()
if row:
    log(
        f"\n  Golden Lockheed Martin verified: id={row[0]}, name='{row[1]}', "
        f"legal_name='{row[2]}', employees={row[3]}, revenue={row[4]}, "
        f"founded={row[5]}, status='{row[6]}'"
    )
    log(
        "  NOTE: employee_count=None and revenue=None are EXPECTED gaps -- "
        "no source has this data for Lockheed Martin"
    )

cur.close()
pg.close()
try:
    ch.close()
except Exception:
    pass

log("\n=== DONE -- Full Lockheed Martin manual merge complete ===")
