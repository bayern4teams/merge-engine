"""
One-time: pull the diffbot tables needed by Stages C/D/E/F into local TSV caches
(via the postgres_fdw on destination, so pure SQL reads from ground_truth).
Caches live under scripts/ground_truth/cache_<table>.tsv (TSVWithNames-ish, pipe-sep
for embedding ease).
"""
import sys, os, time, json
sys.path.insert(0, "/home/bayern/Projects/merge-engine/scripts/ground_truth")
from helpers import psql

DEST = "ground_truth"
OUT = "/home/bayern/Projects/merge-engine/scripts/ground_truth/cache_diffbot"

os.makedirs(OUT, exist_ok=True)

tables = {
    "company": "companyid, entityname, fullname, website, description, summary, types, motto, foundingdate, totalinvestment, totalinvestmentcurrency, ispublic, isnonprofit, isacquired, isdissolved, ceoname, foundername, logo, sourcecategory, metadata_confidence, explicit_origins",
    "companycontact": "companyid, contactid, contactstring, contacttype",
    "companyrelation": "companyid, relatedcompanyid, relationtype, surfaceform, summary, types, confidence, explicit_origin",
    "companyinvestor": "investmentid, investorcompanyid, surfaceform, summary, types, confidence",
    "companyinvestmentround": "companyid, investmentid, date, amount, currency, confidence, explicit_origin",
    "companyfinancials": "companyid, financialid, year, amount, currency, iscurrent, confidence",
    "companyindustry": "companyid, classificationid",
    "companytechnographics": "companyid, techid, technologyname, technologytype, categories, confidence",
    "companyemployeecategory": "companyid, empcatid, category, firsthiredate, confidence",
    "companyexperience": "companyid, personid, personname, role, types, confidence",
    "industryclassification": "classificationid, name, isprimary, confidence",
    "location": "companyid, locationid, street, addressfull, city, region, postalcode, country, latitude, longitude, precision, isprimary, iscurrent",
    "person": "personid, name, summary, role, types, confidence",
}

for tbl, cols in tables.items():
    path = os.path.join(OUT, f"{tbl}.tsv")
    if os.path.exists(path):
        print(f"skip {tbl} (exists)")
        continue
    t0 = time.time()
    q = f"SELECT {cols} FROM gt_origin1.{tbl}"
    out = psql(DEST, q)
    with open(path, "w") as f:
        f.write(q + "\n")  # header line 0 = query
        f.write(out)
    print(f"{tbl}: {len(out.splitlines())} rows, {time.time()-t0:.1f}s -> {path} ({os.path.getsize(path)}b)")
print("done")
