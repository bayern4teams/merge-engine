"""
One-time: pull the full zoominfo Company index (id, name, domain [+ a few seed
fields for later]) into a local sorted TSV cache, so cross-origin matching and
extraction don't hammer ClickHouse repeatedly.
"""
import sys, os, time
sys.path.insert(0, "/home/bayern/Projects/merge-engine/scripts/ground_truth")
from helpers import ch

CACHE = "/home/bayern/Projects/merge-engine/scripts/ground_truth/cache_zi_company.tsv"

if os.path.exists(CACHE) and os.path.getsize(CACHE) > 10_000_000:
    print("cache exists:", CACHE, os.path.getsize(CACHE))
    sys.exit(0)

t0 = time.time()
sql = """
SELECT companyID, companyName, companyDomain, companyEmployeeCountRange,
       companyRevenue, companyRevenueIn000s, companyTicker, companyType
FROM Company
ORDER BY companyID
"""
print("pulling %s ..." % sql.splitlines()[1].strip())
with open(CACHE, "w") as f:
    f.write(ch(sql, fmt="TSVWithNames"))
print("done in %.1fs, size=%d" % (time.time() - t0, os.path.getsize(CACHE)))
