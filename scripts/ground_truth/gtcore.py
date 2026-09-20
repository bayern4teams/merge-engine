"""Core infra + Stages B/C for the manual ground-truth build (no app code).

Deterministic: sorted sets everywhere (§1.5), uuid5 ids via
gt_build.uuid_generate_v5 (§6.4), independent SQL verification after every
step (§1.6). Stages: B seed roots -> C family membership + closure.
"""
import os
import re
import subprocess

DEST = "ground_truth"
PGPW = open("/tmp/.gtpw").read().strip()
RUN_ID = "gt-manual-2026-09-16"
CH_URL = f"http://localhost:8125/?user=postgres&password={PGPW}&database=testzoominfo"
WB = "(?i)\\\\b"  # CH regex word-boundary prefix


def psql(sql: str, db: str = DEST, tuples_only: bool = True) -> str:
    env = dict(os.environ, PGPASSWORD=PGPW)
    cmd = ["psql", "-h", "localhost", "-p", "5434", "-U", "postgres", "-d", db]
    cmd += ["-Atc", sql] if tuples_only else ["-c", sql]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"psql error: {p.stderr}\nSQL: {sql[:300]}")
    return p.stdout


def dpsi(sql: str, sep: str = "|") -> list[list[str]]:
    """Run SQL with unaligned output; split on `sep`. Use sep='\\x1f' (unit sep)
    when values may themselves contain '|'."""
    env = dict(os.environ, PGPASSWORD=PGPW)
    p = subprocess.run(
        ["psql", "-h", "localhost", "-p", "5434", "-U", "postgres", "-d", DEST,
         "-v", "ON_ERROR_STOP=1", "-Atq", "-F", sep, "-c", sql],
        capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"psql error: {p.stderr}\nSQL: {sql[:300]}")
    return [ln.split(sep) for ln in p.stdout.splitlines() if ln.strip()]


def dpsi1(sql: str) -> str:
    return psql(sql).strip()


def ch(sql: str, fmt: str = "TabSeparated") -> str:
    p = subprocess.run(["curl", "-s", CH_URL, "--data-binary", f"{sql} FORMAT {fmt}"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"curl error: {p.stderr}")
    if "DB::Exception" in p.stdout[:3000]:
        raise RuntimeError(f"CH error: {p.stdout[:800]}")
    return p.stdout


def ch_rows(sql: str) -> list[list[str]]:
    out = ch(sql)
    rows = []
    for ln in out.splitlines():
        if ln == "":
            continue
        cells = ln.split("\t")
        # ClickHouse TSV escapes embedded control chars as 2-char sequences; restore
        cells = [c.replace("\\\\", "\x00").replace("\\t", "\t").replace("\\n", "\n")
                  .replace("\\r", "\r").replace("\x00", "\\") for c in cells]
        rows.append(cells)
    return rows


def q(s) -> str:
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''").replace("\x00", "") + "'"


def psql_script(sql: str, db: str = DEST):
    """Run a full SQL script via stdin (no argv limit)."""
    env = dict(os.environ, PGPASSWORD=PGPW)
    p = subprocess.run(
        ["psql", "-h", "localhost", "-p", "5434", "-U", "postgres", "-d", db,
         "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"],
        input=sql, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"psql script error: {p.stderr}\nSQL head: {sql[:200]}")


def psql_stdin(sql_head: str, body: str):
    """Run `COPY ... FROM STDIN` with the given TSV body via -f - (no argv limit)."""
    env = dict(os.environ, PGPASSWORD=PGPW)
    script = sql_head.rstrip(";") + ";\n" + body + "\n\\.\n"
    p = subprocess.run(
        ["psql", "-h", "localhost", "-p", "5434", "-U", "postgres", "-d", DEST,
         "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"],
        input=script, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"psql stdin error: {p.stderr}\nSQL: {sql_head[:200]}")


def tsv_escape(s: str) -> str:
    if s is None:
        return "\\N"
    return (str(s).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\n", "\\n").replace("\r", "\\r"))


def copy_rows(table: str, cols: list[str], rows: list[list[str]]):
    body = "\n".join("\t".join(tsv_escape(c) for c in row) for row in rows)
    psql_stdin(f"COPY gt_build.{table} ({','.join(cols)}) FROM STDIN WITH (FORMAT text)", body)


def exec_multi(statements: list[str]):
    sql = "BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;"
    psql(sql)


def norm_name(n: str) -> str:
    """§6.1 normalizer: lower, strip legal suffixes + punctuation, collapse ws."""
    if not n:
        return ""
    n = n.lower().strip()
    for suf in ("corporation", "proprietary", "limited", "company", "international",
                "inc.", "inc", "ltd.", "ltd", "llc", "corp.", "corp", "gmbh",
                "pty", "plc", "spa", "s.p.a.", "sas", "se", "co.", "co", "sa",
                "ag", "nv", "bv", "the", ",", "."):
        n = re.sub(r"(?<!\w)" + re.escape(suf) + r"(?!\w)", " ", n)
    n = re.sub(r"(?<!\w)defence(?!\w)", "defense", n)   # spelling variant
    n = re.sub(r"(?<!\w)and(?!\w)", " ", n)             # connector noise: "defense and space" ~ "defense space"
    n = re.sub(r"(?<!\w)&(?!\w)", " ", n)
    return " ".join(n.split())


def norm_dom(w: str) -> str:
    if not w:
        return ""
    w = w.lower().strip()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    return w.rstrip("/")


def dom_root(d: str) -> str:
    d = norm_dom(d)
    if not d:
        return ""
    host = d.split("/")[0]
    parts = host.split(".")
    two = {"co.uk", "com.au", "co.in", "com.tr", "com.pk", "com.br", "co.jp",
           "com.cn", "com.sg", "com.hk", "com.tw", "co.nz", "com.my", "com.mx",
           "com.ru", "com.ua", "com.eg", "com.vn", "com.ph", "co.id", "co.kr",
           "com.ar", "co.za", "com.ng", "com.th", "com.il", "co.ke"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# ---------------------------------------------------------------- seed roots
SEED_ROOTS = [
    ("boeing", "diffbot", "EMAakygGHMUiRPfT6DPOr6Q",
     "root: Boeing / The Boeing Company, boeing.com, founding d1916-07-15, public (guide-confirmed)"),
    ("boeing", "zoominfo", "5128546",
     "root: Boeing, boeing.com, NYSE: BA (guide-confirmed seed id)"),
    ("airbus", "diffbot", "Ec_P38BE4MX6mRCDh9NWh_A",
     "root: Airbus SE / AIRBUS SE, airbus.com, d1970-12-18, public (guide-confirmed)"),
    ("airbus", "diffbot", "EXRo_XwecMo6TPp_x5JybaA",
     "root: Airbus / Airbus SAS, airbus.com (guide-confirmed)"),
    ("airbus", "zoominfo", "365384179",
     "root: Airbus Helicopters, domain airbus.com/helicopters.html (guide-confirmed seed id)"),
    ("thuraya", "diffbot", "Eif_jUgzxOoWHqb_7EphYeQ",
     "root: Thuraya Telecommunications, thuraya.com, d1997-04-01"),
    ("thuraya", "diffbot", "EW-cM595xNjS5v41pCDvgwQ",
     "root: THURAYA — §12 states the two diffbot Thuraya rows (THURAYA + "
     "Thuraya Telecommunications) + zoominfo Thuraya are ONE real company "
     "(cluster of 3). Reseller-ish domain thuraya-kazakhstan.kz treated as "
     "scrape artifact on the official-name row; flagged borderline-high in report."),
    ("thuraya", "zoominfo", "345715253",
     "root: Thuraya, thuraya.com (guide-confirmed seed id)"),
    ("ohb", "diffbot", "Ei7Rdy8jOPwSaA07eN7gnpw",
     "root: Ohb Se / OHB SE, ohb.de, d1981, public (guide-confirmed seed id)"),
    ("ohb", "zoominfo", "28656225",
     "root: OHB System, ohb.de, FRA: OHB (guide-confirmed seed id; OHB SE "
     "subsidiary per surfaceform 'OHB System AG'; joins OHB family via "
     "cross-origin domain match §4)"),
    ("capella", "diffbot", "Eab4Feq-pPeSUpmSKYuToDA",
     "root: Capella Space / Capella Space Corporation, capellaspace.com, d2016-03"),
    ("capella", "zoominfo", "398000639",
     "root: Capella Space, capellaspace.com (guide-confirmed seed id)"),
]

# §3 explicit false positives + §6.2/§1.4 domain-veto name matches. They stay
# OUT of the family root/cluster; §4 note lets each distinct real company enter
# the closure as its own SPLIT stub (hop 2) so its facts still land.
FALSE_POSITIVES = [
    ("thuraya", "zoominfo", "480703181", "Al Thuraya Rent A Car", "althurayarentacar.com", "guide §3 explicit FP; §1.4 domain veto"),
    ("thuraya", "zoominfo", "474376460", "Al Thuraya Security", "althurayasecurity.com", "guide §3 explicit FP; §1.4 domain veto"),
    ("thuraya", "zoominfo", "464117011", "Althuraya", "althuraya-usa.com", "guide §3 explicit FP (althuraya-usa.com); §1.4 domain veto"),
    ("thuraya", "zoominfo", "372644191", "IMTCL - Thuraya Satellite Telecom, Pakistan", "thuraya.com.pk", "reseller, domain != thuraya.com (§1.4)"),
    ("airbus", "zoominfo", "73664537", "Monterey Airbus", "montereyairbus.com", "restaurant (guide §3 explicit)"),
    ("capella", "zoominfo", "42603095", "Capella University", "capella.edu", "university (guide §3 explicit)"),
    ("capella", "zoominfo", "353775941", "Capella Graphics", "capellagraphics.com", "different domain"),
    ("capella", "zoominfo", "1320370968", "PT Capella Dinamik Nusantara", "cdn.co.id", "different domain"),
    ("capella", "zoominfo", "356199791", "Capella Solutions Group", "capellasolutionsgroup.com", "different domain"),
    ("capella", "zoominfo", "358319255", "La Capella Organizasyon", "lacapella.org", "different domain"),
    ("capella", "zoominfo", "398715534", "Capella & Alhena Client Solutions Pvt", "capellaalhena.com", "different domain"),
    ("capella", "zoominfo", "449121157", "Capella Apparel", "capellaapparel.com", "different domain"),
    ("capella", "zoominfo", "464412389", "Capella & Auriga", "capella.com.tr", "different domain"),
    ("capella", "zoominfo", "474225240", "Capella Transport", "capellatransport.com", "different domain"),
    ("capella", "zoominfo", "539807602", "Capella Marin Ship & Yacht Agency Services", "capellamarin.net", "different domain"),
    ("capella", "zoominfo", "542901277", "Capella Nova", "capellanova.org", "different domain"),
    ("capella", "zoominfo", "546711959", "A Capella Boğaziçi", "acapellabogazici.com", "different domain"),
    ("capella", "zoominfo", "560339990", "Capella Professional", "capellaprofessionalrecruitment.co.uk", "different domain"),
    ("capella", "zoominfo", "6668665", "Capella Microsystems", "capellamicro.com.tw", "different domain"),
    ("capella", "zoominfo", "537016982", "PT Capella Multidana", "capellamultidana.com", "different domain (financial holding)"),
    ("boeing", "zoominfo", "30757599", "Boeing Trailers AU", "boeingtrailers.com.au", "different domain (trailer maker)"),
    ("boeing", "zoominfo", "346020235", "Boeing 737", "b737.org.uk", "aircraft fansite"),
    ("boeing", "zoominfo", "447961817", "Boeing Material Handling", "boeingmhc.com.ph", "different domain (PH)"),
    ("boeing", "zoominfo", "16224339", "Boeing Employees Flying Association", "befa.org", "employee association, own domain — split stub (§1.3)"),
    ("boeing", "zoominfo", "18143512", "Boeing Employees Tennis Club", "betconline.net", "employee club, own domain — split stub (§1.3)"),
    ("boeing", "zoominfo", "359197544", "Spirit Boeing Employees' Association", "beatoday.com", "employee association, own domain — split stub (§1.3)"),
    ("boeing", "zoominfo", "1268015193", "Boeing Vancouver", "boeingvancouver.com", "own domain — borderline, kept SPLIT (§1.3)"),
    ("boeing", "zoominfo", "1163680190", "Aviation Partners Boeing", "aviationpartnersboeing.com", "joint venture own domain — kept SPLIT"),
    ("boeing", "zoominfo", "482339782", "ATI Boeing Accelerator", "atiboeingaccelerator.com", "own domain — kept SPLIT"),
    ("boeing", "zoominfo", "1226675440", "Boeing Travel Management", "(no domain)", "no domain; below §6.2 merge threshold — kept SPLIT"),
    ("airbus", "zoominfo", "1102392658", "Airbus Helicopters Foundation", "airbusgroupap.com.au", "own domain — kept SPLIT"),
    ("airbus", "zoominfo", "1107110998", "Airbus", "airbus-sv.com", "own domain (A3 innovation center) — kept SPLIT"),
    ("airbus", "zoominfo", "1115568140", "Airbus Group", "airbusgroup.com", "own domain; §12 warns not to assume the concat; kept SPLIT (borderline-high)"),
    ("airbus", "zoominfo", "1142580865", "Airbus Ds Geo Australia", "geo-airbusds.com", "own domain — kept SPLIT"),
    ("airbus", "zoominfo", "359361969", "Airbus Helicopters UK", "airbushelicopters.co.uk", "own domain — borderline, kept SPLIT (§1.3)"),
    ("airbus", "zoominfo", "368973570", "Airbus DS Houston", "airbusdshouston.com", "own domain — kept SPLIT"),
    ("airbus", "zoominfo", "449306384", "Airbus Bank", "airbusbank.com", "own domain — kept SPLIT"),
    ("airbus", "zoominfo", "450171088", "Airbus Aerial", "airbusaerial.com", "own domain — kept SPLIT"),
    ("airbus", "zoominfo", "471447426", "Airbus UTM", "airbusutm.com", "own domain — kept SPLIT"),
    ("airbus", "zoominfo", "507376090", "Airbus Secure Communications", "securecommunications-airbusds.com", "own domain — kept SPLIT"),
    ("airbus", "zoominfo", "538878747", "Airbus Turkiye", "airbusturkiye.com", "own domain — kept SPLIT"),
    ("boeing", "diffbot", "E17cdTqqHO5y1wMmj4p50tg", "Boeing Mesa Fire Department", "boeing.com/features/...", "fire dept named after Boeing — V5 trap: stub only, NOT the Boeing root; kept SPLIT"),
]

# diffbot name-match rows kept as SEED-MEMBER entities (hop-1 name matches; the
# closure/min-hop rule plus §6.1 dedup decides clustering later).
OFFICIAL_DOMAINS = {"boeing": "boeing.com", "airbus": "airbus.com",
                    "thuraya": "thuraya.com", "ohb": "ohb.de",
                    "capella": "capellaspace.com"}


def stage_a_check():
    v = dpsi1("""SELECT (SELECT count(*) FROM kg.taxonomy),
       (SELECT count(*) FROM kg.taxonomy_term),
       (SELECT count(*) FROM kg.taxonomy_allowed_entity_type),
       (SELECT count(*) FROM kg.fact_predicate),
       (SELECT count(*) FROM kg.relationship_type),
       (SELECT count(*) FROM kg.event_type),
       (SELECT count(*) FROM kg.currency),
       (SELECT count(*) FROM kg.schema_version)""")
    assert v == "19|106|9|23|24|25|14|1", f"§2.2 vocab post-check FAILED: {v}"
    e, ki, kf, ev = (int(dpsi1(f"SELECT count(*) FROM kg.{t}"))
                     for t in ("entity", "knowledge_item", "knowledge_fact", "evidence"))
    assert (e, ki, kf, ev) == (0, 0, 0, 0), f"destination not empty: {e},{ki},{kf},{ev}"
    src = dpsi1("SELECT string_agg(source_id::text||':'||uri, ', ' ORDER BY source_id) FROM kg.source")
    assert src.startswith("1:postgresql://localhost:5433"), src
    print("STAGE A verified: vocab 19|106|9|23|24|25|14|1, destination empty, sources 1/2 registered")


def stage_b():
    stmts = [
        "CREATE SCHEMA IF NOT EXISTS gt_build",
        'CREATE EXTENSION IF NOT EXISTS "uuid-ossp" SCHEMA gt_build',
    ]
    for t in ("seed_roots", "false_positives", "company_closure", "family_members",
              "dangling_endpoints", "unresolved_links", "borderline",
              "unmapped_fields", "extract_ledger", "drop_list", "observations"):
        stmts.append(f"DROP TABLE IF EXISTS gt_build.{t}")
    stmts += [
        "CREATE TABLE gt_build.seed_roots (family text, origin text, record_id text, rationale text)",
        "CREATE TABLE gt_build.false_positives (family text, origin text, record_id text, name text, domain text, reason text)",
        "CREATE TABLE gt_build.company_closure (family text, origin text, company_id text, hop int, PRIMARY KEY (family, origin, company_id))",
        "CREATE TABLE gt_build.family_members (family text, origin text, company_id text, via text)",
        "CREATE TABLE gt_build.dangling_endpoints (family text, rel_kind text, endpoint_id text, surfaceform text, relationtype text)",
        "CREATE TABLE gt_build.unresolved_links (origin text, edge_kind text, endpoint_id text, surfaceform text, relationtype text, seen_in text)",
        "CREATE TABLE gt_build.borderline (family text, record_a text, record_b text, confidence float, method text, note text)",
        "CREATE TABLE gt_build.unmapped_fields (origin text, source_table text, field text, rows_count bigint, reason text)",
        "CREATE TABLE gt_build.extract_ledger (origin text, source_table text, rows_extracted bigint, rows_landed bigint)",
        "CREATE TABLE gt_build.drop_list (origin text, source_table text, row_ref text, reason text)",
        "CREATE TABLE gt_build.observations (kind text, note text)",
    ]
    stmts.append("INSERT INTO gt_build.seed_roots VALUES " +
                 ", ".join("(%s,%s,%s,%s)" % tuple(q(x) for x in r) for r in SEED_ROOTS))
    stmts.append("INSERT INTO gt_build.false_positives VALUES " +
                 ", ".join("(%s,%s,%s,%s,%s,%s)" % tuple(q(x) for x in r) for r in FALSE_POSITIVES))
    exec_multi(stmts)
    n_sr = int(dpsi1("SELECT count(*) FROM gt_build.seed_roots"))
    n_fp = int(dpsi1("SELECT count(*) FROM gt_build.false_positives"))
    assert n_sr == len(SEED_ROOTS) and n_fp == len(FALSE_POSITIVES)
    for fam, org, rid, _ in SEED_ROOTS:
        if org == "diffbot":
            n = int(dpsi1(f"SELECT count(*) FROM gt_origin1.company WHERE companyid={q(rid)}"))
        else:
            n = int(ch(f"SELECT count() FROM Company WHERE companyID={q(rid)}").strip())
        assert n > 0, f"seed root {org}:{rid} missing from origin!"
    # guide §3 FP althuraya-usa.com: verify it is captured in FALSE_POSITIVES
    n1 = int(dpsi1("SELECT count(*) FROM gt_origin1.company WHERE website ILIKE '%althuraya-usa%'"))
    n2 = int(ch("SELECT count() FROM Company WHERE companyDomain ILIKE '%althuraya-usa%'").strip())
    assert any(f[2] == "464117011" and f[4] == "althuraya-usa.com" for f in FALSE_POSITIVES)
    psql("INSERT INTO gt_build.observations VALUES "
         f"('fp-absent', 'althuraya-usa.com (guide §3 FP): diffbot={n1} rows, zoominfo={n2} row(s) — captured in false_positives as 464117011')")
    print(f"STAGE B: {n_sr} seed roots verified present in origins; {n_fp} FP rows recorded")


def stage_c():
    # ---- family membership (level 0/1): word-boundary name OR official-domain prefix
    exec_multi(["DELETE FROM gt_build.family_members"])
    per = []
    for fam, root in OFFICIAL_DOMAINS.items():
        pat = q("\\y" + fam + "\\y")
        per.append(
            f"SELECT {q(fam)}, 'diffbot', companyid, 'name' FROM gt_origin1.company "
            f"WHERE entityname ~* {pat} OR fullname ~* {pat}")
        per.append(
            f"SELECT {q(fam)}, 'diffbot', companyid, 'domain' FROM gt_origin1.company "
            f"WHERE website LIKE {q(root + '/%')} OR website = {q(root)}")
    exec_multi(["INSERT INTO gt_build.family_members " + " UNION ALL ".join(per)])
    for fam, root in OFFICIAL_DOMAINS.items():
        rx_root = re.escape(root).replace("\\", "\\\\")  # CH literal needs \\ for regex \.
        rows = ch_rows(
            f"SELECT DISTINCT companyID FROM Company "
            f"WHERE match(companyName, '{WB}{fam}\\\\b') "
            f"OR match(companyDomain, '(?i)^(www\\\\.)?{rx_root}(/|$)') "
            f"ORDER BY companyID")
        ids = sorted({r[0] for r in rows})
        # domain rows vs name rows
        name_rows = ch_rows(f"SELECT DISTINCT companyID FROM Company WHERE match(companyName,'{WB}{fam}\\\\b') ORDER BY companyID")
        name_ids = {r[0] for r in name_rows}
        for chunk_start in range(0, len(ids), 500):
            batch = ids[chunk_start:chunk_start + 500]
            vals = ", ".join(
                f"({q(fam)},'zoominfo',{q(i)},{q('name' if i in name_ids else 'domain')})"
                for i in batch)
            psql(f"INSERT INTO gt_build.family_members VALUES {vals}")

    # verify: §3 seed-query results ⊆ membership
    db_names = {r[0] for r in dpsi(
        "SELECT companyid FROM gt_origin1.company WHERE entityname ~* '\\y(boeing|airbus|thuraya|ohb|capella)\\y' OR fullname ~* '\\y(boeing|airbus|thuraya|ohb|capella)\\y'")}
    mem_db = {r[2] for r in dpsi("SELECT * FROM gt_build.family_members WHERE origin='diffbot'")}
    assert db_names <= mem_db, ("diffbot membership lost rows", db_names - mem_db)
    zi_names = {r[0] for r in ch_rows(
        "SELECT DISTINCT companyID FROM Company WHERE match(companyName,'(?i)\\\\b(boeing|airbus|thuraya|ohb|capella)\\\\b') OR match(companyDomain,'(?i)^(www\\\\.)?(boeing\\\\.com|airbus\\\\.com|airbus\\\\.se|thuraya\\\\.com|ohb\\\\.de|capellaspace\\\\.com)')")}
    mem_zi = {r[2] for r in dpsi("SELECT * FROM gt_build.family_members WHERE origin='zoominfo'")}
    assert zi_names <= mem_zi, ("zi membership lost rows", zi_names - mem_zi)
    print("membership:", dpsi("SELECT family, origin, via, count(*) FROM gt_build.family_members GROUP BY 1,2,3 ORDER BY 1,2,3"))

    # ---- closure: hop0 = seed_roots; hop1 = family name matches; hop2 = family
    # domain-only matches + FP stubs (§4: distinct real near-names may enter as stubs).
    # company-company edges cannot expand (all diffbot relatedcompanyid/investorcompanyid
    # are dangling in this snapshot — recorded via dangling_endpoints below, §4).
    exec_multi([
        "DELETE FROM gt_build.company_closure",
        "INSERT INTO gt_build.company_closure SELECT family, origin, record_id, 0 FROM gt_build.seed_roots",
        # hop1: name matches (min-hop: seed rows keep 0)
        "INSERT INTO gt_build.company_closure "
        "SELECT m.family, m.origin, m.company_id, 1 FROM gt_build.family_members m "
        "WHERE m.via='name' AND NOT EXISTS (SELECT 1 FROM gt_build.company_closure c "
        "  WHERE c.family=m.family AND c.origin=m.origin AND c.company_id=m.company_id) "
        "AND NOT EXISTS (SELECT 1 FROM gt_build.false_positives f "
        "  WHERE f.origin=m.origin AND f.record_id=m.company_id)",
        # hop2: domain-only matches
        "INSERT INTO gt_build.company_closure "
        "SELECT m.family, m.origin, m.company_id, 2 FROM gt_build.family_members m "
        "WHERE m.via='domain' AND NOT EXISTS (SELECT 1 FROM gt_build.company_closure c "
        "  WHERE c.family=m.family AND c.origin=m.origin AND c.company_id=m.company_id) "
        "AND NOT EXISTS (SELECT 1 FROM gt_build.false_positives f "
        "  WHERE f.origin=m.origin AND f.record_id=m.company_id)",
        # hop2: FP stubs (own entities; §4 note)
        "INSERT INTO gt_build.company_closure "
        "SELECT f.family, f.origin, f.record_id, 2 FROM gt_build.false_positives f "
        "WHERE NOT EXISTS (SELECT 1 FROM gt_build.company_closure c "
        "  WHERE c.family=f.family AND c.origin=f.origin AND c.company_id=f.record_id)",
    ])
    # closure ids must exist in their origins
    missing_db = dpsi("SELECT company_id FROM gt_build.company_closure c WHERE origin='diffbot' AND NOT EXISTS (SELECT 1 FROM gt_origin1.company o WHERE o.companyid=c.company_id)")
    assert not missing_db, ("closure diffbot ids missing", missing_db)
    zi_ids = [r[0] for r in dpsi("SELECT company_id FROM gt_build.company_closure WHERE origin='zoominfo' ORDER BY 1")]
    for i in range(0, len(zi_ids), 500):
        batch = zi_ids[i:i + 500]
        inl = ",".join(q(x) for x in sorted(batch))
        n = int(ch(f"SELECT count(DISTINCT companyID) FROM Company WHERE companyID IN ({inl})").strip())
        assert n == len(set(batch)), (n, len(set(batch)))

    # ---- dangling company-company endpoints (§4: unresolved-link evidence, not entities)
    exec_multi([
        "DELETE FROM gt_build.dangling_endpoints",
        "INSERT INTO gt_build.dangling_endpoints "
        "SELECT DISTINCT m.family, 'companyrelation', r.relatedcompanyid, r.surfaceform, r.relationtype "
        "FROM gt_origin1.companyrelation r JOIN gt_build.family_members m "
        "  ON m.company_id=r.companyid AND m.origin='diffbot'",
        "INSERT INTO gt_build.dangling_endpoints "
        "SELECT DISTINCT m.family, 'companyinvestor', i.investorcompanyid, i.surfaceform, 'invests_in' "
        "FROM gt_origin1.companyinvestor i "
        "JOIN gt_origin1.companyinvestmentround r USING (investmentid) "
        "JOIN gt_build.family_members m ON m.company_id=r.companyid AND m.origin='diffbot'",
    ])
    n_res = int(dpsi1("SELECT count(*) FROM gt_build.dangling_endpoints d WHERE EXISTS "
                      "(SELECT 1 FROM gt_origin1.company c WHERE c.companyid=d.endpoint_id)"))
    assert n_res == 0, f"endpoints unexpectedly resolvable: {n_res}"
    dang = dpsi("SELECT rel_kind, count(DISTINCT endpoint_id) FROM gt_build.dangling_endpoints GROUP BY 1")
    print("dangling endpoints (distinct):", dang)
    print("closure:", dpsi("SELECT family, origin, hop, count(*) FROM gt_build.company_closure GROUP BY 1,2,3 ORDER BY 1,2,3"))
    tot = dpsi("SELECT origin, count(DISTINCT company_id) FROM gt_build.company_closure GROUP BY 1")
    print("closure distinct per origin:", tot)
    # §4 magnitude guard: no family should exceed 5,000 companies
    for r in dpsi("SELECT family, count(DISTINCT company_id) FROM gt_build.company_closure GROUP BY 1 HAVING count(DISTINCT company_id) > 5000"):
        raise RuntimeError(f"§4 closure leak guard tripped: {r}")


if __name__ == "__main__":
    stage_a_check()
    stage_b()
    stage_c()
