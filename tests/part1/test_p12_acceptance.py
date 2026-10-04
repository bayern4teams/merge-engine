"""P12 acceptance (derived from the report's Evidence + Fix text only; no implementation seen).

Report: "Catalog tables and metadata columns are analysed and mapped".
  * Catalog / system tables: excluded from matching, projection AND corpus.
  * ETL metadata columns (created_at, etl_batch_id, _row_version): excluded from matching and
    projection, but KEPT in the corpus / run output (provenance, section 3.2 losslessness).
  * Configuration: three layers -- detected defaults (name and value patterns, per origin),
    an optional human override per origin, a per-run override. Precedence default < origin < run.
  * Ceiling: detection by name/value pattern will misclassify a business field that looks like
    metadata (`signup_date`).  Plan accept: protected by the override, risk RECORDED, not hidden.
HANDOFF flag 3: projection exclusion happens through the mapping (an excluded column is never
mapped, so never projected); load_data.py is not edited.

How the checks observe behaviour (all through REAL code, only the DB connector is faked):
  discover_schemas(state)  ->  state['schemas']  ->  map_fields_node._build_source_schema_from_discovered
                                                 ->  resolve_entities node (records handed to matching)

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p12_acceptance.py -q -s -p no:cacheprovider
The negative cases (test_2*, 3*, 4*, 1*) FAIL on the old code (everything is mapped / matched).

ADAPTATION POINTS (the report does not fix these; retarget HERE, never in the assertions):
  override_payload()      : shape of one override spec.  Default
        {"treat_as_business": [col..], "treat_as_metadata": [col..],
         "treat_as_catalog": [table..], "treat_as_data": [table..]}   (bare names, scope = origin / run)
  ORIGIN_KEYS / env P12_ORIGIN_KEY : key(s) on the origin config dict that carry the per-origin override.
        The spec is written under EVERY candidate key (unknown extra keys on a dict are inert).
  RUN_KEYS    / env P12_RUN_KEY    : key(s) on the run state that carry the per-run override.
  MAPPED_FIELDS_FN        : (state, entity) -> {field: info}; the mapping's source-field collector.
  ANNOTATION_RE           : words that make a dict "a classification record" for the risk checks.
  EXCLUDED_MARKER_*       : how discovery may FLAG (instead of remove) an excluded catalog table.
  Exclusion of a catalog table is accepted as removal from schemas OR a truthy flag/category marker.
  Value-pattern detection (the report's "name and value patterns") is NOT asserted beyond what
  sample_row can show: only name/schema-based cases are hard; value-only detection is left to attack.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import importlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", "1")

from src.orchestrator import spool as spool_mod
from src.orchestrator.nodes import map_fields_node as mf_node
from src.orchestrator.spool import entity_rows

# nodes/__init__ re-exports the node FUNCTIONS under the module names, so import modules explicitly
re_node = importlib.import_module("src.orchestrator.nodes.resolve_entities")
discover_schemas = importlib.import_module(
    "src.orchestrator.nodes.discover_schemas"
).discover_schemas

REPO = Path(__file__).resolve().parents[2]

# ── adaptation points ─────────────────────────────────────────────────────────
ORIGIN_KEYS = tuple(
    filter(
        None,
        [
            os.environ.get("P12_ORIGIN_KEY"),
            "metadata_overrides",
            "metadata_override",
            "classification_overrides",
            "column_overrides",
            "overrides",
            "override",
            "metadata",
        ],
    )
)
RUN_KEYS = tuple(
    filter(
        None,
        [
            os.environ.get("P12_RUN_KEY"),
            "metadata_overrides",
            "metadata_override",
            "run_overrides",
            "classification_overrides",
            "column_overrides",
            "overrides",
        ],
    )
)
MAPPED_FIELDS_FN = mf_node._build_source_schema_from_discovered
ANNOTATION_RE = re.compile(
    r"metadata|etl|overrid|detect|classif|exclu|protect|risk|business|heuristic|misclass", re.I
)
EXCLUDED_MARKER_KEYS = (
    "excluded",
    "is_system",
    "system",
    "is_catalog",
    "catalog",
    "skip",
    "ignored",
)
EXCLUDED_MARKER_VALUES = {"catalog", "system", "catalog_table", "system_table", "excluded"}
EXCLUDED_MARKER_FIELDS = ("category", "classification", "kind", "class", "role", "type_class")


def override_payload(
    business: tuple[str, ...] = (),
    metadata: tuple[str, ...] = (),
    catalog: tuple[str, ...] = (),
    data: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "treat_as_business": list(business),
        "treat_as_metadata": list(metadata),
        "treat_as_catalog": list(catalog),
        "treat_as_data": list(data),
    }


# ── fixtures: a fake connector, two origins, one entity concept ───────────────
ENTITY = "customer"
ETL_COLS = ("created_at", "etl_batch_id", "_row_version")
BUSINESS = ("id", "name", "email", "headcount")


def _col(name: str, typ: str = "STRING") -> dict[str, Any]:
    return {"name": name, "type": typ, "nullable": True}


def _tbl(
    schema: str, name: str, cols: list[tuple[str, str]], sample: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": schema,
        "name": name,
        "full_name": f"{schema}.{name}",
        "columns": [_col(c, t) for c, t in cols],
        "foreign_keys": [],
        "primary_key": [cols[0][0]] if cols else [],
        "sample_row": sample,
    }


CUSTOMER_COLS = [
    ("id", "INTEGER"),
    ("name", "STRING"),
    ("email", "STRING"),
    ("headcount", "INTEGER"),
    ("signup_date", "DATE"),
    ("created_at", "TIMESTAMP"),
    ("etl_batch_id", "INTEGER"),
    ("_row_version", "INTEGER"),
]
CUSTOMER_ROW = {
    "id": 1,
    "name": "Acme Widgets",
    "email": "ops@acme.example",
    "headcount": 40,
    "signup_date": "2023-06-01",
    "created_at": "2024-01-05T10:00:00",
    "etl_batch_id": 4711,
    "_row_version": 3,
}
CATALOG_NAMED_AS_ENTITY = [
    # name matches the entity concept, so only SCHEMA/metadata can explain the exclusion
    _tbl(
        "pg_catalog",
        "customer",
        [("cat_oid", "INTEGER"), ("cat_owner", "STRING")],
        {"cat_oid": 7, "cat_owner": "pg"},
    ),
    _tbl(
        "information_schema",
        "customer",
        [("cat_view_def", "STRING"), ("cat_oid2", "INTEGER")],
        {"cat_view_def": "x", "cat_oid2": 1},
    ),
]
CATALOG_BY_NAME = {  # table name -> a column unique to it
    "pg_stat_statements": "queryid",
    "sqlite_sequence": "seq_counter",
}


def _schemas() -> dict[str, dict[str, Any]]:
    def origin_tables() -> list[dict[str, Any]]:
        t = [_tbl("public", "customer", CUSTOMER_COLS, dict(CUSTOMER_ROW))]
        t += copy.deepcopy(CATALOG_NAMED_AS_ENTITY)
        for name, col in CATALOG_BY_NAME.items():
            t.append(_tbl("public", name, [(col, "INTEGER")], {col: 5}))
        t.append(
            _tbl(
                "public",
                "product_catalog",
                [("sku", "STRING"), ("title", "STRING"), ("list_price", "NUMERIC")],
                {"sku": "A-1", "title": "Widget", "list_price": 9.5},
            )
        )
        return t

    return {"oa": {"tables": origin_tables()}, "ob": {"tables": origin_tables()}}


class FakeConnector:
    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    async def discover_schema(self) -> dict[str, Any]:
        return copy.deepcopy(self._schema)

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    schemas = _schemas()

    def create(_typ: str, conn: dict[str, Any]) -> FakeConnector:
        return FakeConnector(schemas[conn["database"]])

    monkeypatch.setattr("src.mcp_server.tools.discover_schema.create_connector", create)
    return schemas


def _origin(oid: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
    o: dict[str, Any] = {"id": oid, "type": "postgres", "connection": {"database": oid}}
    if override is not None:
        for k in ORIGIN_KEYS:
            o[k] = copy.deepcopy(override)
    return o


def _state(
    origin_overrides: dict[str, dict[str, Any]] | None = None, run: dict[str, Any] | None = None
) -> dict[str, Any]:
    origin_overrides = origin_overrides or {}
    st: dict[str, Any] = {
        "origins": [_origin(o, origin_overrides.get(o)) for o in ("oa", "ob")],
        "target_entities": [ENTITY],
    }
    if run is not None:
        for k in RUN_KEYS:
            st[k] = copy.deepcopy(run)
    return st


def discover(state: dict[str, Any]) -> dict[str, Any]:
    """Run the real discover_schemas node; return the merged state."""
    update = asyncio.run(discover_schemas(copy.deepcopy(state)))
    assert not update.get("failure"), update.get("failure")
    merged = {**state, **update}
    merged["_update"] = update
    return merged


def mapped(state: dict[str, Any], origin: str, entity: str = ENTITY) -> set[str]:
    """Source fields the mapping would see for ONE origin (the source schema is keyed by name)."""
    st = dict(state)
    st["schemas"] = {origin: state["schemas"][origin]}
    return set(MAPPED_FIELDS_FN(st, entity))


def _flag_excluded(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if any(node.get(k) is True for k in EXCLUDED_MARKER_KEYS):
        return True
    return any(
        str(node.get(k, "")).lower() in EXCLUDED_MARKER_VALUES for k in EXCLUDED_MARKER_FIELDS
    )


def table_visible_to_analysis(state: dict[str, Any], origin: str, full_name: str) -> bool:
    for t in state["schemas"][origin].get("tables", []):
        if t.get("full_name") == full_name:
            return not _flag_excluded(t)
    return False


# ── matching-node harness: capture exactly what is handed to matching ─────────
def _rows(origin: str) -> list[dict[str, Any]]:
    return [
        {**CUSTOMER_ROW, "id": 1, "_origin_id": origin, "_origin_table": "customer"},
        {
            **CUSTOMER_ROW,
            "id": 2,
            "name": "Beta Supplies",
            "email": "hi@beta.example",
            "created_at": "2024-02-01T00:00:00",
            "_origin_id": origin,
            "_origin_table": "customer",
        },
    ]


def run_matching(state: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    captured: list[dict[str, Any]] = []

    async def fake_impl(**kw: Any) -> dict[str, Any]:
        captured.append(copy.deepcopy({k: v for k, v in kw.items() if k in ("records",)}))
        captured[-1]["kwargs"] = {k: repr(v)[:2000] for k, v in kw.items() if k != "records"}
        return {"clusters": [], "total_records": len(kw["records"])}

    monkeypatch.setattr(re_node, "resolve_entities_impl", fake_impl)
    rows_in = _rows("oa") + _rows("ob")
    snapshot = copy.deepcopy(rows_in)
    st = {**state, "run_id": "p12", "extracted_data": {ENTITY: rows_in}}
    out = asyncio.run(re_node.resolve_entities(st))
    return captured, {**st, **out}, snapshot, rows_in


def _seen_by_matching(captured, origin: str) -> set[str]:
    keys: set[str] = set()
    for call in captured:
        for r in call["records"]:
            if r.get("_origin_id") == origin:
                keys |= set(r)
    return keys


# ═════════════════════════════════════════════════════════════════════════════
# 1. catalog / system tables
# ═════════════════════════════════════════════════════════════════════════════
def test_1_catalog_tables_never_reach_mapping_or_projection(fake_db):
    st = discover(_state())
    for origin in ("oa", "ob"):
        fields = mapped(st, origin)
        leaked = fields & {"cat_oid", "cat_owner", "cat_view_def", "cat_oid2"}
        assert not leaked, (
            f"{origin}: catalog-table columns {sorted(leaked)} reach the mapping source schema "
            "(the tables are named like the entity, only their schema marks them as catalog). "
            "OLD BEHAVIOUR: catalog tables are analysed and mapped."
        )
        # over-exclusion guard: the real business table still maps
        assert {"id", "name", "email", "headcount"} <= fields, fields


@pytest.mark.parametrize("full_name", ["pg_catalog.customer", "information_schema.customer"])
def test_1b_catalog_schema_tables_excluded_from_analysis(fake_db, full_name):
    st = discover(_state())
    assert not table_visible_to_analysis(st, "oa", full_name), (
        f"{full_name} still present and unflagged in state['schemas'] (analysis input)"
    )
    assert table_visible_to_analysis(st, "oa", "public.customer"), (
        "business table must stay visible"
    )


@pytest.mark.parametrize("name", sorted(CATALOG_BY_NAME))
def test_1c_system_tables_named_by_dbms_convention_excluded(fake_db, name):
    st = discover(_state())
    assert not table_visible_to_analysis(st, "oa", f"public.{name}"), (
        f"DBMS-convention system table public.{name} still analysed"
    )


def test_1d_business_catalog_lookalike_is_not_a_system_catalog_by_default_is_recorded(fake_db):
    """`product_catalog` is a business table that merely contains the word. If detection by name
    pattern excludes it, that is the report's own ceiling -- it must then be PROTECTABLE (test 4c)
    and RECORDED. Here: it must not vanish silently."""
    st = discover(_state())
    visible = table_visible_to_analysis(st, "oa", "public.product_catalog")
    if not visible:
        notes = annotations_for(st["_update"], "product_catalog")
        assert notes, (
            "product_catalog was excluded with no record of why (silent misclassification)"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 2. ETL metadata: out of matching + projection, kept in output
# ═════════════════════════════════════════════════════════════════════════════
def test_2a_etl_columns_excluded_from_projection_via_mapping(fake_db):
    st = discover(_state())
    for origin in ("oa", "ob"):
        fields = mapped(st, origin)
        leaked = fields & set(ETL_COLS)
        assert not leaked, (
            f"{origin}: ETL metadata {sorted(leaked)} is mapped (=> projected). "
            "OLD BEHAVIOUR: metadata columns are mapped like business fields."
        )
        assert set(BUSINESS) <= fields, f"over-exclusion: lost business fields from {fields}"


def test_2b_etl_columns_excluded_from_matching_records(fake_db, tmp_path, monkeypatch):
    st = discover(_state())
    captured, _out, _snap, _in = run_matching(st, tmp_path, monkeypatch)
    assert captured, "resolve_entities_impl was never called"
    for origin in ("oa", "ob"):
        seen = _seen_by_matching(captured, origin)
        leaked = seen & set(ETL_COLS)
        assert not leaked, (
            f"{origin}: ETL metadata {sorted(leaked)} handed to matching. "
            "OLD BEHAVIOUR: record preparation passes every column."
        )
        assert set(BUSINESS) <= seen, f"over-exclusion in matching input: {seen}"


def test_2c_etl_columns_kept_in_run_output_and_inputs_not_mutated(fake_db, tmp_path, monkeypatch):
    st = discover(_state())
    _captured, out, snapshot, rows_in = run_matching(st, tmp_path, monkeypatch)
    kept = entity_rows(out, ENTITY)
    assert len(kept) == len(snapshot), "rows lost from the corpus"
    for before, after in zip(snapshot, kept, strict=True):
        for c in ETL_COLS:
            assert after.get(c) == before[c], f"metadata column {c} dropped/changed in run output"
    assert rows_in == snapshot, (
        "record preparation mutated the corpus rows in place (provenance lost)"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3. three override layers: default < per-origin < per-run
# ═════════════════════════════════════════════════════════════════════════════
def test_3a_layer0_detected_default(fake_db):
    st = discover(_state())
    f = mapped(st, "oa")
    assert "etl_batch_id" not in f and "headcount" in f


def test_3b_origin_override_beats_default_and_is_scoped_to_its_origin(fake_db):
    spec = override_payload(business=("etl_batch_id",), metadata=("headcount",))
    st = discover(_state(origin_overrides={"oa": spec}))
    fa, fb = mapped(st, "oa"), mapped(st, "ob")
    assert "etl_batch_id" in fa, "per-origin override (protect) did not beat the detected default"
    assert "headcount" not in fa, (
        "per-origin override (force metadata) did not beat the detected default"
    )
    assert "etl_batch_id" not in fb and "headcount" in fb, (
        "origin oa's override leaked into origin ob"
    )


def test_3c_run_override_beats_origin_override(fake_db):
    origin = override_payload(business=("etl_batch_id",), metadata=("headcount",))
    run = override_payload(business=("headcount",), metadata=("etl_batch_id",))
    st = discover(_state(origin_overrides={"oa": origin}, run=run))
    fa = mapped(st, "oa")
    assert "headcount" in fa, "per-run override (protect) did not beat the per-origin override"
    assert "etl_batch_id" not in fa, (
        "per-run override (force metadata) did not beat the per-origin override"
    )


def test_3d_run_override_alone_beats_default_for_every_origin(fake_db):
    st = discover(_state(run=override_payload(business=("created_at",), metadata=("email",))))
    for origin in ("oa", "ob"):
        f = mapped(st, origin)
        assert "created_at" in f and "email" not in f, (origin, f)


def test_3e_overrides_leave_no_state_behind(fake_db):
    discover(
        _state(
            origin_overrides={"oa": override_payload(business=("etl_batch_id",))},
            run=override_payload(business=("created_at",)),
        )
    )
    st = discover(_state())  # same process, no overrides
    f = mapped(st, "oa")
    assert not (f & set(ETL_COLS)), (
        f"override from a previous call persisted (module-level state): {f}"
    )


def test_3f_override_reaches_matching_with_same_precedence(fake_db, tmp_path, monkeypatch):
    origin = override_payload(business=("etl_batch_id",))
    run = override_payload(metadata=("etl_batch_id",), business=("headcount",))
    st = discover(_state(origin_overrides={"oa": origin}))
    captured, *_ = run_matching(st, tmp_path, monkeypatch)
    assert "etl_batch_id" in _seen_by_matching(captured, "oa"), (
        "origin-protected column missing from matching"
    )
    assert "etl_batch_id" not in _seen_by_matching(captured, "ob")
    st = discover(_state(origin_overrides={"oa": origin}, run=run))
    captured, *_ = run_matching(st, tmp_path, monkeypatch)
    assert "etl_batch_id" not in _seen_by_matching(captured, "oa"), (
        "run layer did not beat origin layer in matching"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 4. protection of a business field that looks like metadata + recorded risk
# ═════════════════════════════════════════════════════════════════════════════
def _walk(node: Any):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk(v)


def annotations_for(update: Any, name: str) -> list[dict[str, Any]]:
    """Dicts that name `name` AND carry classification vocabulary.

    Raw column entries ({name,type,nullable}) and raw sample rows (keys are column names) carry no
    such vocabulary once the fixture's own column names are blanked out of the text.
    """
    blank = re.compile(
        "|".join(
            sorted({c for c, _ in CUSTOMER_COLS} | {"cat_oid", "cat_owner"}, key=len, reverse=True)
        )
    )
    out = []
    for n in _walk(update):
        if not isinstance(n, dict):
            continue
        if not any(
            isinstance(v, str) and (v == name or v.endswith("." + name) or v.startswith(name + "."))
            for v in n.values()
        ):
            continue
        vocab = " ".join(
            [str(k) for k in n] + [str(v) for v in n.values() if isinstance(v, (str, bool))]
        )
        if ANNOTATION_RE.search(blank.sub("", vocab)):
            out.append(n)
    return out


def test_4a_protected_lookalike_is_mapped_and_matched_for_that_origin_only(
    fake_db, tmp_path, monkeypatch
):
    # created_at looks like ETL metadata by name; here it is a business field for oa (e.g. founding date)
    st = discover(_state(origin_overrides={"oa": override_payload(business=("created_at",))}))
    assert "created_at" in mapped(st, "oa"), (
        "override did not protect the look-alike from exclusion"
    )
    assert "created_at" not in mapped(st, "ob")
    captured, *_ = run_matching(st, tmp_path, monkeypatch)
    assert "created_at" in _seen_by_matching(captured, "oa")
    assert "created_at" not in _seen_by_matching(captured, "ob")


def test_4b_override_and_misclassification_risk_are_recorded_not_hidden(fake_db):
    st = discover(_state(origin_overrides={"oa": override_payload(business=("created_at",))}))
    notes = annotations_for(st["_update"], "created_at")
    assert notes, (
        "no classification record for the overridden column anywhere in the discovery output"
    )
    text = json.dumps(notes, default=str).lower()
    assert re.search(r"overrid", text), (
        f"record does not say the column was overridden: {text[:300]}"
    )
    assert re.search(r"origin|oa", text), (
        f"record does not say WHICH layer protected it: {text[:300]}"
    )
    assert re.search(r"metadata|etl|detect", text), (
        f"record does not keep the detected class: {text[:300]}"
    )
    # and the un-overridden origin: the exclusion itself is recorded, not silent
    assert annotations_for(st["_update"], "etl_batch_id"), (
        "excluded metadata columns are not recorded in the output"
    )


def test_4c_signup_date_report_ceiling(fake_db):
    """The report's own example. If this implementation never flags signup_date by default the
    ceiling is not reproducible here: SKIP (stated in the acceptance doc), protection is covered by 4a."""
    base = discover(_state())
    if "signup_date" in mapped(base, "oa"):
        pytest.skip(
            "signup_date is not flagged as metadata by default; ceiling not reproducible (see 4a)"
        )
    assert annotations_for(base["_update"], "signup_date"), (
        "signup_date excluded with no recorded risk"
    )
    st = discover(_state(origin_overrides={"oa": override_payload(business=("signup_date",))}))
    assert "signup_date" in mapped(st, "oa"), "signup_date not protected by the override"
    assert annotations_for(st["_update"], "signup_date"), "protection recorded nowhere"


def test_4d_product_catalog_table_protectable_by_override(fake_db):
    st = discover(_state(origin_overrides={"oa": override_payload(data=("product_catalog",))}))
    assert {"sku", "title", "list_price"} <= mapped(st, "oa", entity="product"), (
        "table-level override (treat_as_data) did not protect a business table that looks like a catalog"
    )


def test_4e_override_cannot_resurrect_a_dbms_catalog_schema_table_silently(fake_db):
    """Catalog override is table-level and recorded; a column override must not drag catalog tables back in."""
    st = discover(
        _state(origin_overrides={"oa": override_payload(business=("cat_oid", "created_at"))})
    )
    assert "cat_oid" not in mapped(st, "oa"), "a COLUMN override re-admitted a catalog-schema table"


# ═════════════════════════════════════════════════════════════════════════════
# 5. robustness (edge cases an implementation must not crash on)
# ═════════════════════════════════════════════════════════════════════════════
def test_5a_empty_and_degenerate_schemas(monkeypatch):
    degenerate = {
        "oa": {"tables": []},
        "ob": {
            "tables": [
                _tbl("public", "customer", [], {}),
                _tbl("public", "customer2", [("id", "INTEGER")], {}),
            ]
        },
    }
    monkeypatch.setattr(
        "src.mcp_server.tools.discover_schema.create_connector",
        lambda _t, c: FakeConnector(degenerate[c["database"]]),
    )
    st = discover(_state())
    assert set(st["schemas"]) == {"oa", "ob"}
    assert mapped(st, "oa") == set()


def test_5b_null_sample_values_and_deterministic(monkeypatch, fake_db):
    for t in fake_db["oa"]["tables"]:
        t["sample_row"] = {c["name"]: None for c in t["columns"]}
    a = discover(_state())["_update"]
    b = discover(_state())["_update"]
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert "etl_batch_id" not in mapped(discover(_state()), "oa"), (
        "name-pattern detection must not need sample values"
    )


def test_5c_discover_update_contract_unchanged(fake_db):
    st = discover(_state())
    assert {"schemas", "errors", "failure", "interrupt_data"} <= set(st["_update"])


# ═════════════════════════════════════════════════════════════════════════════
# 6. plan 2.4: no fitted constants
# ═════════════════════════════════════════════════════════════════════════════
OWNED = (
    "src/orchestrator/nodes/discover_schemas.py",
    "src/mcp_server/tools/discover_schema.py",
    "src/orchestrator/nodes/map_fields_node.py",
    "src/orchestrator/nodes/resolve_entities.py",
)
OWNED_NEW_NAME = re.compile(r"override|metadata|catalog|classif|system_table", re.I)
CORPUS_LITERALS = (
    "France",
    "US",
    "SAS",
    "Berhad",
    "Global",
    "Group",
    "Holdings",
    "companyname",
    "main_entity_id",
    "company_relation",
    "staff",
)
# today's pre-existing hits (recorded 2026-10-04 before any P12 change): any other hit or a higher count is new.
BASELINE_HITS = {("src/orchestrator/nodes/resolve_entities.py", "company_relation"): 3}
BASELINE_NUMERIC_CONSTANTS = {
    ("src/orchestrator/nodes/map_fields_node.py", "_MAX_SAMPLE_RECORDS"),
    ("src/orchestrator/nodes/map_fields_node.py", "_MAX_SAMPLE_VALUES"),
    ("src/orchestrator/nodes/resolve_entities.py", "_MAX_POOL_WORKERS"),
    ("src/orchestrator/nodes/resolve_entities.py", "_DEST_READ_LIMIT"),
}
# P12 spec names NO sanctioned standing constant. Add a name here only if the item's spec does.
SANCTIONED_NEW_CONSTANTS: set[str] = set()


def _owned_files() -> list[str]:
    files = list(OWNED)
    res = subprocess.run(
        ["git", "ls-files", "-m", "-o", "--exclude-standard", "src"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    for p in res.stdout.split():
        if p.endswith(".py") and OWNED_NEW_NAME.search(Path(p).name) and p not in files:
            files.append(p)  # a new override/classifier module the worker added
    return files


def test_6a_no_corpus_literals_beyond_baseline():
    hits = {}
    for f in _owned_files():
        text = (REPO / f).read_text()
        for lit in CORPUS_LITERALS:
            n = len(re.findall(rf"(?<![A-Za-z0-9_]){re.escape(lit)}(?![A-Za-z0-9])", text))
            if n:
                hits[(f, lit)] = n
    new = {k: v for k, v in hits.items() if v > BASELINE_HITS.get(k, 0)}
    assert not new, f"corpus literals added (plan 2.4): {new}"


def test_6b_no_new_numeric_module_constants():
    new = []
    for f in _owned_files():
        tree = ast.parse((REPO / f).read_text())
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not any(
                isinstance(n, ast.Constant)
                and isinstance(n.value, (int, float))
                and not isinstance(n.value, bool)
                for n in ast.walk(value)
            ):
                continue
            for t in targets:
                if (
                    isinstance(t, ast.Name)
                    and (f, t.id) not in BASELINE_NUMERIC_CONSTANTS
                    and t.id not in SANCTIONED_NEW_CONSTANTS
                ):
                    new.append((f, t.id))
    assert not new, (
        f"new numeric module constants {new}: would a count over the origin's data produce them? "
        "(plan 2.4; P12's spec names no sanctioned constant)"
    )


def test_6c_no_inline_float_thresholds_in_new_code():
    """A detection threshold written inline (e.g. `if ratio > 0.9`) is a fitted constant too."""
    bad = []
    for f in _owned_files():
        if f in OWNED:
            continue  # pre-existing files are judged by the diff-based baseline in 6a/6b
        for n in ast.walk(ast.parse((REPO / f).read_text())):
            if (
                isinstance(n, ast.Constant)
                and isinstance(n.value, float)
                and n.value not in (0.0, 1.0, 0.5)
            ):
                bad.append((f, n.lineno, n.value))
    assert not bad, f"inline float constants in the new classifier module: {bad}"
