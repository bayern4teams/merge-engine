"""
Manual Airbus merge -- same 6-step pipeline as the Boeing hand-merge:
  1. Extract all Airbus-related data from origin1 + origin2 (ClickHouse)
  2. Entity resolution (match same real-world entities across origins)
  3. Field mapping (origin schemas -> destination schema)
  4. Conflict resolution / unification (build golden records)
  5. Load into the destination (PostgreSQL)
  6. Verification

Key Airbus-specific challenges vs Boeing:
  - origin1 has TWO top-level entities: Airbus SE (holding) and Airbus SAS (operating)
    with the same CEO + founding date -- must unify them into one golden parent
  - origin2 has 12 fragmented, mis-scaled same-name shards, none matching the real
    Airbus SE (multi-billion aerospace). Entity resolution is genuinely ambiguous.
  - Revenue strings in origin2 ("$6.7 Billion") need parsing, same class as Boeing's crash.
  - origin1 has 25 years of financials (EUR) on the SE entity (1999-2025).

Promoted from the original scratchpad script 2026-07-30 with the destination now
parameterised via --target-db/--target-schema (via `SET search_path`) so this same
script can populate either the real `global_warehouse` destination or an `airbus`
schema inside the read-only `global_warehouse_golden` verification database.

Usage:
    python airbus_manual_merge.py                                   # -> global_warehouse.public
    python airbus_manual_merge.py --target-db global_warehouse_golden --target-schema airbus
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
        "sa",
        "sas",
        "se",
        "gmbh",
        ",",
        ".",
    ]:
        n = n.replace(stop, "")
    return " ".join(n.split())


def normalize_domain(w):
    if not w:
        return ""
    w = w.lower().replace("http://", "").replace("https://", "").replace("www.", "").strip("/")
    return w


# ============================================================================
# STEP 1: EXTRACT
# ============================================================================
log("=== STEP 1: EXTRACT ===")

# -- origin1: all Airbus-family companies --
o1_companies = ch_query("""
    SELECT * FROM origin1.Company
    WHERE entityName ILIKE '%airbus%' OR fullName ILIKE '%airbus%'
""")
log(f"origin1.Company: {len(o1_companies)} Airbus entities")
for c in o1_companies:
    log(f"  {c['companyID'][:12]}.. = {c['entityName']} / {c['fullName']}")

# -- origin2: all Airbus-family companies --
o2_companies = ch_query("""
    SELECT DISTINCT * FROM origin2.Company
    WHERE name ILIKE 'airbus%'
""")
log(f"origin2.Company: {len(o2_companies)} Airbus entities")

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
o1_investments = ch_query(f"""
    SELECT ir.*, inv.surfaceForm as investor_name, inv.summary as investor_summary
    FROM origin1.CompanyInvestmentRound ir
    LEFT JOIN origin1.CompanyInvestor inv ON ir.investmentID = inv.investmentID
    WHERE ir.companyID IN {o1_id_clause}
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
    f"{len(o1_investments)} investments, {len(o1_tech)} technologies, "
    f"{len(o1_financials)} financials, {len(o1_locations)} locations, "
    f"{len(o1_emp_cats)} employee categories"
)

# -- origin2: linked data --
# `sorted(set(...))` not `list(set(...))`: Python's per-process hash randomization
# (PYTHONHASHSEED) reorders a plain set() of strings differently on every launch,
# which changes the literal order of values in the `IN (...)` clause below -- and
# ClickHouse's row order for an unordered `WHERE x IN (...)` query is sensitive to
# that literal order. That non-determinism silently propagated all the way to
# which 500 origin2 people got capped into `person_dest_ids` further down, and
# from there to the final contact_email count (same bug confirmed on the Boeing
# script -- see its comment at the equivalent line).
o2_ids = sorted(set(c["companyID"] for c in o2_companies))
o2_id_clause = "(" + ",".join(f"'{i}'" for i in o2_ids) + ")"

o2_experiences = ch_query(f"""
    SELECT e.*, p.fullName, p.firstName, p.lastName, p.middleName
    FROM origin2.Experience e
    JOIN origin2.Person p ON e.experienceID = p.personID
    WHERE e.companyID IN {o2_id_clause}
""")
# Belt-and-braces: sort the fetched rows themselves by a stable key too, since
# everything downstream (the 500-person cap, the [:500] experience slice) depends
# on this list's order and a raw ClickHouse query offers no ordering guarantee.
o2_experiences = sorted(o2_experiences, key=lambda r: str(r.get("experienceID", "")))
o2_industries = ch_query(f"""
    SELECT ci.companyID, ci.classificationID, ci.isPrimary, i.name as industry_name
    FROM origin2.CompanyIndustry ci
    JOIN origin2.Industry i ON ci.classificationID = i.classificationID
    WHERE ci.companyID IN {o2_id_clause}
""")
o2_person_ids = sorted(set(e["experienceID"] for e in o2_experiences))
o2_contacts = []
if o2_person_ids:
    for i in range(0, len(o2_person_ids), 500):
        batch = o2_person_ids[i : i + 500]
        id_clause = "(" + ",".join(f"'{x}'" for x in batch) + ")"
        o2_contacts.extend(
            ch_query(f"""
            SELECT personID, contactString FROM origin2.PersonContact
            WHERE personID IN {id_clause}
        """)
        )

log(
    f"  origin2 linked: {len(o2_experiences)} experiences/people, "
    f"{len(o2_industries)} industries, {len(o2_contacts)} person contacts"
)

o2_locations = ch_query(f"""
    SELECT * FROM origin2.Location
    WHERE toString(companyID) IN {o2_id_clause}
""")
log(f"  origin2 locations: {len(o2_locations)}")


# ============================================================================
# STEP 2: ENTITY RESOLUTION
# ============================================================================
log("\n=== STEP 2: ENTITY RESOLUTION ===")

o1_by_id = {c["companyID"]: c for c in o1_companies}
o2_by_id = {c["companyID"]: c for c in o2_companies}

# -- INTRA-ORIGIN1 resolution: Airbus SE vs Airbus SAS --
# Decision: Airbus SE (Ec_P38BE4MX6mRCDh9NWh_A) is the holding/parent entity.
# Airbus SAS (EXRo_XwecMo6TPp_x5JybaA) is the main operating subsidiary.
# Both share CEO Guillaume Faury and founding date 1970-12-18.
# For "all info about Airbus" we treat SE as the golden parent and fold SAS's
# linked data (especially its 2019 USD financial + its people) into the same entity.
GOLDEN_O1 = "Ec_P38BE4MX6mRCDh9NWh_A"  # Airbus SE
SAS_O1 = "EXRo_XwecMo6TPp_x5JybaA"  # Airbus SAS (operating sub)

AIRBUS_PARENT_O1_IDS = {GOLDEN_O1, SAS_O1}
AIRBUS_SUB_O1_IDS = [cid for cid in o1_ids if cid not in AIRBUS_PARENT_O1_IDS]

log("INTRA-ORIGIN1 resolution:")
log(f"  Golden parent: {GOLDEN_O1} = 'Airbus SE' / 'AIRBUS SE' (holding, isPublic=True)")
log(f"  Folded in:     {SAS_O1} = 'Airbus' / 'Airbus SAS' (operating sub, isPublic=True)")
log(f"  Subsidiaries:  {len(AIRBUS_SUB_O1_IDS)} entities")
for sid in AIRBUS_SUB_O1_IDS:
    log(f"    {sid[:12]}.. = {o1_by_id[sid]['entityName']}")

# -- CROSS-ORIGIN resolution: origin1 vs origin2 --
# All 12 origin2 shards are in fact real Airbus-group entities, just fragmented in
# the data. The main parent match is origin1 "Airbus SE" <-> origin2 "Airbus
# Helicopters" (biggest, $6.7B) and "Airbus Group" (former name of Airbus SE).
O2_PARENT_MATCHES = {
    "365384179": 0.85,  # Airbus Helicopters = $6.7B, real division, maps to parent
    "1115568140": 0.90,  # Airbus Group = former name of Airbus SE
}
O2_SUBSIDIARY_IDS = [cid for cid in o2_ids if cid not in O2_PARENT_MATCHES]

log("\nCROSS-ORIGIN resolution:")
log(f"  origin2 shards mapped to parent: {len(O2_PARENT_MATCHES)}")
for oid, conf in O2_PARENT_MATCHES.items():
    log(f"    [{conf:.2f}] '{o2_by_id[oid]['name']}' -> parent Airbus SE")
log(f"  origin2 shards as subsidiaries: {len(O2_SUBSIDIARY_IDS)}")
for oid in O2_SUBSIDIARY_IDS:
    log(f"    '{o2_by_id[oid]['name']}' ({o2_by_id[oid].get('revenue', 'N/A')})")


# ============================================================================
# STEP 3: FIELD MAPPING (documented, same as Boeing template)
# ============================================================================
log("\n=== STEP 3: FIELD MAPPING ===")
log(
    "  origin1.Company -> company: entityName->name, fullName->legal_name, "
    "website->website, foundingDate->founded_date, isPublic->status, description->description"
)
log(
    "  origin2.Company -> company: name->name, employeeCount->employee_count, "
    "revenue->revenue, website->website, description->description"
)
log("  origin1.CompanyFinancials -> finance: year->period, amount->revenue, currency->currency")
log("  origin1.CompanyTechnographics -> technologic: technologyName->name, categories->category")
log("  origin1.CompanyRelation -> company_relation: relationType->relation_type")
log("  origin1.CompanyExperience+Person -> person+experience")
log("  origin2.Experience+Person -> person+experience")
log("  origin1.CompanyContact -> contact_email/contact_phone")
log("  origin1.Location -> location")


# ============================================================================
# STEP 4: CONFLICT RESOLUTION & UNIFICATION
# ============================================================================
log("\n=== STEP 4: CONFLICT RESOLUTION & UNIFICATION ===")

c_se = o1_by_id[GOLDEN_O1]  # Airbus SE
c_sas = o1_by_id[SAS_O1]  # Airbus SAS

golden_airbus = {
    "name": "Airbus",
    "legal_name": c_se.get("fullName"),  # "AIRBUS SE" from origin1
    "website": "airbus.com",  # both origins agree on main domain
    "employee_count": parse_employee_count("Over 10,000"),  # origin2 Helicopters (best available)
    "revenue": 73420000000.0,  # origin1 SE 2025 financial: EUR 73.4B
    "revenue_currency": "EUR",  # origin1 financials denominated EUR
    "founded_date": parse_founding_date(c_se.get("foundingDate")),  # d1970-12-18
    "status": "public",  # origin1: isPublic=True
    "description": c_se.get("description")
    if len(c_se.get("description", "") or "") >= len(c_sas.get("description", "") or "")
    else c_sas.get("description"),
    "motto": c_se.get("motto")
    or c_sas.get("motto"),  # "We make it fly" (SE) vs "New standards. Together." (SAS)
    "ceo_name": c_se.get("ceoName"),  # Guillaume Faury
}

log("Golden Airbus company record (conflict resolution):")
for k, v in golden_airbus.items():
    src = ""
    if k in ("legal_name", "founded_date", "ceo_name", "motto"):
        src = " <- origin1 SE (holding entity)"
    elif k == "employee_count":
        src = " <- origin2 Helicopters (best avail; real Airbus is ~134k, gap noted)"
    elif k == "revenue":
        src = " <- origin1 SE 2025 financial (EUR 73.4B)"
    elif k in ("name", "website", "status"):
        src = " <- both origins agree"
    elif k == "description":
        src = " <- origin1 SE (longer)"
    val_display = str(v)[:100] + "..." if v and len(str(v)) > 100 else v
    log(f"  {k}: {val_display}{src}")

o1_people_by_name = {}
for p in o1_people_exp:
    n = normalize_name(p.get("person_full_name", ""))
    if n:
        o1_people_by_name.setdefault(n, []).append(p)

person_matches = []
for e2 in o2_experiences[:2000]:
    fn = (e2.get("firstName", "") or "") + " " + (e2.get("lastName", "") or "")
    n2 = normalize_name(fn)
    if n2 in o1_people_by_name:
        person_matches.append((o1_people_by_name[n2][0], e2))

log(f"\nPerson cross-origin matches: {len(person_matches)}")
for o1p, o2p in person_matches[:5]:
    log(
        f"  '{o1p.get('person_full_name')}' (o1, {o1p.get('role')}) <-> "
        f"'{o2p.get('fullName')}' (o2, {o2p.get('title', '')})"
    )


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
for ind in o2_industries:
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
location_ids = {}  # origin companyID -> dest location id

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

# origin2 locations
for loc in o2_locations:
    cur.execute(
        """
        INSERT INTO location (country_name, city_name, address_line1,
                              postal_code, is_active, updated_at)
        VALUES (%s, %s, %s, %s, true, NOW()) RETURNING id
    """,
        (
            safe_str(loc.get("country")),
            safe_str(loc.get("city")),
            safe_str(loc.get("street")),
            safe_str(loc.get("zip")),
        ),
    )
    cid_str = str(loc.get("companyID", ""))
    if cid_str and cid_str != "None":
        location_ids[f"o2_{cid_str}"] = cur.fetchone()[0]

log(f"  Inserted {len(location_ids)} locations")

# -- 5c: Companies --
log("Loading companies...")
company_dest_ids = {}  # origin companyID -> dest company id

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
        golden_airbus["name"],
        golden_airbus["legal_name"],
        golden_airbus["website"],
        main_ind_id,
        main_loc_id,
        golden_airbus["employee_count"],
        golden_airbus["revenue"],
        golden_airbus["revenue_currency"],
        golden_airbus["founded_date"],
        golden_airbus["status"],
        golden_airbus["description"],
    ),
)
main_company_id = cur.fetchone()[0]

company_dest_ids[GOLDEN_O1] = main_company_id
company_dest_ids[SAS_O1] = main_company_id
for oid in O2_PARENT_MATCHES:
    company_dest_ids[oid] = main_company_id

log(f"  Golden Airbus -> dest id {main_company_id}")

for sub_id in AIRBUS_SUB_O1_IDS:
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

# origin2 subsidiaries (not already matched to parent)
loaded_o2_names = set()
for o2c in o2_companies:
    oid = o2c["companyID"]
    if oid in company_dest_ids:
        continue
    n = normalize_name(o2c.get("name", ""))
    if n in loaded_o2_names:
        continue
    loaded_o2_names.add(n)

    cur.execute(
        """
        INSERT INTO company (name, website, employee_count, revenue, revenue_currency,
                             status, description, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        RETURNING id
    """,
        (
            o2c.get("name"),
            safe_str(o2c.get("website")),
            parse_employee_count(o2c.get("employeeCount")),
            parse_revenue(o2c.get("revenue")),
            "USD" if o2c.get("revenue") else None,
            "public" if o2c.get("ticker") else "private",
            safe_str(o2c.get("description")),
        ),
    )
    company_dest_ids[oid] = cur.fetchone()[0]

log(f"  Total companies loaded: {len(set(company_dest_ids.values()))}")

# -- 5d: Company Relations --
log("Loading company relations...")
# Deterministic order: ClickHouse doesn't guarantee row order without ORDER BY, and
# neither does Python's set()-based batching upstream (string hash randomization
# varies per process). Sort before capping so re-running this script always caps
# on the SAME 300 relations, not an arbitrary 300.
o1_relations = sorted(
    o1_relations,
    key=lambda r: (
        str(r.get("companyID", "")),
        str(r.get("relationType", "")),
        str(r.get("surfaceForm", "")),
    ),
)
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
            a_id,  # self-reference placeholder (related company not in import scope)
            safe_str(rel.get("relationType")),
            safe_str(f"{rel.get('surfaceForm', '')} -- {rel.get('summary', '')}"),
        ),
    )
    rel_count += 1
    if rel_count >= 300:
        break
log(f"  Inserted {rel_count} company relations (capped at 300)")

# -- 5e: Persons --
log("Loading persons...")
person_dest_ids = {}

seen_person_ids = set()
for p in o1_people_exp:
    pid = p["personID"]
    if pid in seen_person_ids:
        continue
    seen_person_ids.add(pid)
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

o2_loaded = 0
for e in o2_experiences:
    if o2_loaded >= 500:
        break
    pid = e["experienceID"]
    if pid in person_dest_ids:
        continue
    cur.execute(
        """
        INSERT INTO person (first_name, middle_name, last_name, created_at, updated_at)
        VALUES (%s, %s, %s, NOW(), NOW()) RETURNING id
    """,
        (safe_str(e.get("firstName")), safe_str(e.get("middleName")), safe_str(e.get("lastName"))),
    )
    person_dest_ids[pid] = cur.fetchone()[0]
    o2_loaded += 1

log(f"  origin2 persons: {o2_loaded}")
log(f"  Total persons loaded: {len(person_dest_ids)}")

# -- 5f: Experiences (person <-> company link) --
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

for e in o2_experiences[:500]:
    p_id = person_dest_ids.get(e["experienceID"])
    c_id = company_dest_ids.get(e["companyID"])
    if not p_id or not c_id:
        continue
    cur.execute(
        """
        INSERT INTO experience (person_id, company_id, job_title, role, is_current, is_active,
                                source, created_at, updated_at)
        VALUES (%s, %s, %s, %s, true, true, %s, NOW(), NOW())
    """,
        (p_id, c_id, safe_str(e.get("title")), safe_str(e.get("role")), "origin2"),
    )
    exp_count += 1

log(f"  Inserted {exp_count} experiences")

# -- 5g: Financials (28 records spanning 1999-2025, mostly EUR from SE) --
log("Loading financials...")
fin_count = 0
for f in o1_financials:
    c_id = company_dest_ids.get(f["companyID"])
    if not c_id:
        continue
    year = f.get("year", 0)
    cur.execute(
        """
        INSERT INTO finance (company_id, period_type, period_start, period_end,
                             revenue, currency, source, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
    """,
        (
            c_id,
            "annual",
            date(year, 1, 1) if year > 1900 else None,
            date(year, 12, 31) if year > 1900 else None,
            float(f.get("amount", 0)),
            f.get("currency", "EUR"),
            "origin1",
        ),
    )
    fin_count += 1
log(f"  Inserted {fin_count} financial records")

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

# -- 5i: Contacts (phone + email) --
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

# Determinism: o2_contacts was assembled by batching over a Python set() of person
# ids, so its order (and therefore which 200 contact RECORDS get sampled below)
# varies run-to-run unless sorted first. Sorting by personID makes the sample --
# and therefore the resulting email count -- the same on every run.
o2_contacts = sorted(o2_contacts, key=lambda r: str(r.get("personID", "")))
o2_email_loaded = 0
for pc in o2_contacts[:200]:
    p_id = person_dest_ids.get(pc["personID"])
    if not p_id:
        continue
    raw = pc.get("contactString", "")
    try:
        emails = json.loads(raw.replace("'", '"')) if raw.startswith("[") else [raw]
    except Exception:
        emails = [raw]
    for em in emails:
        if "@" in str(em):
            cur.execute(
                """
                INSERT INTO contact_email (person_id, email, email_type, is_primary, created_at)
                VALUES (%s, %s, %s, false, NOW())
            """,
                (p_id, str(em).strip(), "PERSONAL"),
            )
            email_count += 1
            o2_email_loaded += 1

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

cur.execute("""SELECT id, name, legal_name, employee_count, revenue, revenue_currency,
                      founded_date, status, description
               FROM company WHERE name = 'Airbus' AND legal_name IS NOT NULL""")
row = cur.fetchone()
if row:
    log(
        f"\n  Golden Airbus verified: id={row[0]}, name='{row[1]}', "
        f"legal_name='{row[2]}', employees={row[3]}, "
        f"revenue={row[4]:,.0f} {row[5]}, "
        f"founded={row[6]}, status='{row[7]}'"
    )

cur.close()
pg.close()
try:
    ch.close()
except Exception:
    pass

log("\n=== DONE -- Full Airbus manual merge complete ===")
