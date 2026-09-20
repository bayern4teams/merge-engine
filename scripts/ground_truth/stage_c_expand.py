"""
Stage C: chain expansion (3-hop) into gt_build.company_closure.

Orchestrates:
- level 0: seed roots + name-match family members (both origins)
- hops 1..3: walk company-company edges from each origin
  (diffbot companyrelation + investment edges; zoominfo company-level = via
   relations are n/a in CH, so zoominfo hops come from name/domain cross-links
   and the cross-origin closure join)
- cross-origin join: a diffbot hop company whose normalized domain/name matches
  a zoominfo (closure or not) company makes the zoominfo one a closure member
- dedupe on (origin, company_id) keeping min hop
"""
import sys
sys.path.insert(0, "/home/bayern/Projects/merge-engine/scripts/ground_truth")
from helpers import psql, ch_rows, normalize_name, normalize_domain, domain_root

DEST = "ground_truth"

# ---- pull diffbot closure edges: companyrelation + investment edges ----------
# companyrelation (companyid, relatedcompanyid, relationtype)
rel_rows = psql(DEST, """
    SELECT companyid, relatedcompanyid, relationtype
    FROM gt_origin1.companyrelation
WHERE companyid IN (SELECT company_id FROM gt_build.company_closure WHERE origin='diffbot')
   OR relatedcompanyid IN (SELECT company_id FROM gt_build.company_closure WHERE origin='diffbot')
""")
print("companyrelation rows (closure-touching):", len(rel_rows.splitlines()) if rel_rows.strip() else 0)
