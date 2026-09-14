"""Validate the 'train once on a big representative sample, reuse via
predict()' hypothesis against the small-sample EM instability observed in
splink_tune_boeing_airbus.py's per-run retraining.

Uses the B.4 Splink Workbench's own train()/predict() split
(splink_workbench.py) -- the model-first architecture that already exists
in this codebase but that the production `resolve_with_splink()` call path
does not use (it retrains from scratch on every call's own, often tiny,
single-subject record set instead).

IMPORTANT (a real bug found while building this script): records passed to
the workbench MUST carry `_origin_id`/`_origin_table`/`_origin_id_row` (or
their non-underscore aliases) or `resolve_entities._record_id()` -- which
`splink_common.records_to_dataframe` calls unconditionally and which ignores
any id field a caller already set -- silently falls back to `str(id(rec))`,
Python's object-identity address. Every record in this script sets those
three fields for exactly that reason.
"""
import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from src.mcp_server.tools import splink_workbench as wb  # noqa: E402
from src.mcp_server.tools.resolve_entities import _domain_of, _record_id  # noqa: E402

SCRATCH = str(Path(__file__).resolve().parent)


def load_diffbot_tsv(path):
    recs = []
    with open(path) as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) != 5:
                continue
            companyid, entityname, fullname, website, summary = row
            recs.append({
                "_origin_id": "testdiffbot",
                "_origin_table": "company",
                "_origin_id_row": companyid,
                "name": entityname or "",
                "fullname": fullname or "",
                "domain": _domain_of(website) if website else "",
                "description": (summary or "")[:150],
            })
    return recs


ZOOMINFO_ROWS = [
    ("482339782", "ATI Boeing Accelerator", "www.atiboeingaccelerator.com"),
    ("1107110998", "Airbus", "www.airbus-sv.com"),
    ("450171088", "Airbus Aerial", "www.airbusaerial.com"),
    ("449306384", "Airbus Bank", "www.airbusbank.com"),
    ("368973570", "Airbus DS Houston", "www.airbusdshouston.com"),
    ("1142580865", "Airbus Ds Geo Australia", "www.geo-airbusds.com"),
    ("1115568140", "Airbus Group", "www.airbusgroup.com"),
    ("365384179", "Airbus Helicopters", "www.airbus.com/helicopters.html"),
    ("1102392658", "Airbus Helicopters Foundation", "www.airbusgroupap.com.au"),
    ("359361969", "Airbus Helicopters UK", "www.airbushelicopters.co.uk"),
    ("507376090", "Airbus Secure Communications", "www.securecommunications-airbusds.com"),
    ("538878747", "Airbus Turkiye", "www.airbusturkiye.com"),
    ("471447426", "Airbus UTM", "www.airbusutm.com"),
    ("1163680190", "Aviation Partners Boeing", "www.aviationpartnersboeing.com"),
    ("5128546", "Boeing", "www.boeing.com"),
    ("346020235", "Boeing 737", "www.b737.org.uk"),
    ("1212960306", "Boeing Aerospace & Autonomy Center", "www.gnieob.com"),
    ("455620818", "Boeing Asia Pacific Aviation Services", "www.bapas-sg.com"),
    ("1310469572", "Boeing Commercial Airplanes", ""),
    ("16224339", "Boeing Employees Flying Association", "www.befa.org"),
    ("18143512", "Boeing Employees Tennis Club", "www.betconline.net"),
    ("562685928", "Boeing International Corporation India Private", "www.boeing.co.in"),
    ("447961817", "Boeing Material Handling", "www.boeingmhc.com.ph"),
    ("30757599", "Boeing Trailers AU", "www.boeingtrailers.com.au"),
    ("1226675440", "Boeing Travel Management", ""),
    ("1268015193", "Boeing Vancouver", "www.boeingvancouver.com"),
    ("73664537", "Monterey Airbus", "www.montereyairbus.com"),
    ("359197544", "Spirit Boeing Employees' Association", "www.beatoday.com"),
    ("559785723", "The Boeing", "www.boeing.com.au"),
]


def zoominfo_recs():
    out = []
    for cid, name, domain in ZOOMINFO_ROWS:
        out.append({
            "_origin_id": "testzoominfo",
            "_origin_table": "Company",
            "_origin_id_row": cid,
            "name": name,
            "fullname": name,
            "domain": _domain_of(domain) if domain else "",
            "description": "",
        })
    return out


_SAMPLE_PATH = Path(SCRATCH) / "diffbot_sample.tsv"
if not _SAMPLE_PATH.exists():
    raise SystemExit(
        f"{_SAMPLE_PATH} not found -- this 3,000-row bulk training sample is "
        "deliberately NOT committed to the repo (a data dump, not code; see "
        "CLAUDE.md's sample_databases rule). Regenerate it with:\n\n"
        "  psql -h <origin host> -p <port> -U <user> -d testdiffbot -t -A -F $'\\t' -c \"\n"
        "  select companyid, entityname, fullname, coalesce(website,''), coalesce(left(summary,150),'')\n"
        "  from diffbot.company where entityname is not null and entityname <> ''\n"
        "  order by random() limit 3000;\" > "
        f"{_SAMPLE_PATH}\n\n"
        "See docs/diagnostics/SPLINK_TUNING_METHODOLOGY.md §3.4 for the full method."
    )

diffbot_boeing_airbus = load_diffbot_tsv(f"{SCRATCH}/diffbot_rows.tsv")
train_sample = load_diffbot_tsv(str(_SAMPLE_PATH))
predict_set = diffbot_boeing_airbus + zoominfo_recs()

print(f"Training sample: {len(train_sample)} records (bulk, cross-company)")
print(f"Predict set: {len(predict_set)} records (Boeing+Airbus, diffbot+zoominfo)")

config = {
    "comparisons": [
        {"comparator": "JaroWinklerAtThresholds", "field": "name", "thresholds": [0.9, 0.7]},
        {"comparator": "ExactMatch", "field": "domain"},
    ],
    "blocking_rules": [
        'substr(lower(l."name"), 1, 4) = substr(lower(r."name"), 1, 4)',
        'l."domain" = r."domain"',
    ],
    "deterministic_rule": 'l."domain" = r."domain"',
    "seed": 42,
    "max_pairs": 500_000,
    "recall": 0.7,
}

result = wb.train(
    "company_diffbot_zoominfo",
    train_sample,
    config,
    store_root="logs/splink_models",
)
print(f"\nTrained model version {result['version']}, round-trip verified: {result['round_trip_verified']}")
print("Training warnings:", result["training"].get("warnings"))

pred = wb.predict(
    "company_diffbot_zoominfo",
    result["version"],
    predict_set,
    store_root="logs/splink_models",
)
scored_pairs = pred["predictions"]
print(f"\nPairs scored against Boeing/Airbus set: {len(scored_pairs)}")

by_id = {str(_record_id(r)): r for r in predict_set}
assert len(by_id) == len(predict_set), "id collision -- _record_id must be unique per record"

scores_by_pair = {}
for p in scored_pairs:
    a, b = p["_record_id_str_l"], p["_record_id_str_r"]
    key = (a, b) if a < b else (b, a)
    scores_by_pair[key] = p["match_probability"]


def find_exact(fullname_or_name, origin):
    for rid, r in by_id.items():
        if r.get("_origin_id") == ("testdiffbot" if origin == "diffbot" else "testzoominfo") and (
            r.get("fullname") == fullname_or_name or r.get("name") == fullname_or_name
        ):
            return rid
    return None


pairs_to_check = [
    ("Boeing Satellite Systems, Inc.", "diffbot", "Boeing Satellite Systems International, Inc.", "diffbot"),
    ("Boeing Co", "diffbot", "Boeing", "zoominfo"),
    ("The Boeing Company", "diffbot", "Boeing", "zoominfo"),
    ("Boeing Co", "diffbot", "The Boeing Company", "diffbot"),
    ("Airbus SAS", "diffbot", "AIRBUS SE", "diffbot"),
    ("AIRBUS SE", "diffbot", "Airbus Group", "zoominfo"),
    ("Boeing Australia Holdings Proprietary Limited", "diffbot", "The Boeing", "zoominfo"),
    ("BOEING INTEGRATED DEFENSE SY", "diffbot", "Boeing Defense & Space Group", "diffbot"),
    ("BOEING INTEGRATED DEFENSE SY", "diffbot", "The Boeing Company Boeing Information Space & Defense Systems", "diffbot"),
]
print("\n--- Known true-duplicate pairs, scored by the BULK-TRAINED model ---")
n_blocked = 0
for n1, o1, n2, o2 in pairs_to_check:
    id1, id2 = find_exact(n1, o1), find_exact(n2, o2)
    if not id1 or not id2:
        print(f"  [not found] {n1}/{o1} <-> {n2}/{o2}")
        continue
    key = (id1, id2) if id1 < id2 else (id2, id1)
    conf = scores_by_pair.get(key)
    if conf is not None:
        n_blocked += 1
    print(f"  {n1} [{o1}] <-> {n2} [{o2}]: {'scored ' + format(conf, '.4f') if conf is not None else 'NOT BLOCKED'}")
print(f"\n{n_blocked}/{len(pairs_to_check)} known true-duplicate pairs blocked & scored.")

sat1 = find_exact("Boeing Satellite Systems, Inc.", "diffbot")
sat2 = find_exact("Boeing Satellite Systems International, Inc.", "diffbot")
key = (sat1, sat2) if sat1 < sat2 else (sat2, sat1)
for p in scored_pairs:
    a, b = p["_record_id_str_l"], p["_record_id_str_r"]
    k = (a, b) if a < b else (b, a)
    if k == key:
        print("\nFull row for Satellite Systems pair:")
        for kk, vv in p.items():
            print(f"  {kk}: {vv}")
