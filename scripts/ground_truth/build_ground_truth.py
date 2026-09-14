#!/usr/bin/env python3
"""
Hand-built golden/ground-truth Airbus + Boeing entity resolution, loaded into
the `ground_truth` PostgreSQL database (kg schema from /home/bayern/Projects/Database/docs).

This is an analyst's hand merge of the REAL origin data (testdiffbot Postgres +
testzoominfo ClickHouse), used as the comparison baseline for merge-engine's own
Airbus/Boeing runs. Every company/fact/relationship below is traceable to a real
origin row (companyid / companyID cited in `origin_ref`), with confidence scores
reflecting how certain the match/classification is, not blind 1.0s.

Not a perfect real-world corporate registry -- a competent analyst's best-evidence
merge within the time available, exactly like the July 2026 hand-merges this
extends. Simplifications documented inline (EUR->USD uses one flat rate, not
historical per-year rates; hierarchy is flattened to parent+subsidiary, not a full
multi-level org chart).
"""
from datetime import date

import psycopg2

DSN = "host=localhost port=5434 dbname=ground_truth user=postgres password=supersecurepassword"
TODAY = date(2026, 9, 10)
EUR_USD = 1.08  # flat approximation, documented simplification

conn = psycopg2.connect(DSN)
conn.autocommit = False
cur = conn.cursor()
cur.execute("SET search_path = kg, public;")

# ---- lookups -----------------------------------------------------------
cur.execute("SELECT currency_id, iso_code FROM currency")
CUR = {row[1]: row[0] for row in cur.fetchall()}

cur.execute("""SELECT tt.taxonomy_term_id, tt.code FROM taxonomy_term tt
               JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='relationship_type'""")
RELTYPE = {code: tid for tid, code in cur.fetchall()}

cur.execute("""SELECT tt.taxonomy_term_id, tt.code FROM taxonomy_term tt
               JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='company_status'""")
STATUS = {code: tid for tid, code in cur.fetchall()}

cur.execute("""SELECT tt.taxonomy_term_id, tt.code FROM taxonomy_term tt
               JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='name_type'""")
NAMETYPE = {code: tid for tid, code in cur.fetchall()}

# fact_predicate: add one extra predicate this analysis needs (ticker symbol),
# additive only, consistent with the schema's own "extensible without ALTER" design.
cur.execute("""INSERT INTO fact_predicate(code,name,subject_entity_type,value_kind,is_multi_valued)
               VALUES ('company.ticker_symbol','Company stock ticker','COMPANY','TEXT',FALSE)
               ON CONFLICT (code) DO NOTHING""")
cur.execute("SELECT predicate_id, code FROM fact_predicate")
PRED = {code: pid for pid, code in cur.fetchall()}

# ---- sources -------------------------------------------------------------
cur.execute("SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='source_type' AND tt.code='DATABASE'")
DB_SOURCE_TYPE = cur.fetchone()[0]
cur.execute("SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='source_type' AND tt.code='MANUAL'")
MANUAL_SOURCE_TYPE = cur.fetchone()[0]
cur.execute("SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='evidence_type' AND tt.code='TABLE_ROW'")
EVIDENCE_TYPE_ROW = cur.fetchone()[0]

def make_source(title, uri, reliability):
    cur.execute("""INSERT INTO source(source_type_term_id, uri, title, publisher, reliability_score, retrieved_at)
                   VALUES (%s,%s,%s,%s,%s, now()) RETURNING source_id""",
                (DB_SOURCE_TYPE if 'origin' in title.lower() else MANUAL_SOURCE_TYPE, uri, title, 'merge-engine forensic audit 2026-09-10', reliability))
    return cur.fetchone()[0]

SRC_ORIGIN1 = make_source("origin1: PostgreSQL testdiffbot (diffbot schema)", "postgresql://localhost:5433/testdiffbot", 0.70)
SRC_ORIGIN2 = make_source("origin2: ClickHouse testzoominfo", "clickhouse://localhost:8125/testzoominfo", 0.70)
SRC_ANALYST = make_source("Analyst hand-merge judgment (this audit)", None, 0.85)

def make_evidence(source_id, locator, snippet):
    cur.execute("""INSERT INTO evidence(source_id, evidence_type_term_id, locator, text_snippet)
                   VALUES (%s,%s,%s,%s) RETURNING evidence_id""",
                (source_id, EVIDENCE_TYPE_ROW, locator, snippet))
    return cur.fetchone()[0]

def link_evidence(item_id, evidence_id, primary=True):
    cur.execute("INSERT INTO knowledge_item_evidence(item_id, evidence_id, is_primary) VALUES (%s,%s,%s)",
                (item_id, evidence_id, primary))

def new_entity(etype):
    cur.execute("INSERT INTO entity(entity_type) VALUES (%s) RETURNING entity_id", (etype,))
    return cur.fetchone()[0]

def new_company(key, evidence_ids, confidence=0.9):
    eid = new_entity('COMPANY')
    cur.execute("INSERT INTO organization(organization_id) VALUES (%s)", (eid,))
    cur.execute("INSERT INTO company(company_id) VALUES (%s)", (eid,))
    MAP[key] = eid
    return eid

def new_person(key):
    eid = new_entity('PERSON')
    cur.execute("INSERT INTO person(person_id) VALUES (%s)", (eid,))
    MAP[key] = eid
    return eid

def new_item(source_id, confidence, extractor='analyst-hand-merge', supersedes=None):
    cur.execute("""INSERT INTO knowledge_item(item_type,source_id,confidence,extractor_name,extractor_version)
                   VALUES ('FACT',%s,%s,%s,'2026-09-10') RETURNING item_id""",
                (source_id, confidence, extractor))
    return cur.fetchone()[0]

def add_name(entity_id, name, name_type, source_id, ev_id, valid_from=None):
    item_id = new_entity  # unused placeholder, entity_name doesn't need knowledge_item
    cur.execute("""INSERT INTO entity_name(entity_id,name,normalized_name,language_code,name_type_term_id,valid_from)
                   VALUES (%s,%s,%s,'en',%s,%s) RETURNING item_id""",
                (entity_id, name, name.lower(), NAMETYPE[name_type], valid_from))

def add_fact_text(entity_id, pred_code, text, source_id, ev_id, confidence=0.9, valid_from=None, valid_to=None, item_type='FACT'):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute("""INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_text, valid_from, valid_to)
                   VALUES (%s,%s,%s,'TEXT',%s,%s,%s)""",
                (item_id, entity_id, PRED[pred_code], text, valid_from, valid_to))

def add_fact_date(entity_id, pred_code, dt, precision, source_id, ev_id, confidence=0.9):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute("""INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_date, date_precision)
                   VALUES (%s,%s,%s,'DATE',%s,%s)""",
                (item_id, entity_id, PRED[pred_code], dt, precision))

def add_fact_numeric(entity_id, pred_code, val, source_id, ev_id, confidence=0.9, valid_from=None):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute("""INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_numeric, valid_from)
                   VALUES (%s,%s,%s,'NUMERIC',%s,%s)""",
                (item_id, entity_id, PRED[pred_code], val, valid_from))

def add_fact_code(entity_id, pred_code, code, taxonomy_term_id, source_id, ev_id, confidence=0.95):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute("""INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_code, value_taxonomy_term_id)
                   VALUES (%s,%s,%s,'CODE',%s,%s)""",
                (item_id, entity_id, PRED[pred_code], code, taxonomy_term_id))

def add_fact_money(entity_id, pred_code, amount, iso, year, source_id, ev_id, confidence=0.9):
    cid = CUR[iso]
    if iso == 'USD':
        norm_amount, norm_cid, rate = amount, cid, 1.0
    else:
        norm_amount, norm_cid, rate = round(amount * EUR_USD, 2), CUR['USD'], EUR_USD
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    vf = date(year, 1, 1) if year else None
    vt = date(year, 12, 31) if year else None
    cur.execute("""INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind,
                       value_numeric, currency_id, normalized_amount, normalized_currency_id,
                       exchange_rate, exchange_rate_date, valid_from, valid_to, date_precision)
                   VALUES (%s,%s,%s,'MONEY',%s,%s,%s,%s,%s,%s,%s,%s,'YEAR')""",
                (item_id, entity_id, PRED[pred_code], amount, cid, norm_amount, norm_cid, rate, TODAY, vf, vt))

def add_relationship(a_key, b_key, reltype_code, source_id, ev_id, confidence=0.9, description=None):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute("""INSERT INTO company_company_relationship(item_id, company_a_id, company_b_id, relationship_type_id, description)
                   VALUES (%s,%s,%s,%s,%s)""",
                (item_id, MAP[a_key], MAP[b_key], RELTYPE[reltype_code], description))

def add_person_company(person_key, company_key, reltype_code, role_title, source_id, ev_id, confidence=0.9):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute("""INSERT INTO person_company_relationship(item_id, person_id, company_id, relationship_type_id, role_title)
                   VALUES (%s,%s,%s,%s,%s)""",
                (item_id, MAP[person_key], MAP[company_key], RELTYPE[reltype_code], role_title))

MAP = {}

# =========================================================================
# AIRBUS
# =========================================================================
ev_se = make_evidence(SRC_ORIGIN1, "diffbot.company:Ec_P38BE4MX6mRCDh9NWh_A",
    "entityname='Airbus SE' fullname='AIRBUS SE' website=airbus.com founding=1970-12-18 "
    "ceoname='Guillaume Faury' ispublic=True; 28 yrs companyfinancials 1999-2025 EUR")
ev_sas = make_evidence(SRC_ORIGIN1, "diffbot.company:EXRo_XwecMo6TPp_x5JybaA",
    "entityname='Airbus' fullname='Airbus SAS' website=airbus.com founding=1970-12-18 "
    "ceoname='Guillaume Faury' ispublic=True; companyfinancials 2019 11.05B USD (conflicting)")
ev_merge_airbus = make_evidence(SRC_ANALYST, "manual-merge:airbus-se+sas",
    "Airbus SE (Ec_P38...) and Airbus/Airbus SAS (EXRo_X...) share CEO (Guillaume Faury), "
    "founding date (1970-12-18) and 20+ identical subsidiary names in diffbot.companyrelation "
    "(Airbus Defence and Space, Airbus Helicopters, Satair, Airbus Military, Airbus Operations "
    "S.A.S., Metron Aviation, Sepang Aircraft Engineering, Navtech...) -> same real corporate "
    "group (SE = holding entity, SAS = French operating subsidiary), merged as one golden entity.")

new_company("airbus", [ev_se, ev_sas])
add_name(MAP["airbus"], "Airbus", "PREFERRED", SRC_ORIGIN1, ev_merge_airbus)
add_name(MAP["airbus"], "Airbus SE", "LEGAL", SRC_ORIGIN1, ev_se)
add_name(MAP["airbus"], "Airbus SAS", "TRADE", SRC_ORIGIN1, ev_sas)
add_fact_text(MAP["airbus"], "company.website", "airbus.com", SRC_ORIGIN1, ev_se, 0.95)
add_fact_date(MAP["airbus"], "company.founded_date", date(1970,12,18), "DAY", SRC_ORIGIN1, ev_se, 0.95)
add_fact_code(MAP["airbus"], "company.status", "ACTIVE", STATUS["ACTIVE"], SRC_ORIGIN1, ev_se, 0.95)
add_fact_text(MAP["airbus"], "company.description",
    "Pan-European aerospace and defence group headquartered in Leiden, Netherlands (SE) with "
    "principal operations in Toulouse, France (SAS); the world's largest airliner manufacturer "
    "alongside Boeing.", SRC_ANALYST, ev_merge_airbus, 0.85)
for yr, amt in [(1999,22553000000),(2000,19427000000),(2001,30798000000),(2002,29901000000),
    (2003,30133000000),(2004,31761000000),(2005,34206000000),(2006,39434000000),(2007,39123000000),
    (2008,43265000000),(2009,42822000000),(2010,45752000000),(2011,49128000000),(2012,56480000000),
    (2013,59256000000),(2014,60713000000),(2015,64450000000),(2016,66581000000),(2017,66767000000),
    (2018,63707000000),(2019,70478000000),(2020,49912000000),(2021,52149000000),(2022,58763000000),
    (2023,65446000000),(2024,69230000000),(2025,73420000000)]:
    add_fact_money(MAP["airbus"], "company.revenue", amt, "EUR", yr, SRC_ORIGIN1, ev_se, 0.9)
# conflicting 2019 figure from the SAS-side record -- schema-correct: kept, not overwritten
add_fact_money(MAP["airbus"], "company.revenue", 11050000000, "USD", 2019, SRC_ORIGIN1, ev_sas, 0.4)

# Airbus people
new_person("guillaume_faury")
add_name(MAP["guillaume_faury"], "Guillaume Faury", "PREFERRED", SRC_ORIGIN1, ev_se)
add_person_company("guillaume_faury", "airbus", "LEADS", "Chief Executive Officer", SRC_ORIGIN1, ev_se, 0.95)

for key, pname in [("lathiere","Bernard Lathière"), ("beteille","Roger Béteille"),
                    ("strauss","Franz Josef Strauß"), ("kracht","Felix Kracht"),
                    ("ziegler","Henri Ziegler")]:
    new_person(key)
    add_name(MAP[key], pname, "PREFERRED", SRC_ORIGIN1, ev_sas)
    add_person_company(key, "airbus", "FOUNDED", "Founder", SRC_ORIGIN1, ev_sas, 0.85)

AIRBUS_SUBS = [
    # key, names(list of (name,type)), website, founded(date,prec)|None, ceo|None, description,
    # origin_source, origin_locator, confidence
    ("airbus_ds", [("Airbus Defence and Space","PREFERRED")], "airbus.com", (date(2006,8,17),"DAY"), None,
     "Defence, space and security division; folds 3 diffbot record variants (Madrid/Toulouse/"
     "Huntsville pages) of the same 2006-formed entity (predecessor: EADS Astrium, 1923 roots).",
     SRC_ORIGIN1, "diffbot.company: EAUKVFaonPKaQxfpQ-VvVVQ, EDDUe8qVMPGGw9gv8IMQwwg, EkA0l0EETOFKzdbAa-DZXmg", 0.95),
    ("airbus_ds_intel", [("Airbus Defence and Space - Intelligence","PREFERRED")], "intelligence-airbusds.com",
     (date(1986,1,1),"YEAR"), None, "Geospatial intelligence arm of Airbus Defence and Space.",
     SRC_ORIGIN1, "diffbot.company:EhAeIXk95OxKA0P0gpFPR5Q", 0.8),
    ("airbus_us_sd", [("Airbus US Space & Defense","PREFERRED")], "airbusus.com", (date(2019,1,1),"YEAR"),
     "Robert Geckle Jr", "US subsidiary of Airbus Defence and Space (Arlington, VA).",
     SRC_ORIGIN1, "diffbot.company:E8SWTOHnJPMO2s9080CYkzg", 0.85),
    ("airbus_helicopters", [("Airbus Helicopters","PREFERRED")], "airbus.com/helicopters.html", None, None,
     "World's largest helicopter manufacturer by volume; Herndon, VA HQ (US arm) shown here.",
     SRC_ORIGIN2, "testzoominfo.Company:365384179", 0.95),
    ("airbus_helicopters_uk", [("Airbus Helicopters UK","PREFERRED")], "airbushelicopters.co.uk", None, None,
     "UK civil helicopter operations, Oxford-headquartered.",
     SRC_ORIGIN2, "testzoominfo.Company:359361969", 0.8),
    ("airbus_helicopters_fdn", [("Airbus Helicopters Foundation","PREFERRED")], "airbusgroupap.com.au", None, None,
     "Nonprofit/foundation arm, Australia.", SRC_ORIGIN2, "testzoominfo.Company:1102392658", 0.7),
    ("airbus_secure_comms", [("Airbus Secure Communications","PREFERRED")], "securecommunications-airbusds.com",
     None, None, "Satcom (Skynet 5 MilSatCom) business, part of Airbus Defence and Space.",
     SRC_ORIGIN2, "testzoominfo.Company:507376090", 0.75),
    ("airbus_ds_houston", [("Airbus DS Houston","PREFERRED")], "airbusdshouston.com", None, None,
     "Simulator/software support for NASA Sonny Carter Training Facility.",
     SRC_ORIGIN2, "testzoominfo.Company:368973570", 0.8),
    ("airbus_ds_geo_au", [("Airbus Ds Geo Australia","PREFERRED")], "geo-airbusds.com", None, None,
     "Geospatial arm, Canberra.", SRC_ORIGIN2, "testzoominfo.Company:1142580865", 0.75),
    ("airbus_aerial", [("Airbus Aerial","PREFERRED")], "airbusaerial.com", None, None,
     "Aerial data/drone imagery services.", SRC_ORIGIN2, "testzoominfo.Company:450171088", 0.9),
    ("airbus_utm", [("Airbus UTM","PREFERRED")], "airbusutm.com", None, None,
     "Unmanned traffic management (UTM) digital air-traffic solutions.",
     SRC_ORIGIN2, "testzoominfo.Company:471447426", 0.6),
    ("airbus_bank", [("Airbus Bank","PREFERRED")], "airbusbank.com", None, None,
     "In-house financial services arm, Munich.", SRC_ORIGIN2, "testzoominfo.Company:449306384", 0.9),
    ("airbus_turkiye", [("Airbus Turkiye","PREFERRED")], "airbusturkiye.com", None, None,
     "Turkey regional office.", SRC_ORIGIN2, "testzoominfo.Company:538878747", 0.6),
    ("airbus_a3", [("Airbus (A3 Silicon Valley)","PREFERRED")], "airbus-sv.com", None, None,
     "A3: Airbus's Silicon Valley innovation center.", SRC_ORIGIN2, "testzoominfo.Company:1107110998", 0.65),
    ("airbus_group_inc", [("Airbus Group, Inc.","PREFERRED")], "airbusgroup.com", None, None,
     "US holding shell; named explicitly in Airbus SE's own asserted subsidiary list "
     "(diffbot.companyrelation), but its $51.8M/499-employee financial profile does not match "
     "the historic EADS/Airbus Group conglomerate -- kept as a low-confidence subsidiary, "
     "flagged as needing verification rather than silently merged into the parent.",
     SRC_ORIGIN2, "testzoominfo.Company:1115568140", 0.55),
]

for key, names, website, founded, ceo, desc, src, locator, conf in AIRBUS_SUBS:
    ev = make_evidence(src, locator, desc)
    new_company(key, [ev])
    for nm, nt in names:
        add_name(MAP[key], nm, nt, src, ev)
    if website:
        add_fact_text(MAP[key], "company.website", website, src, ev, conf)
    if founded:
        add_fact_date(MAP[key], "company.founded_date", founded[0], founded[1], src, ev, conf)
    add_fact_text(MAP[key], "company.description", desc, src, ev, conf)
    add_fact_code(MAP[key], "company.status", "ACTIVE", STATUS["ACTIVE"], src, ev, conf)
    add_relationship("airbus", key, "SUBSIDIARY_OF", SRC_ANALYST, ev_merge_airbus, conf,
                      f"Hand-classified subsidiary of golden Airbus; see evidence at {locator}")
    if ceo:
        pkey = f"ceo_{key}"
        new_person(pkey)
        add_name(MAP[pkey], ceo, "PREFERRED", src, ev)
        add_person_company(pkey, key, "LEADS", "Chief Executive Officer", src, ev, conf)

# Airbus Helicopters revenue/employee facts (real numbers from ClickHouse)
add_fact_money(MAP["airbus_helicopters"], "company.revenue", 6704208000, "USD", None, SRC_ORIGIN2,
                make_evidence(SRC_ORIGIN2,"testzoominfo.Company:365384179","companyRevenueIn000s=6704208"), 0.85)
add_fact_numeric(MAP["airbus_helicopters"], "company.employee_count", 19081, SRC_ORIGIN2,
                make_evidence(SRC_ORIGIN2,"testzoominfo.Company:365384179","companyEmployees=19081"), 0.85)

conn.commit()
print("AIRBUS done:", len(MAP), "entities so far")

# =========================================================================
# BOEING
# =========================================================================
ev_boeing_pg = make_evidence(SRC_ORIGIN1, "diffbot.company:EMAakygGHMUiRPfT6DPOr6Q",
    "entityname='Boeing' fullname='The Boeing Company' website=boeing.com founding=1916-07-15 "
    "ceoname='Robert K. Ortberg' ispublic=True; companyfinancials 2009-2025 USD; "
    "companyinvestmentround 2019-2025 (bond/debt issuances, not VC)")
ev_boeing_ch = make_evidence(SRC_ORIGIN2, "testzoominfo.Company:5128546",
    "companyName='Boeing' companyDescription confirms 'founded in 1916 ... headquartered in "
    "Chicago', companyRevenueIn000s=61060002 (~$61.1B), companyEmployees=142000, "
    "companyTicker='NYSE: BA'")
ev_boeing_alias1 = make_evidence(SRC_ORIGIN1, "diffbot.company:E_uWiYfWHN1m781cl490HSw",
    "entityname='Boeing Co. The', no website, founding=2012 (conflicts with 1916), "
    "location=St. Charles County MO -- low-confidence duplicate scrape of the same company")
ev_boeing_alias2 = make_evidence(SRC_ORIGIN1, "diffbot.company:EhuDKH94UOvS95l8YNOUM3g",
    "entityname='Boeing' fullname='Boeing Co', website=boeing.com, founding=1972 (conflicts "
    "with 1916), location=Oklahoma City -- low-confidence duplicate scrape, real Boeing OKC site")
ev_merge_boeing = make_evidence(SRC_ANALYST, "manual-merge:boeing-family",
    "diffbot EMAakygGHMUiRPfT6DPOr6Q and ClickHouse 5128546 share name, website (boeing.com), "
    "founding year (1916) and 80+ identical asserted subsidiary names (Boeing UK, Boeing "
    "Australia, Boeing Satellite Systems, Boeing Commercial Airplanes, Boeing Defense Space & "
    "Security, Aurora Flight Sciences...) -> same real company. 'Boeing Co. The' and 'Boeing Co' "
    "(no website, conflicting founding years, non-HQ locations) merged in as low-confidence name "
    "variants, not verified as genuinely distinct entities.")

new_company("boeing", [ev_boeing_pg, ev_boeing_ch])
add_name(MAP["boeing"], "Boeing", "PREFERRED", SRC_ORIGIN1, ev_merge_boeing)
add_name(MAP["boeing"], "The Boeing Company", "LEGAL", SRC_ORIGIN1, ev_boeing_pg)
add_fact_text(MAP["boeing"], "company.website", "boeing.com", SRC_ORIGIN1, ev_boeing_pg, 0.95)
add_fact_date(MAP["boeing"], "company.founded_date", date(1916,7,15), "DAY", SRC_ORIGIN1, ev_boeing_pg, 0.95)
add_fact_code(MAP["boeing"], "company.status", "ACTIVE", STATUS["ACTIVE"], SRC_ORIGIN1, ev_boeing_pg, 0.95)
add_fact_text(MAP["boeing"], "company.ticker_symbol", "NYSE:BA", SRC_ORIGIN2, ev_boeing_ch, 0.95)
add_fact_numeric(MAP["boeing"], "company.employee_count", 142000, SRC_ORIGIN2, ev_boeing_ch, 0.8)
add_fact_text(MAP["boeing"], "company.description",
    "American aerospace giant headquartered in Arlington, VA; designs, manufactures and "
    "services commercial jetliners, defense/space systems and services worldwide (segments: "
    "Commercial Airplanes; Defense, Space & Security; Global Services).",
    SRC_ANALYST, ev_merge_boeing, 0.9)
for yr, amt in [(2009,68281000000),(2010,64306000000),(2011,68735000000),(2012,81698000000),
    (2013,86623000000),(2014,90762000000),(2015,96114000000),(2016,93496000000),(2017,94005000000),
    (2018,101127000000),(2019,76559000000),(2020,58158000000),(2021,62286000000),(2022,66608000000),
    (2023,77794000000),(2024,66516999999),(2025,89463000000)]:
    add_fact_money(MAP["boeing"], "company.revenue", amt, "USD", yr, SRC_ORIGIN1, ev_boeing_pg, 0.9)
# ClickHouse's approximate $61.1B figure -- unspecified year, kept as a lower-confidence
# corroborating (not overwriting) observation, per the schema's designed conflict retention.
add_fact_money(MAP["boeing"], "company.revenue", 61060002000, "USD", None, SRC_ORIGIN2, ev_boeing_ch, 0.5)
# low-confidence alias records folded in as alternate names only, not separate entities
add_name(MAP["boeing"], "Boeing Co. The", "FORMER", SRC_ORIGIN1, ev_boeing_alias1, )
add_name(MAP["boeing"], "Boeing Co", "FORMER", SRC_ORIGIN1, ev_boeing_alias2)

new_person("ortberg")
add_name(MAP["ortberg"], "Robert K. Ortberg", "PREFERRED", SRC_ORIGIN1, ev_boeing_pg)
add_person_company("ortberg", "boeing", "LEADS", "Chief Executive Officer", SRC_ORIGIN1, ev_boeing_pg, 0.95)
new_person("w_e_boeing")
add_name(MAP["w_e_boeing"], "William E. Boeing", "PREFERRED", SRC_ORIGIN1, ev_boeing_pg)
add_person_company("w_e_boeing", "boeing", "FOUNDED", "Founder", SRC_ORIGIN1, ev_boeing_pg, 0.9)

BOEING_SUBS = [
    ("boeing_uk", [("Boeing United Kingdom","PREFERRED")], "boeing.co.uk", (date(2002,1,17),"DAY"), None,
     "SUBSIDIARY_OF", "UK subsidiary, London.", SRC_ORIGIN1, "diffbot.company:E-xSjHM5bMJmwwRfrYLwwkw", 0.85),
    ("boeing_china", [("Boeing China","PREFERRED")], "boeing.cn", (date(1916,1,1),"YEAR"), None,
     "SUBSIDIARY_OF", "Boeing (China) Investment Co., Ltd.", SRC_ORIGIN1, "diffbot.company:E6uBhKw_dMey3iYvRy-XfCg", 0.7),
    ("boeing_bds", [("Boeing Defense, Space & Security","PREFERRED")], "boeing.com/defense",
     (date(2002,10,6),"DAY"), "Steve Parker", "SUBSIDIARY_OF",
     "Defense/space/security segment; folds 4 diffbot record variants (Boeing Defense & Space "
     "Group / Boeing Defense Space & Security / BOEING INTEGRATED DEFENSE SY / the odd "
     "'...Information Space & Defense Systems' concatenated record) -- same segment renamed "
     "over time, confirmed by exact name match in Boeing's own asserted subsidiary list.",
     SRC_ORIGIN1, "diffbot.company: EB_GpheMeNNG-R_Ou7ddl9w, EBanS_ZoLMsOAvHBEm1GkMQ, EayiVtq3hPUO814SFTtIK8A, ER9LshRnwPEKwMTlJO9Ym5w", 0.9),
    ("boeing_satellite", [("Boeing Satellite Systems","PREFERRED")], "boeing.com", (date(1995,4,10),"DAY"),
     None, "SUBSIDIARY_OF", "Formerly Hughes Space and Communications; El Segundo, CA.",
     SRC_ORIGIN1, "diffbot.company:EDcF9TDA_Psei4p989FcoEQ", 0.85),
    ("boeing_nas", [("Boeing North American Services","PREFERRED")], "boeing.com", (date(1996,1,1),"YEAR"),
     None, "SUBSIDIARY_OF", "North American services arm.", SRC_ORIGIN1, "diffbot.company:EU4CF3A1bPzS9bhNZkFlvwg", 0.6),
    ("boeing_cas", [("Boeing Commercial Airplanes","PREFERRED")], "boeing.com", (date(1916,1,1),"YEAR"),
     None, "SUBSIDIARY_OF", "Commercial jetliner segment; folds diffbot + ClickHouse duplicate "
     "records, confirmed by exact name match in subsidiary list.", SRC_ORIGIN1,
     "diffbot.company:Egy04D8nNNc-VcFTFZ_QW6A + testzoominfo.Company:1310469572", 0.9),
    ("boeing_mesa_fire", [("Boeing Mesa Fire Department","PREFERRED")], None, None, None, "SUBSIDIARY_OF",
     "Internal facility fire department at the Mesa, AZ plant -- likely not a distinct legal "
     "company at all, kept at low confidence as an internal org unit, not verified as a subsidiary.",
     SRC_ORIGIN1, "diffbot.company:E17cdTqqHO5y1wMmj4p50tg", 0.4),
    ("boeing_india", [("Boeing International Corporation India Private Limited","PREFERRED")], "boeing.co.in",
     (date(2003,1,1),"YEAR"), None, "SUBSIDIARY_OF", "India subsidiary, New Delhi; confirmed by "
     "exact name match in Boeing's own asserted subsidiary list.", SRC_ORIGIN2,
     "testzoominfo.Company:562685928", 0.9),
    ("boeing_australia", [("Boeing Australia","PREFERRED")], "boeing.com.au", None, None, "SUBSIDIARY_OF",
     "Australia operations; confirmed via 'Boeing Australia'/'Boeing Defence Australia' in "
     "subsidiary list.", SRC_ORIGIN2, "testzoominfo.Company:559785723", 0.8),
    ("boeing_vancouver", [("Boeing Vancouver","PREFERRED")], "boeingvancouver.com", None, None,
     "SUBSIDIARY_OF", "Canada (Richmond, BC) operations.", SRC_ORIGIN2, "testzoominfo.Company:1268015193", 0.6),
    ("boeing_aurora", [("Aurora Flight Sciences (Boeing Aerospace & Autonomy Center)","PREFERRED")], None,
     None, None, "SUBSIDIARY_OF", "Aurora Flight Sciences, wholly-owned Boeing subsidiary, "
     "operates the Cambridge MA Aerospace & Autonomy Center -- confirmed explicitly in source text.",
     SRC_ORIGIN2, "testzoominfo.Company:1212960306", 0.85),
    ("boeing_travel", [("Boeing Travel Management","PREFERRED")], None, None, None, "SUBSIDIARY_OF",
     "Internal corporate travel department, Huntington Beach CA -- likely not a distinct legal "
     "company.", SRC_ORIGIN2, "testzoominfo.Company:1226675440", 0.4),
    # Joint ventures / partnerships, NOT subsidiaries
    ("aviation_partners_boeing", [("Aviation Partners Boeing","PREFERRED")], "aviationpartnersboeing.com",
     (date(1999,1,1),"YEAR"), None, "PARTNERS_WITH", "50/50 JV of Aviation Partners Inc. and "
     "Boeing for Blended Winglet technology.", SRC_ORIGIN2, "testzoominfo.Company:1163680190", 0.9),
    ("bapas", [("Boeing Asia Pacific Aviation Services (BAPAS)","PREFERRED")], "bapas-sg.com", None, None,
     "PARTNERS_WITH", "JV between Boeing and SIA Engineering Company.", SRC_ORIGIN2,
     "testzoominfo.Company:455620818", 0.9),
    ("ati_boeing", [("ATI Boeing Accelerator","PREFERRED")], "atiboeingaccelerator.com", None, None,
     "PARTNERS_WITH", "UK Aerospace Technology Institute accelerator program partnered with "
     "Boeing.", SRC_ORIGIN2, "testzoominfo.Company:482339782", 0.7),
    # Employee-affiliated orgs, kept at very low confidence, flagged as questionable "companies"
    ("spirit_boeing_assoc", [("Spirit Boeing Employees' Association","PREFERRED")], "beatoday.com", None,
     None, "SUBSIDIARY_OF", "Employee-run non-profit association, not a real corporate "
     "subsidiary -- kept at low confidence, flagged.", SRC_ORIGIN2, "testzoominfo.Company:359197544", 0.25),
    ("boeing_flying_assoc", [("Boeing Employees Flying Association","PREFERRED")], "befa.org", None, None,
     "SUBSIDIARY_OF", "Employee recreation club, not a real corporate subsidiary -- flagged.",
     SRC_ORIGIN2, "testzoominfo.Company:16224339", 0.25),
    ("boeing_tennis_club", [("Boeing Employees Tennis Club","PREFERRED")], "betconline.net", None, None,
     "SUBSIDIARY_OF", "Employee recreation club, not a real corporate subsidiary -- flagged. "
     "(This is the exact record whose '$1 Million' revenue string crashed merge-engine's numeric "
     "load in the July 2026 diagnosis.)", SRC_ORIGIN2, "testzoominfo.Company:18143512", 0.25),
]

for key, names, website, founded, ceo, reltype, desc, src, locator, conf in BOEING_SUBS:
    ev = make_evidence(src, locator, desc)
    new_company(key, [ev])
    for nm, nt in names:
        add_name(MAP[key], nm, nt, src, ev)
    if website:
        add_fact_text(MAP[key], "company.website", website, src, ev, conf)
    if founded:
        add_fact_date(MAP[key], "company.founded_date", founded[0], founded[1], src, ev, conf)
    add_fact_text(MAP[key], "company.description", desc, src, ev, conf)
    add_fact_code(MAP[key], "company.status", "ACTIVE", STATUS["ACTIVE"], src, ev, conf)
    add_relationship("boeing", key, reltype, SRC_ANALYST, ev_merge_boeing, conf,
                      f"Hand-classified {reltype} of golden Boeing; see evidence at {locator}")
    if ceo:
        pkey = f"ceo_{key}"
        new_person(pkey)
        add_name(MAP[pkey], ceo, "PREFERRED", src, ev)
        add_person_company(pkey, key, "LEADS", "Chief Executive Officer / President", src, ev, conf)

# Boeing Defense Space & Security segment revenue (real numbers)
for yr, amt in [(2016,20180000000),(2017,20561000000),(2018,23195000000)]:
    add_fact_money(MAP["boeing_bds"], "company.revenue", amt, "USD", yr, SRC_ORIGIN1,
                    make_evidence(SRC_ORIGIN1,"diffbot.companyfinancials:EBanS_ZoLMsOAvHBEm1GkMQ",f"{yr} segment revenue"), 0.85)

conn.commit()
print("BOEING done. total entities:", len(MAP))
cur.close()
conn.close()
print("ALL COMMITTED OK")
