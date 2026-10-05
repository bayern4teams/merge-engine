"""P7 acceptance (derived from the report's Evidence + Fix text only; no P7 code seen).

Report P7: IND discovery (P3) -> cliff -> junction test (P4) -> self-INDs are parent pointers
(hierarchy edges) -> emit typed edges, vocabulary DIRECT_PARENT / ULTIMATE_PARENT / BRANCH_OF.
No table is named in the procedure.  A cyclic hierarchy is REPORTED, not silently broken.
`_relationship_index_for_companies` selects tables by the `company_relation` concept NAME today:
that is the fitted part; it must come from the discovered structure instead.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p7_acceptance.py -q -p no:cacheprovider

CONTRACT (the report fixes semantics, not interfaces; this is the minimal one, see
.handoff/P7.acceptance.md):

    await src.mcp_server.tools.join_graph.derive_relationship_edges(graph, schemas, origins)
        -> {"edges": [edge, ...], "cycles": [cycle, ...]}
    hierarchy edge  {"type": "DIRECT_PARENT"|"ULTIMATE_PARENT", "child": [table, key], "parent": [table, key]}
    junction edge   {"type": "RELATED", "table": junction_table, "a": [table, key], "b": [table, key]}
    cycle           {"table": t, "members": [key, ...]}
    key = the VALUE of the referenced (parent) column, compared after join_graph.normalize_val.

ADAPTATION POINTS (one place each):
  _Origin / run_graph   in-memory connector (extract + sample_distinct_values + profile_column).
  derive()              calls the contract function above.
  index_for()           calls node.resolve_entities._relationship_index_for_companies(state, extracted).
  shared() / rec()      calls tools.resolve_entities._shared_neighbors_beyond_common_parent.
  RELATED               the junction edge type (the report names none).
  GREP_*                2.4 ratchet: hits that exist on the parent code (comment/doc only).
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import io
import re
import signal
import tokenize
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.mcp_server.tools import join_graph as jg
from src.mcp_server.tools import resolve_entities as tre

nre = importlib.import_module("src.orchestrator.nodes.resolve_entities")

RELATED = "RELATED"
HIERARCHY = {"DIRECT_PARENT", "ULTIMATE_PARENT"}
KNOWN_TYPES = HIERARCHY | {RELATED}
SIZES = [30, 120, 400]


# ── harness ──────────────────────────────────────────────────────────────────────────────────
class _Origin:
    def __init__(self, rows):
        self.rows = rows

    async def sample_distinct_values(self, table, column, limit=500, filter_values=None):
        vals = [v for v in dict.fromkeys(self.rows[table][column]) if v is not None]
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
        return []  # nothing declared: keys and pointers must be found in the DATA

    async def discover_statistics(self, table_name):
        return {"row_count_estimate": len(next(iter(self.rows[table_name].values())))}

    def capabilities(self):
        return {
            "supports_declared_pk": False,
            "supports_declared_fk": False,
            "supports_sampling": True,
        }

    async def close(self):
        return None


def schema_of(rows):
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


@contextmanager
def deadline(seconds=60):
    """A cycle that loops forever must FAIL, not hang the suite."""

    def boom(*_):
        raise TimeoutError(f"no result within {seconds}s (infinite loop on a cycle?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def run_graph(rows, monkeypatch):
    monkeypatch.setattr(jg, "create_connector", lambda *a, **k: _Origin(rows))
    monkeypatch.setattr("src.mcp_server.connectors.create_connector", lambda *a, **k: _Origin(rows))
    schemas, origins = schema_of(rows)
    with deadline():
        graph = asyncio.run(jg.build_join_graph(schemas, origins))
    return graph, schemas, origins


def derive(rows, monkeypatch):
    """Contract entry.  FAILS (not skips) while the function does not exist."""
    graph, schemas, origins = run_graph(rows, monkeypatch)
    fn = getattr(jg, "derive_relationship_edges", None)
    if fn is None:
        pytest.fail(
            "contract missing: join_graph.derive_relationship_edges(graph, schemas, origins)"
        )
    with deadline():
        out = asyncio.run(fn(graph, schemas, origins))
    assert isinstance(out, dict) and {"edges", "cycles"} <= set(out), "result keys: edges, cycles"
    return out


def ref(x):
    assert len(x) == 2, f"a node reference is [table, key], got {x!r}"
    return (x[0], jg.normalize_val(x[1]))


def hier(out, kind):
    return {(ref(e["child"]), ref(e["parent"])) for e in out["edges"] if e["type"] == kind}


def related(out):
    return {
        (e["table"], frozenset({ref(e["a"]), ref(e["b"])}))
        for e in out["edges"]
        if e["type"] == RELATED
    }


def N(table, key):
    return (table, jg.normalize_val(key))


# ── fixtures: planted truth, NEUTRAL names (no name pattern would recognise a parent pointer) ──
def tree_pointers(n):
    """Two roots; a ternary forest; plus a 6-long chain (depth > 3 so ultimate != direct)."""
    keys = [f"U{i:05d}" for i in range(n)]
    ptr: list[str | None] = [None, None] + [keys[(i - 2) // 3] for i in range(2, n)]
    chain = [f"Z{i:03d}" for i in range(6)]
    keys += chain
    ptr += [None] + chain[:-1]
    return keys, ptr


def world(n):
    """person: self-reference `above` -> person.pcode; tie: junction person x person;
    membership: junction person x grp; touch: all-FK but pair repeats (NOT a junction);
    `rank`: small ints contained in the surrogate `_id` by coincidence (NOT a pointer)."""
    keys, ptr = tree_pointers(n)
    size = len(keys)
    gk = [f"G{i:04d}" for i in range(max(n // 6, 5))]
    tie = list(
        dict.fromkeys(
            (keys[i], keys[(i * 7 + j * 11 + 1) % size]) for i in range(size) for j in range(2)
        )
    )
    mem = list(
        dict.fromkeys((keys[i], gk[(i + j) % len(gk)]) for i in range(size) for j in range(2))
    )
    touch = [(keys[i], gk[i % len(gk)]) for i in range(0, size, 3) for _ in range(2)]
    return {
        "person": {
            "_id": list(range(1, size + 1)),
            "pcode": keys,
            "above": ptr,
            "rank": [1 + i % 5 for i in range(size)],
        },
        "grp": {"_id": [f"g{i}" for i in range(len(gk))], "gcode": gk},
        "tie": {
            "left_ref": [a for a, _ in tie],
            "right_ref": [b for _, b in tie],
            "kind": [f"k{i % 3}" for i in range(len(tie))],
        },
        "membership": {
            "person_ref": [a for a, _ in mem],
            "group_ref": [b for _, b in mem],
            "since": [f"d{i % 11}" for i in range(len(mem))],
        },
        "touch": {"person_ref": [a for a, _ in touch], "group_ref": [b for _, b in touch]},
    }


def oracle(keys, ptr):
    """The rule, restated: DIRECT = the pointer; ULTIMATE = last node of the pointer chain (root)."""
    up = {k: p for k, p in zip(keys, ptr) if p is not None}
    direct, root = set(), {}
    for k in keys:
        if k in up:
            direct.add((k, up[k]))
        cur, seen = k, set()
        while cur in up and cur not in seen:
            seen.add(cur)
            cur = up[cur]
        root[k] = cur
    return up, direct, root


def planted(n):
    rows = world(n)
    return rows, oracle(rows["person"]["pcode"], rows["person"]["above"])


def direct_nodes(direct):
    return {(N("person", c), N("person", p)) for c, p in direct}


# ── 0. precondition: P3 finds exactly the planted INDs, so a P7 failure is not blamed on P3 ──
TRUTH = {
    ("person", "above", "person", "pcode"),
    ("tie", "left_ref", "person", "pcode"),
    ("tie", "right_ref", "person", "pcode"),
    ("membership", "person_ref", "person", "pcode"),
    ("membership", "group_ref", "grp", "gcode"),
    ("touch", "person_ref", "person", "pcode"),
    ("touch", "group_ref", "grp", "gcode"),
}


@pytest.mark.parametrize("n", SIZES)
def test_0_p3_finds_planted_inds_including_self_ind(n, monkeypatch):
    graph, _, _ = run_graph(world(n), monkeypatch)
    got = edges_of(graph)
    assert got == TRUTH, f"missing {sorted(TRUTH - got)} extra {sorted(got - TRUTH)}"


# ── 1. self-reference -> DIRECT_PARENT; ULTIMATE_PARENT = root of the chain ──
@pytest.mark.parametrize("n", SIZES)
def test_1_direct_parent_edges_exact(n, monkeypatch):
    rows, (up, direct, root) = planted(n)
    got = hier(derive(rows, monkeypatch), "DIRECT_PARENT")
    want = direct_nodes(direct)
    assert got == want, f"missing {len(want - got)} extra {len(got - want)}"


@pytest.mark.parametrize("n", SIZES)
def test_1b_direction_child_points_to_parent(n, monkeypatch):
    got = hier(derive(planted(n)[0], monkeypatch), "DIRECT_PARENT")
    # row U00002 holds the pointer to U00000: child is the row HOLDING the pointer
    assert (N("person", "U00002"), N("person", "U00000")) in got
    assert (N("person", "U00000"), N("person", "U00002")) not in got


@pytest.mark.parametrize("n", SIZES)
def test_1c_ultimate_parent_is_the_chain_root(n, monkeypatch):
    rows, (up, direct, root) = planted(n)
    got = hier(derive(rows, monkeypatch), "ULTIMATE_PARENT")
    deep = {k for k in up if up[k] in up}  # depth >= 2: ultimate != direct parent
    must = {(N("person", k), N("person", root[k])) for k in deep}
    assert must <= got, f"depth>=2 nodes lacking their root: {len(must - got)}"
    roots = {N("person", k) for k in rows["person"]["pcode"] if k not in up}
    assert {p for _, p in got} <= roots, "ULTIMATE_PARENT must be a root, never an intermediate"
    allowed = {(N("person", k), N("person", root[k])) for k in up}
    assert got <= allowed, "ULTIMATE_PARENT goes from a non-root to ITS root only"
    # the 6-chain: Z005's direct parent is Z004, its ultimate parent is Z000
    assert (N("person", "Z005"), N("person", "Z000")) in got
    assert (N("person", "Z005"), N("person", "Z004")) not in got


@pytest.mark.parametrize("n", SIZES)
def test_1d_roots_have_no_parent_edges_and_no_cycle_is_invented(n, monkeypatch):
    rows, (up, direct, root) = planted(n)
    out = derive(rows, monkeypatch)
    roots = {N("person", k) for k in rows["person"]["pcode"] if k not in up}
    children = {c for e in out["edges"] if e["type"] in HIERARCHY for c in [ref(e["child"])]}
    assert not (roots & children)
    assert out["cycles"] == [], "an acyclic hierarchy reports no cycle"


@pytest.mark.parametrize("n", [30, 120])
def test_1e_row_order_and_names_do_not_matter(n, monkeypatch):
    rows, (up, direct, root) = planted(n)
    tmap, new = {}, {}
    for ti, (t, cols) in enumerate(rows.items()):
        tmap[t] = f"t{ti}"
        new[tmap[t]] = {
            (c if c == "_id" else f"c{ti}_{ci}"): list(reversed(v))
            for ci, (c, v) in enumerate(cols.items())
        }
    out = derive(new, monkeypatch)
    pt = tmap["person"]
    want = {(N(pt, c), N(pt, p)) for c, p in direct}
    assert hier(out, "DIRECT_PARENT") == want


# ── 2. junction -> relationship edges between the two referenced entities ──
@pytest.mark.parametrize("n", SIZES)
def test_2_junction_edges_between_referenced_rows(n, monkeypatch):
    rows = world(n)
    got = related(derive(rows, monkeypatch))
    for tname, ca, cb, ta, tb in (
        ("membership", "person_ref", "group_ref", "person", "grp"),
        ("tie", "left_ref", "right_ref", "person", "person"),
    ):
        want = {
            (tname, frozenset({N(ta, a), N(tb, b)}))
            for a, b in zip(rows[tname][ca], rows[tname][cb])
        }
        have = {g for g in got if g[0] == tname}
        assert have == want, f"{tname}: missing {len(want - have)} extra {len(have - want)}"


@pytest.mark.parametrize("n", SIZES)
def test_2b_non_junction_all_fk_table_emits_no_relationship(n, monkeypatch):
    """`touch`: every column is an FK but the pair repeats, so no minimal key is the FK union."""
    got = related(derive(world(n), monkeypatch))
    assert not [g for g in got if g[0] == "touch"]


@pytest.mark.parametrize("n", SIZES)
def test_2c_junction_is_never_a_hierarchy_edge(n, monkeypatch):
    """No direction is observable in a junction, so it never yields a parent edge; the
    person x person junction `tie` leaves the DIRECT_PARENT set exactly the self-reference."""
    rows, (up, direct, root) = planted(n)
    out = derive(rows, monkeypatch)
    assert hier(out, "DIRECT_PARENT") == direct_nodes(direct)
    for e in out["edges"]:
        if e["type"] in HIERARCHY:
            assert e["child"][0] == e["parent"][0] == "person", e


# ── 3. cycles are reported, not silently broken, and terminate ──
def cyc_world(n):
    keys, ptr = tree_pointers(n)
    keys += ["C1", "C2", "C3", "E1", "E2", "D1"]  # ring of 3, ring of 2, a row hanging below a ring
    ptr += ["C2", "C3", "C1", "E2", "E1", "C1"]
    return {
        "person": {
            "_id": list(range(1, len(keys) + 1)),
            "pcode": keys,
            "above": ptr,
            "label": [f"l{i}" for i in range(len(keys))],
        }
    }


@pytest.mark.parametrize("n", [30, 120])
def test_3_cycles_reported(n, monkeypatch):
    out = derive(cyc_world(n), monkeypatch)
    members = sorted(sorted(jg.normalize_val(m) for m in c["members"]) for c in out["cycles"])
    assert members == [["c1", "c2", "c3"], ["e1", "e2"]], members
    assert all(c["table"] == "person" for c in out["cycles"])


@pytest.mark.parametrize("n", [30, 120])
def test_3b_cycle_edges_not_dropped_and_no_fake_root(n, monkeypatch):
    rows = cyc_world(n)
    out = derive(rows, monkeypatch)
    keys, ptr = rows["person"]["pcode"], rows["person"]["above"]
    want = {(N("person", k), N("person", p)) for k, p in zip(keys, ptr) if p is not None}
    assert hier(out, "DIRECT_PARENT") == want, "every pointer stays an edge, cycle members included"
    bad = {"c1", "c2", "c3", "e1", "e2", "d1"}
    ult = hier(out, "ULTIMATE_PARENT")
    assert not [u for u in ult if u[0][1] in bad or u[1][1] in bad], (
        "a chain that ends in a cycle has no root: no ULTIMATE_PARENT may be invented for it"
    )
    assert (N("person", "Z005"), N("person", "Z000")) in ult, "the acyclic part is unaffected"


def test_3c_self_pointing_row_terminates(monkeypatch):
    rows = cyc_world(30)
    rows["person"]["above"][2] = rows["person"]["pcode"][2]  # a row that is its own parent
    out = derive(rows, monkeypatch)  # must return (deadline) and not raise
    assert isinstance(out["cycles"], list)


# ── 4. negative cases that fail on the parent code: names no longer decide ──
def fk_roles(table, cols, parent="person", key="pcode", origin="o1"):
    return {
        "junction": [
            {
                "table": table,
                "origin": origin,
                "fk_columns": list(cols),
                "references": [f"{c} -> {parent}.{key}" for c in cols],
                "complete": True,
            }
        ],
        "weak": [],
    }


def rel_rows(table, cols, pairs, origin="o1"):
    return [
        {cols[0]: a, cols[1]: b, "_origin_id": origin, "_origin_table": table} for a, b in pairs
    ]


PAIRS = [("A1", "B1"), ("A1", "B2"), ("B1", "B2"), ("C9", "A1")]
WANT_INDEX = {"a1": {"b1", "b2", "c9"}, "b1": {"a1", "b2"}, "b2": {"a1", "b1"}, "c9": {"a1"}}


def index_for(state, extracted):
    state = {**state, "extracted_data": extracted}
    with deadline():
        out = nre._relationship_index_for_companies(state, extracted)
    return {k: set(v) for k, v in out.items()}


@pytest.mark.parametrize("tname", ["zz_link", "q7", "relation_of_things"])
def test_4_neutral_table_name_still_feeds_the_index(tname):
    """`company_relation` is not special: any discovered junction whose two FK columns
    reference the same table feeds the symmetric index, under any table/column names."""
    cols = ("lk_a", "lk_b")
    got = index_for({"table_roles": fk_roles(tname, cols)}, {tname: rel_rows(tname, cols, PAIRS)})
    assert got == WANT_INDEX


def test_4b_the_name_company_relation_without_structure_does_not_feed_it():
    """Old code selected tables by concept NAME: a table called company_relation that the
    discovered structure does not mark as a junction contributes nothing."""
    cols = ("companyid", "relatedcompanyid")
    extracted = {"company_relation": rel_rows("company_relation", cols, PAIRS)}
    assert index_for({"table_roles": {"junction": [], "weak": []}}, extracted) == {}
    assert index_for({}, extracted) == {}


def test_4c_a_name_matched_table_next_to_a_neutral_junction_adds_nothing():
    cols = ("lk_a", "lk_b")
    extracted = {
        "zz_link": rel_rows("zz_link", cols, PAIRS),
        "company_relation": rel_rows(
            "company_relation", ("companyid", "relatedcompanyid"), [("X1", "X2"), ("X2", "X3")]
        ),
    }
    assert index_for({"table_roles": fk_roles("zz_link", cols)}, extracted) == WANT_INDEX


def test_4d_junction_between_two_different_entities_is_not_a_same_type_index():
    """JUDGEMENT (see md): the index is `id -> other ids of the same kind`.  A junction whose FKs
    reference two DIFFERENT tables relates two kinds of thing; pooling their ids would let a
    person id 'relate' to a group id."""
    cols = ("lk_a", "lk_b")
    roles = fk_roles("zz_link", cols)
    roles["junction"][0]["references"] = ["lk_a -> person.pcode", "lk_b -> grp.gcode"]
    extracted = {"zz_link": rel_rows("zz_link", cols, PAIRS)}
    assert index_for({"table_roles": roles}, extracted) == {}
    # non-vacuous: the same rows DO feed the index when both FKs reference one table (test 4)
    assert index_for({"table_roles": fk_roles("zz_link", cols)}, extracted) == WANT_INDEX


def test_4e_null_and_blank_endpoints_skipped_empty_input_is_empty():
    cols = ("lk_a", "lk_b")
    roles = {"table_roles": fk_roles("zz_link", cols)}
    rows = rel_rows("zz_link", cols, [("A1", None), (None, "B1"), ("", "B2"), ("A1", "B1")])
    assert index_for(roles, {"zz_link": rows}) == {"a1": {"b1"}, "b1": {"a1"}}
    assert index_for(roles, {"zz_link": []}) == {}


def rec(code):
    return {"code": code, "nm": code, "_origin_id": "o1", "_origin_table": "ent"}


def shared(index, r1, r2):
    return tre._shared_neighbors_beyond_common_parent(index, r1, r2)


def test_4f_shared_neighbours_work_with_a_neutral_identifier_column():
    """`_company_native_id` finds the id by the literal column name `companyid` today."""
    idx = {"p": {"n1", "n2", "n3"}, "q": {"n1", "n2"}, "n1": {"p", "q"}, "n2": {"p", "q"}}
    assert shared(idx, rec("p"), rec("q")) == {"n1", "n2"}


def test_4g_common_parent_guard_is_kept_one_shared_neighbour_is_not_evidence():
    idx = {"p": {"par"}, "q": {"par"}, "par": {"p", "q"}}
    assert shared(idx, rec("p"), rec("q")) == set()


# ── 5. coincidental containment is not a parent pointer ──
@pytest.mark.parametrize("n", SIZES)
def test_5_non_fk_column_with_coincidental_containment_gives_no_edge(n, monkeypatch):
    """person.rank (1..5) is contained in person._id (1..N) and is not a pointer."""
    rows = world(n)
    out = derive(rows, monkeypatch)
    keys = {jg.normalize_val(k) for k in rows["person"]["pcode"]}
    direct = [e for e in out["edges"] if e["type"] == "DIRECT_PARENT"]
    assert all(jg.normalize_val(e["parent"][1]) in keys for e in direct), "a parent is a KEY value"
    assert len(direct) == len([p for p in rows["person"]["above"] if p is not None])


def test_5b_tables_without_any_self_ind_have_no_hierarchy_edges(monkeypatch):
    rows = {
        "alpha": {"pk": [f"A{i:04d}" for i in range(60)], "val": [f"v{i % 9}" for i in range(60)]},
        "beta": {"pk": [f"B{i:04d}" for i in range(40)], "val": [f"w{i % 7}" for i in range(40)]},
    }
    assert derive(rows, monkeypatch) == {"edges": [], "cycles": []}


# ── BRANCH_OF: the report gives no measurable structural rule; it must not be invented ──
@pytest.mark.parametrize("n", [30, 120])
def test_6_branch_of_never_derived_from_structure_alone(n, monkeypatch):
    """A branch and a subsidiary have the same shape (a pointer into the same key); telling them
    apart needs declared data (REL_POINTER_ROLE [S]), which a fixture of values does not carry."""
    for rows in (world(n), cyc_world(n)):
        types = {e["type"] for e in derive(rows, monkeypatch)["edges"]}
        assert "BRANCH_OF" not in types
        assert types <= KNOWN_TYPES, f"unknown edge type(s): {types - KNOWN_TYPES}"


# ── 7. plan 2.4: no fitted constants ──
SRC = Path(jg.__file__)
TRE_SRC, NRE_SRC = Path(tre.__file__), Path(nre.__file__)
GREP = re.compile(
    r"company_?relation|companyid|relatedcompanyid|relationtype|main_entity_id|testdiffbot"
    r"|companyexperience|companyname|\bstaff\b|\bfrance\b|\bberhad\b|\bholdings\b",
    re.I,
)
GREP_JG_BASELINE = {"comment": 4, "doc": 0}  # prose hits on the parent code; code hits must be 0
BASELINE_NUM_CONSTS = {"_DEFAULT_SAMPLE_SIZE", "_DEFAULT_MAX_HOPS", "_HIGH_FANOUT_THRESHOLD"}
BASELINE_JG_FUNCS = {
    "normalize_val", "_resolve_primary_key", "_singularise_table", "_make_profile", "_binomial_cdf",
    "_may_include", "_chance", "_profile_columns", "_find_candidate_edges", "_verify_inclusion",
    "_cut_at_cliff", "_index_origins", "build_join_graph", "_hop_from", "_bfs_distances",
    "_enumerate_shortest_paths", "find_paths",
}  # fmt: skip
STRUCTURAL = {0, 1, 2}  # empty / single / pair: a corpus count is not what produces these


def split_code(src: str) -> tuple[str, str, str]:
    """(code with comments and docstrings blanked, comment text, docstring text)."""
    tree = ast.parse(src)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            b = node.body
            first = b[0] if b else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                spans.append((first.lineno, first.end_lineno))
    lines = src.splitlines()
    docs = []
    for lo, hi in spans:
        docs.append("\n".join(lines[lo - 1 : hi]))
        for i in range(lo - 1, hi):
            lines[i] = ""
    comments, code = [], []
    skip = (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)
    for t in tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline):
        if t.type == tokenize.COMMENT:
            comments.append(t.string)
        elif t.type not in skip:
            code.append(t.string)
    return " ".join(code), "\n".join(comments), "\n".join(docs)


def func_source(path: Path, name: str) -> str | None:
    src = path.read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(src.splitlines()[node.lineno - 1 : node.end_lineno])
    return None


def test_7_grep_join_graph_no_corpus_literal_in_code():
    code, comments, docs = split_code(SRC.read_text())
    assert not GREP.findall(code), f"corpus literal in code: {sorted(set(GREP.findall(code)))}"
    assert len(GREP.findall(comments)) <= GREP_JG_BASELINE["comment"]
    assert len(GREP.findall(docs)) <= GREP_JG_BASELINE["doc"]


@pytest.mark.parametrize(
    "path,name",
    [
        (TRE_SRC, "build_relationship_index"),
        (TRE_SRC, "_company_native_id"),
        (TRE_SRC, "_shared_neighbors_beyond_common_parent"),
        (NRE_SRC, "_relationship_index_for_companies"),
    ],
)
def test_7b_grep_relationship_functions_name_no_origin_table_or_column(path, name):
    """FAILS on the parent code: these functions hold the literals `companyid`,
    `relatedcompanyid`, `company_relation`.  (Docstrings/comments are prose, not logic.)"""
    src = func_source(path, name)
    if src is None:
        return  # removed/replaced by a name-free mechanism: nothing left to name a table
    code, _, _ = split_code(src)
    assert not GREP.findall(code), f"{name} names the corpus: {sorted(set(GREP.findall(code)))}"


def test_7c_no_new_module_level_numeric_constant_in_join_graph():
    tree = ast.parse(SRC.read_text())
    consts = {
        n.targets[0].id
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, (int, float))
    }
    assert consts <= BASELINE_NUM_CONSTS, f"new numeric constants: {consts - BASELINE_NUM_CONSTS}"


def test_7d_new_derivation_code_has_only_structural_literals():
    """Every function P7 adds to join_graph.py: a numeric literal other than 0/1/2, or a list of
    >=3 strings, is a corpus-shaped choice."""
    tree = ast.parse(SRC.read_text())
    new = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name not in BASELINE_JG_FUNCS
    ]
    assert any(n.name == "derive_relationship_edges" for n in new), (
        "derive_relationship_edges missing"
    )
    bad = []
    for fn in new:
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
                and node.value not in STRUCTURAL
            ):
                bad.append((fn.name, node.value))
            if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
                strs = [
                    e for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                if len(strs) >= 3:
                    bad.append((fn.name, "string list"))
    assert not bad, f"non-structural literals in new code: {bad}"


def test_7e_hierarchy_vocabulary_is_the_reports_not_invented():
    src = SRC.read_text()
    assert "DIRECT_PARENT" in src and "ULTIMATE_PARENT" in src, "typed vocabulary missing"
    assert not re.search(r"\b(PARENT_OF|SUBSIDIARY_OF|IS_CHILD_OF)\b", src)
