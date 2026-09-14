"""Live Splink tuning run: real diffbot + zoominfo Boeing/Airbus company records.

Standalone script (not part of the app) -- run with the project's own venv,
against the fix in src/mcp_server/tools/splink_matcher.py, to measure the
before/after effect of the blocking-rule-union + domain-normalisation fix
on real production data. Prints pair counts and scores for a set of
hand-labelled true-duplicate pairs (built by direct inspection of the real
rows, same method the audit report's ground_truth build used).
"""
import asyncio
import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from src.mcp_server.tools.resolve_entities import _record_id  # noqa: E402
from src.mcp_server.tools.splink_matcher import score_pairs_with_splink  # noqa: E402

_HERE = Path(__file__).resolve().parent


def diffbot_records():
    recs = []
    with open(_HERE / "diffbot_rows.tsv") as f:
        for row in csv.reader(f, delimiter="\t"):
            companyid, entityname, fullname, website, summary = row
            recs.append({
                "_origin_id": "testdiffbot",
                "_origin_table": "company",
                "_origin_id_row": companyid,
                "entityname": entityname,
                "fullname": fullname,
                "website": website,
                "summary": summary,
            })
    return recs


# Pulled directly via the ClickHouse MCP tool this session (testzoominfo.Company)
ZOOMINFO_ROWS = [
    ("482339782","ATI Boeing Accelerator","www.atiboeingaccelerator.com","www.atiboeingaccelerator.com"),
    ("1107110998","Airbus","www.airbus-sv.com","www.airbus-sv.com"),
    ("450171088","Airbus Aerial","www.airbusaerial.com","www.airbusaerial.com"),
    ("449306384","Airbus Bank","www.airbusbank.com","www.airbusbank.com"),
    ("368973570","Airbus DS Houston","www.airbusdshouston.com","www.airbusdshouston.com"),
    ("1142580865","Airbus Ds Geo Australia","www.geo-airbusds.com","www.geo-airbusds.com"),
    ("1115568140","Airbus Group","www.airbusgroup.com","www.airbusgroup.com"),
    ("365384179","Airbus Helicopters","www.airbus.com/helicopters.html","www.airbus.com/helicopters.html"),
    ("1102392658","Airbus Helicopters Foundation","www.airbusgroupap.com.au","www.airbusgroupap.com.au"),
    ("359361969","Airbus Helicopters UK","www.airbushelicopters.co.uk","www.airbushelicopters.co.uk"),
    ("507376090","Airbus Secure Communications","www.securecommunications-airbusds.com","www.securecommunications-airbusds.com"),
    ("538878747","Airbus Turkiye","www.airbusturkiye.com","www.airbusturkiye.com"),
    ("471447426","Airbus UTM","www.airbusutm.com","www.airbusutm.com"),
    ("1163680190","Aviation Partners Boeing","www.aviationpartnersboeing.com","www.aviationpartnersboeing.com"),
    ("5128546","Boeing","www.boeing.com","www.boeing.com"),
    ("346020235","Boeing 737","www.b737.org.uk","www.b737.org.uk"),
    ("1212960306","Boeing Aerospace & Autonomy Center","www.gnieob.com","www.gnieob.com"),
    ("455620818","Boeing Asia Pacific Aviation Services","www.bapas-sg.com","www.bapas-sg.com"),
    ("1310469572","Boeing Commercial Airplanes","",""),
    ("16224339","Boeing Employees Flying Association","www.befa.org","www.befa.org"),
    ("18143512","Boeing Employees Tennis Club","www.betconline.net","www.betconline.net"),
    ("562685928","Boeing International Corporation India Private","www.boeing.co.in","www.boeing.co.in"),
    ("447961817","Boeing Material Handling","www.boeingmhc.com.ph","www.boeingmhc.com.ph"),
    ("30757599","Boeing Trailers AU","www.boeingtrailers.com.au","www.boeingtrailers.com.au"),
    ("1226675440","Boeing Travel Management","",""),
    ("1268015193","Boeing Vancouver","www.boeingvancouver.com","www.boeingvancouver.com"),
    ("73664537","Monterey Airbus","www.montereyairbus.com","www.montereyairbus.com"),
    ("359197544","Spirit Boeing Employees' Association","www.beatoday.com","www.beatoday.com"),
    ("559785723","The Boeing","www.boeing.com.au","www.boeing.com.au"),
]


def zoominfo_records():
    recs = []
    for cid, name, domain, website in ZOOMINFO_ROWS:
        recs.append({
            "_origin_id": "testzoominfo",
            "_origin_table": "Company",
            "_origin_id_row": cid,
            "companyName": name,
            "companyDomain": domain,
            "website": website,
        })
    return recs


async def main():
    records = diffbot_records() + zoominfo_records()
    print(f"Total records: {len(records)}  (diffbot {len(diffbot_records())}, zoominfo {len(zoominfo_records())})")

    scored = await score_pairs_with_splink("company", records)
    if scored is None:
        print("Splink returned None (fell back to builtin) -- see warnings above")
        return
    print(f"Candidate pairs scored: {len(scored)}")

    by_id = {str(_record_id(r)): r for r in records}
    def label(rid):
        r = by_id[rid]
        name = r.get("entityname") or r.get("companyName")
        origin = r.get("_origin_id")
        return f"{name} [{origin}]"

    # Known true-duplicate pairs, hand-identified from the real rows above.
    true_pairs_by_name = [
        ("Boeing", "testdiffbot", "Boeing Co", "testdiffbot"),
        ("Boeing", "testdiffbot", "The Boeing Company", "testdiffbot"),
        ("Boeing Co", "testdiffbot", "Boeing", "testzoominfo"),
        ("The Boeing Company", "testdiffbot", "Boeing", "testzoominfo"),
        ("BOEING INTEGRATED DEFENSE SY", "testdiffbot", "Boeing Defense & Space Group", "testdiffbot"),
        ("BOEING INTEGRATED DEFENSE SY", "testdiffbot", "The Boeing Company Boeing Information Space & Defense Systems", "testdiffbot"),
        ("Boeing Satellite Systems, Inc.", "testdiffbot", "Boeing Satellite Systems International, Inc.", "testdiffbot"),
        ("Airbus SAS", "testdiffbot", "AIRBUS SE", "testdiffbot"),
        ("Airbus SAS", "testdiffbot", "Airbus Group", "testzoominfo"),
        ("AIRBUS SE", "testdiffbot", "Airbus Group", "testzoominfo"),
        ("Boeing Australia Holdings Proprietary Limited", "testdiffbot", "The Boeing", "testzoominfo"),
    ]

    # Build id lookup by (fullname-or-name, origin)
    def find_id(name, origin):
        for r in records:
            rn = r.get("fullname") or r.get("entityname") or r.get("companyName")
            if rn == name and r.get("_origin_id") == origin:
                return str(_record_id(r))
        return None

    scores_by_pair = {}
    for a, b, conf in scored:
        key = (a, b) if a < b else (b, a)
        scores_by_pair[key] = conf

    print("\n--- Hand-labelled true-duplicate pairs: blocked? scored? ---")
    n_blocked = 0
    for n1, o1, n2, o2 in true_pairs_by_name:
        id1, id2 = find_id(n1, o1), find_id(n2, o2)
        if id1 is None or id2 is None:
            print(f"  [record not found] {n1}/{o1} <-> {n2}/{o2}")
            continue
        key = (id1, id2) if id1 < id2 else (id2, id1)
        conf = scores_by_pair.get(key)
        status = f"scored {conf:.4f}" if conf is not None else "NOT BLOCKED"
        if conf is not None:
            n_blocked += 1
        print(f"  {n1} [{o1}] <-> {n2} [{o2}]: {status}")
    print(f"\n{n_blocked}/{len(true_pairs_by_name)} known true-duplicate pairs were blocked (proposed for scoring) at all.")


asyncio.run(main())
