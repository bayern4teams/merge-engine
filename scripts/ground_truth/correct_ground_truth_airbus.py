#!/usr/bin/env python3
"""
Surgical corrections to the Airbus portion of `ground_truth`, per a
re-examination of the real diffbot.companyrelation subsidiary assertions
(not available/consulted when the original build_ground_truth.py was
written). Non-destructive: nothing is deleted or overwritten -- old facts
get `valid_to` set and are superseded by new ones with `valid_from`, exactly
the temporal pattern the schema is designed for, so the original reasoning
stays in the audit trail.

Four corrections, each backed by a live SQL query against testdiffbot,
documented in the evidence text:

1. "Airbus Defense & Space" (diffbot EkA0l0EETOFKzdbAa-DZXmg, Huntsville AL
   stub -- no founding date, no CEO, no alldescriptions) was originally
   folded into the EUROPEAN "Airbus Defence and Space" cluster (Madrid/
   Toulouse). Re-checked: diffbot.companyrelation asserts "Airbus Defence
   and Space" (UK spelling) as a subsidiary of Airbus SE FOUR separate times,
   matching the Madrid and Toulouse/astrium.eads.net records by description
   verbatim -- but NEVER asserts a US-spelling "Airbus Defense & Space".
   Meanwhile "Airbus US Space & Defense" (E8SWTOHnJPMO2s9080CYkzg) is a rich
   record (CEO Robert Geckle Jr, airbusus.com, founded 2019) whose real-world
   company genuinely operates a Huntsville, AL satellite site alongside its
   Arlington VA HQ. Reclassified: the Huntsville stub is a low-confidence
   duplicate scrape of Airbus US Space & Defense, not the European entity.
2. "Airbus Group, Inc." confidence raised (0.55 -> 0.8): companyrelation
   explicitly asserts it as a subsidiary at confidence 0.85 ("Aerospace and
   defense company of America; holding company") -- direct corroboration
   the original build didn't have.
3. "Airbus Helicopters Foundation" (zoominfo 1102392658, domain
   airbusgroupap.com.au) renamed to "Airbus Group Australia Pacific":
   companyrelation asserts exactly that name (confidence 0.81, "Importer and
   distributor") as an Airbus SE subsidiary -- the zoominfo "Foundation"
   label was very likely a mis-scrape of "AP" (Asia Pacific) in the domain.
4. New standalone entity: "Monterey Airbus" (zoominfo 73664537, an airport
   shuttle service) -- a confirmed decoy, previously absent from ground
   truth entirely, so no comparison could check whether the app wrongly
   pulled it into the Airbus cluster. Added with NO company_company_
   relationship to Airbus, which is itself the ground-truth assertion:
   "this company is not part of Airbus."
"""

from datetime import date

import psycopg2

DSN = "host=localhost port=5434 dbname=ground_truth user=postgres password=supersecurepassword"
TODAY = date(2026, 9, 13)

conn = psycopg2.connect(DSN)
conn.autocommit = False
cur = conn.cursor()
cur.execute("SET search_path = kg, public;")

cur.execute("SELECT predicate_id, code FROM fact_predicate")
PRED = {code: pid for pid, code in cur.fetchall()}
cur.execute("""SELECT tt.taxonomy_term_id, tt.code FROM taxonomy_term tt
               JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='name_type'""")
NAMETYPE = {code: tid for tid, code in cur.fetchall()}
cur.execute("""SELECT tt.taxonomy_term_id, tt.code FROM taxonomy_term tt
               JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='company_status'""")
STATUS = {code: tid for tid, code in cur.fetchall()}
cur.execute(
    "SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='source_type' AND tt.code='DATABASE'"
)
DB_SOURCE_TYPE = cur.fetchone()[0]
cur.execute(
    "SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='source_type' AND tt.code='MANUAL'"
)
MANUAL_SOURCE_TYPE = cur.fetchone()[0]
cur.execute(
    "SELECT taxonomy_term_id FROM taxonomy_term tt JOIN taxonomy t ON t.taxonomy_id=tt.taxonomy_id WHERE t.code='evidence_type' AND tt.code='TABLE_ROW'"
)
EVIDENCE_TYPE_ROW = cur.fetchone()[0]

AIRBUS_DS = "01a094ea-db42-76b7-a753-af3d0ca6d34b"
AIRBUS_US_SD = "01a094ea-db51-76cc-aeca-7dfdd0b62d88"
AIRBUS_GROUP_INC = "01a094ea-db90-7e3f-93de-894890559600"
AIRBUS_HELI_FDN = "01a094ea-db64-73d2-8495-6f6537ba8a51"
AIRBUS_PARENT = "01a094ea-daec-7d9a-b58b-60e8077a221b"

cur.execute(
    """INSERT INTO source(source_type_term_id, uri, title, publisher, reliability_score, retrieved_at)
               VALUES (%s,%s,%s,%s,%s, now()) RETURNING source_id""",
    (
        DB_SOURCE_TYPE,
        "postgresql://localhost:5433/testdiffbot#diffbot.companyrelation",
        "origin1 re-examination: diffbot.companyrelation subsidiary assertions",
        "merge-engine ground-truth precision pass 2026-09-13",
        0.75,
    ),
)
SRC_RELATION = cur.fetchone()[0]

cur.execute(
    """INSERT INTO source(source_type_term_id, uri, title, publisher, reliability_score, retrieved_at)
               VALUES (%s,%s,%s,%s,%s, now()) RETURNING source_id""",
    (
        MANUAL_SOURCE_TYPE,
        None,
        "Analyst correction pass (ground-truth precision rebuild)",
        "merge-engine ground-truth precision pass 2026-09-13",
        0.85,
    ),
)
SRC_CORRECTION = cur.fetchone()[0]


def make_evidence(source_id, locator, snippet):
    cur.execute(
        """INSERT INTO evidence(source_id, evidence_type_term_id, locator, text_snippet)
                   VALUES (%s,%s,%s,%s) RETURNING evidence_id""",
        (source_id, EVIDENCE_TYPE_ROW, locator, snippet),
    )
    return cur.fetchone()[0]


def new_item(source_id, confidence, extractor="analyst-correction"):
    cur.execute(
        """INSERT INTO knowledge_item(item_type,source_id,confidence,extractor_name,extractor_version)
                   VALUES ('FACT',%s,%s,%s,'2026-09-13') RETURNING item_id""",
        (source_id, confidence, extractor),
    )
    return cur.fetchone()[0]


def link_evidence(item_id, evidence_id, primary=True):
    cur.execute(
        "INSERT INTO knowledge_item_evidence(item_id, evidence_id, is_primary) VALUES (%s,%s,%s)",
        (item_id, evidence_id, primary),
    )


def supersede_description(entity_id, new_text, source_id, ev_id, confidence):
    """Set valid_to=TODAY on the current live description fact (audit trail
    preserved, not deleted) and insert the corrected one with valid_from=TODAY."""
    cur.execute(
        """UPDATE knowledge_fact SET valid_to=%s
                   WHERE subject_entity_id=%s AND predicate_id=%s AND valid_to IS NULL""",
        (TODAY, entity_id, PRED["company.description"]),
    )
    item_id = new_item(source_id, confidence)
    link_evidence(item_id, ev_id)
    cur.execute(
        """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_text, valid_from)
                   VALUES (%s,%s,%s,'TEXT',%s,%s)""",
        (item_id, entity_id, PRED["company.description"], new_text, TODAY),
    )


# ---- Correction 1: Huntsville stub -> alias of Airbus US Space & Defense --
ev1 = make_evidence(
    SRC_CORRECTION,
    "diffbot.company:EkA0l0EETOFKzdbAa-DZXmg reclassified",
    "Original build folded this Huntsville-AL stub (no founding date, no CEO, "
    "alldescriptions=[]) into the European 'Airbus Defence and Space' cluster. "
    "Re-checked diffbot.companyrelation for Airbus SE (Ec_P38BE4MX6mRCDh9NWh_A): "
    "4 separate 'Airbus Defence and Space' (UK spelling) subsidiary assertions "
    "exist, 2 matching the Madrid and Toulouse records verbatim by description -- "
    "ZERO assertions of a US-spelling 'Airbus Defense & Space'. Airbus US Space & "
    "Defense (E8SWTOHnJPMO2s9080CYkzg, CEO Robert Geckle Jr, airbusus.com, founded "
    "2019) genuinely operates a Huntsville AL satellite site alongside its "
    "Arlington VA HQ in the real world. Reclassified as a low-confidence alias of "
    "that entity, not the European Airbus Defence and Space SA/SAS.",
)
cur.execute(
    """INSERT INTO entity_name(entity_id,name,normalized_name,language_code,name_type_term_id,valid_from)
               VALUES (%s,%s,%s,'en',%s,%s)""",
    (AIRBUS_US_SD, "Airbus Defense & Space", "airbus defense & space", NAMETYPE["FORMER"], TODAY),
)
supersede_description(
    AIRBUS_US_SD,
    "US subsidiary of Airbus Defence and Space (Arlington, VA); also scraped by "
    "diffbot as a bare 'Airbus Defense & Space' stub record attributed to "
    "Huntsville, AL, where this company's real-world satellite operations are "
    "based -- folded in here as an alternate name at low confidence (see "
    "correction evidence), not left misclassified under the European entity.",
    SRC_CORRECTION,
    ev1,
    0.6,
)
supersede_description(
    AIRBUS_DS,
    "Defence, space and security division; folds 2 diffbot record variants "
    "(Madrid/Toulouse) of the same 2006-formed entity (predecessor: EADS "
    "Astrium, 1923 roots). CORRECTED 2026-09-13: a third variant "
    "(Huntsville, AL) previously folded in here was re-classified as a "
    "probable duplicate of 'Airbus US Space & Defense' instead -- see that "
    "entity's own alias name and this correction's evidence.",
    SRC_CORRECTION,
    ev1,
    0.9,
)

# ---- Correction 2: Airbus Group, Inc. confidence raised, corroborated -----
ev2 = make_evidence(
    SRC_RELATION,
    "diffbot.companyrelation: companyid=Ec_P38BE4MX6mRCDh9NWh_A, relatedcompanyid=894a00a3790755be",
    "surfaceform='Airbus Group, Inc.', summary='Aerospace and defense company "
    "of America; holding company', confidence=0.85, relationtype=subsidiarie -- "
    "direct corroboration from Airbus SE's own asserted-relationship data, not "
    "available when this entity was first built at 0.55 confidence.",
)
supersede_description(
    AIRBUS_GROUP_INC,
    "US holding company (Airbus Group, Inc.); named explicitly in Airbus SE's "
    "own asserted subsidiary list (diffbot.companyrelation, confidence 0.85, "
    "relationtype=subsidiarie -- see correction evidence). Financial profile "
    "($50-100M revenue, 251-500 employees per zoominfo) is a regional US "
    "holding entity, not the pre-2017 global 'Airbus Group' parent (that "
    "entity is the 'Airbus'/'Airbus SE' golden record above, under its "
    "former name -- see Correction 2 note). Confidence raised 0.55 -> 0.8.",
    SRC_RELATION,
    ev2,
    0.8,
)
# The historical rename itself: "Airbus Group" WAS Airbus SE's real legal name
# 2000-2017, before the golden entity's own evidence captured it. Recorded as
# a FORMER name on the parent, distinct from the "Airbus Group, Inc." US
# subsidiary above -- same string, two different real entities, exactly the
# kind of collision a pure string-match merge would get wrong and ground
# truth needs to hold apart explicitly.
ev2b = make_evidence(
    SRC_CORRECTION,
    "manual-correction:airbus-group-historical-name",
    "'Airbus Group' (no suffix) was the Airbus SE holding company's own legal "
    "name from 2000 until its 2017 rename to 'Airbus SE' -- a FORMER name of "
    "the PARENT entity, not to be confused with 'Airbus Group, Inc.' (the "
    "distinct US regional subsidiary above, which keeps that name today).",
)
cur.execute(
    """INSERT INTO entity_name(entity_id,name,normalized_name,language_code,name_type_term_id,valid_from,valid_to)
               VALUES (%s,%s,%s,'en',%s,%s,%s)""",
    (
        AIRBUS_PARENT,
        "Airbus Group",
        "airbus group",
        NAMETYPE["FORMER"],
        date(2000, 1, 1),
        date(2017, 1, 1),
    ),
)

# ---- Correction 3: "Helicopters Foundation" -> "Airbus Group Australia Pacific" --
ev3 = make_evidence(
    SRC_RELATION,
    "diffbot.companyrelation: companyid=EXRo_XwecMo6TPp_x5JybaA, relatedcompanyid=1014a57b9aef86a9",
    "surfaceform='Airbus Group Australia Pacific', summary='Importer and "
    "distributor', confidence=0.81, relationtype=subsidiarie -- matches "
    "zoominfo 1102392658's domain (airbusgroupap.com.au = Airbus Group Asia/"
    "Australia Pacific) far better than its zoominfo-assigned name 'Airbus "
    "Helicopters Foundation'. Reclassified: this was very likely a ZoomInfo "
    "mis-labelling, not a real 'Foundation' entity.",
)
cur.execute(
    """UPDATE entity_name SET name_type_term_id=%s, valid_to=%s
               WHERE entity_id=%s AND name='Airbus Helicopters Foundation' AND valid_to IS NULL""",
    (NAMETYPE["FORMER"], TODAY, AIRBUS_HELI_FDN),
)
cur.execute(
    """INSERT INTO entity_name(entity_id,name,normalized_name,language_code,name_type_term_id,valid_from)
               VALUES (%s,%s,%s,'en',%s,%s)""",
    (
        AIRBUS_HELI_FDN,
        "Airbus Group Australia Pacific",
        "airbus group australia pacific",
        NAMETYPE["PREFERRED"],
        TODAY,
    ),
)
supersede_description(
    AIRBUS_HELI_FDN,
    "Importer and distributor arm, Australia/Asia-Pacific region (zoominfo's "
    "'Airbus Helicopters Foundation' label reclassified -- domain "
    "airbusgroupap.com.au and diffbot.companyrelation's own 'Airbus Group "
    "Australia Pacific' subsidiary assertion (confidence 0.81) both point to "
    "this being a mis-labelled ZoomInfo record, not a nonprofit foundation).",
    SRC_RELATION,
    ev3,
    0.75,
)

# ---- Correction 4: add the confirmed decoy "Monterey Airbus" --------------
cur.execute(
    """INSERT INTO source(source_type_term_id, uri, title, publisher, reliability_score, retrieved_at)
               VALUES (%s,%s,%s,%s,%s, now()) RETURNING source_id""",
    (
        DB_SOURCE_TYPE,
        "clickhouse://localhost:8125/testzoominfo",
        "origin2: ClickHouse testzoominfo (re-cited for Monterey Airbus)",
        "merge-engine ground-truth precision pass 2026-09-13",
        0.70,
    ),
)
SRC_ORIGIN2_RECITE = cur.fetchone()[0]
ev4 = make_evidence(
    SRC_ORIGIN2_RECITE,
    "testzoominfo.Company:73664537",
    "companyName='Monterey Airbus', companyDomain=montereyairbus.com, "
    "companyDescription='Monterey Airbus offers Convenient, Courteous, & "
    "Cost-Effective airport shuttle service to SFO, SJC, & point-to-point "
    "transportation.' -- name contains 'Airbus' coincidentally (a shuttle "
    "brand), unrelated to the aerospace manufacturer.",
)
cur.execute("INSERT INTO entity(entity_type) VALUES ('COMPANY') RETURNING entity_id")
monterey_id = cur.fetchone()[0]
cur.execute("INSERT INTO organization(organization_id) VALUES (%s)", (monterey_id,))
cur.execute("INSERT INTO company(company_id) VALUES (%s)", (monterey_id,))
cur.execute(
    """INSERT INTO entity_name(entity_id,name,normalized_name,language_code,name_type_term_id,valid_from)
               VALUES (%s,%s,%s,'en',%s,%s)""",
    (monterey_id, "Monterey Airbus", "monterey airbus", NAMETYPE["PREFERRED"], None),
)
item_id = new_item(SRC_ORIGIN2_RECITE, 0.9)
link_evidence(item_id, ev4)
cur.execute(
    """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_text)
               VALUES (%s,%s,%s,'TEXT',%s)""",
    (item_id, monterey_id, PRED["company.website"], "montereyairbus.com"),
)
item_id = new_item(SRC_ORIGIN2_RECITE, 0.95)
link_evidence(item_id, ev4)
cur.execute(
    """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_text)
               VALUES (%s,%s,%s,'TEXT',%s)""",
    (
        item_id,
        monterey_id,
        PRED["company.description"],
        "Airport shuttle service (SFO/SJC), Monterey CA. NOT part of the "
        "Airbus aerospace group -- name collision only, confirmed decoy.",
    ),
)
status_item_id = new_item(SRC_ORIGIN2_RECITE, 0.9)
link_evidence(status_item_id, ev4)
cur.execute(
    """INSERT INTO knowledge_fact(item_id, subject_entity_id, predicate_id, value_kind, value_code, value_taxonomy_term_id)
               VALUES (%s,%s,%s,'CODE',%s,%s)""",
    (status_item_id, monterey_id, PRED["company.status"], "ACTIVE", STATUS["ACTIVE"]),
)

conn.commit()
print("Corrections applied and committed.")
cur.execute("SELECT count(*) FROM entity")
print("Total ground_truth entities now:", cur.fetchone()[0])
cur.close()
conn.close()
