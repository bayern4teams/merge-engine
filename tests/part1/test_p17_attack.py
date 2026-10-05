"""P17 attack (verifier). Strict xfail = a defect found in the P17 implementation (flips to a
failure the day it is fixed, so the marker must then be removed). Reuses the acceptance harness.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p17_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.orchestrator.nodes import load_data as nld
from tests.part1.test_p17_acceptance import FakeDest, run_node


def _sch(**tables):
    return {t: {"columns": [{"name": c} for c in cols]} for t, cols in tables.items()}


def _state(entity="widget", recs=None, mapping=None, unmapped=None):
    return {
        "unified_entities": {entity: recs or []},
        "field_mappings": {entity: mapping or {}},
        "unmapped_fields": {entity: unmapped} if unmapped else {},
        "destination": {"type": "postgresql", "tables": {}},
    }


def _m(*cols):
    return {c: {"dest_column": c, "requires_schema_change": False} for c in cols}


def _recs(n, **cols):
    return [
        {"id": f"r{i}", "_cluster_id": f"r{i}", **{k: f"{k}{i}" for k in cols}} for i in range(n)
    ]


# ---------------- no false defect on a table that is fine ----------------
def test_healthy_table_all_mapped_columns_present_is_not_defective():
    st = _state(recs=_recs(5, name=1), mapping=_m("name"))
    assert nld.destination_schema_defects(st, _sch(widget=["id", "name", "_source_ids"])) == []


def test_healthy_table_with_pure_provenance_only_extra_cols():
    st = _state(recs=_recs(5, name=1), mapping=_m("name"))
    assert nld.destination_schema_defects(st, _sch(widget=["id", "name", "_loaded_at"])) == []


@pytest.mark.xfail(
    strict=True,
    reason="FALSE POSITIVE: unmapped_fields (source fields with no destination column, the normal "
    "mapper output) are treated as 'needed' columns, so a healthy [id,name] table whose only "
    "payload column is fully mapped is reported defective and EMPTIED because some unrelated "
    "source field (fax) is unmapped",
)
def test_healthy_table_with_unrelated_unmapped_source_field_is_not_defective():
    st = _state(
        recs=_recs(5, name=1, fax=1),
        mapping=_m("name"),
        unmapped=[{"field": "fax", "reason": "no destination column"}],
    )
    assert nld.destination_schema_defects(st, _sch(widget=["id", "name", "_source_ids"])) == []


@pytest.mark.xfail(
    strict=True,
    reason="FALSE POSITIVE end to end (same cause): the healthy table receives 0 rows",
)
def test_healthy_table_with_unmapped_field_still_loads_all_rows(tmp_path):
    st = {
        **_state(
            recs=_recs(7, name=1, fax=1),
            mapping=_m("name"),
            unmapped=[{"field": "fax", "reason": "no destination column"}],
        ),
        "target_entities": ["widget"],
        "extraction_order": ["widget"],
        "pending_schema_changes": [],
        "errors": [],
        "load_approval": True,
        "dry_run": False,
        "run_id": "p17-atk",
    }
    d = FakeDest({"widget": ["id", "name", "_source_ids"]})
    out = run_node(st, d, tmp_path)
    assert len(d.rows["widget"]) == 7, (len(d.rows["widget"]), out.get("destination_defects"))


# ---------------- defective shapes ----------------
def test_id_only_table_is_defective_and_counts_rows():
    st = _state(entity="x_y", recs=_recs(4, a_id=1, b_id=1), mapping=_m("a_id", "b_id"))
    (d,) = nld.destination_schema_defects(st, _sch(x_y=["id"]))
    assert d["missing_columns"] == ["a_id", "b_id"] and d["unprojectable_rows"] == 4
    assert d["table"] == "x_y" and len(d["held_links"]) == 4


def test_zero_column_table_does_not_crash_or_false_positive():
    st = _state(recs=_recs(3, a_id=1), mapping=_m("a_id"))
    assert nld.destination_schema_defects(st, _sch(widget=[])) == []


def test_missing_table_entirely_is_not_reported():
    st = _state(recs=_recs(3, a_id=1), mapping=_m("a_id"))
    assert nld.destination_schema_defects(st, {}) == []


@pytest.mark.xfail(
    strict=True,
    reason="GAP: a table whose only column is its key but is not literally named `id` "
    "(get_table_schema().primary_key says so) is never judged: payload is computed against the "
    "literal 'id'. The old key-only-row load is unchanged for it",
)
def test_pk_only_table_with_non_id_key_name_is_defective():
    st = _state(entity="x_y", recs=_recs(4, a_id=1, b_id=1), mapping=_m("a_id", "b_id"))
    s = {"x_y": {"primary_key": "x_y_pk", "columns": [{"name": "x_y_pk"}]}}
    assert nld.destination_schema_defects(st, s), "key-only table with non-id PK not reported"


def test_composite_key_link_table_with_both_columns_is_fine():
    st = _state(entity="x_y", recs=_recs(4, a_id=1, b_id=1), mapping=_m("a_id", "b_id"))
    assert nld.destination_schema_defects(st, _sch(x_y=["a_id", "b_id"])) == []


def test_null_and_blank_link_values_are_not_counted():
    recs = _recs(6, a_id=1, b_id=1)
    recs[0]["a_id"] = recs[0]["b_id"] = None
    recs[1]["a_id"] = recs[1]["b_id"] = "  "
    st = _state(entity="x_y", recs=recs, mapping=_m("a_id", "b_id"))
    (d,) = nld.destination_schema_defects(st, _sch(x_y=["id"]))
    assert d["unprojectable_rows"] == 4


def test_zero_value_and_false_are_counted_as_values():
    recs = [{"id": "a", "a_id": 0, "b_id": None}, {"id": "b", "a_id": False, "b_id": None}]
    st = _state(entity="x_y", recs=recs, mapping=_m("a_id", "b_id"))
    (d,) = nld.destination_schema_defects(st, _sch(x_y=["id"]))
    assert d["unprojectable_rows"] == 2  # 0 and False are real values


def test_all_null_links_report_nothing():
    st = _state(
        entity="x_y", recs=[{"id": "a", "a_id": None, "b_id": None}], mapping=_m("a_id", "b_id")
    )
    assert nld.destination_schema_defects(st, _sch(x_y=["id"])) == []


def test_multiple_defective_tables_all_reported_sorted_missing():
    st = {
        "unified_entities": {"p_q": _recs(2, z_id=1, a_id=1), "r_s": _recs(3, k_id=1)},
        "field_mappings": {"p_q": _m("z_id", "a_id"), "r_s": _m("k_id")},
        "destination": {"tables": {}},
    }
    out = nld.destination_schema_defects(st, _sch(p_q=["id"], r_s=["id"], other=["id", "n"]))
    assert {d["table"] for d in out} == {"p_q", "r_s"}
    assert next(d for d in out if d["table"] == "p_q")["missing_columns"] == ["a_id", "z_id"]


def test_pure_function_issues_no_connector_calls():
    st = _state(recs=_recs(3, a_id=1), mapping=_m("a_id"))
    nld.destination_schema_defects(st, _sch(widget=["id"]))  # no connector exists to call


def test_read_destination_schemas_is_read_only_and_tolerates_failure():
    class C:
        calls = []

        def list_tables(self):
            return ["t"]

        def get_table_schema(self, t):
            self.calls.append(("schema", t))
            return {"columns": [{"name": "id"}]}

        def __getattr__(self, n):
            raise AssertionError(f"unexpected connector call {n}")

    assert nld.read_destination_schemas(C()) == {"t": {"columns": [{"name": "id"}]}}

    class Bad:
        def list_tables(self):
            raise RuntimeError("nope")

    assert nld.read_destination_schemas(Bad()) == {}


# ---------------- node-level: partial projectability ----------------
def _node_state(entity, recs, mapping):
    return {
        **_state(entity=entity, recs=recs, mapping=mapping),
        "target_entities": [entity],
        "extraction_order": [entity],
        "pending_schema_changes": [],
        "errors": [],
        "load_approval": True,
        "dry_run": False,
        "run_id": "p17-atk",
    }


def test_partially_projectable_table_is_left_empty_and_no_ddl(tmp_path):
    recs = _recs(5, a_id=1, b_id=1)
    d = FakeDest({"x_y": ["id", "a_id"]})
    out = run_node(_node_state("x_y", recs, _m("a_id", "b_id")), d, tmp_path)
    assert d.rows["x_y"] == {} and not d.ddl and d.cols == d.cols0
    (df,) = out["destination_defects"]
    assert df["missing_columns"] == ["b_id"]


@pytest.mark.xfail(
    strict=True,
    reason="INCONSISTENT COUNT: the whole entity is skipped, but unprojectable_rows counts only "
    "rows with a value in a MISSING column; rows carrying only the present column are neither "
    "written nor counted (5 rows skipped, defect says 3)",
)
def test_rows_skipped_equal_rows_reported(tmp_path):
    recs = _recs(5, a_id=1, b_id=1)
    for r in recs[:2]:
        r["b_id"] = None  # projectable on a_id alone
    d = FakeDest({"x_y": ["id", "a_id"]})
    out = run_node(_node_state("x_y", recs, _m("a_id", "b_id")), d, tmp_path)
    (df,) = out["destination_defects"]
    assert df["unprojectable_rows"] == 5 or len(d.rows["x_y"]) == 2, (
        df["unprojectable_rows"],
        d.rows,
    )


def test_defective_run_is_not_a_clean_completion(tmp_path):
    d = FakeDest({"x_y": ["id"]})
    out = run_node(_node_state("x_y", _recs(4, a_id=1), _m("a_id")), d, tmp_path)
    rep = out["reconciliation_report"]
    assert out["errors"] and rep["destination_defects"]
    assert any("x_y" in f for f in rep["data_quality"]["red_flags"])


def test_two_defective_tables_node_level(tmp_path):
    st = _node_state("p_q", _recs(3, a_id=1), _m("a_id"))
    st["unified_entities"]["r_s"] = _recs(2, k_id=1)
    st["field_mappings"]["r_s"] = _m("k_id")
    st["target_entities"] = st["extraction_order"] = ["p_q", "r_s"]
    d = FakeDest({"p_q": ["id"], "r_s": ["id"]})
    out = run_node(st, d, tmp_path)
    assert {x["table"] for x in out["destination_defects"]} == {"p_q", "r_s"}
    assert d.rows == {"p_q": {}, "r_s": {}} and not d.ddl


@pytest.mark.xfail(
    strict=True,
    reason="OLD FAILURE VIA ANOTHER DOOR: when the table does not exist, destination_schema_defects "
    "skips it ('ensure_table's business') and ensure_table then CREATEs a bare (id) table "
    "(postgresql_destination.ensure_table) and key-only rows land. Real DDL + the 539-key-only-row "
    "shape, unreported",
)
def test_missing_table_is_not_created_and_loaded_with_key_only_rows(tmp_path):
    class Dest(FakeDest):
        def ensure_table(self, table):
            self.ddl.append(f"create_table {table}")
            self.cols[table] = ["id"]
            self.rows[table] = {}
            return True

        def list_tables(self):
            return list(self.cols)

    d = Dest({"other": ["id"]})
    st = _node_state("x_y", _recs(4, a_id=1), _m("a_id"))
    run_node(st, d, tmp_path)
    assert not d.ddl and not d.rows.get("x_y")


# ---------------- grep ratchets (plan 2.4) ----------------
SRC = Path(__file__).resolve().parents[2] / "src/orchestrator/nodes/load_data.py"


def _p17_block():
    txt = SRC.read_text()
    a = txt.index("def _resolve_dest_table")
    b = txt.index("def _write_run_report")
    return txt[a:b]


def test_no_corpus_literals_in_p17_code():
    blk = _p17_block()
    code = "\n".join(ln for ln in blk.splitlines() if not ln.strip().startswith("#"))
    # strip docstrings
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    for lit in (
        "company_industry",
        "industry",
        "staff",
        "main_entity_id",
        "company",
        "pagila",
        "origin",
    ):
        assert lit not in code, lit
    assert not re.findall(r"(?<![\w.])\d{2,}(?![\w.])", code), "bare numeric constant in P17 logic"
