"""P4 acceptance (derived from the report's Evidence + Fix text only; no P4 code seen).

Report P4: junction = a table whose MINIMAL UNIQUE COLUMN COMBINATION equals the union of >=2
foreign-key column sets (payload columns irrelevant); an all-FK table is NOT thereby a junction;
weak entity (FK + local discriminator) is PROPOSED at the gate, never silently acted on; repeated
text on a row becomes a dimension row plus a link with ZERO new destination columns.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p4_acceptance.py -q -p no:cacheprovider

ADAPTATION POINTS (the report fixes semantics, not interfaces; one place each):
  _Origin            in-memory origin: sample_distinct_values + extract + profile_column (exact
                     FULL uniqueness, as the real Postgres connector) + discover_*.  A fix that
                     needs another connector method for the composite-key test adds it HERE.
  claims()           role lookup.  The implementation may publish roles under any key; a table is
                     "claimed" for WORD when it sits in a structure labelled WORD (a key, a sibling
                     string, or a string value).  Searched: analyze_dependencies' output and the
                     payload of human_nodes._build_deps_interrupt.  Add a source to ROLE_SOURCES
                     if roles live elsewhere (e.g. plan_scope output).
  FakeDest           in-memory destination (get_foreign_keys/get_table_schema/ensure_table/
                     load_batch).  Dimension find-or-create is expected to need no lookup method
                     (deterministic ids, as _write_child_rows does); add one here if it does.
  DEST_SHAPES        two destination shapes, so a hard-coded dimension name cannot pass.
  GREP_BASELINE      2.4 ratchet: hits present before P4 (comments/docstrings); code hits must be 0.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import io
import re
import tokenize
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.mcp_server.tools import join_graph as jg
from src.mcp_server.tools import load_data as tld
from src.orchestrator.nodes import human_nodes as hn
from src.orchestrator.nodes import load_data as nld
from src.orchestrator.nodes import map_fields_node as mfn
from tests.part1 import p4_fixtures as fx

ad = importlib.import_module("src.orchestrator.nodes.analyze_dependencies")
ps = importlib.import_module("src.mcp_server.tools.plan_scope")
mf = importlib.import_module("src.mcp_server.tools.map_fields")

OWNED = [Path(m.__file__) for m in (ps, ad, mf, mfn, nld, hn)] + [Path(tld.__file__)]
# NOTE tools/load_data.py is NOT in HANDOFF's P4 ownership but find-or-create of a dimension row
# lives in _write_child_rows there (it drops "parent holds the FK" today).  Lead: confirm.
GREP_PATTERNS = {
    "industry": r"industry",
    "product_category": r"product_?categor",
    "company_industry": r"company_industry",
    "staff": r"\bstaff\b",
    "origin_names": r"testdiffbot|companyexperience|companyname|companyid|main_entity_id"
    r"|company_relation",
}
# {file: {bucket: {pattern: n}}} on the parent code.  code/strings must stay 0.
GREP_BASELINE = {
    "plan_scope.py": {"comment": {"origin_names": 1}},
    "analyze_dependencies.py": {"comment": {"origin_names": 2}},
    "map_fields.py": {"comment": {"origin_names": 2}},
    "load_data.py": {  # two files share this name; counts are the sum of both
        "comment": {"industry": 2, "origin_names": 3},
        "doc": {"industry": 10, "origin_names": 2},
    },
}
BASELINE_NUM_CONSTS = {
    "_PROBE_TOTAL",
    "_PROBE_PER_TABLE",
    "_HEURISTIC_ACCEPT",
    "_MAX_SAMPLE_RECORDS",
    "_MAX_SAMPLE_VALUES",
    "_SCOPED_MERGE_GUARDRAIL_MIN_CLUSTERS",
}
# ast count of list/set/tuple literals with >=5 string constants (a hand-written name list)
BASELINE_NAME_LISTS = {"plan_scope.py": 1, "map_fields.py": 2}


# ── schema side: harness ─────────────────────────────────────────────────────────────────────
class _Origin:
    def __init__(self, rows):
        self.rows = rows

    async def sample_distinct_values(self, table, column, limit=500, filter_values=None):
        vals = list(dict.fromkeys(self.rows[table][column]))
        if filter_values is not None:
            keep = {jg.normalize_val(v) for v in filter_values}
            vals = [v for v in vals if jg.normalize_val(v) in keep]
        return vals[:limit]

    async def profile_column(self, table, column, mode="SAMPLED", sample_size=10_000):
        v = self.rows[table][column]
        nn = [x for x in v if x is not None]
        return {
            "mode": mode,
            "total_count": len(v),
            "null_count": len(v) - len(nn),
            "null_ratio": 0.0,
            "distinct_count": len(set(nn)),
            "min_value": None,
            "max_value": None,
        }

    async def extract(
        self, table_name, columns, filter=None, batch_size=1000, cursor=None, watermark=None
    ):
        cols = self.rows[table_name]
        names = list(cols) if columns in (None, ["*"]) else list(columns)
        total = len(next(iter(cols.values())))
        lo = int(cursor or 0)
        hi = min(lo + batch_size, total)
        batch = [{c: cols[c][i] for c in names} for i in range(lo, hi)]
        return {
            "rows": batch,
            "column_names": names,
            "total_count": total,
            "next_cursor": str(hi) if hi < total else None,
            "origin_ids": list(range(lo, hi)),
        }

    async def discover_indexes(self, table_name):
        return []  # nothing declared: the keys must be found in the DATA

    async def discover_statistics(self, table_name):
        return {"row_count_estimate": len(next(iter(self.rows[table_name].values())))}

    async def discover_schema(self):
        return {
            "tables": [
                {"name": t, "columns": [{"name": c} for c in r]} for t, r in self.rows.items()
            ]
        }

    def capabilities(self):
        return {
            "supports_declared_pk": False,
            "supports_declared_fk": False,
            "supports_sampling": True,
        }

    async def close(self):
        return None


class _StubLLM:
    def __init__(self, cfg):
        pass

    async def call_json(self, *a, **k):
        return {}


def _schema_of(rows):
    tables = [
        {"name": t, "columns": [{"name": c, "type": "text"} for c in cols]}
        for t, cols in rows.items()
    ]
    return {"o1": {"tables": tables}}, [{"id": "o1", "type": "stub", "connection": {}}]


def edges_of(graph):
    return {
        (e.child_table, e.child_column, e.parent_table, e.parent_column)
        for lst in graph._adjacency.values()
        for e in lst
    }


@pytest.fixture(scope="module", params=[30, 120], ids=lambda n: f"n{n}")
def ran(request):
    """(state_in, node_output, deps_interrupt_payload) for the planted-role fixture."""
    rows = fx.rows(request.param)
    schemas, origins = _schema_of(rows)
    state = {
        "target_entities": list(rows),
        "schemas": schemas,
        "origins": origins,
        "entity_tables": {t: [{"table": t, "origin_id": "o1"}] for t in rows},
        "llm_config": {"stub": True},
    }
    factory = lambda *a, **k: _Origin(rows)  # noqa: E731
    with (
        patch.object(jg, "create_connector", factory),
        patch("src.mcp_server.connectors.create_connector", factory),
        patch.object(ad, "LLMClient", _StubLLM),
        patch.object(ad, "create_connector", factory, create=True),
        patch.object(ps, "create_connector", factory, create=True),
    ):
        out = asyncio.run(ad.analyze_dependencies(dict(state)))
    merged = {**state, **out}
    # the payload is built from STATE alone, exactly as workflow_manager does at the pause
    # (interrupt_before: human_review_deps' body has NOT run) -- plan 2.6.
    return merged, out, hn._build_deps_interrupt(merged)


def claims(obj: Any, word: str, tables: set[str]) -> set[str]:
    """Tables that sit in a structure labelled `word` (see ADAPTATION POINTS)."""
    found: set[str] = set()
    neg = re.compile(rf"\b(not|non|no)[_ -]?{word}")

    def labelled(text: str) -> bool:
        t = text.lower()
        return word in t and not neg.search(t)

    def has_word(v: Any) -> bool:
        if isinstance(v, str):
            return labelled(v)
        if isinstance(v, dict):
            return any((labelled(str(k)) and bool(x)) or has_word(x) for k, x in v.items())
        if isinstance(v, (list, tuple, set)):
            return any(has_word(x) for x in v)
        return False

    def walk(node: Any, ctx: bool) -> None:
        if isinstance(node, dict):
            if any(k in tables for k in node):  # {table: role-info}
                for k, v in node.items():
                    if k in tables and (ctx or has_word(v)):
                        found.add(k)
                    walk(v, ctx)
                return
            record = ctx or any(isinstance(v, str) and labelled(v) for v in node.values())
            for k, v in node.items():
                kctx = record or (labelled(str(k)) and bool(v))
                walk(v, kctx)
        elif isinstance(node, (list, tuple, set)):
            for x in node:
                walk(x, ctx)
        elif isinstance(node, str) and ctx and node in tables:
            found.add(node)

    walk(obj, False)
    return found


def roles(ran_, word):
    state, out, payload = ran_
    tables = set(fx.rows(30))
    src = [{k: v for k, v in out.items() if k != "join_graph"}, payload.get("data", {})]
    return set().union(*(claims(s, word, tables) for s in src))


J = {t for t, r in fx.ROLES.items() if r == "J"}
N = {t for t, r in fx.ROLES.items() if r == "N"}
W = {t for t, r in fx.ROLES.items() if r == "W"}
PARENTS = {"student", "course", "building"}


# ── 0. precondition: the planted FKs are what P3 finds (else every role check is vacuous) ──
def test_0_precondition_planted_fks_are_found(ran):
    assert edges_of(ran[1]["join_graph"]) == fx.TRUTH


# ── 1. payload-carrying junction detected (minimal UCC == union of >= 2 FK sets) ──
def test_1_payload_junction_detected(ran):
    got = roles(ran, "junction")
    assert "enrolment" in got, f"payload junction (no surrogate id) not detected; claimed={got}"


def test_1b_junction_with_surrogate_id_detected(ran):
    """JUDGEMENT: with a surrogate `_id` the FK pair is still a minimal unique combination
    (no proper subset is unique), and non-key attributes are 'explicitly irrelevant'."""
    assert "booking" in roles(ran, "junction")


# ── 2. negatives: all-FK / extension / weak tables are not junctions ──
def test_2_non_junctions_rejected_and_detection_not_vacuous(ran):
    got = roles(ran, "junction")
    assert "enrolment" in got, "vacuous: nothing detected at all"
    bad = got & (N | W | PARENTS)
    assert not bad, f"classified as junction although the FK union is not their minimal key: {bad}"


@pytest.mark.parametrize("table", ["touch", "card"])
def test_2b_all_fk_table_is_not_a_junction(ran, table):
    """The deleted rule 'all columns are FKs => edge table' calls both of these a junction."""
    assert "enrolment" in roles(ran, "junction")  # non-vacuous
    assert table not in roles(ran, "junction")


# ── 3. weak entity: proposed at the gate, never silently acted on ──
def test_3_weak_entities_proposed_in_deps_payload(ran):
    _, _, payload = ran
    weak = claims(payload["data"], "weak", set(fx.rows(30)))
    assert weak >= W, f"weak entities missing from the gate payload: {W - weak}"
    assert not (weak & (J | PARENTS)), f"junction/parent flagged weak: {weak & (J | PARENTS)}"


def test_3b_weak_entity_not_silently_acted_on(ran):
    state, out, _ = ran
    assert not (W & roles(ran, "junction")), "weak entity silently classified as junction"
    tree = {k: sorted(v) for k, v in out["dependency_tree"].items()}
    assert tree["room"] == ["building"], tree  # its ordinary FK dependencies are untouched
    assert tree["shift"] == ["building", "student"], tree
    assert set(out["extraction_order"]) >= W  # still extracted, not dropped or rewritten


def test_3c_gate_pauses_and_carries_the_proposal(ran):
    """interrupt_before: only the payload builder runs at the pause; on resume the gate body
    runs only when feedback exists.  No feedback -> pause with the proposal; feedback -> echo."""
    state = ran[0]
    paused = asyncio.run(hn.human_review_deps(dict(state)))
    assert "interrupt_data" in paused and "dependency_tree" not in paused
    assert claims(paused["interrupt_data"]["data"], "weak", set(fx.rows(30))) >= W
    tree = {"room": ["building"]}
    resumed = asyncio.run(
        hn.human_review_deps({**state, "human_feedback": {"dependency_tree": tree}})
    )
    assert resumed["dependency_tree"] == tree


# ── 4. dimension rows: repeated text -> one dimension row + a link, zero new columns ──
_PROVENANCE = ("_source_ids", "_loaded_at", "_source_count")
DEST_SHAPES = {
    "report_shape": dict(
        main="company",
        dim="industry",
        link="industry_id",
        self_fk="parent_industry_id",
        dim_cols=["name", "sub_industry", "industry_group"],
    ),
    "neutral_shape": dict(
        main="account",
        dim="segment",
        link="segment_ref",
        self_fk="parent_segment_ref",
        dim_cols=["name", "note"],
    ),
}
DDL = re.compile(r"alter|add_col|create|drop|ddl|execute|new_col|schema_change", re.I)


class FakeDest:
    def __init__(self, main, dim, link, self_fk, dim_cols):
        self.main, self.dim, self.link, self.self_fk = main, dim, link, self_fk
        prov = list(_PROVENANCE)
        self.cols = {
            main: ["id", "name", link, *prov],
            dim: ["id", *dim_cols, self_fk, *prov],
        }
        self.cols0 = {t: list(c) for t, c in self.cols.items()}
        self.rows: dict[str, dict[str, dict]] = {main: {}, dim: {}}
        self.written_keys: dict[str, set[str]] = {main: set(), dim: set()}
        self.called: list[str] = []

    def __getattr__(self, name):  # unknown method probes are recorded (hasattr stays False)
        if name.startswith("__"):
            raise AttributeError(name)
        self.called.append(name)
        raise AttributeError(name)

    def list_tables(self):
        return list(self.cols)

    def get_foreign_keys(self, table):
        return {self.main: {self.link: self.dim}, self.dim: {self.self_fk: self.dim}}.get(table, {})

    def get_table_schema(self, table):
        return {
            "primary_key": "id",
            "columns": [
                {"name": c, "type": "text", "nullable": c != "id"} for c in self.cols[table]
            ],
        }

    def ensure_table(self, table):
        return False

    def load_batch(self, table_name, records, mapping=None, upsert_key="id"):
        assert table_name in self.cols, f"write to unknown table {table_name}"
        ins = 0
        for rec in records:
            self.written_keys[table_name] |= set(rec)
            ins += rec[upsert_key] not in self.rows[table_name]
            self.rows[table_name].setdefault(rec[upsert_key], {}).update(rec)
        return {"inserted": ins, "updated": len(records) - ins}


def _records(n_rows, texts):
    out = []
    for i in range(n_rows):
        t = texts[i % len(texts)] if i % 7 != 3 else None  # some rows carry no text
        out.append({"_cluster_id": f"cl{i:04d}", "title": f"Name {i}", "dimtext": t})
    return out


def _load(shape, records, dest=None):
    d = dest or FakeDest(**shape)
    mapping = {
        "title": {"dest_column": "name", "dest_table": shape["main"], "confidence": 1.0},
        "dimtext": {"dest_column": "name", "dest_table": shape["dim"], "confidence": 0.9},
    }
    res = asyncio.run(
        tld.load_data_impl(
            shape["main"], records, mapping=mapping, connector=d, table_name=shape["main"]
        )
    )
    return d, res


@pytest.mark.parametrize("shape_id", list(DEST_SHAPES))
@pytest.mark.parametrize(
    ("n_rows", "texts"),
    [
        (20, ["alpha", "beta", "gamma"]),
        (90, [f"t{i}" for i in range(7)]),
        (300, [f"x{i}" for i in range(12)]),
    ],
)
def test_4_repeated_text_becomes_dimension_row_plus_link(shape_id, n_rows, texts):
    shape = DEST_SHAPES[shape_id]
    recs = _records(n_rows, texts)
    d, res = _load(shape, recs)
    assert res["success"]
    dim_rows = list(d.rows[shape["dim"]].values())
    names = [r.get("name") for r in dim_rows]
    expected = sorted({r["dimtext"] for r in recs if r["dimtext"] is not None})
    assert len(expected) >= 3
    assert sorted(names) == expected, "one dimension row per DISTINCT text (not per record)"
    by_name = {r["name"]: r["id"] for r in dim_rows}
    assert len(set(by_name.values())) == len(expected)
    mains = d.rows[shape["main"]]
    assert len(mains) == n_rows
    for rec in recs:
        row = mains[rec["_cluster_id"]]
        if rec["dimtext"] is None:
            assert row.get(shape["link"]) in (None, ""), "link set for a row with no text"
        else:
            assert row.get(shape["link"]) == by_name[rec["dimtext"]], (
                "link must point at the dimension row"
            )
    assert None not in names and "" not in names  # no empty dimension row for the empty text


@pytest.mark.parametrize("shape_id", list(DEST_SHAPES))
def test_4b_dimension_load_adds_no_column_and_no_ddl(shape_id):
    shape = DEST_SHAPES[shape_id]
    d, res = _load(shape, _records(60, ["a1", "b2", "c3", "d4"]))
    assert res["success"]
    assert d.rows[shape["dim"]], "vacuous: nothing was materialised"  # fails on the old code
    for table, keys in d.written_keys.items():
        assert keys <= set(d.cols[table]), (
            f"{table}: wrote undeclared column(s) {keys - set(d.cols[table])}"
        )
    assert d.cols == d.cols0, "destination schema changed"
    assert not [c for c in d.called if DDL.search(c)], f"DDL-ish probe/call: {d.called}"
    assert not res.get("child_tables", {}).get(shape["dim"], {}).get("dropped_fields")


@pytest.mark.parametrize("shape_id", list(DEST_SHAPES))
def test_4c_dimension_load_is_idempotent(shape_id):
    shape = DEST_SHAPES[shape_id]
    recs = _records(40, ["a1", "b2", "c3"])
    d, _ = _load(shape, recs)
    before = {k: dict(v) for k, v in d.rows[shape["dim"]].items()}
    assert before, "vacuous: nothing was materialised"
    _load(shape, recs, dest=d)
    assert d.rows[shape["dim"]].keys() == before.keys(), "re-run duplicated dimension rows"


def test_4d_apply_schema_changes_still_refuses():
    """The hard-refusal backstop is untouched: it records an error and executes nothing."""
    d = FakeDest(**DEST_SHAPES["report_shape"])
    errors: list[str] = []
    asyncio.run(
        nld._apply_schema_changes(
            d, [{"table": "company", "column": "industry"}], errors, connector=d
        )
    )
    assert errors and "Refused" in errors[0]
    assert d.cols == d.cols0 and not d.called


def test_4e_schema_change_gate_defaults_closed():
    p = inspect.signature(mf.map_fields_impl).parameters.get("allow_schema_changes")
    assert p is None or p.default is False


@pytest.mark.parametrize("shape_id", list(DEST_SHAPES))
def test_4f_mapper_routes_text_to_dimension_with_no_schema_change(shape_id):
    s = DEST_SHAPES[shape_id]
    d = FakeDest(**s)
    schema = lambda t: {"table": t, "columns": [{"name": c, "type": "text"} for c in d.cols[t]]}  # noqa: E731
    unified = {
        "name": {"type": "str", "sample_value": "Name 1", "sample_values": ["Name 1", "Name 2"]},
        s["dim"]: {"type": "str", "sample_value": "alpha", "sample_values": ["alpha", "beta"]},
    }
    res = asyncio.run(
        mf.map_fields_impl(s["main"], unified, schema(s["main"]), None, [schema(s["dim"])])
    )
    assert res["schema_changes"] == []
    m = res["field_mapping"].get(s["dim"])
    assert m and m.get("dest_table") == s["dim"], f"text not routed to the dimension table: {res}"
    assert m["dest_column"] != s["link"], "text mapped onto the FK (link) column"
    assert m["dest_column"] in d.cols[s["dim"]]


@pytest.mark.parametrize("shape_id", list(DEST_SHAPES))
def test_4g_mapping_node_keeps_pending_schema_changes_empty(shape_id):
    s = DEST_SHAPES[shape_id]
    d = FakeDest(**s)
    state = {
        "user_request": "x",
        "target_entities": [s["main"]],
        "unified_entities": {
            s["main"]: [
                {"id": "c1", "name": "Name 1", s["dim"]: "alpha"},
                {"id": "c2", "name": "Name 2", s["dim"]: "beta"},
            ]
        },
        "destination": {"type": "postgresql", "tables": {}},
        "field_mappings": {},
        "pending_schema_changes": [],
        "errors": [],
    }
    with patch("src.mcp_server.tools.load_data._get_destination_connector", lambda cfg: d):
        out = asyncio.run(mfn.map_fields(state))
    assert out["pending_schema_changes"] == []
    m = out["field_mappings"][s["main"]].get(s["dim"])
    assert m and m.get("dest_table") == s["dim"], out["field_mappings"]
    assert s["dim"] not in [
        u.get("field") for u in out.get("unmapped_fields", {}).get(s["main"], [])
    ]


# ── 6. section 2.4 grep + no fitted constants, over the owned files ──
def _buckets(path: Path):
    src = path.read_text()
    docs = set()
    for n in ast.walk(ast.parse(src)):
        if (
            isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body
        ):
            e = n.body[0]
            if isinstance(e, ast.Expr) and isinstance(getattr(e, "value", None), ast.Constant):
                docs.add((e.lineno, e.end_lineno))
    res: dict[str, dict[str, int]] = {"code": {}, "doc": {}, "comment": {}}
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT:
            b = "comment"
        elif t.type == tokenize.STRING and any(a <= t.start[0] <= z for a, z in docs):
            b = "doc"
        elif t.type in (tokenize.NAME, tokenize.STRING):
            b = "code"
        else:
            continue
        for k, pat in GREP_PATTERNS.items():
            n = len(re.findall(pat, t.string, re.I))
            if n:
                res[b][k] = res[b].get(k, 0) + n
    return res


def test_6_no_corpus_names_in_owned_files():
    totals: dict[str, dict[str, dict[str, int]]] = {}
    for p in OWNED:
        for b, hits in _buckets(p).items():
            for k, n in hits.items():
                cell = totals.setdefault(p.name, {}).setdefault(b, {})
                cell[k] = cell.get(k, 0) + n
    for name, by_bucket in totals.items():
        assert not by_bucket.get("code"), f"corpus literal in CODE of {name}: {by_bucket['code']}"
        for b in ("comment", "doc"):
            for k, n in by_bucket.get(b, {}).items():
                base = GREP_BASELINE.get(name, {}).get(b, {}).get(k, 0)
                assert n <= base, f"new '{k}' in {b} of {name}: {n} > baseline {base}"


def test_6b_no_fitted_constants_or_name_lists():
    for p in OWNED:
        tree = ast.parse(p.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                v = node.value.value
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    for t in node.targets:
                        assert getattr(t, "id", None) in BASELINE_NUM_CONSTS, (
                            f"new constant {t.id} in {p.name}"
                        )
        lists = sum(
            isinstance(n, (ast.List, ast.Set, ast.Tuple))
            and sum(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts) >= 5
            for n in ast.walk(tree)
        )
        assert lists <= BASELINE_NAME_LISTS.get(p.name, 0), (
            f"new hand-written name list in {p.name}"
        )


def test_6c_junction_test_has_no_name_features():
    """The junction/weak decision is data-driven: no literal like `id`/`ref`/`key`/`x_id` in the
    functions that implement it (found by name; at least one must exist, else nothing is tested)."""
    names = re.compile(r"_?(id|ref|key|link)s?|\w+_(id|ref|key)", re.I)
    seen = 0
    for p in (Path(ps.__file__), Path(ad.__file__)):
        text = p.read_text()
        for fn in ast.walk(ast.parse(text)):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and re.search(
                r"junction|weak|ucc|unique|minimal", fn.name, re.I
            ):
                seen += 1
                lits = [
                    n.value
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                ]
                bad = [x for x in lits if names.fullmatch(x)]
                assert not bad, f"name-based test in {p.name}:{fn.name}: {bad}"
    assert seen, "no junction/weak/UCC function found in plan_scope.py or analyze_dependencies.py"
