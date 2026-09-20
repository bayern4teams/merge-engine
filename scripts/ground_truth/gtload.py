#!/usr/bin/env python3
"""Stage F/G — field mapping + transactional load into ground_truth.kg.

Reads gt_build staging (s_*) + clusters from gtbuild.py stage e, writes
kg.* in FK order (§9), then updates extract_ledger.rows_landed.
Idempotent: deterministic uuid5 item ids + ON CONFLICT DO NOTHING.
"""
import hashlib
import re
import sys
import time
import uuid

sys.path.insert(0, "/home/bayern/Projects/merge-engine/scripts/ground_truth")
from gtcore import (RUN_ID, copy_rows, dpsi, dpsi1, norm_dom, norm_name, psql,
                    psql_script, psql_stdin, q)


def run_batched(stmts, max_bytes=2_000_000):
    """Execute many statements in transactions bounded by total byte size."""
    import os
    dump_dir = os.environ.get("GT_DUMP_DIR")
    buf = []
    total = 0
    idx = 0
    def _send(b):
        nonlocal idx
        script = "BEGIN;\n" + ";\n".join(b) + ";\nCOMMIT;"
        if dump_dir:
            idx += 1
            with open(f"{dump_dir}/batch_{idx:04d}.sql", "w") as f:
                f.write(script)
        psql_script(script)
    for st in stmts:
        sz = len(st.encode())
        if buf and total + sz > max_bytes:
            _send(buf)
            buf, total = [], 0
        buf.append(st)
        total += sz
    if buf:
        _send(buf)

NS_URL = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
T0 = time.time()
CUR = {}   # iso -> currency_id
PRED = {}  # code -> predicate_id


def log(*a):
    print(f"[{time.time()-T0:7.1f}s]", *a, flush=True)


def sha_hex(s):
    return hashlib.sha256(s.encode()).hexdigest()


def u5(name):
    return str(uuid.uuid5(NS_URL, name))


# ------------------------------------------------------------ value coercion
def d_parse(raw):
    """diffbot d-dates ('d1916-07-15', 'd1916-XX-XX', 'd2016-03') -> (iso, precision). §7/§8."""
    if not raw or not raw.startswith("d"):
        return None, None
    r = raw[1:].strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}|XX))?(?:-(\d{2}|XX))?", r)
    if not m:
        return None, None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if mo in (None, "XX"):
        return f"{y}-01-01", "YEAR"
    if d in (None, "XX"):
        return f"{y}-{mo}-01", "MONTH"
    iso = f"{y}-{mo}-{d}"
    try:
        import datetime as dt
        dt.date.fromisoformat(iso)
    except ValueError:
        return None, None
    return iso, "DAY"


def emp_range(v):
    if not v:
        return None
    v = v.replace(",", "")
    if "Over" in v:
        m = re.search(r"(\d+)", v)
        return int(m.group(1)) if m else None
    m = re.search(r"(\d+)\s*-\s*(\d+)", v)
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m = re.search(r"(\d+)", v)
    return int(m.group(1)) if m else None


def revenue(v):
    if not v:
        return None
    v = v.replace("$", "").replace(",", "").strip()
    mult = 1
    if "Trillion" in v:
        mult, v = 1_000_000_000_000, v.replace("Trillion", "").strip()
    elif "Billion" in v:
        mult, v = 1_000_000_000, v.replace("Billion", "").strip()
    elif "Million" in v:
        mult, v = 1_000_000, v.replace("Million", "").strip()
    try:
        return float(v) * mult
    except ValueError:
        return None


def conf_or(v, default):
    try:
        f = float(v)
        return f if 0 <= f <= 1 else default
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------ predicates
RAW_PREDICATES = [
    # (code, name, subject, kind, multi)
    ("company_companyid", "Company source id (raw)", "COMPANY", "TEXT", False),
    ("company_sourcecategory", "Source category", "COMPANY", "TEXT", False),
    ("company_types", "Type tags", "COMPANY", "TEXT", True),
    ("company_motto", "Motto", "COMPANY", "TEXT", False),
    ("company_logo", "Logo URL", "COMPANY", "TEXT", False),
    ("company_totalinvestment", "Total investment", "COMPANY", "NUMERIC", True),
    ("company_totalinvestment_currency", "Total investment currency", "COMPANY", "TEXT", True),
    ("company_ispublic", "Is public", "COMPANY", "BOOLEAN", False),
    ("company_isnonprofit", "Is nonprofit", "COMPANY", "BOOLEAN", False),
    ("company_isacquired", "Is acquired", "COMPANY", "BOOLEAN", False),
    ("company_isdissolved", "Is dissolved", "COMPANY", "BOOLEAN", False),
    ("company_ceoname", "CEO name (scrape)", "COMPANY", "TEXT", True),
    ("company_foundername", "Founder name (scrape)", "COMPANY", "TEXT", True),
    ("company_metadata_confidence", "Scrape metadata confidence", "COMPANY", "NUMERIC", False),
    ("company_explicit_origins", "Explicit origins", "COMPANY", "TEXT", False),
    ("company_phone", "Phone", "COMPANY", "TEXT", True),
    ("company_contact_email", "Contact e-mail (PROFESSIONAL)", "COMPANY", "TEXT", True),
    ("company_contact_other", "Contact (untyped)", "COMPANY", "TEXT", True),
    ("company_location_street", "Location street", "COMPANY", "TEXT", True),
    ("company_location_address_full", "Location address full", "COMPANY", "TEXT", True),
    ("company_location_city", "Location city", "COMPANY", "TEXT", True),
    ("company_location_region", "Location region", "COMPANY", "TEXT", True),
    ("company_location_postalcode", "Location postal code", "COMPANY", "TEXT", True),
    ("company_location_country", "Location country", "COMPANY", "TEXT", True),
    ("company_location_latitude", "Location latitude", "COMPANY", "TEXT", True),
    ("company_location_longitude", "Location longitude", "COMPANY", "TEXT", True),
    ("company_location_precision", "Location precision", "COMPANY", "TEXT", True),
    ("company_location_isprimary", "Location is primary", "COMPANY", "TEXT", True),
    ("company_location_iscurrent", "Location is current", "COMPANY", "TEXT", True),
    ("company_industry", "Industry label", "COMPANY", "TEXT", True),
    ("company_empcategory", "Employee category", "COMPANY", "TEXT", True),
    ("company_empcategory_firsthiredate", "Employee category first-hire date", "COMPANY", "DATE", True),
    ("company_technologyname", "Technology name", "COMPANY", "TEXT", True),
    ("company_technologytype", "Technology type", "COMPANY", "TEXT", True),
    ("company_technology_categories", "Technology categories", "COMPANY", "TEXT", True),
    ("company_investmentround_date", "Investment round date", "COMPANY", "DATE", True),
    ("company_investmentround_amount", "Investment round amount", "COMPANY", "NUMERIC", True),
    ("company_investmentround_currency", "Investment round currency", "COMPANY", "TEXT", True),
    ("company_investmentround_investor", "Investment round investor (unresolved)", "COMPANY", "TEXT", True),
    ("company_employees_exact", "Employee count (exact)", "COMPANY", "NUMERIC", False),
    ("company_revenue_range", "Revenue range label", "COMPANY", "TEXT", False),
    ("company_companytype", "Company type (zi)", "COMPANY", "TEXT", False),
    ("person_role", "Person role (diffbot)", "PERSON", "TEXT", False),
    ("person_summary", "Person summary (diffbot)", "PERSON", "TEXT", False),
    ("person_types", "Person type tags (diffbot)", "PERSON", "TEXT", False),
    ("person_confidence", "Person row confidence", "PERSON", "NUMERIC", False),
    ("person_first_name", "First name", "PERSON", "TEXT", False),
    ("person_last_name", "Last name", "PERSON", "TEXT", False),
    ("person_middle_initial", "Middle initial", "PERSON", "TEXT", False),
    ("person_free_mails", "Free e-mails (zoominfo)", "PERSON", "TEXT", True),
]

UNMAPPED = [
    # (origin, table, field, reason)
    ("diffbot", "company", "descriptors", "column exists in source but guide §0.1 does not list it; no canonical/raw predicate defined — reported, not loaded"),
    ("diffbot", "company", "created_at/_version", "source bookkeeping columns — provenance is carried by evidence locator instead"),
    ("diffbot", "location", "(all)", "no kg.location table in ground_truth (§0.3); mapped to company_location_* raw facts instead"),
    ("zoominfo", "Location", "(all)", "same: raw company_location_* facts"),
    ("diffbot", "companyindustry", "entity_taxonomy_membership", "kg taxonomy id 1 (Industry) has 0 seeded terms (§2.0 vocab intact) — industry names stored as company_industry TEXT facts; membership rows would need new taxonomy terms = vocab change (forbidden §2.0)"),
    ("zoominfo", "CompanyIndustry", "entity_taxonomy_membership", "same as above"),
    ("diffbot", "companyrelation", "company_company_relationship", "100% of relatedcompanyid values are dangling (0/6174 resolve in diffbot.company) — recorded as unresolved-link evidence per §4; NO company-company relationship rows exist"),
    ("diffbot", "companyinvestor", "INVESTS_IN relationship", "investorcompanyid 100% dangling (0/1260 resolve) — investor name kept as company_investmentround_investor fact + unresolved-link evidence"),
    ("diffbot", "company", "ispublic -> company.status CODE 'public'", "Company Status taxonomy (4) has no PUBLIC term (§2.0 vocab fixed) — status emitted as taxonomy membership term 5 PRIVATE when False (§7) + raw company_ispublic BOOLEAN always"),
    ("diffbot", "location", "country -> company.headquarters_country CODE", "no country taxonomy — §7 composite-CODE path impossible; raw company_location_country TEXT used (§7 alt path)"),
    ("zoominfo", "Experience", "personHashtags", "no mapping defined in §7; would need a new predicate not covered by §7 — reported unmapped"),
]


def ensure_vocab():
    global CUR, PRED
    CUR = {r[1]: int(r[0]) for r in dpsi("SELECT currency_id, iso_code FROM kg.currency")}
    PRED = {r[0]: int(r[1]) for r in dpsi("SELECT code, predicate_id FROM kg.fact_predicate")}
    # register raw predicates (append-only, §8); predicate_id is identity-assigned
    stmts = []
    for i, (code, name, subj, kind, multi) in enumerate(RAW_PREDICATES):
        if code in PRED:
            continue
        stmts.append(
            "INSERT INTO kg.fact_predicate (code, name, subject_entity_type, "
            f"value_kind, is_multi_valued) VALUES ({q(code)},{q(name)},{q(subj)},{q(kind)},"
            f"{'true' if multi else 'false'}) ON CONFLICT (code) DO NOTHING")
    if stmts:
        psql("BEGIN;\n" + ";\n".join(stmts) + ";\nCOMMIT;")
        PRED = {r[0]: int(r[1]) for r in dpsi("SELECT code, predicate_id FROM kg.fact_predicate")}
    log(f"fact_predicate rows: {len(PRED)} (canonical 23 + raw {len(PRED)-23})")


# ------------------------------------------------------------ fact helpers
FACTS = []      # (item_id, subject, pred_code, kind, value, currency, valid_from, valid_to, precision, conf, source_id, ev_key)
FACT_KEYS = set()
ITEMS = {}      # item_id -> (item_type, source_id, conf, kind_meta)
EVID = {}       # ev_key -> (source_id, locator, snippet)
KIE = set()     # (item_id, evidence_id_key, is_primary)
NAMES = set()   # (entity_id, name, norm, name_type)


def add_fact(entity_id, pred, kind, value, *, source_id, ev_key, conf=0.9,
             currency=None, valid_from=None, valid_to=None, prec=None,
             dedupe=True, numeric2=None):
    """kind: TEXT/NUMERIC/DATE/BOOLEAN/MONEY. value per kind."""
    if value is None or value == "":
        return None
    key = sha_hex(f"fact:{entity_id}|{pred}|{kind}|{value}|{currency or ''}|{valid_from or ''}")
    if dedupe and key in FACT_KEYS:
        return key
    FACT_KEYS.add(key)
    item_id = u5("kg_fact:" + key)
    FACTS.append((item_id, entity_id, pred, kind, value, currency,
                  valid_from, valid_to, prec, conf, source_id, ev_key))
    ITEMS[item_id] = ("FACT", source_id, conf)
    KIE.add((item_id, ev_key, True))
    return item_id


def evidence(ev_key, source_id, locator, snippet):
    if ev_key not in EVID:
        EVID[ev_key] = (source_id, locator, snippet)


def bool_txt(v):
    return str(v).strip().lower() == "true"


# ================================================================ LOAD
def stage_g():
    log("=== STAGE F/G: field mapping + load ===")
    ensure_vocab()
    # source 3 = analyst judgment (§2.2 note)
    psql("""INSERT INTO kg.source (source_id, source_type_term_id, uri, title)
            OVERRIDING SYSTEM VALUE
            VALUES (3, 31, 'manual:merge-judgment', 'analyst: manual merge judgments (this build)')
            ON CONFLICT (source_id) DO NOTHING""")

    clusters = dpsi("""SELECT cluster_key, entity_id::text, canonical_name FROM gt_build.clusters""")
    members = dpsi("SELECT cluster_key, origin, record_id FROM gt_build.cluster_members ORDER BY 1,2,3")
    mem_by_cluster = {}
    for ck, org, rid in members:
        mem_by_cluster.setdefault(ck, []).append((org, rid))
    log(f"companies: {len(clusters)} clusters / {len(members)} member records")

    persons = dpsi("SELECT cluster_key, entity_id::text, display_name FROM gt_build.person_clusters")
    pmembers = dpsi("SELECT cluster_key, origin, record_id FROM gt_build.person_members ORDER BY 1,2,3")
    p_mem = {(org, rid): ck for ck, org, rid in pmembers}
    log(f"persons: {len(persons)}")

    # ---------- 1) entity + subtypes (one txn) -----------------------------
    stmts = []
    for ck, eid, cname in clusters:
        stmts.append(f"INSERT INTO kg.entity (entity_id, entity_type) VALUES ({q(eid)}::uuid,'COMPANY') ON CONFLICT (entity_id) DO NOTHING")
        stmts.append(f"INSERT INTO kg.organization (organization_id) VALUES ({q(eid)}::uuid) ON CONFLICT DO NOTHING")
        stmts.append(f"INSERT INTO kg.company (company_id) VALUES ({q(eid)}::uuid) ON CONFLICT DO NOTHING")
    for i in range(0, len(stmts), 300):
        psql("BEGIN;\n" + ";\n".join(stmts[i:i + 300]) + ";\nCOMMIT;")
    stmts = []
    for ck, eid, dname in persons:
        stmts.append(f"INSERT INTO kg.entity (entity_id, entity_type) VALUES ({q(eid)}::uuid,'PERSON') ON CONFLICT (entity_id) DO NOTHING")
        stmts.append(f"INSERT INTO kg.person (person_id) VALUES ({q(eid)}::uuid) ON CONFLICT DO NOTHING")
    for i in range(0, len(stmts), 300):
        psql("BEGIN;\n" + ";\n".join(stmts[i:i + 300]) + ";\nCOMMIT;")
    n_e = int(dpsi1("SELECT count(*) FROM kg.entity WHERE deleted_at IS NULL"))
    n_c = int(dpsi1("SELECT count(*) FROM kg.company"))
    n_p = int(dpsi1("SELECT count(*) FROM kg.person"))
    assert n_e == n_c + n_p, (n_e, n_c, n_p)
    log(f"entities loaded: {n_e} (companies {n_c}, persons {n_p})")

    # ---------- 2) evidence rows (one per source record, §9.8) -------------
    # company records
    dbco = dpsi("""SELECT companyid, entityname, fullname, website FROM gt_build.s_db_company""")
    for cid, en, fn, ws in dbco:
        evidence(f"diffbot.company:{cid}", 1, f"diffbot.company:{cid}",
                 f"entityname={en!r} fullname={fn!r} website={ws!r}")
    zico = dpsi("SELECT companyid, name, domain, ticker, emp_range, revenue FROM gt_build.s_zi_company")
    for cid, nm, dom, tk, er, rv in zico:
        evidence(f"testzoominfo.Company:{cid}", 2, f"testzoominfo.Company:{cid}",
                 f"companyName={nm!r} companyDomain={dom!r} ticker={tk!r} employees={er!r} revenue={rv!r}")
    # persons
    for pid, nm in dpsi("SELECT personid, name FROM gt_build.s_db_persons"):
        evidence(f"diffbot.person:{pid}", 1, f"diffbot.person:{pid}", f"name={nm!r}")
    for pid, nm in dpsi("SELECT personid, name FROM gt_build.s_zi_person"):
        evidence(f"testzoominfo.Person:{pid}", 2, f"testzoominfo.Person:{pid}", f"name={nm!r}")

    # ---------- 3) COMPANY facts per cluster -------------------------------
    co_by_member = {("diffbot", r[0]): r for r in dpsi("""SELECT companyid, entityname, fullname,
        sourcecategory, description, summary, alldescriptions, types, motto, website, logo,
        foundingdate, totalinvestment, totalinvestmentcurrency, ispublic, isnonprofit,
        isacquired, isdissolved, ceoname, foundername, metadata_confidence, explicit_origins
        FROM gt_build.s_db_company""")}
    zi_by_member = {("zoominfo", r[0]): r for r in dpsi("""SELECT companyid, name, description,
        domain, website, emp_range, phone, revenue, revenue000s, employees, revenue_range,
        ticker, ctype FROM gt_build.s_zi_company""")}

    cluster_of = {ck: eid for ck, eid, _ in clusters}

    def company_desc(r_db, r_zi):
        cands = []
        if r_db:
            for idx in (4, 5, 6):  # description, summary, alldescriptions
                v = r_db[idx] if idx < len(r_db) else ""
                if v:
                    cands.append(v)
        if r_zi and len(r_zi) > 2 and r_zi[2]:
            cands.append(r_zi[2])
        return max(cands, key=len) if cands else None

    n_f0 = len(FACTS)
    for ck, eid, _cn in clusters:
        mems = mem_by_cluster.get(ck, [])
        dbs = [co_by_member[m] for m in mems if m in co_by_member]
        zis = [zi_by_member[m] for m in mems if m in zi_by_member]
        # website canonical (§7 normalize)
        doms = []
        for m, r in [(m, co_by_member.get(m)) for m in mems]:
            pass
        for m in mems:
            if m in co_by_member:
                ws = norm_dom(co_by_member[m][9])
                if ws:
                    doms.append((ws, 1, f"diffbot.company:{m[1]}"))
            if m in zi_by_member:
                zr = zi_by_member[m]
                for idx in (3, 4):
                    ws = norm_dom(zr[idx])
                    if ws:
                        doms.append((ws, 2, f"testzoominfo.Company:{m[1]}"))
        for ws, sid, evk in doms:
            add_fact(eid, "company.website", "TEXT", ws, source_id=sid, ev_key=evk)
        # legal names -> canonical fact 1
        for m in mems:
            if m in co_by_member and co_by_member[m][2]:
                add_fact(eid, "company.legal_name", "TEXT", co_by_member[m][2],
                         source_id=1, ev_key=f"diffbot.company:{m[1]}")
        # description
        desc = company_desc(dbs[0] if dbs else None, zis[0] if zis else None)
        if desc:
            # evidence: the record it came from
            src = None
            for r in dbs:
                for idx in (4, 5, 6):
                    if (r[idx] or "") == desc:
                        src = (1, f"diffbot.company:{r[0]}")
                        break
                if src:
                    break
            if not src and zis:
                src = (2, f"testzoominfo.Company:{zis[0][0]}")
            add_fact(eid, "company.description", "TEXT", desc, source_id=src[0], ev_key=src[1])
        # founded date
        for r in dbs:
            iso, prec = d_parse(r[11])
            if iso:
                add_fact(eid, "company.founded_date", "DATE", iso, source_id=1,
                         ev_key=f"diffbot.company:{r[0]}", prec=prec)
        # employee count: zi range → NUMERIC; exact → raw
        for zr in zis:
            ec = emp_range(zr[5])
            if ec is not None:
                add_fact(eid, "company.employee_count", "NUMERIC", str(ec), source_id=2,
                         ev_key=f"testzoominfo.Company:{zr[0]}")
            try:
                if zr[9] and int(zr[9]) > 0:
                    add_fact(eid, "company_employees_exact", "NUMERIC", zr[9], source_id=2,
                             ev_key=f"testzoominfo.Company:{zr[0]}")
            except ValueError:
                pass
        # revenue: zi companyRevenue → MONEY (§7 prefer explicit over 000s)
        for zr in zis:
            amt = revenue(zr[7])
            if amt is None and zr[8] and zr[8] != "0":
                try:
                    amt = int(zr[8]) * 1000
                except ValueError:
                    amt = None
            if amt is not None:
                add_fact(eid, "company.revenue", "MONEY", f"{amt:.2f}", source_id=2,
                         ev_key=f"testzoominfo.Company:{zr[0]}", currency="USD")
        # ticker
        for zr in zis:
            if zr[11]:
                add_fact(eid, "company.ticker_symbol", "TEXT", zr[11], source_id=2,
                         ev_key=f"testzoominfo.Company:{zr[0]}")
        # booleans + misc diffbot raw
        for r in dbs:
            ek = f"diffbot.company:{r[0]}"
            cf = conf_or(r[20] if len(r) > 20 else None, 0.9)
            add_fact(eid, "company_ispublic", "BOOLEAN", bool_txt(r[14]), source_id=1, ev_key=ek, conf=cf)
            add_fact(eid, "company_isnonprofit", "BOOLEAN", bool_txt(r[15]), source_id=1, ev_key=ek, conf=cf)
            add_fact(eid, "company_isacquired", "BOOLEAN", bool_txt(r[16]), source_id=1, ev_key=ek, conf=cf)
            add_fact(eid, "company_isdissolved", "BOOLEAN", bool_txt(r[17]), source_id=1, ev_key=ek, conf=cf)
            add_fact(eid, "company_sourcecategory", "TEXT", r[3], source_id=1, ev_key=ek)
            add_fact(eid, "company_types", "TEXT", r[7], source_id=1, ev_key=ek)
            add_fact(eid, "company_motto", "TEXT", r[8], source_id=1, ev_key=ek)
            add_fact(eid, "company_logo", "TEXT", r[10], source_id=1, ev_key=ek)
            if r[12] and r[12] != "0":
                add_fact(eid, "company_totalinvestment", "NUMERIC", r[12], source_id=1, ev_key=ek)
                add_fact(eid, "company_totalinvestment_currency", "TEXT", r[13], source_id=1, ev_key=ek)
            add_fact(eid, "company_ceoname", "TEXT", r[18], source_id=1, ev_key=ek)
            add_fact(eid, "company_foundername", "TEXT", r[19], source_id=1, ev_key=ek)
            add_fact(eid, "company_metadata_confidence", "NUMERIC", r[20], source_id=1, ev_key=ek)
            add_fact(eid, "company_explicit_origins", "TEXT", r[21], source_id=1, ev_key=ek)
            # raw source id stays TEXT (§8)
            add_fact(eid, "company_companyid", "TEXT", r[0], source_id=1, ev_key=ek)
        for zr in zis:
            ek = f"testzoominfo.Company:{zr[0]}"
            add_fact(eid, "company_companyid", "TEXT", zr[0], source_id=2, ev_key=ek)
            add_fact(eid, "company_revenue_range", "TEXT", zr[10], source_id=2, ev_key=ek)
            add_fact(eid, "company_companytype", "TEXT", zr[12], source_id=2, ev_key=ek)
            if zr[6]:
                add_fact(eid, "company_phone", "TEXT", zr[6], source_id=2, ev_key=ek)
        # contacts (§7)
        for m in mems:
            if m[0] != "diffbot":
                continue
            for ctid, cstr, ctype, ccf in dpsi(f"""SELECT contactid, contactstring, contacttype, confidence
                    FROM gt_build.s_db_companycontact WHERE companyid={q(m[1])}"""):
                ek = f"diffbot.companycontact:{ctid}"
                evidence(ek, 1, f"diffbot.companycontact:{ctid}",
                         f"contactstring={cstr!r} contacttype={ctype!r}")
                pred = {"OFFICE": "company_phone",
                        "PROFESSIONAL": "company_contact_email"}.get(ctype, "company_contact_other")
                val = cstr if ctype in ("OFFICE", "PROFESSIONAL") else f"{ctype}: {cstr}"
                add_fact(eid, pred, "TEXT", val, source_id=1, ev_key=ek,
                         conf=conf_or(ccf, 0.9))
        # locations → raw predicates
        for m in mems:
            if m[0] == "diffbot":
                for row in dpsi(f"""SELECT locationid, street, addressfull, city, region,
                        postalcode, country, latitude, longitude, precision, isprimary, iscurrent
                        FROM gt_build.s_db_location WHERE companyid={q(m[1])}"""):
                    ek = f"diffbot.location:{row[0]}"
                    evidence(ek, 1, ek, f"city={row[3]!r} country={row[6]!r} isprimary={row[10]!r}")
                    fields = [("company_location_street", 1), ("company_location_address_full", 2),
                              ("company_location_city", 3), ("company_location_region", 4),
                              ("company_location_postalcode", 5), ("company_location_country", 6),
                              ("company_location_latitude", 7), ("company_location_longitude", 8),
                              ("company_location_precision", 9), ("company_location_isprimary", 10),
                              ("company_location_iscurrent", 11)]
                    for pred, idx in fields:
                        add_fact(eid, pred, "TEXT", row[idx], source_id=1, ev_key=ek)
            else:
                for row in dpsi(f"""SELECT locationid, street, city, state, zip, country
                        FROM gt_build.s_zi_loc WHERE companyid={q(m[1])}"""):
                    ek = f"testzoominfo.Location:{row[0]}|{m[1]}"
                    evidence(ek, 2, ek, f"City={row[2]!r} CountryCode={row[5]!r}")
                    for pred, v in (("company_location_street", row[1]),
                                    ("company_location_city", row[2]),
                                    ("company_location_region", row[3]),
                                    ("company_location_postalcode", row[4]),
                                    ("company_location_country", row[5])):
                        add_fact(eid, pred, "TEXT", v, source_id=2, ev_key=ek)
        # industries (§7: taxonomy id 1 has no terms → TEXT facts)
        for m in mems:
            if m[0] == "diffbot":
                for cn in [r[0] for r in dpsi(
                        f"""SELECT DISTINCT ci.name FROM gt_build.s_db_classifications ci
                            WHERE ci.classificationid IN
                              (SELECT classificationid FROM gt_build.s_db_companyindustry
                               WHERE companyid={q(m[1])}) AND ci.name != '' ORDER BY 1""")]:
                    add_fact(eid, "company_industry", "TEXT", cn, source_id=1,
                             ev_key=f"diffbot.company:{m[1]}")
            else:
                for iname in [r[0] for r in dpsi(
                        f"""SELECT DISTINCT i.name FROM gt_build.s_zi_industry i
                            WHERE i.industryid IN
                              (SELECT industryid FROM gt_build.s_zi_ind WHERE companyid={q(m[1])})
                              AND i.name != '' ORDER BY 1""")]:
                    add_fact(eid, "company_industry", "TEXT", iname, source_id=2,
                             ev_key=f"testzoominfo.Company:{m[1]}")
        # employee categories
        for m in mems:
            if m[0] != "diffbot":
                continue
            for eid2, cat, fhd in dpsi(f"""SELECT empcatid, category, firsthiredate
                    FROM gt_build.s_db_companyemployeecategory WHERE companyid={q(m[1])}"""):
                ek = f"diffbot.companyemployeecategory:{eid2}"
                evidence(ek, 1, ek, f"category={cat!r} firsthiredate={fhd!r}")
                add_fact(eid, "company_empcategory", "TEXT", cat, source_id=1, ev_key=ek)
                iso, prec = d_parse(fhd)
                if iso:
                    add_fact(eid, "company_empcategory_firsthiredate", "DATE", iso,
                             source_id=1, ev_key=ek, prec=prec)
        # technographics
        for m in mems:
            if m[0] != "diffbot":
                continue
            for tid, tname, ttype, cats in dpsi(f"""SELECT techid, technologyname, technologytype,
                    categories FROM gt_build.s_db_companytechnographics WHERE companyid={q(m[1])}"""):
                ek = f"diffbot.companytechnographics:{tid}"
                evidence(ek, 1, ek, f"technologyname={tname!r} technologytype={ttype!r}")
                add_fact(eid, "company_technologyname", "TEXT", tname, source_id=1, ev_key=ek)
                add_fact(eid, "company_technologytype", "TEXT", ttype, source_id=1, ev_key=ek)
                add_fact(eid, "company_technology_categories", "TEXT", cats, source_id=1, ev_key=ek)
        # financials → company.revenue MONEY per year (§7)
        for m in mems:
            if m[0] != "diffbot":
                continue
            for fid, year, amount, ccy in dpsi(f"""SELECT financialid, year, amount, currency
                    FROM gt_build.s_db_companyfinancials WHERE companyid={q(m[1])}"""):
                ek = f"diffbot.companyfinancials:{fid}"
                evidence(ek, 1, ek, f"year={year} amount={amount} currency={ccy}")
                cid = CUR.get(ccy)
                if cid is None:
                    psql(f"""INSERT INTO gt_build.drop_list VALUES
                             ('diffbot','diffbot.companyfinancials',{q(fid)},
                              {q('currency '+ccy+' not in kg.currency (vocab fixed §2.0) — dropped with reason')})""")
                    continue
                rate = 1.08 if ccy == "EUR" else 1.0
                add_fact(eid, "company.revenue", "MONEY", str(amount), source_id=1,
                         ev_key=ek, currency=ccy,
                         valid_from=f"{year}-01-01", valid_to=f"{year}-12-31", prec="YEAR")
        # investment rounds (§7 raw path; investor dangling → fact + unresolved link)
        for m in mems:
            if m[0] != "diffbot":
                continue
            for iid, rdate, ramt, rccy, surf in dpsi(f"""SELECT r.investmentid, r.date, r.amount,
                    r.currency, coalesce(string_agg(i.surfaceform,' | ' ORDER BY i.surfaceform),'')
                    FROM gt_build.s_db_companyinvestmentround r
                    LEFT JOIN gt_build.s_db_investors i USING (investmentid)
                    WHERE r.companyid={q(m[1])} GROUP BY 1,2,3,4""", sep="\x1f")[:]:
                ek = f"diffbot.companyinvestmentround:{iid}"
                evidence(ek, 1, ek, f"date={rdate!r} amount={ramt} currency={rccy!r} investor={surf!r}")
                iso, prec = d_parse(rdate)
                add_fact(eid, "company_investmentround_date", "DATE", iso or "",
                         source_id=1, ev_key=ek, prec=prec)
                if ramt and ramt != "0":
                    add_fact(eid, "company_investmentround_amount", "NUMERIC", ramt,
                             source_id=1, ev_key=ek)
                add_fact(eid, "company_investmentround_currency", "TEXT", rccy,
                         source_id=1, ev_key=ek)
                if surf:
                    add_fact(eid, "company_investmentround_investor", "TEXT", surf,
                             source_id=1, ev_key=ek)
    log(f"company facts staged: {len(FACTS)-n_f0}")

    # ---------- 4) PERSON facts ---------------------------------------------
    n_f1 = len(FACTS)
    peid_of = {c: e for c, e, _ in persons}
    for pid, name, summary, role, types, confidence in dpsi(
            "SELECT personid, name, summary, role, types, confidence FROM gt_build.s_db_persons"):
        ck = p_mem[("diffbot", pid)]
        eid = peid_of[ck]
        ek = f"diffbot.person:{pid}"
        evidence(ek, 1, ek, f"name={name!r} role={role!r}")
        cf = conf_or(confidence, 0.9)
        add_fact(eid, "person_role", "TEXT", role, source_id=1, ev_key=ek, conf=cf)
        add_fact(eid, "person_summary", "TEXT", summary, source_id=1, ev_key=ek, conf=cf)
        add_fact(eid, "person_types", "TEXT", types, source_id=1, ev_key=ek, conf=cf)
        add_fact(eid, "person_confidence", "NUMERIC", confidence, source_id=1, ev_key=ek, conf=cf)
    for pid, name, fn, ln, mi in dpsi(
            "SELECT personid, name, first_name, last_name, middle_initial FROM gt_build.s_zi_person"):
        ck = p_mem.get(("zoominfo", pid))
        if not ck:
            continue
        eid = peid_of[ck]
        ek = f"testzoominfo.Person:{pid}"
        evidence(ek, 2, ek, f"name={name!r}")
        add_fact(eid, "person_first_name", "TEXT", fn, source_id=2, ev_key=ek)
        add_fact(eid, "person_last_name", "TEXT", ln, source_id=2, ev_key=ek)
        add_fact(eid, "person_middle_initial", "TEXT", mi, source_id=2, ev_key=ek)
    # free mails (§7 comma-split; '' skipped)
    for pid, mails in dpsi("SELECT personid, freemails FROM gt_build.s_zi_pcontact"):
        ck = p_mem.get(("zoominfo", pid))
        if not ck:
            continue
        eid = peid_of[ck]
        ek = f"testzoominfo.PersonContact:{pid}"
        evidence(ek, 2, ek, f"freeMails={mails!r}")
        for m in sorted({x.strip().strip("[]'\" ") for x in
                         re.split(r"[,\s]+", mails.replace("\\'", "")) if "@" in x}):
            add_fact(eid, "person_free_mails", "TEXT", m, source_id=2, ev_key=ek)
    log(f"person facts staged: {len(FACTS)-n_f1}")

    # ---------- 5) NAMES -----------------------------------------------------
    ent_of_cluster = {c: e for c, e, _cn in clusters}
    for ck, eid, _cn in clusters:
        for m in mem_by_cluster.get(ck, []):
            if m in co_by_member:
                r = co_by_member[m]
                if r[1]:
                    NAMES.add((eid, r[1], norm_name(r[1]), 37))
                if r[2] and r[2] != r[1]:
                    NAMES.add((eid, r[2], norm_name(r[2]), 38))
            if m in zi_by_member:
                zr = zi_by_member[m]
                if zr[1]:
                    NAMES.add((eid, zr[1], norm_name(zr[1]), 37))
    for pid, name in dpsi("SELECT personid, name FROM gt_build.s_db_persons"):
        eid = peid_of[p_mem[("diffbot", pid)]]
        if name:
            NAMES.add((eid, name, norm_name(name), 37))
    for pid, name in dpsi("SELECT personid, name FROM gt_build.s_zi_person"):
        eid = peid_of.get(p_mem.get(("zoominfo", pid), ""), "")
        if name and eid:
            NAMES.add((eid, name, norm_name(name), 37))
    log(f"entity_name rows staged: {len(NAMES)}")

    # ---------- 6) RELATIONSHIPS --------------------------------------------
    # person_company: diffbot experience + zi experience (no cap, §D)
    rels = []   # (item_id, person_eid, company_eid, rel_type_id, role_title, source, ev_key, conf)
    ROLE2RT = {"CEO": 69, "Founder": 68, "BoardMember": 70}
    co_eid_by_member = {}
    for ck, members_ in mem_by_cluster.items():
        for m in members_:
            co_eid_by_member[m] = cluster_of[ck]
    _pe = {c: e for c, e, _ in persons}
    p_eid_by_member = {(org, rid): _pe[ck] for ck, org, rid in pmembers}

    for cid, pid, pname, role, cf in dpsi(
            "SELECT companyid, personid, personname, role, confidence FROM gt_build.s_db_companyexperience"):
        ceid = co_eid_by_member.get(("diffbot", cid))
        peid = p_eid_by_member.get(("diffbot", pid))
        if not ceid or not peid:
            continue
        rt = ROLE2RT.get(role, 67)
        ek = f"diffbot.companyexperience:{cid}|{pid}"
        evidence(ek, 1, ek, f"role={role!r} personname={pname!r}")
        item = u5(f"kg_rel:pc|{peid}|{ceid}|{rt}|{role}")
        rels.append((item, peid, ceid, rt, role, 1, ek, conf_or(cf, 0.9)))
    n_db_rel = len(rels)
    for pid, cid, title, jtitle in dpsi(
            """SELECT e.personid, e.companyid, e.title, e.jobtitle FROM gt_build.s_zi_exp_raw e
               WHERE e.personid IN (SELECT personid FROM gt_build.s_zi_person_ids_ok)
               ORDER BY 1,2"""):
        ceid = co_eid_by_member.get(("zoominfo", cid))
        peid = p_eid_by_member.get(("zoominfo", pid))
        if not ceid or not peid:
            continue
        rt = 67  # §7: zi Experience.title/jobTitle -> WORKS_FOR
        ek = f"testzoominfo.Experience:{pid}|{cid}"
        evidence(ek, 2, ek, f"title={title!r}")
        item = u5(f"kg_rel:pc|{peid}|{ceid}|{rt}|{jtitle or title}")
        rels.append((item, peid, ceid, rt, (jtitle or title), 2, ek, 0.9))
    log(f"person-company relationships staged: {len(rels)} (diffbot {n_db_rel}, zi {len(rels)-n_db_rel})")

    # company-company: all endpoints dangling (§4) → unresolved-link evidence
    unres = dpsi("""SELECT family, rel_kind, endpoint_id, surfaceform, relationtype
                    FROM gt_build.dangling_endpoints ORDER BY rel_kind, endpoint_id""")
    for fam, kind, ep, surf, rt in unres:
        ek = f"unresolved:{kind}:{ep}"
        evidence(ek, 1, f"diffbot.companyrelation->{ep}",
                 f"dangling endpoint id={ep!r} surfaceform={surf!r} relationtype={rt!r} family={fam!r}")
    log(f"unresolved company-company endpoints as evidence: {len(unres)}")

    # ---------- 7) WRITE (FK order §9) ---------------------------------------
    log("writing knowledge_item + knowledge_fact ...")
    # items
    item_rows = sorted(set(ITEMS.keys()) | {r[0] for r in rels})
    seen = set()
    dedup_rows = []
    for iid in item_rows:
        if iid in seen:
            continue
        seen.add(iid)
        if iid in ITEMS:
            it, sid, cf = ITEMS[iid]
        else:
            it, sid, cf = "RELATIONSHIP", None, None
        dedup_rows.append([iid, it, sid, cf])
    # relationship items: type RELATIONSHIP, source_id + confidence from rel tuple
    rel_items = {r[0]: (r[5], r[7]) for r in rels}
    _rels_by_item = {}
    for r in rels:
        _rels_by_item.setdefault(r[0], r)
    rels = list(_rels_by_item.values())
    out = []
    for row in dedup_rows:
        if row[0] in rel_items:
            row[1] = "RELATIONSHIP"
            row[2], row[3] = rel_items[row[0]]
        out.append(row)
    ki_rows = []
    for r in out:
        ki_rows.append(f"({q(r[0])}::uuid,{q(r[1])},{r[2] if r[2] else 'NULL'},"
                       f"{r[3] if r[3] is not None else 0.9},{q('manual-ground-truth-build')},{q('1.0')})")
    # multi-row INSERT chunks sized by bytes, appended ON CONFLICT
    CHUNK = 4000
    ins_stmts = []
    for i in range(0, len(ki_rows), CHUNK):
        ins_stmts.append(
            "INSERT INTO kg.knowledge_item (item_id, item_type, source_id, confidence,"
            " extractor_name, extractor_version) VALUES " +
            ",".join(ki_rows[i:i + CHUNK]) + " ON CONFLICT (item_id) DO NOTHING")
    run_batched(ins_stmts, max_bytes=100_000)
    log(f"knowledge_item rows: {len(out)}")

    # facts
    FACT_COLS = ("item_id, subject_entity_id, predicate_id, value_kind, value_text,"
                 " value_numeric, value_date, value_boolean, currency_id,"
                 " normalized_amount, normalized_currency_id, exchange_rate,"
                 " exchange_rate_date, valid_from, valid_to, date_precision")
    fact_stmts = []
    for (item_id, entity_id, pred_code, kind, value, cur_iso, vf, vt, prec, cf, sid, evk) in FACTS:
        pid = PRED[pred_code]
        v_txt = v_num = v_dat = v_bool = cur_c = n_amt = n_cur = rate = rdate = ""
        if kind == "TEXT":
            v_txt = q(value)
        elif kind == "NUMERIC":
            v_num = q(value)
        elif kind == "DATE":
            v_dat = q(value) + "::date"
        elif kind == "BOOLEAN":
            v_bool = "true" if str(value).lower() in ("true", "1") else "false"
        elif kind == "MONEY":
            c = CUR.get(cur_iso, CUR["USD"])
            rate = 1.08 if cur_iso == "EUR" else 1.0
            usd = f"{float(value)*rate:.2f}" if cur_iso != "USD" else value
            v_num = q(value)
            cur_c, n_amt, n_cur = c, q(usd), CUR["USD"]
            rdate = "'2026-01-01'::date"
        vfc = q(vf) + "::date" if vf else "NULL"
        vtc = q(vt) + "::date" if vt else "NULL"
        prc = q(prec) if prec else "'DAY'"  # NOT NULL, schema default 'DAY'
        fact_stmts.append(
            f"INSERT INTO kg.knowledge_fact ({FACT_COLS}) VALUES "
            f"({q(item_id)}::uuid, {q(entity_id)}::uuid, {pid}, {q(kind)}, "
            f"{v_txt or 'NULL'}, {v_num or 'NULL'}, {v_dat or 'NULL'}, {v_bool or 'NULL'}, "
            f"{cur_c or 'NULL'}, {n_amt or 'NULL'}, {n_cur or 'NULL'}, "
            f"{rate if rate != '' else 'NULL'}, {rdate or 'NULL'}, "
            f"{vfc}, {vtc}, {prc}) ON CONFLICT (item_id) DO NOTHING")
    run_batched(fact_stmts)
    log(f"knowledge_fact rows: {int(dpsi1('SELECT count(*) FROM kg.knowledge_fact'))}")

    # entity_name
    en_stmts = []
    for (eid, name, norm, ntype) in sorted(NAMES):
        iid = u5(f"kg_name:{eid}:{name}")
        en_stmts.append(
            "INSERT INTO kg.entity_name (item_id, entity_id, name, normalized_name, language_code,"
            " name_type_term_id) VALUES "
            f"({q(iid)}::uuid,{q(eid)}::uuid,{q(name)},{q(norm)},'en',{ntype}) "
            "ON CONFLICT (item_id) DO NOTHING")
    run_batched(en_stmts)
    log(f"entity_name rows: {int(dpsi1('SELECT count(*) FROM kg.entity_name'))}")

    # relationships + graph_edge (forward; reverse only for symmetric types —
    # WORKS_FOR/FOUNDED/LEADS/BOARD_MEMBER are not symmetric ⇒ none)
    rel_stmts, edge_stmts = [], []
    sym_ids = {int(r[0]) for r in dpsi("SELECT relationship_type_id FROM kg.relationship_type WHERE is_symmetric")}
    for (item, peid, ceid, rt, role_title, sid, ek, cf) in rels:
        rel_stmts.append(
            "INSERT INTO kg.person_company_relationship (item_id, person_id, company_id,"
            " relationship_type_id, role_title) VALUES "
            f"({q(item)}::uuid,{q(peid)}::uuid,{q(ceid)}::uuid,{rt},{q(role_title)})"
            " ON CONFLICT (item_id) DO NOTHING")
        edge_stmts.append(
            "INSERT INTO kg.graph_edge (edge_id, source_entity_id, target_entity_id,"
            " relationship_type_id, relationship_item_id, confidence, is_reverse_projection)"
            f" SELECT {q(u5('kg_edge:fwd:' + item))}::uuid,{q(peid)}::uuid,{q(ceid)}::uuid,"
            f"{rt},{q(item)}::uuid,{cf},false"
            " WHERE NOT EXISTS (SELECT 1 FROM kg.graph_edge e WHERE e.edge_id ="
            f" {q(u5('kg_edge:fwd:' + item))}::uuid)")
        if rt in sym_ids:
            edge_stmts.append(
                "INSERT INTO kg.graph_edge (edge_id, source_entity_id, target_entity_id,"
                " relationship_type_id, relationship_item_id, confidence, is_reverse_projection)"
                f" SELECT {q(u5('kg_edge:rev:' + item))}::uuid,{q(ceid)}::uuid,{q(peid)}::uuid,"
                f"{rt},{q(item)}::uuid,{cf},true"
                " WHERE NOT EXISTS (SELECT 1 FROM kg.graph_edge e WHERE e.edge_id ="
                f" {q(u5('kg_edge:rev:' + item))}::uuid)")
    run_batched(rel_stmts)
    run_batched(edge_stmts)
    log(f"person_company_relationship: {int(dpsi1('SELECT count(*) FROM kg.person_company_relationship'))}; "
        f"graph_edge: {int(dpsi1('SELECT count(*) FROM kg.graph_edge'))}")

    # evidence rows (uuid5 deterministic)
    ev_rows = []
    ev_id = {}
    for ek, (sid, locator, snippet) in sorted(EVID.items()):
        eid5 = u5("kg_evidence:" + ek)
        ev_id[ek] = eid5
        ev_rows.append((eid5, sid, 35, snippet, locator))
    ev_stmts = []
    for i in range(0, len(ev_rows), 400):
        ev_stmts.append(
            "INSERT INTO kg.evidence (evidence_id, source_id, evidence_type_term_id,"
            " text_snippet, locator) VALUES " +
            ",".join(f"({q(e)}::uuid,{s},35,{q(sn)},{q(lo)})"
                     for e, s, t, sn, lo in ev_rows[i:i + 400]) +
            " ON CONFLICT (evidence_id) DO NOTHING")
    run_batched(ev_stmts, max_bytes=100_000)
    log(f"evidence rows: {int(dpsi1('SELECT count(*) FROM kg.evidence'))}")

    # knowledge_item_evidence
    kie_stmts = []
    for (item_id, ek, primary) in sorted(KIE):
        if ek in ev_id:
            kie_stmts.append("INSERT INTO kg.knowledge_item_evidence (item_id, evidence_id, is_primary)"
                             f" VALUES ({q(item_id)}::uuid,{q(ev_id[ek])}::uuid,{'true' if primary else 'false'})"
                             " ON CONFLICT (item_id, evidence_id) DO NOTHING")
    # link relationship items too
    for (item, peid, ceid, rt, role, sid, ek, cf) in rels:
        if ek in ev_id:
            kie_stmts.append("INSERT INTO kg.knowledge_item_evidence (item_id, evidence_id, is_primary)"
                             f" VALUES ({q(item)}::uuid,{q(ev_id[ek])}::uuid,true)"
                             " ON CONFLICT (item_id, evidence_id) DO NOTHING")
    # unresolved endpoint evidence: attach to the source company's evidence set via its own item?
    # (no item exists — they are reported in gt_build.unresolved_links instead, §4)
    run_batched(kie_stmts)
    log(f"knowledge_item_evidence: {int(dpsi1('SELECT count(*) FROM kg.knowledge_item_evidence'))}")

    # ---------- 8) unresolved links into gt_build ----------------------------
    psql("TRUNCATE gt_build.unresolved_links")
    vals = []
    for fam, kind, ep, surf, rt in unres:
        vals.append(f"('diffbot',{q(kind)},{q(ep)},{q(surf)},{q(rt)},{q('family ' + fam)})")
    for i in range(0, len(vals), 500):
        psql("INSERT INTO gt_build.unresolved_links VALUES " + ",".join(vals[i:i + 500]))

    # ---------- 9) audit + entity_map (§6.5) ---------------------------------
    psql(f"""INSERT INTO kg.merge_engine_entity_map (entity_type, record_id, cluster_id, first_seen_run)
             SELECT 'company', origin||':'||record_id, cluster_key, {q(RUN_ID)}
             FROM gt_build.cluster_members
             ON CONFLICT (entity_type, record_id) DO UPDATE SET cluster_id=EXCLUDED.cluster_id""")
    psql(f"""INSERT INTO kg.merge_engine_entity_map (entity_type, record_id, cluster_id, first_seen_run)
             SELECT 'person', origin||':'||record_id, cluster_key, {q(RUN_ID)}
             FROM gt_build.person_members
             ON CONFLICT (entity_type, record_id) DO UPDATE SET cluster_id=EXCLUDED.cluster_id""")
    # merge audit: every pair decision (accepted + rejected + borderline) (§6.5),
    # cluster_id resolved via the members of each side.
    psql(f"""INSERT INTO kg.merge_engine_merge_audit
             (run_id, entity_type, cluster_id, record_a, record_b, confidence, method, evidence,
              decided_by, human_override)
             SELECT {q(RUN_ID)}, 'company',
                    coalesce(ma.cluster_key, mb.cluster_key, ''),
                    pd.origin_a||':'||pd.rec_a, pd.origin_b||':'||pd.rec_b, pd.confidence, pd.method,
                    jsonb_build_object('note', pd.note, 'decision', pd.decision),
                    CASE WHEN pd.method LIKE 'analyst%' THEN 'analyst' ELSE 'auto' END,
                    CASE WHEN pd.method LIKE 'analyst%' THEN 'guide §12 pinned' ELSE NULL END
             FROM gt_build.pair_decisions pd
             LEFT JOIN gt_build.cluster_members ma
                    ON ma.origin=pd.origin_a AND ma.record_id=pd.rec_a
             LEFT JOIN gt_build.cluster_members mb
                    ON mb.origin=pd.origin_b AND mb.record_id=pd.rec_b
             ON CONFLICT (run_id, entity_type, record_a, record_b) DO UPDATE
               SET confidence=EXCLUDED.confidence, method=EXCLUDED.method,
                   evidence=EXCLUDED.evidence, cluster_id=EXCLUDED.cluster_id""")
    # person borderline pairs (§1.3 kept split)
    psql(f"""INSERT INTO kg.merge_engine_merge_audit
             (run_id, entity_type, cluster_id, record_a, record_b, confidence, method, evidence, decided_by, human_override)
             SELECT {q(RUN_ID)}, 'person', '', record_a, record_b, confidence, method,
                    jsonb_build_object('note', note), 'auto', NULL
             FROM gt_build.borderline WHERE family='persons'
             ON CONFLICT (run_id, entity_type, record_a, record_b) DO NOTHING""")
    log(f"entity_map: {int(dpsi1('SELECT count(*) FROM kg.merge_engine_entity_map'))}; "
        f"merge_audit: {int(dpsi1('SELECT count(*) FROM kg.merge_engine_merge_audit'))}")

    # ---------- 10) ledger landed counts (companies: distinct member records landed)
    for org, tbl in ((("diffbot", "diffbot.company"), ("zoominfo", "testzoominfo.Company"))):
        n = int(dpsi1(f"SELECT count(*) FROM kg.merge_engine_entity_map WHERE entity_type='company' AND split_part(record_id,':',1)={q(org)}"))
        psql(f"UPDATE gt_build.extract_ledger SET rows_landed={n} WHERE origin={q(org)} AND source_table={q(tbl)}")
    # persons landed
    n_p = int(dpsi1("SELECT count(*) FROM kg.merge_engine_entity_map WHERE entity_type='person'"))
    n_pc = int(dpsi1("SELECT count(*) FROM kg.person_company_relationship"))
    psql(f"UPDATE gt_build.extract_ledger SET rows_landed={n_p} WHERE source_table='diffbot.person' AND origin='diffbot'")
    psql(f"UPDATE gt_build.extract_ledger SET rows_landed=(SELECT count(*) FROM kg.merge_engine_entity_map WHERE entity_type='person' AND split_part(record_id,':',1)='zoominfo') WHERE source_table='testzoominfo.Person'")
    psql(f"UPDATE gt_build.extract_ledger SET rows_landed={n_pc} WHERE source_table='testzoominfo.Experience'")
    psql(f"UPDATE gt_build.extract_ledger SET rows_landed=(SELECT count(*) FROM kg.person_company_relationship r JOIN kg.knowledge_item ki USING(item_id) WHERE ki.source_id=1) WHERE source_table='diffbot.companyexperience'")
    # deduplicate: INSERT can create duplicates in the ledger; consolidate here
    psql("""INSERT INTO gt_build.extract_ledger (origin, source_table, rows_extracted, rows_landed)
            SELECT origin, source_table, MIN(rows_extracted),
                   MIN(rows_landed) FILTER (WHERE rows_landed IS NOT NULL)
            FROM gt_build.extract_ledger
            GROUP BY 1, 2""")
    log("ledger rows_landed updated")

    # unmapped report
    psql("TRUNCATE gt_build.unmapped_fields")
    vals = ",".join(f"({q(o)},{q(t)},{q(fl)},NULL,{q(r)})" for o, t, fl, r in UNMAPPED)
    psql(f"INSERT INTO gt_build.unmapped_fields VALUES {vals}")
    log("unmapped_fields report written")


if __name__ == "__main__":
    stage_g()
