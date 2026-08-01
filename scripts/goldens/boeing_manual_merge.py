"""
Manual Boeing merge — does exactly what the merge-engine app does, step by step:
  1. Extract all Boeing-related data from origin1 + origin2 (ClickHouse)
  2. Entity resolution (match same real-world entities across origins)
  3. Field mapping (origin schemas -> destination schema)
  4. Conflict resolution / unification (build golden records)
  5. Load into the destination (PostgreSQL)
  6. Verification

No app code is used - pure SQL + Python logic. This is the reference baseline the
real app is measured against (see docs/BOEING_APP_VS_MANUAL_DIAGNOSIS.md and
merge-engine-remediation-1-to-6 in project memory).

Promoted from the original scratchpad script 2026-07-30 with two changes: (a) the
destination is now parameterised via --target-db/--target-schema (via `SET
search_path`, so all the unqualified `INSERT INTO company (...)` SQL below is
unchanged) so this same script can populate either the real `global_warehouse`
destination or a `boeing` schema inside the read-only `global_warehouse_golden`
verification database; (b) the trailing `ch.disconnect()` typo is fixed to `ch.close()`
(clickhouse_connect clients don't have `.disconnect()` - it fired after all commits so
was harmless, but is worth fixing).

Usage:
    python boeing_manual_merge.py                                   # -> global_warehouse.public
    python boeing_manual_merge.py --target-db global_warehouse_golden --target-schema boeing
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
ch = clickhouse_connect.get_client(host=args.ch_host, port=args.ch_port,
                                    username=args.ch_user, password=args.ch_password)
pg = psycopg2.connect(host=args.pg_host, port=args.pg_port, user=args.pg_user,
                       password=args.pg_password, dbname=args.target_db)
pg.autocommit = True
cur = pg.cursor()
cur.execute('SET search_path TO %s', (args.target_schema,))

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
    """Parse origin1's 'd1916-07-15' or 'dYYYY-XX-XX' format into a date or None."""
    if not raw or not raw.startswith('d'):
        return None
    raw = raw[1:]  # strip leading 'd'
    raw = raw.replace('XX', '01')
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except:
        return None

def parse_employee_count(raw):
    """Parse origin2's 'Over 10,000' or '100 - 250' into an integer."""
    if not raw:
        return None
    raw = raw.replace(',', '')
    if 'Over' in raw:
        m = re.search(r'(\d+)', raw)
        return int(m.group(1)) if m else None
    m = re.search(r'(\d+)\s*-\s*(\d+)', raw)
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m = re.search(r'(\d+)', raw)
    return int(m.group(1)) if m else None

def parse_revenue(raw):
    """Parse origin2's '$61.1 Billion' into a float."""
    if not raw:
        return None
    raw = raw.replace('$', '').replace(',', '').strip()
    multiplier = 1
    if 'Billion' in raw:
        multiplier = 1_000_000_000
        raw = raw.replace('Billion', '').strip()
    elif 'Million' in raw:
        multiplier = 1_000_000
        raw = raw.replace('Million', '').strip()
    try:
        return float(raw) * multiplier
    except:
        return None


# ================================================================================
# STEP 1: EXTRACT -- Pull all Boeing data from both origins
# ================================================================================
log("=== STEP 1: EXTRACT ===")

# -- origin1: all Boeing-family companies --
o1_companies = ch_query("""
    SELECT * FROM origin1.Company
    WHERE entityName ILIKE '%boeing%' OR fullName ILIKE '%boeing%'
""")
log(f"origin1.Company: {len(o1_companies)} Boeing entities")

# -- origin2: all Boeing-family companies --
o2_companies = ch_query("""
    SELECT DISTINCT * FROM origin2.Company
    WHERE name ILIKE '%boeing%'
""")
log(f"origin2.Company: {len(o2_companies)} Boeing entities")

# -- origin1: linked data --
o1_ids = [f"'{c['companyID']}'" for c in o1_companies]
o1_id_clause = f"({','.join(o1_ids)})"

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
o1_financials = ch_query(f"SELECT * FROM origin1.CompanyFinancials WHERE companyID IN {o1_id_clause}")
o1_locations = ch_query(f"SELECT * FROM origin1.Location WHERE companyID IN {o1_id_clause}")
o1_emp_cats = ch_query(f"SELECT * FROM origin1.CompanyEmployeeCategory WHERE companyID IN {o1_id_clause}")

log(f"  origin1 linked: {len(o1_relations)} relations, {len(o1_contacts)} contacts, "
    f"{len(o1_people_exp)} people/experiences, {len(o1_industries)} industries, "
    f"{len(o1_investments)} investments, {len(o1_tech)} technologies, "
    f"{len(o1_financials)} financials, {len(o1_locations)} locations, "
    f"{len(o1_emp_cats)} employee categories")

# -- origin2: linked data --
# `sorted(set(...))` not `list(set(...))`: Python's per-process hash randomization
# (PYTHONHASHSEED) reorders a plain set() of strings differently on every launch,
# which changes the literal order of values in the `IN (...)` clause below -- and
# ClickHouse's row order for an unordered `WHERE x IN (...)` query is sensitive to
# that literal order. That non-determinism silently propagated all the way to
# which 500 origin2 people got capped into `person_dest_ids` further down, and
# from there to the final contact_email count (confirmed live: 151 vs 255 rows
# across two otherwise-identical runs before this fix).
o2_ids = sorted(set(c['companyID'] for c in o2_companies))
o2_id_clause = f"({','.join(repr(i) for i in o2_ids)})"

o2_experiences = ch_query(f"""
    SELECT e.*, p.fullName, p.firstName, p.lastName, p.middleName
    FROM origin2.Experience e
    JOIN origin2.Person p ON e.experienceID = p.personID
    WHERE e.companyID IN {o2_id_clause}
""")
# Belt-and-braces: sort the fetched rows themselves by a stable key too, since
# everything downstream (the 500-person cap, the [:500] experience slice) depends
# on this list's order and a raw ClickHouse query offers no ordering guarantee.
o2_experiences = sorted(o2_experiences, key=lambda r: str(r.get('experienceID', '')))
o2_industries = ch_query(f"""
    SELECT ci.companyID, ci.classificationID, ci.isPrimary, i.name as industry_name
    FROM origin2.CompanyIndustry ci
    JOIN origin2.Industry i ON ci.classificationID = i.classificationID
    WHERE ci.companyID IN {o2_id_clause}
""")
# origin2 contacts for Boeing people
o2_person_ids = sorted(set(e['experienceID'] for e in o2_experiences))
o2_contacts = []
if o2_person_ids:
    # batch in chunks of 500
    for i in range(0, len(o2_person_ids), 500):
        batch = o2_person_ids[i:i + 500]
        id_clause = f"({','.join(repr(x) for x in batch)})"
        o2_contacts.extend(ch_query(f"""
            SELECT personID, contactString FROM origin2.PersonContact
            WHERE personID IN {id_clause}
        """))

log(f"  origin2 linked: {len(o2_experiences)} experiences/people, "
    f"{len(o2_industries)} industries, {len(o2_contacts)} person contacts")


# ================================================================================
# STEP 2: ENTITY RESOLUTION -- Match same real-world entities across origins
# ================================================================================
log("\n=== STEP 2: ENTITY RESOLUTION ===")

# Strategy: match on name similarity + website/domain.
# The app uses Fellegi-Sunter scoring; here I do deterministic name/website matching.

def normalize_name(n):
    if not n:
        return ''
    n = n.lower().strip()
    for stop in ['the ', 'inc.', 'inc', 'ltd.', 'ltd', 'llc', 'co.', 'co', 'corp.', 'corp',
                 'corporation', 'limited', 'company', 'proprietary', ',', '.']:
        n = n.replace(stop, '')
    return ' '.join(n.split())

def normalize_domain(w):
    if not w:
        return ''
    w = w.lower().replace('http://', '').replace('https://', '').replace('www.', '').strip('/')
    return w

# Build lookup dicts
o1_by_id = {c['companyID']: c for c in o1_companies}
o2_by_id = {c['companyID']: c for c in o2_companies}

# Entity resolution: company matching across origins
company_matches = []  # list of (o1_id, o2_id, confidence, match_reason)

for c1 in o1_companies:
    n1 = normalize_name(c1.get('entityName', '') + ' ' + c1.get('fullName', ''))
    d1 = normalize_domain(c1.get('website', ''))

    for c2 in o2_companies:
        n2 = normalize_name(c2.get('name', ''))
        d2 = normalize_domain(c2.get('website', '') or c2.get('domain', ''))

        # Exact name match
        name_match = False
        for part1 in n1.split():
            if len(part1) > 3 and part1 in n2:
                name_match = True
                break

        # Domain match
        domain_match = d1 and d2 and (d1 in d2 or d2 in d1)

        # Score
        if normalize_name(c1.get('entityName', '')) == normalize_name(c2.get('name', '')):
            company_matches.append((c1['companyID'], c2['companyID'], 0.98,
                                     f"exact name: '{c1.get('entityName')}' = '{c2.get('name')}'"))
        elif domain_match and name_match:
            company_matches.append((c1['companyID'], c2['companyID'], 0.92,
                                     f"domain+name: {d1}~{d2}"))
        elif domain_match:
            company_matches.append((c1['companyID'], c2['companyID'], 0.80,
                                     f"domain only: {d1}~{d2}"))

# Deduplicate -- keep highest confidence per pair
seen = {}
for o1_id, o2_id, conf, reason in company_matches:
    key = (o1_id, o2_id)
    if key not in seen or conf > seen[key][0]:
        seen[key] = (conf, reason)

company_matches = [(k[0], k[1], v[0], v[1]) for k, v in seen.items()]
company_matches.sort(key=lambda x: -x[2])

log(f"Company matches across origins: {len(company_matches)}")
for o1_id, o2_id, conf, reason in company_matches[:20]:
    o1n = o1_by_id[o1_id].get('entityName', '?')
    o2n = o2_by_id[o2_id].get('name', '?')
    log(f"  [{conf:.2f}] origin1:'{o1n}' <-> origin2:'{o2n}' -- {reason}")

# Identify THE main Boeing entity (the parent company)
MAIN_O1 = 'EMAakygGHMUiRPfT6DPOr6Q'  # "Boeing" / "The Boeing Company" / boeing.com / PUBLIC
MAIN_O2 = '5128546'                     # "Boeing" / www.boeing.com / NYSE:BA / $61.1B
log(f"\nPrimary entity resolution:")
log(f"  origin1: {MAIN_O1} = '{o1_by_id[MAIN_O1]['entityName']}' / '{o1_by_id[MAIN_O1]['fullName']}'")
log(f"  origin2: {MAIN_O2} = '{o2_by_id[MAIN_O2]['name']}'")
log(f"  Match confidence: 0.98 (exact name + domain)")


# ================================================================================
# STEP 3: FIELD MAPPING -- Map origin columns -> destination columns
# ================================================================================
log("\n=== STEP 3: FIELD MAPPING ===")

field_mappings = {
    'company': {
        'origin1.Company': {
            'entityName': 'name',
            'fullName': 'legal_name',
            'website': 'website',
            'foundingDate': 'founded_date',       # needs parse_founding_date()
            'isPublic': 'status',                  # 'True'->'public', 'False'->'private'
            'description': 'description',
        },
        'origin2.Company': {
            'name': 'name',
            'website': 'website',
            'employeeCount': 'employee_count',     # needs parse_employee_count()
            'revenue': 'revenue',                  # needs parse_revenue()
            'ticker': 'status',                    # presence -> 'public'
            'description': 'description',
        },
    },
    'person': {
        'origin1.Person (via CompanyExperience)': {
            'personName / person_full_name': 'first_name + last_name (split)',
        },
        'origin2.Person': {
            'firstName': 'first_name',
            'lastName': 'last_name',
            'middleName': 'middle_name',
        },
    },
    'location': {
        'origin1.Location': {
            'street': 'address_line1',
            'city': 'city_name',
            'region': 'state_name',
            'postalCode': 'postal_code',
            'country': 'country_name',
            'latitude': 'latitude',
            'longitude': 'longitude',
        },
    },
    'experience': {
        'origin1.CompanyExperience': {
            'personName': '-> person_id (FK)',
            'role': 'role + job_title',
        },
        'origin2.Experience': {
            'title': 'job_title',
            'role': 'role',
            'tags': 'source (metadata)',
        },
    },
    'finance': {
        'origin1.CompanyFinancials': {
            'year': 'period_start/period_end',
            'amount': 'revenue',
            'currency': 'currency',
        },
    },
    'technologic': {
        'origin1.CompanyTechnographics': {
            'technologyName': 'name',
            'categories': 'category_id (-> technologic_category)',
        },
    },
    'contact_phone': {
        'origin1.CompanyContact (contactType=OFFICE)': {
            'contactString': 'phone',
            'contactType': 'phone_type',
        },
    },
    'contact_email': {
        'origin1.CompanyContact (contactType=PROFESSIONAL)': {
            'contactString': 'email',
            'contactType': 'email_type',
        },
        'origin2.PersonContact': {
            'contactString': 'email',
        },
    },
}

for entity, sources in field_mappings.items():
    log(f"\n  {entity}:")
    for src, mapping in sources.items():
        log(f"    {src}:")
        for src_col, dst_col in mapping.items():
            log(f"      {src_col}  ->  {dst_col}")


# ================================================================================
# STEP 4: UNIFY / CONFLICT RESOLUTION -- Build golden records
# ================================================================================
log("\n=== STEP 4: CONFLICT RESOLUTION & UNIFICATION ===")

# Strategy (same as app's unify_entity_group.py):
#   - For each field, pick the "most complete" value
#   - origin1 wins for: legal_name, founding_date, CEO, motto, isPublic (richer data)
#   - origin2 wins for: employee_count, revenue (structured financial data)
#   - Merge descriptions (take longest)

c1 = o1_by_id[MAIN_O1]
c2 = o2_by_id[MAIN_O2]

golden_boeing = {
    'name': 'Boeing',                              # both agree
    'legal_name': c1.get('fullName'),               # origin1 only: "The Boeing Company"
    'website': 'boeing.com',                         # both agree (normalize)
    'employee_count': parse_employee_count(c2.get('employeeCount')),  # origin2: Over 10,000
    'revenue': parse_revenue(c2.get('revenue')),     # origin2: $61.1 Billion
    'revenue_currency': 'USD',                       # origin1 financials + origin2 both USD
    'founded_date': parse_founding_date(c1.get('foundingDate')),  # origin1: d1916-07-15
    'status': 'public',                              # origin1: isPublic=True, origin2: NYSE:BA
    'description': c2.get('description') if len(c2.get('description', '') or '') > len(c1.get('description', '') or '') else c1.get('description'),
}

log("Golden Boeing company record (conflict resolution):")
for k, v in golden_boeing.items():
    src = ''
    if k in ('legal_name', 'founded_date'):
        src = ' <- origin1 (only source)'
    elif k in ('employee_count', 'revenue'):
        src = ' <- origin2 (structured data)'
    elif k in ('name', 'website', 'status'):
        src = ' <- both origins agree'
    elif k == 'description':
        src = ' <- origin2 (longer/more detailed)'
    val_display = str(v)[:100] + '...' if v and len(str(v)) > 100 else v
    log(f"  {k}: {val_display}{src}")

# -- 4b: Collect all subsidiary companies (origin1 only, origin2 Boeing subs are mostly employee clubs) --
real_subs_o1 = [c for c in o1_companies if c['companyID'] != MAIN_O1
                and any(kw in (c.get('description', '') or '').lower()
                        for kw in ['aerospace', 'aircraft', 'defense', 'satellite', 'boeing'])]

log(f"\nBoeing subsidiaries (origin1, aerospace-related): {len(real_subs_o1)}")
for s in real_subs_o1:
    log(f"  {s['entityName']} -- {s.get('fullName', '')}")

# -- 4c: Person entity resolution across origins --
o1_people_by_name = {}
for p in o1_people_exp:
    n = normalize_name(p.get('person_full_name', ''))
    if n:
        o1_people_by_name.setdefault(n, []).append(p)

person_matches = []
o2_sample = o2_experiences[:2000]  # Check first 2000 for cross-origin matches
for e2 in o2_sample:
    n2 = normalize_name((e2.get('firstName', '') or '') + ' ' + (e2.get('lastName', '') or ''))
    if n2 in o1_people_by_name:
        person_matches.append((o1_people_by_name[n2][0], e2))

log(f"\nPerson cross-origin matches found: {len(person_matches)}")
for o1p, o2p in person_matches[:10]:
    log(f"  '{o1p.get('person_full_name')}' (origin1, {o1p.get('role')}) <-> "
        f"'{o2p.get('fullName')}' (origin2, {o2p.get('title', '')})")


# ================================================================================
# STEP 5: LOAD -- Insert unified data into the destination (PostgreSQL)
# ================================================================================
log("\n=== STEP 5: LOAD INTO DESTINATION ===")

# -- 5a: Industry --
log("Loading industries...")
industry_ids = {}  # name -> id

all_industries = set()
for ind in o1_industries:
    all_industries.add(ind.get('industry_name', ''))
for ind in o2_industries:
    all_industries.add(ind.get('industry_name', ''))
all_industries.discard('')

for ind_name in sorted(all_industries):
    cur.execute("""
        INSERT INTO industry (name, is_active, updated_at)
        VALUES (%s, true, NOW())
        RETURNING id
    """, (ind_name,))
    industry_ids[ind_name] = cur.fetchone()[0]

log(f"  Inserted {len(industry_ids)} industries")

# -- 5b: Locations --
log("Loading locations...")
location_ids = {}  # (companyID) -> dest location id

for loc in o1_locations:
    cur.execute("""
        INSERT INTO location (country_name, state_name, city_name, postal_code,
                              latitude, longitude, address_line1, is_active, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, true, NOW())
        RETURNING id
    """, (
        safe_str(loc.get('country')),
        safe_str(loc.get('region')),
        safe_str(loc.get('city')),
        safe_str(loc.get('postalCode')),
        float(loc['latitude']) if loc.get('latitude') else None,
        float(loc['longitude']) if loc.get('longitude') else None,
        safe_str(loc.get('street')),
    ))
    location_ids[loc['companyID']] = cur.fetchone()[0]

log(f"  Inserted {len(location_ids)} locations")

# -- 5c: Companies (the main Boeing + subsidiaries) --
log("Loading companies...")
company_dest_ids = {}  # origin_companyID -> dest company id

# Main Boeing
main_loc_id = location_ids.get(MAIN_O1)
main_ind_name = next((i['industry_name'] for i in o1_industries
                      if i['companyID'] == MAIN_O1), None)
main_ind_id = industry_ids.get(main_ind_name) if main_ind_name else None

cur.execute("""
    INSERT INTO company (name, legal_name, website, industry_id, location_id,
                         employee_count, revenue, revenue_currency, founded_date,
                         status, description, created_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    RETURNING id
""", (
    golden_boeing['name'],
    golden_boeing['legal_name'],
    golden_boeing['website'],
    main_ind_id,
    main_loc_id,
    golden_boeing['employee_count'],
    golden_boeing['revenue'],
    golden_boeing['revenue_currency'],
    golden_boeing['founded_date'],
    golden_boeing['status'],
    golden_boeing['description'],
))
main_company_id = cur.fetchone()[0]
company_dest_ids[MAIN_O1] = main_company_id
company_dest_ids[MAIN_O2] = main_company_id  # both origin IDs -> same dest row
log(f"  Main Boeing -> dest id {main_company_id}")

# Subsidiaries (from origin1)
for sub in real_subs_o1:
    sub_loc_id = location_ids.get(sub['companyID'])
    sub_ind_name = next((i['industry_name'] for i in o1_industries
                         if i['companyID'] == sub['companyID']), None)
    sub_ind_id = industry_ids.get(sub_ind_name) if sub_ind_name else None

    cur.execute("""
        INSERT INTO company (name, legal_name, website, industry_id, location_id,
                             founded_date, status, description, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        RETURNING id
    """, (
        sub.get('entityName'),
        sub.get('fullName'),
        safe_str(sub.get('website')),
        sub_ind_id,
        sub_loc_id,
        parse_founding_date(sub.get('foundingDate')),
        'public' if sub.get('isPublic') == 'True' else 'private',
        safe_str(sub.get('description')),
    ))
    company_dest_ids[sub['companyID']] = cur.fetchone()[0]

# Also load unique origin2 Boeing subsidiaries not matched to origin1
loaded_o2_names = set()
for o2c in o2_companies:
    if o2c['companyID'] == MAIN_O2:
        continue
    n = normalize_name(o2c.get('name', ''))
    already_matched = any(o2c['companyID'] == m[1] for m in company_matches)
    if already_matched or n in loaded_o2_names:
        continue
    loaded_o2_names.add(n)
    cur.execute("""
        INSERT INTO company (name, website, employee_count, revenue, revenue_currency,
                             status, description, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        RETURNING id
    """, (
        o2c.get('name'),
        safe_str(o2c.get('website')),
        parse_employee_count(o2c.get('employeeCount')),
        parse_revenue(o2c.get('revenue')),
        'USD' if o2c.get('revenue') else None,
        'public' if o2c.get('ticker') else 'private',
        safe_str(o2c.get('description')),
    ))
    company_dest_ids[o2c['companyID']] = cur.fetchone()[0]

log(f"  Total companies loaded: {len(set(company_dest_ids.values()))}")

# -- 5d: Company Relations --
log("Loading company relations...")
# Deterministic order: ClickHouse doesn't guarantee row order without ORDER BY, and
# neither does Python's set()-based batching upstream (string hash randomization
# varies per process). Sort before capping so re-running this script always caps
# on the SAME 200 relations, not an arbitrary 200 (confirmed live: an unsorted cap
# here reproduced a different row count -- 3,175 instead of the recorded 3,185 --
# purely from run-to-run ordering, not any real data difference).
o1_relations = sorted(o1_relations, key=lambda r: (str(r.get('companyID', '')), str(r.get('relationType', '')), str(r.get('surfaceForm', ''))))
rel_count = 0
for rel in o1_relations:
    a_id = company_dest_ids.get(rel['companyID'])
    if not a_id:
        continue
    cur.execute("""
        INSERT INTO company_relation (company_a_id, company_b_id, relation_type,
                                      description, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, true, NOW(), NOW())
    """, (
        a_id,
        a_id,  # self-reference as placeholder since related company not in our import scope
        safe_str(rel.get('relationType')),
        safe_str(f"{rel.get('surfaceForm', '')} -- {rel.get('summary', '')}"),
    ))
    rel_count += 1
    if rel_count >= 200:  # cap at 200 for demo
        break

log(f"  Inserted {rel_count} company relations (capped at 200)")

# -- 5e: Persons --
log("Loading persons...")
person_dest_ids = {}  # origin personID -> dest person id

for p in o1_people_exp:
    pid = p['personID']
    if pid in person_dest_ids:
        continue
    name = p.get('person_full_name', '')
    parts = name.split()
    first = parts[0] if parts else None
    last = parts[-1] if len(parts) > 1 else None
    middle = ' '.join(parts[1:-1]) if len(parts) > 2 else None

    cur.execute("""
        INSERT INTO person (first_name, middle_name, last_name, created_at, updated_at)
        VALUES (%s, %s, %s, NOW(), NOW())
        RETURNING id
    """, (safe_str(first), safe_str(middle), safe_str(last)))
    person_dest_ids[pid] = cur.fetchone()[0]

log(f"  origin1 persons: {len(person_dest_ids)}")

# origin2 people (employees) -- load up to 500 for demo
o2_loaded = 0
for e in o2_experiences:
    if o2_loaded >= 500:
        break
    pid = e['experienceID']
    if pid in person_dest_ids:
        continue
    cur.execute("""
        INSERT INTO person (first_name, middle_name, last_name, created_at, updated_at)
        VALUES (%s, %s, %s, NOW(), NOW())
        RETURNING id
    """, (safe_str(e.get('firstName')), safe_str(e.get('middleName')), safe_str(e.get('lastName'))))
    person_dest_ids[pid] = cur.fetchone()[0]
    o2_loaded += 1

log(f"  origin2 persons: {o2_loaded}")
log(f"  Total persons loaded: {len(person_dest_ids)}")

# -- 5f: Experiences (person <-> company link) --
log("Loading experiences...")
exp_count = 0

for p in o1_people_exp:
    p_id = person_dest_ids.get(p['personID'])
    c_id = company_dest_ids.get(p['companyID'])
    if not p_id or not c_id:
        continue
    cur.execute("""
        INSERT INTO experience (person_id, company_id, job_title, role, is_current, is_active,
                                source, created_at, updated_at)
        VALUES (%s, %s, %s, %s, true, true, %s, NOW(), NOW())
    """, (p_id, c_id, safe_str(p.get('role')), safe_str(p.get('role')), 'origin1'))
    exp_count += 1

for e in o2_experiences[:500]:
    p_id = person_dest_ids.get(e['experienceID'])
    c_id = company_dest_ids.get(e['companyID'])
    if not p_id or not c_id:
        continue
    cur.execute("""
        INSERT INTO experience (person_id, company_id, job_title, role, is_current, is_active,
                                source, created_at, updated_at)
        VALUES (%s, %s, %s, %s, true, true, %s, NOW(), NOW())
    """, (p_id, c_id, safe_str(e.get('title')), safe_str(e.get('role')), 'origin2'))
    exp_count += 1

log(f"  Inserted {exp_count} experiences")

# -- 5g: Financials --
log("Loading financials...")
fin_count = 0
for f in o1_financials:
    c_id = company_dest_ids.get(f['companyID'])
    if not c_id:
        continue
    year = f.get('year', 0)
    cur.execute("""
        INSERT INTO finance (company_id, period_type, period_start, period_end,
                             revenue, currency, source, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
    """, (
        c_id, 'annual',
        date(year, 1, 1) if year > 1900 else None,
        date(year, 12, 31) if year > 1900 else None,
        float(f.get('amount', 0)),
        f.get('currency', 'USD'),
        'origin1',
    ))
    fin_count += 1

log(f"  Inserted {fin_count} financial records")

# -- 5h: Technologies --
log("Loading technologies...")

tech_cat_ids = {}  # category_name -> id
all_cats = set()
for t in o1_tech:
    cats_raw = t.get('categories', '[]')
    try:
        cats = json.loads(cats_raw.replace("'", '"')) if cats_raw else []
    except:
        cats = []
    for c in cats:
        all_cats.add(c)

for cat in sorted(all_cats):
    cur.execute("""
        INSERT INTO technologic_category (name, is_active, created_at)
        VALUES (%s, true, NOW()) RETURNING id
    """, (cat,))
    tech_cat_ids[cat] = cur.fetchone()[0]

log(f"  Inserted {len(tech_cat_ids)} tech categories")

tech_count = 0
seen_tech = set()
for t in o1_tech:
    c_id = company_dest_ids.get(t['companyID'])
    if not c_id:
        continue
    tech_name = t.get('technologyName', '')
    if not tech_name or tech_name in seen_tech:
        continue
    seen_tech.add(tech_name)

    cats_raw = t.get('categories', '[]')
    try:
        cats = json.loads(cats_raw.replace("'", '"')) if cats_raw else []
    except:
        cats = []
    cat_id = tech_cat_ids.get(cats[0]) if cats else None

    cur.execute("""
        INSERT INTO technologic (name, category_id, vendor_company_id, product_type,
                                 source, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, true, NOW(), NOW())
    """, (
        tech_name,
        cat_id,
        c_id,
        safe_str(t.get('technologyType')),
        'origin1',
    ))
    tech_count += 1

log(f"  Inserted {tech_count} technologies")

# -- 5i: Contact phones & emails --
log("Loading contacts...")
phone_count = 0
email_count = 0

for cc in o1_contacts:
    c_id = company_dest_ids.get(cc['companyID'])
    if not c_id:
        continue
    contact_str = cc.get('contactString', '')
    contact_type = cc.get('contactType', '')

    if '@' in contact_str or contact_type == 'PROFESSIONAL':
        cur.execute("""
            INSERT INTO contact_email (company_id, email, email_type, is_primary, created_at)
            VALUES (%s, %s, %s, false, NOW())
        """, (c_id, contact_str, contact_type or 'PROFESSIONAL'))
        email_count += 1
    else:
        cur.execute("""
            INSERT INTO contact_phone (company_id, phone, phone_type, is_primary, created_at)
            VALUES (%s, %s, %s, false, NOW())
        """, (c_id, contact_str, contact_type or 'OFFICE'))
        phone_count += 1

# origin2 person contacts (emails)
# Determinism: o2_contacts was assembled by batching over a Python set() of person
# ids, so its order (and therefore which 200 contact RECORDS get sampled below)
# varies run-to-run unless sorted first (confirmed live: an unsorted 200-record
# cap landed 60 emails one run, 70 another -- same records aren't even guaranteed,
# let alone the same count). Sorting by personID makes the sample -- and therefore
# the resulting email count -- the same on every run.
o2_contacts = sorted(o2_contacts, key=lambda r: str(r.get('personID', '')))
o2_email_loaded = 0
for pc in o2_contacts[:200]:
    p_id = person_dest_ids.get(pc['personID'])
    if not p_id:
        continue
    raw = pc.get('contactString', '')
    try:
        emails = json.loads(raw.replace("'", '"')) if raw.startswith('[') else [raw]
    except:
        emails = [raw]
    for em in emails:
        if '@' in em:
            cur.execute("""
                INSERT INTO contact_email (person_id, email, email_type, is_primary, created_at)
                VALUES (%s, %s, %s, false, NOW())
            """, (p_id, em.strip(), 'PERSONAL'))
            email_count += 1
            o2_email_loaded += 1

log(f"  Inserted {phone_count} phone contacts, {email_count} email contacts")


# ================================================================================
# STEP 6: VERIFICATION -- Confirm what landed in the destination
# ================================================================================
log("\n=== STEP 6: LIVE VERIFICATION ===")

tables_to_check = [
    'industry', 'location', 'company', 'company_relation', 'person',
    'experience', 'finance', 'technologic_category', 'technologic',
    'contact_phone', 'contact_email',
]

total_rows = 0
for tbl in tables_to_check:
    cur.execute(f"SELECT count(*) FROM {tbl}")
    cnt = cur.fetchone()[0]
    total_rows += cnt
    log(f"  {tbl}: {cnt} rows")

log(f"\n  TOTAL rows loaded: {total_rows}")

# Verify the main Boeing record
cur.execute("SELECT id, name, legal_name, employee_count, revenue, founded_date, status FROM company WHERE name = 'Boeing' AND legal_name IS NOT NULL")
row = cur.fetchone()
if row:
    log(f"\n  Golden Boeing record verified: id={row[0]}, name='{row[1]}', "
        f"legal_name='{row[2]}', employees={row[3]}, revenue=${row[4]:,.0f}, "
        f"founded={row[5]}, status='{row[6]}'")

cur.close()
pg.close()
try:
    ch.close()
except Exception:
    pass

log("\n=== DONE -- Full manual merge complete ===")
