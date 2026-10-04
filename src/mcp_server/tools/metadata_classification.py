"""P12: catalog/system tables and ETL metadata columns, with three override layers.

Two categories, deliberately not conflated (report P12):

* **catalog / system table** - excluded from analysis, mapping, projection AND the corpus: it is
  removed from the discovered schema, so no later node ever sees it.
* **ETL metadata column** - excluded from matching and projection (it is never mapped, so never
  projected) but KEPT in the corpus as provenance: the table keeps the column, and lists it in
  ``metadata_columns`` for the nodes that must look past it.

Layers, lowest to highest precedence: detected default -> per-origin override -> per-run override.
An override spec is ``{"treat_as_business": [col..], "treat_as_metadata": [col..],
"treat_as_catalog": [table..], "treat_as_data": [table..]}``; names are bare and case-insensitive.
Inside one layer "business"/"data" beat "metadata"/"catalog" (keeping data is the recoverable
mistake). Every non-default outcome and every exclusion is written to ``schema["classification"]``
together with the misclassification risk, so it is never silent.

Pure functions, no module-level mutable state (the constants below are frozen).

ponytail: Ceiling (report P12) - detection is by NAME (columns) and by DBMS convention (tables), so
a business field that looks like metadata (``signup_date``-style, a ``created_at`` that is really a
founding date) is misclassified; the override layers and the recorded risk are the mitigation.
Value patterns need more than the one ``sample_row`` discovery holds - deferred.
Deferred alternative: classify fields by the section 3.5 taxonomy (measured features), so metadata
falls out as a class instead of a list. Trigger: Part 3, with section 3.5.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

# The key an override spec lives under: on an origin config dict (per-origin) and on the run state
# (per-run).
OVERRIDE_KEY = "metadata_overrides"

# DBMS facts, not corpus: schemas the database engine itself owns (PostgreSQL, MySQL, SQL Server),
# name prefixes the engines reserve (PostgreSQL ``pg_``, SQLite ``sqlite_``, MongoDB ``system.``).
_CATALOG_SCHEMAS = frozenset(
    {"pg_catalog", "information_schema", "pg_toast", "mysql", "performance_schema", "sys"}
)
_CATALOG_PREFIXES = ("pg_", "sqlite_", "system.")

# Generic audit/ETL vocabulary (row lifecycle stamps, pipeline markers), matched on whole name
# tokens, never substrings.
_PIPELINE = frozenset({"etl", "elt"})
_LIFECYCLE = frozenset(
    {"created", "updated", "modified", "inserted", "loaded", "ingested", "synced", "deleted"}
)
_STAMP = frozenset({"at", "on", "ts", "time", "timestamp", "date", "dt", "by"})
_TIME_TYPES = ("TIMESTAMP", "DATE", "TIME")

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

BUSINESS, ETL = "business", "etl_metadata"
DATA, CATALOG = "data", "catalog"


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", _CAMEL.sub("_", name).lower()) if t]


def detect_column(name: str, col_type: str = "") -> str:
    """Why ``name`` looks like ETL metadata, or '' when it does not."""
    toks = _tokens(name)
    found = set(toks)
    if found & _PIPELINE:
        return f"pipeline token {sorted(found & _PIPELINE)[0]!r} in the name"
    if "rowversion" in found or ("row", "version") in pairwise(toks):
        return "row-version token in the name"
    life = found & _LIFECYCLE
    if life and (found & _STAMP or any(t in str(col_type).upper() for t in _TIME_TYPES)):
        return f"row-lifecycle token {sorted(life)[0]!r} with a timestamp/actor marker or type"
    return ""


def detect_table(table: dict[str, Any]) -> str:
    """Why ``table`` looks like a DBMS catalog/system table, or '' when it does not."""
    name = str(table.get("name", "")).lower()
    full = str(table.get("full_name") or name).lower()
    schema = str(table.get("schema") or (full.rsplit(".", 1)[0] if "." in full else "")).lower()
    if schema in _CATALOG_SCHEMAS or schema.startswith("pg_"):
        return f"table lives in the DBMS-owned schema {schema!r}"
    if name.startswith(_CATALOG_PREFIXES):
        return "table name carries a DBMS-reserved prefix"
    return ""


def _names(spec: Any, key: str) -> frozenset[str]:
    raw = spec.get(key) if isinstance(spec, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    return frozenset(str(n).strip().lower() for n in raw or [])


def _decide(
    names: set[str], detected: str, layers: list[tuple[str, Any]], kind: tuple[str, str, str, str]
) -> tuple[str, str]:
    """(effective class, which layer decided). Later layers overwrite earlier ones; inside one
    layer the keep class wins. ``kind`` = (keep key, drop key, keep class, drop class)."""
    keep_key, drop_key, keep, drop = kind
    effective, by = detected, "detected default"
    for label, spec in layers:
        if names & _names(spec, keep_key):
            effective, by = keep, label
        elif names & _names(spec, drop_key):
            effective, by = drop, label
    return effective, by


_TABLE_KIND = ("treat_as_data", "treat_as_catalog", DATA, CATALOG)
_COLUMN_KIND = ("treat_as_business", "treat_as_metadata", BUSINESS, ETL)


def classify_schema(
    schema: dict[str, Any],
    origin_overrides: Any = None,
    run_overrides: Any = None,
    origin_id: str = "",
) -> dict[str, Any]:
    """Return ``schema`` with catalog tables removed, ``metadata_columns`` on every kept table and
    a ``classification`` record of every exclusion / override / risk. The input is not mutated."""
    layers: list[tuple[str, Any]] = [
        (f"origin override ({origin_id})" if origin_id else "origin override", origin_overrides),
        ("run override", run_overrides),
    ]
    tables_out: list[dict[str, Any]] = []
    table_records: list[dict[str, Any]] = []
    column_records: list[dict[str, Any]] = []
    for table in schema.get("tables", []):
        name = str(table.get("name", ""))
        full = str(table.get("full_name") or name)
        why = detect_table(table)
        detected = CATALOG if why else DATA
        effective, by = _decide({name.lower(), full.lower()}, detected, layers, _TABLE_KIND)
        if detected == CATALOG or effective == CATALOG or by != "detected default":
            table_records.append(
                {
                    "table": full,
                    "detected": detected,
                    "effective": effective,
                    "decided_by": by,
                    "evidence": why,
                    "risk": (
                        "catalog detection is by DBMS convention; a business table with a "
                        "look-alike name is excluded unless protected by a treat_as_data override"
                        if effective == CATALOG
                        else "detected as a catalog table but kept: " + by
                        if detected == CATALOG
                        else ""
                    ),
                }
            )
        if effective == CATALOG:
            continue
        meta_cols: list[str] = []
        for col in table.get("columns", []):
            cname = str(col.get("name", ""))
            ev = detect_column(cname, str(col.get("type", "")))
            detected_c = ETL if ev else BUSINESS
            eff_c, by_c = _decide({cname.lower()}, detected_c, layers, _COLUMN_KIND)
            if eff_c == ETL:
                meta_cols.append(cname)
            if detected_c == ETL or eff_c == ETL or by_c != "detected default":
                column_records.append(
                    {
                        "table": full,
                        "column": cname,
                        "detected": detected_c,
                        "effective": eff_c,
                        "decided_by": by_c,
                        "evidence": ev,
                        "risk": (
                            "name-pattern detection can misclassify a business field that looks "
                            "like ETL metadata; protect it with a treat_as_business override"
                            if eff_c == ETL
                            else "detected as ETL metadata but kept as a business field: " + by_c
                        ),
                    }
                )
        tables_out.append({**table, "metadata_columns": meta_cols})
    return {
        **schema,
        "tables": tables_out,
        "classification": {"tables": table_records, "columns": column_records},
    }


def metadata_columns_by_table(schemas: dict[str, Any]) -> dict[tuple[str, str], frozenset[str]]:
    """``{(origin_id, table name or full name, lower-cased): metadata column names, lower-cased}``
    for every table that has any - the lookup record preparation uses."""
    out: dict[tuple[str, str], frozenset[str]] = {}
    for origin_id, schema in (schemas or {}).items():
        for table in (schema or {}).get("tables", []) or []:
            cols = frozenset(str(c).lower() for c in table.get("metadata_columns") or [])
            if not cols:
                continue
            for key in {str(table.get("name", "")), str(table.get("full_name") or "")} - {""}:
                out[(str(origin_id), key.lower())] = cols
    return out
