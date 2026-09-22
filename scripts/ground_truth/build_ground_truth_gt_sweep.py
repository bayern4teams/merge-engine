#!/usr/bin/env python3
"""
Hand-built golden/ground-truth Thuraya + OHB SE + Capella Space entity
resolution, loaded into the `ground_truth` PostgreSQL database (kg schema),
alongside the existing Boeing/Airbus ground truth from build_ground_truth.py.

Same discipline as that script: an analyst's hand merge of the REAL origin
data (testdiffbot Postgres + testzoominfo ClickHouse), every company/fact/
relationship traceable to a real origin row (companyid/companyID cited in
origin_ref), confidence scores reflecting actual certainty. These three
subjects were picked (2026-09-15 ground-truth sweep) by exact website-domain
match between origins rather than by name -- Boeing/Airbus's own false
positives (see report §4/§00a) are exactly what name-based picking risks.
Each subject's real-world decoy (confirmed during that sweep) is included as
its own standalone entity, unlinked to the golden company, the same pattern
build_ground_truth.py used for "Monterey Airbus".
"""

from datetime import date

import psycopg2

DSN = "host=localhost port=5434 dbname=ground_truth user=postgres password=supersecurepassword"
TODAY = date(2026, 9, 15)
EUR_USD = 1.08  # same flat approximation as build_ground_truth.py, documented simplification

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

cur.execute("SELECT predicate_id, code FROM fact_predicate")
PRED = {code: pid for pid, code in cur.fetchall()}

# ---- sources -------------------------------------------------------------
cur.execute(
    "SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='source_type' AND tt.code='DATABASE'"
)
DB_SOURCE_TYPE = cur.fetchone()[0]
cur.execute(
    "SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='source_type' AND tt.code='MANUAL'"
)
MANUAL_SOURCE_TYPE = cur.fetchone()[0]
cur.execute(
    "SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='evidence_type' AND tt.code='TABLE_ROW'"
)
EVIDENCE_TYPE_ROW = cur.fetchone()[0]


def make_source(title, uri, reliability):
    cur.execute(
        """INSERT INTO source(source_type_term_id, uri, title, publisher, reliability_score, retrieved_at)
                   VALUES (%s,%s,%s,%s,%s, now()) RETURNING source_id""",
        (
            DB_SOURCE_TYPE if "origin" in title.lower() else MANUAL_SOURCE_TYPE,
            uri,
            title,
            "merge-engine ground-truth sweep 2026-09-15",
            reliability,
        ),
    )
    return cur.fetchone()[0]


SRC_ORIGIN1 = make_source(
    "origin1: PostgreSQL testdiffbot (diffbot schema)",
    "postgresql://localhost:5433/testdiffbot",
    0.70,
)
SRC_ORIGIN2 = make_source(
    "origin2: ClickHouse testzoominfo", "clickhouse://localhost:8125/testzoominfo", 0.70
)
SRC_ANALYST = make_source(
    "Analyst hand-merge judgment (ground-truth sweep, 2026-09-15)", None, 0.85
)


def make_evidence(source_id, locator, snippet):
    cur.execute(
        """INSERT INTO evidence(source_id, evidence_type_term_id, locator, text_snippet)
                   VALUES (%s,%s,%s,%s) RETURNING evidence_id""",
        (source_id, EVIDENCE_TYPE_ROW, locator, snippet),
    )
    return cur.fetchone()[0]


def link_evidence(item_id, evidence_id, primary=True):
    cur.execute(
        "INSERT INTO knowledge_item_evidence(item_id, evidence_id, is_primary) VALUES (%s,%s,%s)",
        (item_id, evidence_id, primary),
    )


def new_entity(etype):
    cur.execute("INSERT INTO entity(entity_type) VALUES (%s) RETURNING entity_id", (etype,))
    return cur.fetchone()[0]


def new_company(key, evidence_ids, confidence=0.9):
    eid = new_entity("COMPANY")
    cur.execute("INSERT INTO organization(organization_id) VALUES (%s)", (eid,))
    cur.execute("INSERT INTO company(company_id) VALUES (%s)", (eid,))
    MAP[key] = eid
    return eid


def new_person(key):
    eid = new_entity("PERSON")
    cur.execute("INSERT INTO person(person_id) VALUES (%s)", (eid,))
    MAP[key] = eid
    return eid


def new_item(source_id, confidence, extractor="analyst-hand-merge", supersedes=None):
    cur.execute(
        """INSERT INTO knowledge_item(item_type,source_id,confidence,extractor_name,extractor_version)
                   VALUES ('FACT',%s,%s,%s,'2026-09-15') RETURNING item_id""",
        (source_id, confidence, extractor),
    )
    return cur.fetchone()[0]


def add_name(entity_id, name, name_type, source_id, ev_id, valid_from=None):
    cur.execute(
        """INSERT INTO entity_name(entity_id,name,normalized_name,language_code,name_type_term_id,valid_from)
                   VALUES (%s,%s,%s,'en',%s,%s) RETURNING item_id""",
        (entity_id, name, name.lower(), NAMETYPE[name_type], valid_from),
    )


def add_fact_text(
    entity_id, pred_code, text, source_id, ev_id, confidence=0.9, valid_from=None, valid_to=None
):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute(
        """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_text, valid_from, valid_to)
                   VALUES (%s,%s,%s,'TEXT',%s,%s,%s)""",
        (item_id, entity_id, PRED[pred_code], text, valid_from, valid_to),
    )


def add_fact_date(entity_id, pred_code, dt, precision, source_id, ev_id, confidence=0.9):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute(
        """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_date, date_precision)
                   VALUES (%s,%s,%s,'DATE',%s,%s)""",
        (item_id, entity_id, PRED[pred_code], dt, precision),
    )


def add_fact_numeric(entity_id, pred_code, val, source_id, ev_id, confidence=0.9, valid_from=None):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute(
        """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_numeric, valid_from)
                   VALUES (%s,%s,%s,'NUMERIC',%s,%s)""",
        (item_id, entity_id, PRED[pred_code], val, valid_from),
    )


def add_fact_code(entity_id, pred_code, code, taxonomy_term_id, source_id, ev_id, confidence=0.95):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute(
        """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_code, value_taxonomy_term_id)
                   VALUES (%s,%s,%s,'CODE',%s,%s)""",
        (item_id, entity_id, PRED[pred_code], code, taxonomy_term_id),
    )


def add_fact_money(entity_id, pred_code, amount, iso, year, source_id, ev_id, confidence=0.9):
    cid = CUR[iso]
    if iso == "USD":
        norm_amount, norm_cid, rate = amount, cid, 1.0
    else:
        norm_amount, norm_cid, rate = round(amount * EUR_USD, 2), CUR["USD"], EUR_USD
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    vf = date(year, 1, 1) if year else None
    vt = date(year, 12, 31) if year else None
    cur.execute(
        """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind,
                       value_numeric, currency_id, normalized_amount, normalized_currency_id,
                       exchange_rate, exchange_rate_date, valid_from, valid_to, date_precision)
                   VALUES (%s,%s,%s,'MONEY',%s,%s,%s,%s,%s,%s,%s,%s,'YEAR')""",
        (
            item_id,
            entity_id,
            PRED[pred_code],
            amount,
            cid,
            norm_amount,
            norm_cid,
            rate,
            TODAY,
            vf,
            vt,
        ),
    )


def add_relationship(
    a_key, b_key, reltype_code, source_id, ev_id, confidence=0.9, description=None
):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute(
        """INSERT INTO company_company_relationship(item_id, company_a_id, company_b_id, relationship_type_id, description)
                   VALUES (%s,%s,%s,%s,%s)""",
        (item_id, MAP[a_key], MAP[b_key], RELTYPE[reltype_code], description),
    )


def add_person_company(
    person_key, company_key, reltype_code, role_title, source_id, ev_id, confidence=0.9
):
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute(
        """INSERT INTO person_company_relationship(item_id, person_id, company_id, relationship_type_id, role_title)
                   VALUES (%s,%s,%s,%s,%s)""",
        (item_id, MAP[person_key], MAP[company_key], RELTYPE[reltype_code], role_title),
    )


MAP = {}

# =========================================================================
# THURAYA
# =========================================================================
ev_thuraya_diffbot = make_evidence(
    SRC_ORIGIN1,
    "diffbot.company:Eif_jUgzxOoWHqb_7EphYeQ",
    "entityname='Thuraya Telecommunications' website=thuraya.com founding=1997-04-01 "
    "ceoname='Sulaiman Al Ali' ispublic=False; 10 companyexperience rows, 45 companytechnographics rows",
)
ev_thuraya_zoominfo = make_evidence(
    SRC_ORIGIN2,
    "testzoominfo.Company:345715253",
    "companyName='Thuraya' companyDomain=www.thuraya.com companyEmployees=220 "
    "companyRevenue=$67.2M companyType=PRIVATE; 28 Experience rows",
)
ev_thuraya_merge = make_evidence(
    SRC_ANALYST,
    "manual-merge:thuraya-diffbot+zoominfo",
    "Exact website-domain match (thuraya.com / www.thuraya.com) across both origins, both "
    "describing the same 1997-founded, Dubai-headquartered mobile satellite operator -> same "
    "real company, merged as one golden entity. This pair auto-merged correctly in the app's "
    "own builtin-engine run (wf_f26f4144) but was NOT auto-merged under Splink (wf_4e27e8de, "
    "0 of 7 candidate clusters merged) -- see the audit artifact §00d.",
)
new_company("thuraya", [ev_thuraya_diffbot, ev_thuraya_zoominfo])
add_name(MAP["thuraya"], "Thuraya", "PREFERRED", SRC_ORIGIN2, ev_thuraya_zoominfo)
add_name(MAP["thuraya"], "Thuraya Telecommunications", "LEGAL", SRC_ORIGIN1, ev_thuraya_diffbot)
add_name(
    MAP["thuraya"], "Thuraya Telecommunications Company", "TRADE", SRC_ORIGIN1, ev_thuraya_diffbot
)
add_fact_text(
    MAP["thuraya"], "company.website", "thuraya.com", SRC_ORIGIN1, ev_thuraya_diffbot, 0.95
)
add_fact_date(
    MAP["thuraya"],
    "company.founded_date",
    date(1997, 4, 1),
    "DAY",
    SRC_ORIGIN1,
    ev_thuraya_diffbot,
    0.9,
)
add_fact_code(
    MAP["thuraya"],
    "company.status",
    "ACTIVE",
    STATUS["ACTIVE"],
    SRC_ORIGIN1,
    ev_thuraya_diffbot,
    0.95,
)
add_fact_text(
    MAP["thuraya"],
    "company.description",
    "UAE-based mobile satellite services provider; a subsidiary of Al Yah Satellite "
    "Communications Company (Yahsat), itself part of Mubadala Investment Company. Offers "
    "satellite voice, SMS, data and roaming across two-thirds of the globe, serving maritime, "
    "energy, government, aviation, media and humanitarian sectors.",
    SRC_ANALYST,
    ev_thuraya_merge,
    0.85,
)
add_fact_numeric(
    MAP["thuraya"], "company.employee_count", 220, SRC_ORIGIN2, ev_thuraya_zoominfo, 0.75, TODAY
)
add_fact_money(
    MAP["thuraya"], "company.revenue", 67229000, "USD", 2025, SRC_ORIGIN2, ev_thuraya_zoominfo, 0.6
)

new_person("sulaiman_al_ali")
add_name(MAP["sulaiman_al_ali"], "Sulaiman Al Ali", "PREFERRED", SRC_ORIGIN1, ev_thuraya_diffbot)
add_person_company(
    "sulaiman_al_ali",
    "thuraya",
    "LEADS",
    "Chief Executive Officer",
    SRC_ORIGIN1,
    ev_thuraya_diffbot,
    0.85,
)

# Decoy, unlinked -- confirmed NOT the same company (different domain), same pattern as
# build_ground_truth.py's "Monterey Airbus".
ev_thuraya_kz = make_evidence(
    SRC_ORIGIN1,
    "diffbot.company:EW-cM595xNjS5v41pCDvgwQ",
    "entityname='THURAYA' website=thuraya-kazakhstan.kz -- a Kazakhstan reseller ('represented "
    "in Kazakhstan by TOO ZHARYQ'), not the real Thuraya operator. Confirmed distinct: scored "
    "0.93 similarity to the real entity by the app's own matcher (wf_f26f4144) and correctly "
    "kept separate at the human_review_matches gate.",
)
new_company("thuraya_kz_decoy", [ev_thuraya_kz])
add_name(
    MAP["thuraya_kz_decoy"],
    "THURAYA (Kazakhstan reseller)",
    "PREFERRED",
    SRC_ORIGIN1,
    ev_thuraya_kz,
)
add_fact_text(
    MAP["thuraya_kz_decoy"],
    "company.website",
    "thuraya-kazakhstan.kz",
    SRC_ORIGIN1,
    ev_thuraya_kz,
    0.9,
)
add_fact_text(
    MAP["thuraya_kz_decoy"],
    "company.description",
    "Kazakhstan-based reseller of Thuraya satellite communications equipment/services, a "
    "distinct legal entity from Thuraya Telecommunications itself.",
    SRC_ANALYST,
    ev_thuraya_kz,
    0.8,
)

# =========================================================================
# OHB SE
# =========================================================================
ev_ohb_diffbot = make_evidence(
    SRC_ORIGIN1,
    "diffbot.company:Ei7Rdy8jOPwSaA07eN7gnpw",
    "entityname='Ohb Se' fullname='OHB SE' website=ohb.de founding=1981 ceoname='Marco R Fuchs' "
    "ispublic=True; 6 companyfinancials rows 2020-2025 EUR, 12 companyexperience, "
    "companyrelation asserts 'OHB System AG' as subsidiary at confidence 0.87",
)
ev_ohb_zoominfo = make_evidence(
    SRC_ORIGIN2,
    "testzoominfo.Company:28656225",
    "companyName='OHB System' companyDomain=www.ohb.de companyEmployees=2984 "
    "companyRevenue=$955M companyType=PRIVATE; 8 Experience rows",
)
ev_ohb_merge = make_evidence(
    SRC_ANALYST,
    "manual-merge:ohb-diffbot+zoominfo",
    "Exact website-domain match (ohb.de / www.ohb.de) across both origins, both describing the "
    "same Bremen-headquartered, 1981-founded space and technology group -> same real company, "
    "merged as one golden entity. Auto-merged correctly under BOTH engines live "
    "(wf_f7531e65 builtin, wf_4ce0e118 splink) -- the cleanest of the three new subjects.",
)
new_company("ohb", [ev_ohb_diffbot, ev_ohb_zoominfo])
add_name(MAP["ohb"], "OHB SE", "PREFERRED", SRC_ORIGIN1, ev_ohb_diffbot)
add_name(MAP["ohb"], "OHB System", "TRADE", SRC_ORIGIN2, ev_ohb_zoominfo)
add_fact_text(MAP["ohb"], "company.website", "ohb.de", SRC_ORIGIN1, ev_ohb_diffbot, 0.95)
add_fact_date(
    MAP["ohb"], "company.founded_date", date(1981, 1, 1), "YEAR", SRC_ORIGIN1, ev_ohb_diffbot, 0.85
)
add_fact_code(
    MAP["ohb"], "company.status", "ACTIVE", STATUS["ACTIVE"], SRC_ORIGIN1, ev_ohb_diffbot, 0.95
)
add_fact_text(
    MAP["ohb"],
    "company.description",
    "German space and technology group headquartered in Bremen; one of the three major space "
    "systems companies in Europe. Operates through Space Systems, Aerospace/Access-to-Space and "
    "Digital segments -- satellites, launcher components, and satellite-data services. Publicly "
    "listed (Germany's first space-sector listed company).",
    SRC_ANALYST,
    ev_ohb_merge,
    0.85,
)
add_fact_numeric(
    MAP["ohb"], "company.employee_count", 2984, SRC_ORIGIN2, ev_ohb_zoominfo, 0.75, TODAY
)
for yr, amt in [
    (2020, 880319000),
    (2021, 905001000),
    (2022, 944520000),
    (2023, 1047796000),
    (2024, 1001490000),
    (2025, 1248000000),
]:
    add_fact_money(MAP["ohb"], "company.revenue", amt, "EUR", yr, SRC_ORIGIN1, ev_ohb_diffbot, 0.85)
# zoominfo's own snapshot (USD, likely a different fiscal-year basis) -- kept, not overwritten,
# same "conflicting figure kept as a lower-confidence fact" discipline as build_ground_truth.py's
# Airbus SAS 2019 revenue.
add_fact_money(
    MAP["ohb"], "company.revenue", 954974000, "USD", 2025, SRC_ORIGIN2, ev_ohb_zoominfo, 0.5
)

new_person("marco_fuchs")
add_name(MAP["marco_fuchs"], "Marco R Fuchs", "PREFERRED", SRC_ORIGIN1, ev_ohb_diffbot)
add_person_company(
    "marco_fuchs", "ohb", "LEADS", "Chief Executive Officer", SRC_ORIGIN1, ev_ohb_diffbot, 0.9
)

# Decoy/subsidiary, linked -- OHB Italia is a REAL subsidiary (not a name-collision decoy like
# the other two), confirmed by its own description; recorded as SUBSIDIARY_OF, not left
# unlinked, since the evidence supports a real relationship rather than mere name confusion.
ev_ohb_italia = make_evidence(
    SRC_ORIGIN1,
    "diffbot.company:EHTmUo5LPO0qTCLh7ZjCgKg",
    "entityname='OHB Italia' website=ohb-italia.it founding=1981; own description: 'part of "
    "German Group OHB SE... national leader for Design, Production and Integration of complex "
    "Space Systems' -- a distinct legal entity (own domain, own diffbot company_id), not "
    "interchangeable with the parent for entity-resolution purposes.",
)
new_company("ohb_italia", [ev_ohb_italia])
add_name(MAP["ohb_italia"], "OHB Italia", "PREFERRED", SRC_ORIGIN1, ev_ohb_italia)
add_fact_text(
    MAP["ohb_italia"], "company.website", "ohb-italia.it", SRC_ORIGIN1, ev_ohb_italia, 0.9
)
add_fact_text(
    MAP["ohb_italia"],
    "company.description",
    "Italian subsidiary of OHB SE; design, production and integration of space systems "
    "(satellites, Earth observation, space situational awareness, ISS, space science).",
    SRC_ORIGIN1,
    ev_ohb_italia,
    0.85,
)
add_relationship(
    "ohb_italia",
    "ohb",
    "SUBSIDIARY_OF",
    SRC_ORIGIN1,
    ev_ohb_italia,
    0.8,
    "OHB Italia S.p.A. self-describes as part of German Group OHB SE (diffbot description text); "
    "not independently corroborated in diffbot.companyrelation the way 'OHB System AG' is "
    "(confidence 0.87 there) -- kept at a slightly lower confidence for that reason.",
)

# =========================================================================
# CAPELLA SPACE
# =========================================================================
ev_capella_diffbot = make_evidence(
    SRC_ORIGIN1,
    "diffbot.company:Eab4Feq-pPeSUpmSKYuToDA",
    "entityname='Capella Space' website=capellaspace.com founding=2016-03 ceoname='Frank Backes' "
    "ispublic=False; 11 companyinvestmentround rows 2014-2023, 37 companyinvestor rows, "
    "6 companyexperience rows",
)
ev_capella_zoominfo = make_evidence(
    SRC_ORIGIN2,
    "testzoominfo.Company:398000639",
    "companyName='Capella Space' companyDomain=www.capellaspace.com companyEmployees=160 "
    "companyRevenue=$59.1M companyType=PRIVATE; 93 Experience rows",
)
ev_capella_merge = make_evidence(
    SRC_ANALYST,
    "manual-merge:capella-diffbot+zoominfo",
    "Exact website-domain match (capellaspace.com / www.capellaspace.com) across both origins, "
    "both describing the same 2016-founded synthetic-aperture-radar Earth-observation company "
    "-> same real company, merged as one golden entity. Auto-merged correctly under the builtin "
    "engine (wf_b01222ff, 1 cluster) but NOT under Splink (wf_bb4fcdf6, 2 clusters, 0 merged) -- "
    "traced live to a Splink deterministic-prior-estimation failure on a 2-record company set "
    "('recall must be at least 1.00'), forcing a noisy single-pair EM fallback; see §00d.",
)
new_company("capella", [ev_capella_diffbot, ev_capella_zoominfo])
add_name(MAP["capella"], "Capella Space", "PREFERRED", SRC_ORIGIN1, ev_capella_diffbot)
add_fact_text(
    MAP["capella"], "company.website", "capellaspace.com", SRC_ORIGIN1, ev_capella_diffbot, 0.95
)
add_fact_date(
    MAP["capella"],
    "company.founded_date",
    date(2016, 3, 1),
    "MONTH",
    SRC_ORIGIN1,
    ev_capella_diffbot,
    0.85,
)
add_fact_code(
    MAP["capella"],
    "company.status",
    "ACTIVE",
    STATUS["ACTIVE"],
    SRC_ORIGIN1,
    ev_capella_diffbot,
    0.95,
)
add_fact_text(
    MAP["capella"],
    "company.description",
    "American space company providing on-demand 50cm high-resolution synthetic aperture radar "
    "(SAR) Earth-observation imagery via a constellation of small satellites -- imagery through "
    "clouds and at night, for government and commercial customers.",
    SRC_ORIGIN1,
    ev_capella_diffbot,
    0.9,
)
add_fact_numeric(
    MAP["capella"], "company.employee_count", 160, SRC_ORIGIN2, ev_capella_zoominfo, 0.75, TODAY
)
add_fact_money(
    MAP["capella"], "company.revenue", 59128000, "USD", 2025, SRC_ORIGIN2, ev_capella_zoominfo, 0.6
)

new_person("frank_backes")
add_name(MAP["frank_backes"], "Frank Backes", "PREFERRED", SRC_ORIGIN1, ev_capella_diffbot)
add_person_company(
    "frank_backes",
    "capella",
    "LEADS",
    "Chief Executive Officer",
    SRC_ORIGIN1,
    ev_capella_diffbot,
    0.85,
)

# Funding history -- the exact data the app's own `company_investor` gap (§00b, reconfirmed
# §00d) currently drops entirely (0 of 37 real investor rows loaded, either engine). Recorded
# here as real facts/relationships so this gap has something concrete to verify a future fix
# against, not just a row count.
add_fact_text(
    MAP["capella"],
    "company.description",
    "Funding history per diffbot.companyinvestmentround (11 rounds, 2014-07-31 to 2023-01-10): "
    "notable rounds include $29.4M (2019-12-11), $97.0M Series C (2022-04-25), and $60.0M "
    "(2023-01-10). 37 distinct investor relationships recorded across these rounds.",
    SRC_ORIGIN1,
    ev_capella_diffbot,
    0.85,
)
for ikey, iname, amount_usd, rdate in [
    ("spark_capital", "Spark Capital", 29400000, date(2019, 12, 11)),
    ("dcvc", "DCVC", 29400000, date(2019, 12, 11)),
    ("nightdragon", "NightDragon", 29400000, date(2019, 12, 11)),
]:
    ev_investor = make_evidence(
        SRC_ORIGIN1,
        f"diffbot.companyinvestor:0b4c56c24180e15f (investmentid) -> {iname}",
        f"{iname} listed as an investor in Capella Space's $29.4M round dated {rdate.isoformat()} "
        f"(diffbot.companyinvestmentround investmentid=0b4c56c24180e15f), confidence 0.98 in source.",
    )
    new_company(ikey, [ev_investor])
    add_name(MAP[ikey], iname, "PREFERRED", SRC_ORIGIN1, ev_investor)
    add_relationship(
        ikey,
        "capella",
        "INVESTS_IN",
        SRC_ORIGIN1,
        ev_investor,
        0.9,
        f"Investor in Capella Space's {rdate.isoformat()} round ($29.4M total round size).",
    )

# Decoy, unlinked -- one representative unrelated ZoomInfo "Capella *" company (of 5+ found in
# the sweep), confirmed distinct: different domain entirely, no relationship to the real company.
ev_capella_univ = make_evidence(
    SRC_ORIGIN2,
    "testzoominfo.Company:42603095",
    "companyName='Capella University' companyDomain=www.capella.edu -- an online for-profit "
    "university, unrelated to Capella Space beyond sharing a name. One of 5+ unrelated "
    "'Capella *' ZoomInfo companies found during the sweep (others: Capella Microsystems, "
    "Capella Apparel, Capella Transport, Capella Graphics, ...), none sharing a domain with "
    "capellaspace.com.",
)
new_company("capella_university_decoy", [ev_capella_univ])
add_name(
    MAP["capella_university_decoy"], "Capella University", "PREFERRED", SRC_ORIGIN2, ev_capella_univ
)
add_fact_text(
    MAP["capella_university_decoy"],
    "company.website",
    "capella.edu",
    SRC_ORIGIN2,
    ev_capella_univ,
    0.9,
)

conn.commit()
print("THURAYA + OHB SE + CAPELLA SPACE done. total entities this script:", len(MAP))
cur.close()
conn.close()
print("ALL COMMITTED OK")
