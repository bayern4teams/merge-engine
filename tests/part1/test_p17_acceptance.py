"""P17 acceptance (derived from the report's Evidence + Fix text only; no P17 code seen).

Report P17 ("report, never repair"): a destination table that is structurally incapable of
carrying the links (the one-`id`-column shape, `company_industry` in the report) must
  1. be REPORTED LOUDLY at the pre-load gate: the table, the missing linkage columns and the
     number of rows that cannot be projected;
  2. have the links HELD in the run output (Part 1);
  3. be LEFT EMPTY rather than receive N rows of pure key (the old behaviour);
  4. be RE-PROJECTED once the schema's owner fixes the DDL - by the owner, never by the app;
and "no new destination columns, ever" stays intact: no ALTER / CREATE / DDL is issued.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p17_acceptance.py -q -p no:cacheprovider

ADAPTATION POINTS (the report fixes semantics, not interfaces; one place each):
  FakeDest        in-memory destination.  load_batch FILTERS each record to the table's real
                  columns exactly as the real connector does ("load_batch drops any column the
                  table still lacks"), so an id-only table receives key-only rows on the old code.
                  Every call is spied; DDL-ish calls are recorded in .ddl.
  run_node()      drives load_data_node (the only P17-owned seam) with an approved state.
  find_defect()   the report names no key for the defect, so it is located structurally: any node
                  (dict/list/str) of the node's returned update that (a) names the table, (b) names
                  EVERY missing linkage column, (c) carries the unprojectable row count, (d) says
                  defect-ish words in its text or key path, and (e) does NOT also exist in the
                  healthy twin run (so a report that merely echoes mappings/counts cannot pass).
  CARRIERS        how the unprojectable links reach the node: "mapped" (the mapping targets columns
                  the table lacks) is required; "unmapped" (mapper recorded them in unmapped_fields
                  because no column exists) is a JUDGEMENT carrier.
  GREP_BASELINE   2.4 ratchet: hits present before P17 (comments/docstrings); code hits must be 0.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from src.mcp_server.tools import load_data as tld
from src.orchestrator.nodes import load_data as nld
from tests.part1.test_p4_acceptance import GREP_PATTERNS, _buckets

DEFECT_WORDS = re.compile(r"defect|linkage|missing|unproject|cannot|incapable|schema", re.I)
DDL = re.compile(
    r"\b(alter|create|drop|add\s+column|truncate)\b|add_col|new_col|create_table", re.I
)

# Two defect shapes: different names, different link-column spellings (one without `_id`),
# different counts - a hard-coded table name, column name or count cannot pass both.
SHAPES = {
    "pair": dict(table="member_group", links=["member_id", "group_id"], n=37, healthy="roster"),
    "tag": dict(
        table="tag_link", links=["item_ref", "tag_ref", "weight_ref"], n=53, healthy="item"
    ),
}
PROV = ["_source_ids", "_loaded_at", "_source_count"]


# -- harness --------------------------------------------------------------------------------
class FakeDest:
    """tables: {name: [columns]}.  Spies every call; refuses nothing, records everything."""

    def __init__(self, tables):
        self.cols = {t: list(c) for t, c in tables.items()}
        self.cols0 = {t: list(c) for t, c in tables.items()}
        self.rows = {t: {} for t in tables}
        self.writes = []  # every load_batch (table, n), even if later rolled back
        self.ddl = []
        self.called = []

    def list_tables(self):
        return list(self.cols)

    def get_foreign_keys(self, table):
        return {}

    def get_table_schema(self, table):
        return {
            "primary_key": "id",
            "columns": [
                {"name": c, "type": "text", "nullable": c != "id"} for c in self.cols[table]
            ],
        }

    def ensure_table(self, table):
        self.called.append("ensure_table")
        return False  # the table exists; nothing is created

    def load_batch(self, table_name, records, mapping=None, upsert_key="id"):
        assert table_name in self.cols, f"write to unknown table {table_name}"
        self.writes.append((table_name, len(records)))
        ins = 0
        for rec in records:
            row = {k: v for k, v in rec.items() if k in self.cols[table_name]}  # real behaviour
            ins += row[upsert_key] not in self.rows[table_name]
            self.rows[table_name].setdefault(row[upsert_key], {}).update(row)
        return {"inserted": ins, "updated": len(records) - ins}

    # DDL surface: any use is recorded, and really changes the schema so a test can see it
    def add_column(self, table, column, *a, **k):
        self.ddl.append(f"add_column {table}.{column}")
        self.cols[table].append(column)

    def create_table(self, table, *a, **k):
        self.ddl.append(f"create_table {table}")
        self.cols.setdefault(table, ["id"])

    def alter_table(self, *a, **k):
        self.ddl.append("alter_table")

    def execute(self, sql, *a, **k):
        self.ddl.append(f"execute {sql}")

    @contextmanager
    def atomic(self, dry_run=False):
        snap = copy.deepcopy(self.rows)
        yield
        if dry_run:
            self.rows = snap

    def close(self):
        return None

    def __getattr__(self, name):  # unknown probes recorded; hasattr(...) stays False
        if name.startswith("__"):
            raise AttributeError(name)
        self.called.append(name)
        raise AttributeError(name)


def _records(shape):
    out = []
    for i in range(shape["n"]):
        rec = {"id": f"cl{i:04d}", "_cluster_id": f"cl{i:04d}"}
        for j, col in enumerate(shape["links"]):
            rec[col] = f"{col[:1]}{(i * (j + 3)) % 11}-v{i}"  # distinct, recognisable values
        out.append(rec)
    return out


def _state(shape, carrier="mapped", dry_run=False):
    t, links, h = shape["table"], shape["links"], shape["healthy"]
    if carrier == "mapped":
        mapping_t = {
            c: {"dest_column": c, "dest_table": t, "requires_schema_change": True, "confidence": 1}
            for c in links
        }
        unmapped = {}
    else:  # mapper obeyed "no new columns": nothing mapped, the fields are recorded as unmapped
        mapping_t = {}
        unmapped = {t: [{"field": c, "reason": "no destination column"} for c in links]}
    healthy_recs = [
        {"id": f"h{i}", "_cluster_id": f"h{i}", "label": f"Label {i}"} for i in range(9)
    ]
    return {
        "user_request": "x",
        "target_entities": [t, h],
        "extraction_order": [h, t],
        "unified_entities": {t: _records(shape), h: healthy_recs},
        "field_mappings": {
            t: mapping_t,
            h: {"label": {"dest_column": "label", "requires_schema_change": False}},
        },
        "unmapped_fields": unmapped,
        "destination": {"type": "postgresql", "tables": {}},
        "pending_schema_changes": [],
        "errors": [],
        "load_approval": True,
        "dry_run": dry_run,
        "run_id": "p17-test",
    }


def _dest(shape, linked=False):
    """linked=False: the defective destination (table = `id` only).  linked=True: the twin where
    the schema's owner has fixed the DDL (same table NAME, linkage columns present)."""
    t = shape["table"]
    cols_t = ["id", *shape["links"], *PROV] if linked else ["id"]
    return FakeDest({t: cols_t, shape["healthy"]: ["id", "label", *PROV]})


def run_node(state, dest, tmp_path):
    with (
        patch.object(nld, "_get_destination_connector", lambda cfg: dest),
        patch.object(tld, "_get_destination_connector", lambda cfg: dest),
        patch.object(nld, "_RUN_REPORTS_DIR", tmp_path / "run_reports"),
    ):
        return asyncio.run(nld.load_data_node(copy.deepcopy(state)))


def _nodes(obj, path=""):
    """Yield (key-path, node) for every dict/list/str in obj."""
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _nodes(v, f"{path}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _nodes(v, f"{path}[{i}]")


def _text(node):
    return json.dumps(node, default=str, sort_keys=True)


def find_defect(out, shape, healthy_out=None):
    """Smallest node naming table + every missing link column + row count, defect-worded,
    and absent from the healthy twin's output.  None if there is none."""
    base = {_text(n) for _, n in _nodes(healthy_out)} if healthy_out is not None else set()
    best = None
    for path, node in _nodes(out):
        txt = _text(node)
        if txt in base or shape["table"] not in txt:
            continue
        if not all(c in txt for c in shape["links"]):
            continue
        if not re.search(rf"(?<![\w.]){shape['n']}(?![\w.])", txt):
            continue
        if not DEFECT_WORDS.search(path + " " + txt):
            continue
        if best is None or len(txt) < len(best[1]):
            best = (path, txt, node)
    return best


@pytest.fixture(params=list(SHAPES), ids=list(SHAPES))
def shape(request):
    return SHAPES[request.param]


@pytest.fixture
def twin(shape, tmp_path):
    """The same run against the FIXED destination: the baseline of 'nothing is wrong'."""
    d = _dest(shape, linked=True)
    return d, run_node(_state(shape), d, tmp_path / "twin")


# -- 0. harness precondition: with the DDL fixed every row lands (else the checks are vacuous) --
def test_0_twin_destination_projects_every_row(shape, twin):
    d, out = twin
    t = shape["table"]
    assert len(d.rows[t]) == shape["n"], "precondition: with linkage columns present, all rows land"
    assert all(r.get(c) is not None for r in d.rows[t].values() for c in shape["links"])
    assert not out.get("failure")


# -- 1. the defect is reported: table, missing linkage columns, unprojectable row count --
@pytest.mark.parametrize("carrier", ["mapped", pytest.param("unmapped", id="unmapped-JUDGEMENT")])
def test_1_defect_names_table_missing_columns_and_row_count(shape, carrier, tmp_path, twin):
    out = run_node(_state(shape, carrier), _dest(shape), tmp_path)
    assert find_defect(out, shape, twin[1]), (
        f"no defect report naming {shape['table']!r}, {shape['links']} and {shape['n']} rows; "
        f"returned keys={list(out)}"
    )


def test_1b_JUDGEMENT_defect_names_only_the_columns_that_are_missing(shape, tmp_path, twin):
    """Precision (JUDGEMENT): the table has `id` and the FIRST link column; only the rest are
    'missing'.  The present one must not be listed as missing."""
    t, links = shape["table"], shape["links"]
    d = FakeDest({t: ["id", links[0]], shape["healthy"]: ["id", "label"]})
    out = run_node(_state(shape), d, tmp_path)
    hit = find_defect(out, {**shape, "links": links[1:]}, twin[1])
    assert hit, "defect naming the genuinely missing columns not found"
    present = [v for _, v in _nodes(hit[2]) if v == links[0]]
    assert not present, f"present column {links[0]!r} listed as missing: {hit[2]}"


def test_1c_defect_is_in_the_returned_state_and_survives_json(shape, tmp_path, twin):
    """HITL resume trap (plan 2.6): the gate payload is built from STATE alone, so the defect must
    be in the node's returned update (a log line or an exception is invisible to the gate)."""
    out = run_node(_state(shape), _dest(shape), tmp_path)
    assert isinstance(out, dict)
    assert find_defect(out, shape, twin[1]), "defect not in the returned state update"
    json.dumps(out, default=str)  # checkpointable / gate-payload serialisable
    rep = out.get("reconciliation_report")
    assert isinstance(rep, dict) and find_defect(rep, shape, twin[1]), (
        "defect not in the reconciliation report the reviewer reads"
    )


def test_1d_defect_reported_in_dry_run_too(shape, tmp_path, twin):
    d = _dest(shape)
    out = run_node(_state(shape, dry_run=True), d, tmp_path)
    assert find_defect(out, shape, twin[1])
    assert not [w for w in d.writes if w[0] == shape["table"] and w[1] > 0]


def test_1e_defect_lands_in_the_persisted_run_report(shape, tmp_path, twin):
    run_node(_state(shape), _dest(shape), tmp_path)
    files = list((tmp_path / "run_reports").glob("*.json"))
    assert files, "no run report written"
    assert find_defect(json.loads(files[0].read_text()), shape, twin[1])


# -- 2. the table is left empty: 0 rows, no write at all (old: N key-only rows) --
def test_2_defective_table_receives_zero_rows(shape, tmp_path):
    d = _dest(shape)
    run_node(_state(shape), d, tmp_path)
    t = shape["table"]
    assert len(d.rows[t]) == 0, f"{len(d.rows[t])} key-only rows were loaded into {t}"
    assert not [w for w in d.writes if w[0] == t and w[1] > 0], d.writes


# -- 3. report, never repair: no new column, no DDL (spy on the connector) --
def test_3_no_ddl_issued_and_schema_unchanged(shape, tmp_path, twin):
    d = _dest(shape)
    out = run_node(_state(shape), d, tmp_path)
    assert find_defect(out, shape, twin[1]), "vacuous: no defect was reported"  # old code fails
    assert not d.ddl, f"DDL issued: {d.ddl}"
    assert d.cols == d.cols0, "destination schema changed"
    assert not [c for c in d.called if DDL.search(c)], d.called


def test_3b_pending_schema_change_for_the_missing_columns_is_still_refused(shape, tmp_path, twin):
    """Even a pending 'add these columns' request (the old mapper's output) must not execute."""
    st = _state(shape)
    st["pending_schema_changes"] = [
        {"table": shape["table"], "column": c, "type": "text"} for c in shape["links"]
    ]
    d = _dest(shape)
    out = run_node(st, d, tmp_path)
    assert find_defect(out, shape, twin[1]), "vacuous: no defect was reported"
    assert not d.ddl and d.cols == d.cols0
    assert len(d.rows[shape["table"]]) == 0


# -- 4. the links are held in the run output, not discarded (JUDGEMENT: Part 1 "run output") --
def test_4_unprojectable_links_are_held_in_the_run_output(shape, tmp_path):
    out = run_node(_state(shape), _dest(shape), tmp_path)
    files = [p.read_text() for p in (tmp_path / "run_reports").glob("*.json")]
    blob = _text(out) + "".join(files)
    held = sum(1 for r in _records(shape) if all(str(r[c]) in blob for c in shape["links"]))
    assert held == shape["n"], f"only {held}/{shape['n']} link rows are held in the run output"


# -- 5. regression: healthy tables load unchanged, no false defect --
def test_5_healthy_table_in_same_run_loads_unchanged(shape, tmp_path, twin):
    d = _dest(shape)
    out = run_node(_state(shape), d, tmp_path)
    h = shape["healthy"]
    assert find_defect(out, shape, twin[1]), "vacuous: no defect was reported"  # old code fails
    assert len(d.rows[h]) == 9 and all(r["label"].startswith("Label") for r in d.rows[h].values())
    assert not out.get("failure"), out.get("failure")
    assert out["reconciliation_report"].get("load_rolled_back") is False


def test_5b_all_healthy_run_reports_no_defect_and_loads_everything(shape, twin):
    d, out = twin
    assert len(d.rows[shape["table"]]) == shape["n"]
    for path, _ in _nodes(out):
        assert not re.search(r"defect|unprojectable|cannot[_ ]project", path, re.I), path


def test_5c_nothing_to_project_is_not_a_defect(shape, tmp_path):
    """Same name, id-only columns, but the records are bare ids with NO links to carry: nothing is
    unprojectable.  Guards a rule keyed on 'the table is one column wide'."""
    t = shape["table"]
    st = _state(shape)
    st["unified_entities"][t] = [{"id": f"k{i}", "_cluster_id": f"k{i}"} for i in range(5)]
    st["field_mappings"][t] = {}
    out = run_node(st, _dest(shape), tmp_path)
    assert not [p for p, n in _nodes(out) if re.search(r"unprojectable|defect", p, re.I) and n], (
        "false defect for a table that has no links to project"
    )


# -- 6. re-projection once the schema's owner fixes the DDL: no re-merge, same state --
def test_6_reprojects_after_the_owner_fixes_the_ddl(shape, tmp_path, twin):
    st = _state(shape)
    broken = _dest(shape)
    out1 = run_node(st, broken, tmp_path / "a")
    assert find_defect(out1, shape, twin[1]), "vacuous: no defect before the fix"
    assert len(broken.rows[shape["table"]]) == 0
    fixed = _dest(shape, linked=True)  # the owner altered the table; the app did nothing
    out2 = run_node(st, fixed, tmp_path / "b")  # identical state: no re-merge
    t = shape["table"]
    assert len(fixed.rows[t]) == shape["n"]
    assert all(r.get(c) is not None for r in fixed.rows[t].values() for c in shape["links"])
    assert not find_defect(out2, shape, twin[1]), "defect still reported after the DDL was fixed"


# -- 7. section 2.4: no corpus literals, no fitted constants --
OWNED = [Path(nld.__file__), Path(tld.__file__)]
# Snapshots of the PARENT code at derive time.  code hits must stay 0; doc/comment hits and the
# counts below may not grow.
GREP_BASELINE = {
    "orchestrator/load_data.py": {"comment": {"industry": 2}},
    "mcp_server/load_data.py": {
        "doc": {"origin_names": 2, "industry": 10},
        "comment": {"origin_names": 3},
    },
}
NUM_BASELINE = {
    "orchestrator/load_data.py": {0.95, 3, 4, 5, 10, 100, 300, 400},
    "mcp_server/load_data.py": {5432, 8123},
}
ID_LITERAL_BASELINE = {"orchestrator/load_data.py": 14, "mcp_server/load_data.py": 2}
_TRIVIAL = {0, 1, 2, -1}
_ID_LIT = re.compile(r"^[a-z][a-z0-9]*_(id|ref|key|fk)$", re.I)


def _key(path):
    return path.parent.parent.name + "/" + path.name


@pytest.mark.parametrize("path", OWNED, ids=_key)
def test_7_no_corpus_literals_grep(path):
    got = _buckets(path)
    base = GREP_BASELINE.get(_key(path), {})
    assert not got["code"], f"corpus literal in CODE of {path.name}: {got['code']}"
    for bucket in ("doc", "comment"):
        for pat, n in got[bucket].items():
            assert n <= base.get(bucket, {}).get(pat, 0), (
                f"new {pat} literal(s) in {bucket}s of {path.name}: {n} > baseline"
            )
    assert set(GREP_PATTERNS) >= {"industry", "company_industry", "staff", "origin_names"}


@pytest.mark.parametrize("path", OWNED, ids=_key)
def test_7b_no_new_numeric_constants_or_linkage_name_literals(path):
    tree = ast.parse(path.read_text())
    nums = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    } - _TRIVIAL
    new = nums - NUM_BASELINE.get(_key(path), set())
    assert not new, (
        f"new numeric constant(s) in {path.name}: {sorted(new)}; would a corpus count produce them?"
    )
    ids = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _ID_LIT.match(n.value)
    ]
    assert len(ids) <= ID_LITERAL_BASELINE.get(_key(path), 0), f"hard-coded linkage names: {ids}"
