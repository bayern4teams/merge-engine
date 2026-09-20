#!/usr/bin/env python3
"""Manual ground-truth build: Stages D (extract), E (entity resolution),
F/G (field-map + transactional load), §10 gates. No app code — pure SQL.

Run: .venv/bin/python scripts/ground_truth/gtbuild.py <stage>
Stages: d | e | g | gates | all (g implies g-load from staging produced by e)
Deterministic ids per §6.4; sorted intermediates per §1.5; every step
independently re-queried per §1.6.
"""
import hashlib
import sys
import time
import uuid

NS_URL = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_URL


def sha_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def uuid5_ns(name: str) -> uuid.UUID:
    return uuid.uuid5(NS_URL, name)

sys.path.insert(0, "/home/bayern/Projects/merge-engine/scripts/ground_truth")
from gtcore import (DEST, RUN_ID, ch, ch_rows, copy_rows, dom_root, dpsi, dpsi1,
                    exec_multi, norm_dom, norm_name, psql, q)

NS = "'6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid"
T0 = time.time()


def log(*a):
    print(f"[{time.time()-T0:7.1f}s]", *a, flush=True)


def in_chunks(sorted_ids, fn, n=900):
    for i in range(0, len(sorted_ids), n):
        fn(sorted_ids[i:i + n])


# ============================================================ STAGE D
def stage_d():
    """Extraction: pull every row attached to the closure into gt_build.s_* staging."""
    log("=== STAGE D: extraction ===")
    db_ids = sorted({r[0] for r in dpsi(
        "SELECT DISTINCT company_id FROM gt_build.company_closure WHERE origin='diffbot'")})
    zi_ids = sorted({r[0] for r in dpsi(
        "SELECT DISTINCT company_id FROM gt_build.company_closure WHERE origin='zoominfo'")})
    log(f"closure: diffbot={len(db_ids)} zi={len(zi_ids)}")

    # ---- diffbot side (FDW mirror gt_origin1 = live 5433) -----------------
    dbin = ",".join(q(x) for x in db_ids)
    tables = {
        "company": ("companyid",
                    "companyid, entityname, fullname, sourcecategory, description, summary, alldescriptions, types, motto, website, logo, foundingdate, totalinvestment, totalinvestmentcurrency, ispublic, isnonprofit, isacquired, isdissolved, ceoname, foundername, metadata_confidence, explicit_origins"),
        "companycontact": ("companyid",
                    "contactid, companyid, contactstring, contacttype, confidence"),
        "location": ("companyid",
                     "locationid, companyid, street, addressfull, city, region, postalcode, country, latitude, longitude, precision, isprimary, iscurrent"),
        "companyfinancials": ("companyid",
                     "financialid, companyid, year, amount, currency, iscurrent, confidence"),
        "companyindustry": ("companyid", "companyid, classificationid"),
        "companyinvestmentround": ("companyid",
                     "investmentid, companyid, date, amount, currency, confidence, explicit_origin"),
        "companytechnographics": ("companyid",
                     "techid, companyid, technologyname, technologytype, categories, confidence"),
        "companyemployeecategory": ("companyid",
                     "empcatid, companyid, category, firsthiredate, confidence"),
        "companyexperience": ("companyid",
                     "companyid, personid, personname, role, confidence, types"),
        "companyrelation": (None, None),  # special: both sides
    }
    for t, (col, cols) in tables.items():
        psql(f"DROP TABLE IF EXISTS gt_build.s_db_{t}")
        if t == "companyrelation":
            psql(f"""CREATE TABLE gt_build.s_db_{t} AS
                     SELECT companyid, relatedcompanyid, surfaceform, summary, types, confidence,
                            relationtype, explicit_origin
                     FROM gt_origin1.companyrelation
                     WHERE companyid IN ({dbin}) OR relatedcompanyid IN ({dbin})
                     ORDER BY companyid, relatedcompanyid""")
        else:
            psql(f"""CREATE TABLE gt_build.s_db_{t} AS
                     SELECT {cols} FROM gt_origin1.{t}
                     WHERE {col} IN ({dbin})
                     ORDER BY 1, 2""")
        n = int(dpsi1(f"SELECT count(*) FROM gt_build.s_db_{t}"))
        log(f"s_db_{t}: {n}")
        psql(f"""INSERT INTO gt_build.extract_ledger VALUES
                 ('diffbot', {q('diffbot.'+t)}, {n}, NULL)""")

    # second-order ids: classifications + persons
    psql("""DROP TABLE IF EXISTS gt_build.s_db_classifications""")
    psql("""CREATE TABLE gt_build.s_db_classifications AS
            SELECT DISTINCT ic.classificationid, ic.name, ic.isprimary, ic.confidence
            FROM gt_origin1.industryclassification ic
            WHERE ic.classificationid IN (SELECT classificationid FROM gt_build.s_db_companyindustry)
            ORDER BY 1""")
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_db_classifications"))
    log(f"s_db_classifications: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('diffbot','diffbot.industryclassification',{n},NULL)")

    psql("""DROP TABLE IF EXISTS gt_build.s_db_persons""")
    psql("""CREATE TABLE gt_build.s_db_persons AS
            SELECT p.personid, p.name, p.summary, p.role, p.types, p.confidence
            FROM gt_origin1.person p
            WHERE p.personid IN (SELECT personid FROM gt_build.s_db_companyexperience)
            ORDER BY 1""")
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_db_persons"))
    log(f"s_db_persons: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('diffbot','diffbot.person',{n},NULL)")

    # investors for closure rounds (investor ids are dangling; keep surfaceform)
    psql("""DROP TABLE IF EXISTS gt_build.s_db_investors""")
    psql("""CREATE TABLE gt_build.s_db_investors AS
            SELECT i.investmentid, i.investorcompanyid, i.surfaceform, i.summary, i.confidence
            FROM gt_origin1.companyinvestor i
            WHERE i.investmentid IN (SELECT investmentid FROM gt_build.s_db_companyinvestmentround)
            ORDER BY 1, 2""")
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_db_investors"))
    log(f"s_db_investors: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('diffbot','diffbot.companyinvestor',{n},NULL)")

    # ---- zoominfo side (ClickHouse HTTP) ----------------------------------
    def zi_inl(batch):
        return ",".join(q(x) for x in batch)

    psql("DROP TABLE IF EXISTS gt_build.s_zi_company")
    psql("""CREATE TABLE gt_build.s_zi_company (
              companyid text, name text, description text, domain text, website text,
              emp_range text, phone text, revenue text, revenue000s text, employees text,
              revenue_range text, ticker text, ctype text, dup_rows int)""")
    for batch_i in range(0, len(zi_ids), 400):
        batch = zi_ids[batch_i:batch_i + 400]
        rows = ch_rows(
            f"SELECT companyID, argMax(companyName,_version), argMax(companyDescription,_version), "
            f"argMax(companyDomain,_version), argMax(website,_version), "
            f"argMax(companyEmployeeCountRange,_version), argMax(companyPhone,_version), "
            f"argMax(companyRevenue,_version), toString(argMax(companyRevenueIn000s,_version)), "
            f"toString(argMax(companyEmployees,_version)), argMax(companyRevenueRange,_version), "
            f"argMax(companyTicker,_version), argMax(companyType,_version), count() "
            f"FROM Company WHERE companyID IN ({zi_inl(batch)}) "
            f"GROUP BY companyID ORDER BY companyID")
        if rows:
            copy_rows("s_zi_company",
                      ["companyid", "name", "description", "domain", "website", "emp_range",
                       "phone", "revenue", "revenue000s", "employees", "revenue_range",
                       "ticker", "ctype", "dup_rows"],
                      [[(r + [""] * 14)[:14]][0] for r in rows])
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_company"))
    log(f"s_zi_company: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('zoominfo','testzoominfo.Company',{n},NULL)")

    psql("DROP TABLE IF EXISTS gt_build.s_zi_ind")
    psql("""CREATE TABLE gt_build.s_zi_ind (companyid text, industryid text, isprimary text)""")
    for batch_i in range(0, len(zi_ids), 400):
        batch = zi_ids[batch_i:batch_i + 400]
        rows = ch_rows(
            f"SELECT DISTINCT companyID, industryID, isPrimary FROM CompanyIndustry "
            f"WHERE companyID IN ({zi_inl(batch)}) ORDER BY companyID, industryID")
        if rows:
            psql("INSERT INTO gt_build.s_zi_ind VALUES " +
                 ", ".join("(" + ",".join(q(c) for c in r) + ")" for r in rows))
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_ind"))
    log(f"s_zi_ind: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('zoominfo','testzoominfo.CompanyIndustry',{n},NULL)")

    # industries referenced
    psql("DROP TABLE IF EXISTS gt_build.s_zi_industry")
    psql("""CREATE TABLE gt_build.s_zi_industry (industryid text, name text)""")
    ind_ids = sorted({r[0] for r in dpsi("SELECT DISTINCT industryid FROM gt_build.s_zi_ind")})
    for batch_i in range(0, len(ind_ids), 400):
        batch = ind_ids[batch_i:batch_i + 400]
        rows = ch_rows(f"SELECT industryID, any(coalesce(name,'')) FROM Industry "
                       f"WHERE industryID IN ({zi_inl(batch)}) GROUP BY industryID ORDER BY industryID")
        if rows:
            psql("INSERT INTO gt_build.s_zi_industry VALUES " +
                 ", ".join("(" + ",".join(q(c) for c in r) + ")" for r in rows))
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_industry"))
    log(f"s_zi_industry: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('zoominfo','testzoominfo.Industry',{n},NULL)")

    psql("DROP TABLE IF EXISTS gt_build.s_zi_loc")
    psql("""CREATE TABLE gt_build.s_zi_loc (locationid text, companyid text, street text,
              city text, state text, zip text, country text)""")
    for batch_i in range(0, len(zi_ids), 400):
        batch = zi_ids[batch_i:batch_i + 400]
        rows = ch_rows(
            f"SELECT DISTINCT locationID, toString(companyID), coalesce(Street,''), coalesce(City,''), "
            f"coalesce(State,''), coalesce(Zip,''), coalesce(CountryCode,'') "
            f"FROM Location WHERE companyID IN ({zi_inl(batch)}) "
            f"ORDER BY locationID, companyID")
        if rows:
            copy_rows("s_zi_loc", ["locationid", "companyid", "street", "city",
                                   "state", "zip", "country"],
                      [[(r + [""] * 7)[:7]][0] for r in rows])
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_loc"))
    log(f"s_zi_loc: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('zoominfo','testzoominfo.Location',{n},NULL)")

    # Experience: full for closure (no cap). Split loadable (Person row exists) vs dropped.
    psql("DROP TABLE IF EXISTS gt_build.s_zi_exp_raw")
    psql("""CREATE TABLE gt_build.s_zi_exp_raw (personid text, companyid text, title text, jobtitle text)""")
    for batch_i in range(0, len(zi_ids), 400):
        batch = zi_ids[batch_i:batch_i + 400]
        rows = ch_rows(
            f"SELECT DISTINCT personID, companyID, coalesce(title,''), coalesce(jobTitle,'') "
            f"FROM Experience WHERE companyID IN ({zi_inl(batch)}) "
            f"ORDER BY personID, companyID, title, jobTitle")
        if rows:
            copy_rows("s_zi_exp_raw", ["personid", "companyid", "title", "jobtitle"],
                      rows)
    n_raw = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_exp_raw"))
    log(f"s_zi_exp_raw: {n_raw}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('zoominfo','testzoominfo.Experience',{n_raw},NULL)")

    # which closure persons have a Person row (name available)?
    pexp_ids = sorted({r[0] for r in dpsi("SELECT DISTINCT personid FROM gt_build.s_zi_exp_raw")})
    log(f"distinct zi persons in closure experience: {len(pexp_ids)}")
    psql("DROP TABLE IF EXISTS gt_build.s_zi_person_ids_ok")
    psql("CREATE TABLE gt_build.s_zi_person_ids_ok (personid text PRIMARY KEY)")
    # COPY cannot skip conflicts; batches are distinct per query so no dupes arise
    for batch_i in range(0, len(pexp_ids), 900):
        batch = pexp_ids[batch_i:batch_i + 900]
        rows = ch_rows(f"SELECT DISTINCT personID FROM Person "
                       f"WHERE personID IN ({zi_inl(batch)}) ORDER BY personID")
        if rows:
            copy_rows("s_zi_person_ids_ok", ["personid"], [[r[0]] for r in rows])
    n_ok = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_person_ids_ok"))
    log(f"zi persons WITH Person row: {n_ok} (without: {len(pexp_ids)-n_ok} -> drop_list)")
    # documented drop-list (§5/§6: nothing silently dropped)
    psql("""INSERT INTO gt_build.drop_list
            SELECT 'zoominfo','testzoominfo.Experience', e.personid||'|'||e.companyid,
                   'personID has no row in testzoominfo.Person — no name resolvable,
                    relationship would violate NOT NULL name; kept split & reported (§5)'
            FROM gt_build.s_zi_exp_raw e
            WHERE e.personid NOT IN (SELECT personid FROM gt_build.s_zi_person_ids_ok)""")

    psql("DROP TABLE IF EXISTS gt_build.s_zi_person")
    psql("""CREATE TABLE gt_build.s_zi_person (personid text, name text, first_name text,
              last_name text, middle_initial text)""")
    ok_ids = sorted({r[0] for r in dpsi("SELECT personid FROM gt_build.s_zi_person_ids_ok")})
    for batch_i in range(0, len(ok_ids), 900):
        batch = ok_ids[batch_i:batch_i + 900]
        rows = ch_rows(f"SELECT personID, any(coalesce(name,'')), any(coalesce(firstName,'')), "
                       f"any(coalesce(lastName,'')), any(coalesce(middleInitial,'')) "
                       f"FROM Person WHERE personID IN ({zi_inl(batch)}) "
                       f"GROUP BY personID ORDER BY personID")
        if rows:
            copy_rows("s_zi_person", ["personid", "name", "first_name", "last_name",
                                      "middle_initial"],
                      [[(r + [""] * 5)[:5]][0] for r in rows])
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_person"))
    log(f"s_zi_person: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('zoominfo','testzoominfo.Person',{n},NULL)")

    psql("DROP TABLE IF EXISTS gt_build.s_zi_pcontact")
    psql("""CREATE TABLE gt_build.s_zi_pcontact (personid text, freemails text)""")
    for batch_i in range(0, len(pexp_ids), 900):
        batch = pexp_ids[batch_i:batch_i + 900]
        rows = ch_rows(f"SELECT DISTINCT personID, freeMails FROM PersonContact "
                       f"WHERE personID IN ({zi_inl(batch)}) AND freeMails != '' "
                       f"ORDER BY personID, freeMails")
        if rows:
            psql("INSERT INTO gt_build.s_zi_pcontact VALUES " +
                 ", ".join("(" + ",".join(q(c) for c in r) + ")" for r in rows))
    n = int(dpsi1("SELECT count(*) FROM gt_build.s_zi_pcontact"))
    log(f"s_zi_pcontact: {n}")
    psql(f"INSERT INTO gt_build.extract_ledger VALUES ('zoominfo','testzoominfo.PersonContact',{n},NULL)")

    # ---- GATE (§5): every closure id present in its own origin's company extract
    miss_db = dpsi("""SELECT c.company_id FROM gt_build.company_closure c
                      WHERE c.origin='diffbot' AND NOT EXISTS
                      (SELECT 1 FROM gt_build.s_db_company s WHERE s.companyid=c.company_id)""")
    miss_zi = dpsi("""SELECT c.company_id FROM gt_build.company_closure c
                      WHERE c.origin='zoominfo' AND NOT EXISTS
                      (SELECT 1 FROM gt_build.s_zi_company s WHERE s.companyid=c.company_id)""")
    assert not miss_db and not miss_zi, ("§5 coverage gate FAILED", miss_db[:5], miss_zi[:5])
    log("§5 extraction coverage gate PASS: every closure id in its company extract")


# ============================================================ STAGE E
def stage_e():
    """Entity resolution -> gt_build.clusters / cluster_members / pair_decisions."""
    log("=== STAGE E: entity resolution ===")
    CIVEC = ("fire department", "police", "university", "college", "school",
             "association", "club", "foundation", "fansite", "department")

    # ---- candidate company records = closure + per-record normalized keys ----
    psql("DROP TABLE IF EXISTS gt_build.cand")
    psql("""CREATE TABLE gt_build.cand AS
            SELECT DISTINCT c.family, c.origin, c.company_id,
                   coalesce(sd.entityname, sz.name, '') AS primary_name,
                   coalesce(sd.fullname, '') AS legal_name,
                   coalesce(sd.website, sz.domain, sz.website, '') AS norm_domain
            FROM gt_build.company_closure c
            LEFT JOIN gt_build.s_db_company sd ON sd.companyid = c.company_id AND c.origin='diffbot'
            LEFT JOIN gt_build.s_zi_company sz ON sz.companyid = c.company_id AND c.origin='zoominfo'""")
    # normalized keys computed in python (shared normalizer), written back
    rows = dpsi("SELECT family, origin, company_id, primary_name, legal_name, norm_domain FROM gt_build.cand")
    upd = []
    for fam, org, cid, pname, lname, dom in rows:
        np_, nl, nd = norm_name(pname), norm_name(lname), norm_dom(dom)
        dr = dom_root(nd)
        civ = any(k in np_ for k in CIVEC)
        upd.append((fam, org, cid, np_, nl, nd, dr, civ))
    psql("ALTER TABLE gt_build.cand ADD COLUMN IF NOT EXISTS norm_primary text, "
         "ADD COLUMN IF NOT EXISTS norm_legal text, "
         "ADD COLUMN IF NOT EXISTS norm_domain2 text, ADD COLUMN IF NOT EXISTS dom_root text, "
         "ADD COLUMN IF NOT EXISTS civic boolean, ADD COLUMN IF NOT EXISTS is_fp boolean, "
         "ADD COLUMN IF NOT EXISTS country text")
    for col, idx in (("norm_primary", 3), ("norm_legal", 4), ("norm_domain2", 5), ("dom_root", 6)):
        for i in range(0, len(upd), 500):
            batch = upd[i:i + 500]
            case = " ".join(
                f"WHEN (family,origin,company_id)=({q(u[0])},{q(u[1])},{q(u[2])}) THEN {q(u[idx])}"
                for u in batch)
            psql(f"UPDATE gt_build.cand SET {col} = CASE {case} END")
    for i in range(0, len(upd), 500):
        batch = upd[i:i + 500]
        case = " ".join(
            f"WHEN (family,origin,company_id)=({q(u[0])},{q(u[1])},{q(u[2])}) THEN {'true' if u[7] else 'false'}"
            for u in batch)
        psql(f"UPDATE gt_build.cand SET civic = CASE {case} END")
    # FP flag + primary country
    psql("""UPDATE gt_build.cand SET is_fp = EXISTS
            (SELECT 1 FROM gt_build.false_positives f
             WHERE f.origin=cand.origin AND f.record_id=cand.company_id)""")
    psql("""UPDATE gt_build.cand SET country = coalesce(
            (SELECT lower(l.country) FROM gt_build.s_db_location l
             WHERE l.companyid=cand.company_id AND lower(l.isprimary)='true' LIMIT 1),
            (SELECT lower(l.country) FROM gt_build.s_db_location l
             WHERE l.companyid=cand.company_id ORDER BY l.locationid LIMIT 1),
            (SELECT lower(z.country) FROM gt_build.s_zi_loc z
             WHERE z.companyid=cand.company_id ORDER BY z.locationid LIMIT 1), '')""")
    n_cand = int(dpsi1("SELECT count(*) FROM gt_build.cand"))
    log(f"candidate company records: {n_cand}")

    # ---- pairwise decisions (family-scoped; over-merge worse -> conservative) --
    psql("DROP TABLE IF EXISTS gt_build.pair_decisions")
    psql("""CREATE TABLE gt_build.pair_decisions (
              family text, origin_a text, rec_a text, origin_b text, rec_b text,
              decision text, confidence float, method text, note text,
              PRIMARY KEY (family, origin_a, rec_a, origin_b, rec_b))""")
    # candidate pairs: same family; rule-block evaluated here in SQL
    psql(f"""INSERT INTO gt_build.pair_decisions
        SELECT a.family, a.origin, a.company_id, b.origin, b.company_id,
          CASE
            -- explicit FP never merges (§1.3/V5 traps)
            WHEN a.is_fp OR b.is_fp THEN 'rejected'
            -- §1.4 veto: two different non-empty domain roots
            WHEN a.dom_root != '' AND b.dom_root != '' AND a.dom_root != b.dom_root
              THEN 'rejected'
            -- civic-named entity never merges into a corporate family
            WHEN a.civic OR b.civic THEN 'rejected'
            -- §6.1 legal-name veto: two different explicit legal names -> split
            WHEN a.norm_legal != '' AND b.norm_legal != ''
              AND a.norm_legal != b.norm_legal THEN 'rejected'
            -- same name but one row carries a DIFFERENT legal name => distinct
            -- (sub)company sharing the brand -> borderline, stays split (§1.3)
            WHEN a.norm_primary = b.norm_primary
              AND ((a.norm_legal != '' AND a.norm_legal != a.norm_primary)
                OR (b.norm_legal != '' AND b.norm_legal != b.norm_primary))
              THEN 'borderline'
            -- A: same normalized primary name AND same normalized domain
            WHEN a.norm_primary = b.norm_primary AND a.norm_domain2 != ''
              AND a.norm_domain2 = b.norm_domain2 THEN 'accepted'
            -- B: same normalized primary name, same domain root (path differs)
            WHEN a.norm_primary = b.norm_primary AND a.dom_root = b.dom_root
              AND a.dom_root != '' THEN 'accepted'
            -- C: same normalized primary name both without domain
            WHEN a.norm_primary = b.norm_primary AND a.norm_domain2 = '' AND b.norm_domain2 = ''
              THEN 'accepted'
            -- C2: WITHIN origin, same normalized name, one side has no domain
            -- (§6.1 rule 2; no domain conflict exists) -> accepted 0.90
            WHEN a.origin = b.origin AND a.norm_primary = b.norm_primary
              AND (a.norm_domain2 = '' OR b.norm_domain2 = '')
              THEN 'accepted'
            -- legal-name equality (cross-check on full names)
            WHEN a.norm_legal != '' AND a.norm_legal = b.norm_legal THEN 'accepted'
            -- E: domain equal but different names -> borderline (§6.3 repair)
            WHEN a.norm_domain2 != '' AND a.norm_domain2 = b.norm_domain2 THEN 'borderline'
            -- §6.1 rule 3 (name containment) is BORDERLINE per §1.3: it catches
            -- divisions of the parent (Boeing Satellite Systems vs The Boeing
            -- Company) as often as true duplicates; divisions stay separate.
            WHEN a.origin = b.origin AND a.norm_primary != '' AND b.norm_primary != ''
              AND (strpos(b.norm_primary, a.norm_primary) > 0
                   OR strpos(a.norm_primary, b.norm_primary) > 0)
              THEN 'borderline'
            ELSE 'borderline'
          END,
          CASE
            WHEN a.norm_primary = b.norm_primary AND a.norm_domain2 != ''
              AND a.norm_domain2 = b.norm_domain2 THEN 0.98
            WHEN a.norm_primary = b.norm_primary AND a.dom_root = b.dom_root
              AND a.dom_root != '' THEN 0.95
            WHEN a.norm_primary = b.norm_primary THEN 0.9
            WHEN a.norm_legal != '' AND a.norm_legal = b.norm_legal THEN 0.95
            WHEN a.norm_domain2 != '' AND a.norm_domain2 = b.norm_domain2 THEN 0.7
            WHEN strpos(b.norm_primary, a.norm_primary) > 0
                  OR strpos(a.norm_primary, b.norm_primary) > 0 THEN 0.8
            ELSE 0.5
          END,
          'rule-based §6.1/§6.2',
          coalesce(a.norm_primary,'') || ' ~ ' || coalesce(b.norm_primary,'')
        FROM gt_build.cand a JOIN gt_build.cand b
          ON a.family = b.family
         AND (a.origin < b.origin OR (a.origin = b.origin AND a.company_id < b.company_id))""")
    nd = dpsi("SELECT decision, count(*) FROM gt_build.pair_decisions GROUP BY 1")
    log("pair decisions:", nd)

    # ---- §12 analyst edges (pinned; they survive the §6.3 conflict repair):
    # Thuraya = one cluster of 3 despite the thuraya-kazakhstan.kz scrape domain;
    # Airbus SE + Airbus SAS merged on shared-CEO evidence (§12 prior analyst pass).
    for fam, ra, oa, rb, ob, note in (
        ("thuraya", "EW-cM595xNjS5v41pCDvgwQ", "diffbot", "Eif_jUgzxOoWHqb_7EphYeQ", "diffbot",
         "§12 pitfall: two diffbot Thuraya rows + zoominfo Thuraya = one real company"),
        ("thuraya", "EW-cM595xNjS5v41pCDvgwQ", "diffbot", "345715253", "zoominfo",
         "§12: THURAYA row joins the Thuraya cluster despite scrape-artifact domain"),
        ("thuraya", "Eif_jUgzxOoWHqb_7EphYeQ", "diffbot", "345715253", "zoominfo",
         "§12: Thuraya Telecommunications = zoominfo Thuraya (thuraya.com both)"),
        ("airbus", "Ec_P38BE4MX6mRCDh9NWh_A", "diffbot", "EXRo_XwecMo6TPp_x5JybaA", "diffbot",
         "§12: prior analyst pass merged Airbus SE + Airbus SAS on shared CEO/founding "
         "evidence (both airbus.com, both d1970-12-18, both public)"),
    ):
        psql(f"""INSERT INTO gt_build.pair_decisions VALUES
            ({q(fam)},{q(oa)},{q(ra)},{q(ob)},{q(rb)},'accepted',0.90,'analyst §12',{q(note)})
            ON CONFLICT (family,origin_a,rec_a,origin_b,rec_b) DO UPDATE
              SET decision='accepted', confidence=0.90, method='analyst §12', note={q(note)}""")

    # ---- union-find in python over accepted edges ---------------------------
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    members = [r[0] for r in dpsi(
        "SELECT DISTINCT origin || ':' || company_id FROM gt_build.cand")]
    domroot_of = {r[0]: r[1] for r in dpsi(
        "SELECT DISTINCT origin||':'||company_id, dom_root FROM gt_build.cand")}
    accepted = dpsi("""SELECT origin_a, rec_a, origin_b, rec_b, confidence, method
                        FROM gt_build.pair_decisions WHERE decision='accepted'""")

    def build(edges):
        parent.clear()
        for m in members:
            find(m)
        for oa, ra, ob, rb, _cf, _mt in edges:
            union(f"{oa}:{ra}", f"{ob}:{rb}")
        out = {}
        for m in members:
            out.setdefault(find(m), []).append(m)
        return out

    comps = build(accepted)

    # ---- §6.3 REPAIR: a component carrying two conflicting domain roots is
    # re-cut without the auto edges that created the conflict; those edges are
    # demoted to borderline. Pinned 'analyst §12' edges are never removed.
    demoted = []
    def conflicts(mset):
        roots = {domroot_of.get(m, "") for m in mset if domroot_of.get(m, "")}
        return len(roots) > 1
    if any(conflicts(v) for v in comps.values()):
        keep, drop = [], []
        for e in accepted:
            (drop if (e[5] != "analyst §12") else keep).append(e)
        # greedily re-add auto edges that don't create a conflict
        cand_edges = list(keep)
        for e in sorted(drop):
            trial = cand_edges + [e]
            c2 = build(trial)
            if not any(conflicts(v) for v in c2.values()):
                cand_edges.append(e)
            else:
                demoted.append(e)
        comps = build(cand_edges)
        accepted = cand_edges
    log(f"§6.3 repair: demoted {len(demoted)} domain-conflicting edges")
    if demoted:
        vals = ",".join(
            f"('repair',{q(f'{e[0]}:{e[1]}')},{q(f'{e[2]}:{e[3]}')},{e[4]},{q('conflict-split §6.3')},{q(e[5])})"
            for e in demoted)
        psql(f"INSERT INTO gt_build.borderline (family,record_a,record_b,confidence,method,note) VALUES {vals}")
    log(f"clusters: {len(comps)} company clusters over {len(members)} records")

    psql("DROP TABLE IF EXISTS gt_build.clusters, gt_build.cluster_members")
    psql("""CREATE TABLE gt_build.clusters (
              cluster_key text PRIMARY KEY, entity_type text, entity_id uuid,
              canonical_name text, seed_family text)""")
    psql("""CREATE TABLE gt_build.cluster_members (
              cluster_key text, origin text, record_id text,
              PRIMARY KEY (cluster_key, origin, record_id))""")
    # family per record can be multiple (shared companies): collect separately
    fam_of = {}
    for r in dpsi("SELECT DISTINCT origin||':'||company_id, family FROM gt_build.cand"):
        fam_of.setdefault(r[0], set()).add(r[1])
    name_of = {}
    for r in dpsi("SELECT DISTINCT origin||':'||company_id, primary_name FROM gt_build.cand"):
        name_of.setdefault(r[0], r[1])

    stmts = []
    for root, mem in sorted(comps.items()):
        mem = sorted(set(mem))
        key = sha_hex("|".join(mem))
        fams = sorted({f for m in mem for f in fam_of.get(m, set())})
        canonical = name_of.get(mem[0], "")
        eid = uuid5_ns("merge-engine:kg:company:" + key)
        stmts.append(
            "INSERT INTO gt_build.clusters VALUES (" + q(key) + ",'company',"
            + q(str(eid)) + "::uuid," + q(canonical) + "," + q(",".join(fams)) + ")")
        stmts.append("INSERT INTO gt_build.cluster_members VALUES " +
                     ",".join(f"({q(key)},{q(m.split(':')[0])},{q(m.split(':')[1])})" for m in mem))
    for i in range(0, len(stmts), 40):
        exec_multi(stmts[i:i + 40])
    # verify: every cand record in exactly one cluster
    n_in = int(dpsi1("SELECT count(DISTINCT (origin,record_id)) FROM gt_build.cluster_members"))
    n_cand_d = int(dpsi1("SELECT count(DISTINCT (origin,company_id)) FROM gt_build.cand"))
    assert n_in == n_cand_d, (n_in, n_cand_d)
    n_ent = int(dpsi1("SELECT count(*) FROM gt_build.clusters WHERE entity_id IS NULL"))
    assert n_ent == 0

    # ---- persons: one cluster per (origin,personid); cross-origin exact-name
    # pairs recorded borderline only (no auto-merge; §1.3) ----------------------
    psql("DROP TABLE IF EXISTS gt_build.person_clusters, gt_build.person_members")
    psql("""CREATE TABLE gt_build.person_clusters (
              cluster_key text PRIMARY KEY, entity_id uuid, display_name text)""")
    psql("""CREATE TABLE gt_build.person_members (
              cluster_key text, origin text, record_id text, display_name text,
              PRIMARY KEY (cluster_key, origin, record_id))""")
    db_p = dpsi("""SELECT DISTINCT personid, name FROM gt_build.s_db_persons
                   ORDER BY personid""")
    zi_p = dpsi("""SELECT personid, name FROM gt_build.s_zi_person ORDER BY personid""")
    # bulk-load via COPY: cluster_key, entity_id, display_name
    import gtcore
    pc_rows = []
    pm_rows = []
    for org, ppl in (("diffbot", db_p), ("zoominfo", zi_p)):
        for pid, name in ppl:
            sha = sha_hex(f"{org}:{pid}")
            eid = uuid5_ns(f"merge-engine:kg:person:{sha}")
            pc_rows.append([sha, str(eid), name])
            pm_rows.append([sha, org, pid, name])
    gtcore.copy_rows("person_clusters", ["cluster_key", "entity_id", "display_name"], pc_rows)
    gtcore.copy_rows("person_members", ["cluster_key", "origin", "record_id", "display_name"], pm_rows)
    log(f"persons: diffbot={len(db_p)} zoominfo={len(zi_p)} (one entity each)")

    # cross-origin same-name person pairs at same family company -> borderline
    psql("""INSERT INTO gt_build.borderline
            SELECT DISTINCT 'persons', 'diffbot:'||dp.personid, 'zoominfo:'||zp.personid, 0.6,
                   'name-equality only',
                   'same name "'||dp.name||'" but distinct person ids across origins — kept split (§1.3)'
            FROM gt_build.s_db_persons dp
            JOIN gt_build.s_db_companyexperience de ON de.personid = dp.personid
            JOIN gt_build.cand dc ON dc.company_id = de.companyid AND dc.origin='diffbot'
            JOIN gt_build.s_zi_exp_raw ze ON ze.companyid IN (
                 SELECT company_id FROM gt_build.cand WHERE family = dc.family AND origin='zoominfo')
            JOIN gt_build.s_zi_person zp ON zp.personid = ze.personid
            WHERE lower(dp.name) = lower(zp.name)""")
    n_bl_p = int(dpsi1("SELECT count(*) FROM gt_build.borderline WHERE family='persons'"))
    log(f"borderline cross-origin person name pairs (kept split): {n_bl_p}")

    # surface borderline pairs from pair_decisions into the borderline table
    psql("""INSERT INTO gt_build.borderline
            SELECT family, origin_a||':'||rec_a, origin_b||':'||rec_b, confidence, method, note
            FROM gt_build.pair_decisions WHERE decision='borderline'""")

    # V5 trap assertion: traps must be their own single-member clusters
    for trap_origin, trap_id in [("zoominfo", "73664537"), ("zoominfo", "480703181"),
                                 ("zoominfo", "42603095"), ("diffbot", "E17cdTqqHO5y1wMmj4p50tg")]:
        n_mem = int(dpsi1(f"""SELECT count(*) FROM gt_build.cluster_members m
                             WHERE m.cluster_key = (SELECT cluster_key FROM gt_build.cluster_members
                                                    WHERE origin={q(trap_origin)} AND record_id={q(trap_id)})"""))
        assert n_mem == 1, f"V5 trap {trap_id} merged into cluster of {n_mem}!"
    log("V5 trap split-check PASS (Monterey Airbus / Al Thuraya / Capella U / Boeing Mesa FD alone)")

    # §12 check: Thuraya = one cluster with 3 members
    n_th = int(dpsi1("""SELECT count(*) FROM gt_build.cluster_members WHERE cluster_key =
                        (SELECT cluster_key FROM gt_build.cluster_members
                         WHERE origin='zoominfo' AND record_id='345715253')"""))
    log(f"thuraya cluster members: {n_th}")
    # Boeing cluster member list for eyeballing
    log("boeing-family clusters:",
        dpsi("""SELECT left(c.cluster_key,8), c.seed_family, count(*),
                       string_agg(m.origin||':'||left(m.record_id,12),'|' ORDER BY m.origin, m.record_id)
                FROM gt_build.clusters c JOIN gt_build.cluster_members m USING (cluster_key)
                WHERE c.seed_family LIKE '%boeing%' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 15""")[:15])


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "d"
    {"d": stage_d, "e": stage_e}[stage]()
