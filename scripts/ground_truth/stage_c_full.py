"""
Stage C full: build gt_build.company_closure (3-hop family expansion), locally.

Pure Python over cached origins - deterministic, sorted sets everywhere.

Graph edges (company-company, per guide §4 row 1-2):
  diffbot companyrelation (both directions)
  diffbot investment edge: companyinvestor.investorcompanyid -> investee
  (zoominfo has no company-company edge table -> its hops come solely from
   seed roots + family-name matches + cross-origin join against diffbot hops)

Cross-origin join: for each NEW diffbot hop company, match zoominfo companies by
  normalized name (exact) or by domain root (official-family domain or exact
  normalized domain) -> add as zoominfo closure member (same hop as the diffbot
  company plus one).
"""

import os
import re
import sys
import time
from collections import defaultdict, deque

sys.path.insert(0, "/home/bayern/Projects/merge-engine/scripts/ground_truth")
from gtcore import dom_root, norm_dom, norm_name, psql

DEST = "ground_truth"
CD = "/home/bayern/Projects/merge-engine/scripts/ground_truth/cache_diffbot"
CH_CACHE = "/home/bayern/Projects/merge-engine/scripts/ground_truth/cache_zi_company.tsv"
MAX_HOP = 3


def read_lines(path):
    """Slurp a cache TSV (each is ~2 MB) so the handle closes deterministically."""
    with open(path, encoding="utf-8") as fh:
        return fh.readlines()


def sq(v):
    """SQL single-quote a literal, doubling embedded quotes (same rule as gtcore.q)."""
    return "'" + str(v).replace("'", "''") + "'"


# ---------------- read families ----------------
families = {}
for line in psql(
    "SELECT family, name_pattern, official_domain FROM gt_build.families"
).splitlines():
    if not line.strip():
        continue
    fam, np_, od = line.split("|")
    families[fam] = {"pattern": np_, "official_domain": norm_dom(od)}
print("families:", {k: v["pattern"] for k, v in families.items()})

# ---------------- read seed roots ----------------
seed_roots = []  # (family, origin, record_id)
for line in psql("SELECT family, origin, record_id FROM gt_build.seed_roots").splitlines():
    if not line.strip():
        continue
    fam, org, rid = line.split("|")
    seed_roots.append((fam, org, rid))

# ---------------- diffbot company cache ----------------
db_rows = {}  # companyid -> row dict
db_normname = defaultdict(list)  # normalized name -> [companyid]
db_domain = {}  # companyid -> normalized domain
for line in read_lines(os.path.join(CD, "company.tsv")):
    line = line.rstrip("\n")
    if line.startswith("SELECT") or not line.strip():
        continue
    cells = line.split("|")
    # companyid|entityname|fullname|website|description|... (13 cols)
    cid = cells[0].strip()
    row = {
        "id": cid,
        "entityname": cells[1].strip() if len(cells) > 1 else "",
        "fullname": cells[2].strip() if len(cells) > 2 else "",
        "website": cells[3].strip() if len(cells) > 3 else "",
    }
    db_rows[cid] = row
    for nm in (row["entityname"], row["fullname"]):
        nn = norm_name(nm)
        if nn:
            db_normname[nn].append(cid)
    dom = norm_dom(row["website"])
    db_domain[cid] = dom
print("diffbot company rows:", len(db_rows))

# also index diffbot by lowercased raw name token for word-boundary search:
db_raw_name = {}  # cid -> [entityname, fullname]
for cid, row in db_rows.items():
    db_raw_name[cid] = (row["entityname"], row["fullname"])

# ---------------- zoominfo index ----------------
zi_rows = {}
zi_normname = defaultdict(list)
zi_domain = {}
zi_domroot = defaultdict(list)
for line in read_lines(CH_CACHE):
    cells = line.rstrip("\n").split("\t")
    if cells[0] == "companyID":
        continue
    cid = cells[0]
    name = cells[1] if len(cells) > 1 else ""
    dom = cells[2] if len(cells) > 2 else ""
    zi_rows[cid] = {"id": cid, "name": name, "domain": dom}
    nn = norm_name(name)
    if nn:
        zi_normname[nn].append(cid)
    nd = norm_dom(dom)
    if nd:
        zi_domain[cid] = nd
        zi_domroot[dom_root(nd)].append(cid)
zi_normname = {k: sorted(set(v)) for k, v in zi_normname.items()}
print("zoominfo company rows:", len(zi_rows))


def db_name_match(cid: str, pattern: str) -> bool:
    """word-boundary name match of a diffbot company against a family pattern."""
    row = db_rows.get(cid)
    if not row:
        return False
    rx = re.compile(r"\b" + pattern + r"\b", re.IGNORECASE)
    return bool(rx.search(row["entityname"])) or bool(rx.search(row["fullname"]))


# ---------------- diffbot adjacency ----------------
adj = defaultdict(set)
for line in read_lines(os.path.join(CD, "companyrelation.tsv")):
    line = line.rstrip("\n")
    if line.startswith("SELECT") or not line.strip():
        continue
    cells = line.split("|")
    a, b = cells[0].strip().strip('"'), cells[1].strip().strip('"')
    adj[a].add(b)
    adj[b].add(a)
for line in read_lines(os.path.join(CD, "companyinvestor.tsv")):
    line = line.rstrip("\n")
    if line.startswith("SELECT") or not line.strip():
        continue
    cells = line.split("|")
    inv = cells[1].strip().strip('"')
    # companyinvestor: investmentid|investorcompanyid|surfaceform|...
    # need investee: investmentid -> companyinvestmentround.companyid
    pass
# investment edges: join investmentid
round_investee = {}
for line in read_lines(os.path.join(CD, "companyinvestmentround.tsv")):
    line = line.rstrip("\n")
    if line.startswith("SELECT") or not line.strip():
        continue
    cells = line.split("|")
    cid, iid = cells[0].strip().strip('"'), cells[1].strip().strip('"')
    round_investee[iid] = cid
for line in read_lines(os.path.join(CD, "companyinvestor.tsv")):
    line = line.rstrip("\n")
    if line.startswith("SELECT") or not line.strip():
        continue
    cells = line.split("|")
    iid = cells[0].strip().strip('"')
    inv = cells[1].strip().strip('"')
    invst = round_investee.get(iid)
    if inv and invst:
        adj[inv].add(invst)
        adj[invst].add(inv)
print("diffbot adjacency nodes:", len(adj))

# ---------------- BFS closure ----------------
print("building closure...")
t_stage = time.time()
# closure[(family, origin, id)] = hop
closure = {}
# level 0: seed roots
for fam, org, rid in seed_roots:
    closure.setdefault((fam, org, rid), 0)

# level 1: family-name matches
for fam, info in families.items():
    # diffbot name matches (3940 rows scan per family - cheap)
    for cid in db_rows:
        if db_name_match(cid, info["pattern"]):
            k = (fam, "diffbot", cid)
            closure[k] = min(closure.get(k, 99), 1)

# zoominfo name matches: use a token index (word-boundary on lowercase equals
# a token substring on a whitespace-split token set)
zi_tokens = {}  # token -> set of cids
for cid, row in zi_rows.items():
    for tok in re.split(r"[\s\-/.,&+]+", row["name"].lower()):
        if tok:
            zi_tokens.setdefault(tok, set()).add(cid)
for fam, info in families.items():
    fpat = info["pattern"].lower()
    tok_members = set()
    for cid in zi_tokens.get(fpat, ()):
        # exact token equal -> word match
        tok_members.add(cid)
    # also compound names like "airbusgroup" won't match "airbus" token; the
    # name 'Airbus Group' yields tokens {'airbus','group'} -> airbus matched.
    for cid in tok_members:
        k = (fam, "zoominfo", cid)
        closure[k] = min(closure.get(k, 99), 1)


# BFS hops 1..3 over diffbot edges
def bfs_db(fam):
    """expand diffbot closure for a family by company-company edges from hop<3 members."""
    # seed queue = all diffbot members <3 hops
    q = deque()
    for (f, org, cid), hop in closure.items():
        if f == fam and org == "diffbot" and hop < MAX_HOP:
            q.append((cid, hop))
    seen = set(closure.get((fam, "diffbot", c)) for c, h in q if False)  # noop
    visited = set()
    while q:
        cid, hop = q.popleft()
        if (fam, "diffbot", cid) in visited:
            continue
        visited.add((fam, "diffbot", cid))
        if hop >= MAX_HOP:
            continue
        for nxt in adj.get(cid, ()):
            if nxt not in db_rows:  # dangling id -> unresolved
                continue
            k = (fam, "diffbot", nxt)
            nh = hop + 1
            if nh < closure.get(k, 99):
                closure[k] = nh
                if nh < MAX_HOP:
                    q.append((nxt, nh))


for fam, info in families.items():
    bfs_db(fam)
print("BFS done:", time.time() - t_stage, "closure:", len(closure))


# cross-origin join: after diffbot closure, match zoominfo by name/domain
def cross_origin(fam, info):
    official_root = dom_root(info["official_domain"])
    # for each diffbot closure member, look up zi matches by EXACT normalized
    # name or EXACT normalized domain (or family-domain-root)
    for (f, org, cid), hop in list(closure.items()):
        if f != fam or org != "diffbot":
            continue
        row = db_rows.get(cid)
        if not row:
            continue
        candidates = set()
        for nm in (row["entityname"], row["fullname"]):
            nn = norm_name(nm)
            if nn:
                candidates.update(zi_normname.get(nn, ()))
        dom = db_domain.get(cid)
        if dom:
            # exact domain equality
            candidates.update(zi_domain.get(dom, ()))
            # family official domain root (e.g. boeing.com -> zi boeing.com)
            if dom_root(dom) == official_root:
                candidates.update(zi_domroot.get(official_root, ()))
        for zc in candidates:
            k = (fam, "zoominfo", zc)
            closure[k] = min(closure.get(k, 99), hop + 1)


for fam, info in families.items():
    cross_origin(fam, info)
print("cross-origin done:", time.time() - t_stage, "closure:", len(closure))

# hop clamp to 3
for k, h in list(closure.items()):
    if h > MAX_HOP:
        closure[k] = MAX_HOP

# ---------------- write closure ----------------
rows = sorted((fam, org, cid, hop) for (fam, org, cid), hop in closure.items())
print("closure rows:", len(rows))
# batch insert via a single multi-row INSERT (single psql call, chunked)
CHUNK = 500
for i in range(0, len(rows), CHUNK):
    chunk = rows[i : i + CHUNK]
    vals = ", ".join(f"({sq(fam)},{sq(org)},{sq(cid)},{hop})" for fam, org, cid, hop in chunk)
    sql = (
        "INSERT INTO gt_build.company_closure(family, origin, company_id, hop) VALUES "
        + vals
        + " ON CONFLICT (family, origin, company_id) DO UPDATE SET hop = LEAST(gt_build.company_closure.hop, EXCLUDED.hop)"
    )
    psql(sql)
print("wrote closure rows:", len(rows))
