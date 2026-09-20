"""
Shared helpers for the ground-truth manual build (post-truncate).
Pure SQL + Python orchestration tooling - no merge-engine app code.
Connections:
  origin1 diffbot:   psql 5433 testdiffbot (via FDW gt_origin1 on destination)
  origin2 zoominfo:  ClickHouse http://localhost:8125 testzoominfo
  destination:       psql 5434 ground_truth kg / gt_build
"""
import os
import re
import subprocess

PGPASSWORD = "supersecurepassword"


def psql(db: str, sql: str, host: str = "localhost", port: int = 5434, tuples_only: bool = True) -> str:
    """Run SQL against a postgres DB, return raw output."""
    env = dict(os.environ, PGPASSWORD=PGPASSWORD)
    if tuples_only:
        cmd = ["psql", "-h", host, "-p", str(port), "-U", "postgres", "-d", db, "-Atc", sql]
    else:
        cmd = ["psql", "-h", host, "-p", str(port), "-U", "postgres", "-d", db, "-c", sql]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"psql error: {p.stderr}\nSQL: {sql[:200]}")
    return p.stdout


def ch(sql: str, fmt: str = "TSV") -> str:
    """Run SQL against ClickHouse testzoominfo, return raw TSV."""
    url = ("http://localhost:8125/?user=postgres&password=supersecurepassword"
           "&database=testzoominfo")
    cmd = ["curl", "-s", url, "--data-binary", f"{sql} FORMAT {fmt}"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"curl error: {p.stderr}")
    return p.stdout


def ch_rows(sql: str) -> list[list[str]]:
    """Run CH SQL, return rows as list of string lists."""
    out = ch(sql, fmt="TSV")
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        rows.append(line.split("\t"))
    return rows


def normalize_name(n: str) -> str:
    """§6.1 name normalizer: lower, strip legal suffixes + punctuation, collapse ws."""
    if not n:
        return ""
    n = n.lower().strip()
    for stop in [
        "the ", "inc.", "inc", "ltd.", "ltd", "llc", "co.", "co", "corp.", "corp",
        "corporation", "limited", "company", "proprietary", "pty", "sa", "sas", "se",
        "gmbh", "s.p.a.", "spa", "ag", "nv", "bv", "plc", "s.r.l.", "srls", "llp",
        ",", ".", "&", "+",
    ]:
        n = n.replace(stop, " ")
    return " ".join(n.split())


def normalize_domain(w: str) -> str:
    """§7 website normalizer: strip scheme/www/trailing-slash, lowercase."""
    if not w:
        return ""
    w = w.lower().strip()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    w = w.rstrip("/")
    return w.strip()


def domain_root(d: str) -> str:
    """Registration root of a domain (e.g. sub.airbus.com -> airbus.com).

    Handles multi-part public suffixes: boeing.co.uk -> boeing.co.uk,
    airbus.com.au -> airbus.com.au, sub.example.com -> example.com.
    """
    if not d:
        return ""
    d = normalize_domain(d)
    parts = d.split(".")
    if len(parts) <= 2:
        return d
    # two-level public suffixes (ccTLD registries)
    two_level = {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk", "ltd.uk", "plc.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au",
        "co.nz", "org.nz", "net.nz", "ac.nz", "govt.nz", "geek.nz", "gen.nz", "kiwi.nz", "maori.nz", "school.nz",
        "co.za", "org.za", "net.za", "gov.za", "ac.za", "web.za", "law.za", "school.za", "nom.za",
        "com.br", "net.br", "org.br", "gov.br", "edu.br", "art.br", "esp.br", "eco.br", "mus.br", "adv.br", "blog.br", "flog.br", "fot.br", "ong.br", "radio.br", "rec.br", "sci.br", "tmp.br", "wiki.br", "jor.br", "lel.br",
        "com.tr", "net.tr", "org.tr", "gov.tr", "edu.tr", "biz.tr", "gen.tr", "info.tr", "k12.tr", "name.tr", "web.tr", "av.tr", "dr.tr", "pol.tr", "bel.tr", "com.mx", "net.mx", "org.mx", "edu.mx", "gob.mx",
        "co.in", "org.in", "net.in", "firm.in", "gen.in", "ind.in", "ac.in", "edu.in", "res.in", "gov.in", "mil.in", "nic.in",
        "co.jp", "or.jp", "ne.jp", "ac.jp", "ad.jp", "ed.jp", "go.jp", "gr.jp", "lg.jp",
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "bj.cn", "sh.cn", "gd.cn",
        "co.il", "org.il", "net.il", "ac.il", "gov.il", "muni.il", "idf.il",
        "com.pk", "net.pk", "org.pk", "edu.pk", "gov.pk", "gob.pk", "web.pk", "biz.pk", "fam.pk",
        "com.my", "net.my", "org.my", "gov.my", "edu.my", "mil.my", "name.my",
        "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg", "per.sg",
        "com.ar", "net.ar", "org.ar", "gob.ar", "edu.ar", "int.ar", "mil.ar", "mus.ar",
        "co.kr", "or.kr", "ne.kr", "re.kr", "pe.kr", "go.kr", "ac.kr", "hs.kr", "ms.kr", "es.kr", "sc.kr", "kg.kr", "seoul.kr",
        "co.id", "or.id", "web.id", "sch.id", "ac.id", "go.id", "mil.id", "net.id",
        "co.ke", "or.ke", "ne.ke", "go.ke", "ac.ke", "sc.ke", "me.ke", "mobi.ke", "info.ke",
        "co.ug", "or.ug", "ac.ug", "go.ug", "sc.ug", "ne.ug",
        "com.ng", "org.ng", "net.ng", "gov.ng", "edu.ng", "name.ng", "sch.ng", "mil.ng", "mobi.ng", "biz.ng",
        "com.eg", "org.eg", "net.eg", "gov.eg", "edu.eg", "ac.eg", "mil.eg",
        "com.ru", "net.ru", "org.ru", "pp.ru", "spb.ru", "msk.ru", "int.ru", "su.ru",
        "com.ua", "net.ua", "org.ua", "edu.ua", "gov.ua", "in.ua", "kiev.ua",
        "co.th", "or.th", "net.th", "ac.th", "go.th", "in.th", "mi.th",
        "com.tw", "net.tw", "org.tw", "edu.tw", "gov.tw", "idv.tw", "club.tw",
        "com.hk", "net.hk", "org.hk", "edu.hk", "gov.hk", "idv.hk",
        "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn", "biz.vn", "info.vn", "pro.vn", "name.vn", "ac.vn",
        "com.ph", "net.ph", "org.ph", "gov.ph", "edu.ph", "mil.ph", "ngo.ph", "i.ph",
        "co.tz", "or.tz", "ne.tz", "go.tz", "ac.tz", "sc.tz", "mil.tz",
        "com.uy", "net.uy", "org.uy", "edu.uy", "gub.uy",
    }
    if len(parts) >= 3 and ".".join(parts[-2:]) in two_level:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


if __name__ == "__main__":
    print("helpers OK")
