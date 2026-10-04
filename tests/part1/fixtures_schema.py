"""Synthetic fixtures with PLANTED ground truth for Part 1 items P1, P3, P17.

Pure data, no imports from src/, deterministic (no randomness). Names are
neutral on purpose: nothing here is a corpus literal (plan 2.4).
"""

from __future__ import annotations

# ── P1: concatenated-lowercase source fields vs a destination vocabulary ──

P1_DEST_TABLE = "org"
# (name, type) -- the destination vocabulary: id + eleven delimited columns.
P1_DEST_COLUMNS = [
    ("id", "text"),
    ("org_label", "text"),
    ("legal_title", "text"),
    ("postal_address", "text"),
    ("phone_number", "text"),
    ("founded_date", "text"),
    ("employee_count", "text"),
    ("tax_registration_number", "text"),
    ("website_url", "text"),
    ("parent_org_ref", "text"),
    ("primary_contact_email", "text"),
    ("mailing_postal_code", "text"),
]

# Each set: {source_field: planted destination column, or None = must stay unmapped}.
P1_SETS: dict[str, dict[str, str | None]] = {
    # P1 proper: no delimiter, no case transition.
    "concat": {
        "orglabel": "org_label",
        "legaltitle": "legal_title",
        "postaladdress": "postal_address",
        "phonenumber": "phone_number",
        "foundingdate": "founded_date",  # inflection differs: needs a real split
        "employeecount": "employee_count",
        "taxregistration": "tax_registration_number",  # a strict subset of the parts
        "websiteurl": "website_url",
        "parentorgref": "parent_org_ref",
        "contactemail": "primary_contact_email",  # strict subset of the parts
        "postalcode": "mailing_postal_code",  # strict subset of the parts
        # planted negatives: no destination column carries these concepts
        "legacyflag": None,
        "colorcode": None,
        "presspoint": None,  # the report's own "ambiguous split" example
    },
    # Controls: same concepts, delimited. The report says these already work.
    "camel": {
        "orgLabel": "org_label",
        "legalTitle": "legal_title",
        "postalAddress": "postal_address",
        "phoneNumber": "phone_number",
        "foundingDate": "founded_date",
        "employeeCount": "employee_count",
        "taxRegistration": "tax_registration_number",
        "websiteUrl": "website_url",
        "parentOrgRef": "parent_org_ref",
        "contactEmail": "primary_contact_email",
        "postalCode": "mailing_postal_code",
        "legacyFlag": None,
        "colorCode": None,
        "pressPoint": None,
    },
    "snake": {
        "org_label": "org_label",
        "legal_title": "legal_title",
        "postal_address": "postal_address",
        "phone_number": "phone_number",
        "founding_date": "founded_date",
        "employee_count": "employee_count",
        "tax_registration": "tax_registration_number",
        "website_url": "website_url",
        "parent_org_ref": "parent_org_ref",
        "contact_email": "primary_contact_email",
        "postal_code": "mailing_postal_code",
        "legacy_flag": None,
        "color_code": None,
        "press_point": None,
    },
}


def p1_destination_schema() -> dict:
    return {
        "table": P1_DEST_TABLE,
        "columns": [{"name": n, "type": t} for n, t in P1_DEST_COLUMNS],
    }


def p1_unified_schema(fields: list[str]) -> dict:
    return {f: {"type": "text", "sample_value": "v", "sample_values": ["v", "w"]} for f in fields}


# ── P3: child column whose name is not <parent>id; plus a coincidental decoy ──

P3_ORIGIN = "o1"
N_ORG = 200


def _col(name: str) -> dict:
    return {"name": name, "type": "text"}


# No declared PKs/FKs (like the report's ClickHouse/Mongo-style origins):
# the surrogate `_id` is the only key `_resolve_primary_key` can see.
P3_TABLES = [
    {"name": "org", "columns": [_col("_id"), _col("org_code"), _col("orgtitle")]},
    {"name": "member", "columns": [_col("_id"), _col("owner_ref"), _col("rolename")]},
    {"name": "note", "columns": [_col("_id"), _col("org_id"), _col("body")]},
    {"name": "ticket", "columns": [_col("_id"), _col("orgid"), _col("subject")]},
]
P3_ENTITY_TABLES = {t["name"]: [{"table": t["name"], "origin_id": P3_ORIGIN}] for t in P3_TABLES}
P3_TARGET_ENTITIES = ["org", "member", "note", "ticket"]

# Planted truth.  (child_table, child_column, parent_table, parent_column)
P3_TRUE_EDGES = {
    ("member", "owner_ref", "org", "org_code"),  # name does not match <parent>id
    ("note", "org_id", "org", "org_code"),  # name matches, but the key is the business key
}
P3_FALSE_EDGES = {
    # ticket.orgid holds ticket numbers 1..60, which coincidentally sit inside org._id 1..200
    ("ticket", "orgid", "org", "_id"),
}
P3_TRUE_TREE = {"org": [], "member": ["org"], "note": ["org"], "ticket": []}


def p3_rows() -> dict[str, dict[str, list]]:
    """{table: {column: [values]}} -- column-oriented, small and exact."""
    codes = [f"K{i:05d}" for i in range(1000, 1000 + N_ORG)]
    return {
        "org": {
            "_id": list(range(1, N_ORG + 1)),
            "org_code": codes,
            "orgtitle": [f"t{i}" for i in range(N_ORG)],
        },
        "member": {
            "_id": list(range(1, 301)),
            "owner_ref": [codes[i % 150] for i in range(300)],
            "rolename": ["r"] * 300,
        },
        "note": {
            "_id": list(range(1, 121)),
            "org_id": [codes[(i * 7) % N_ORG] for i in range(120)],
            "body": ["b"] * 120,
        },
        "ticket": {
            "_id": list(range(1, 61)),
            "orgid": list(range(1, 61)),
            "subject": ["s"] * 60,
        },
    }


# ── P17: single-`id` destination link table ──

P17_DEST_TABLE = "topic_link"
P17_N_ROWS = 40
# Source link rows each carry two linkage values; the destination can hold neither.
P17_SOURCE_FIELDS = ["org_ref", "topic_ref"]


def p17_destination_schema() -> dict:
    return {"table": P17_DEST_TABLE, "columns": [{"name": "id", "type": "text"}]}


def p17_records() -> list[dict]:
    return [
        {
            "id": f"c{i:03d}",
            "_cluster_id": f"c{i:03d}",
            "org_ref": f"K{1000 + i:05d}",
            "topic_ref": f"T{i % 7}",
        }
        for i in range(P17_N_ROWS)
    ]
