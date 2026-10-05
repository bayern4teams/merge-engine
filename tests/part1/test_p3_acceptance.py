"""P3 acceptance (derived from the report's Evidence + Fix text only; no implementation seen).

Report: "Join inference targets the surrogate key".  Fix: value-based IND discovery,
name-agnostic; parameter-free mechanisms only: the cliff (cut the ranked list at its
largest score gap), RHS-uniqueness prefilter, PK-subset-of-PK filter.  NOT the acyclicity
rule (junction tables and self-references must survive).

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p3_acceptance.py -q -p no:cacheprovider

ADAPTATION POINTS (one place each):
  _StubOrigin          in-memory connector; implements sample_distinct_values only (as the
                       existing baseline does).  If the implementation calls another connector
                       method, add it here.
  run_graph / run_node public entries: jg.build_join_graph and ad.analyze_dependencies; the
                       connector factory is patched at jg.create_connector, the LLM at ad.LLMClient.
  edges_of             reads graph._adjacency (same as tests/part1/baseline_schema.py).
  CLIFF_WORDS          weak static check that the cliff exists in the owned files.
  GREP_BASELINE        2.4 ratchet: comment-only hits present on the parent code.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import io
import re
import tokenize
from pathlib import Path

import pytest

from src.mcp_server.tools import join_graph as jg
from tests.part1 import fixtures_schema as fx

ad = importlib.import_module("src.orchestrator.nodes.analyze_dependencies")
JG_SRC, AD_SRC = Path(jg.__file__), Path(ad.__file__)

CLIFF_WORDS = re.compile(r"cliff|largest[_ ]gap|score[_ ]gap|max[_ ]gap", re.I)
# new module-level numeric constants are forbidden; these exist on the parent code
BASELINE_CONSTS = {
    "_DEFAULT_SAMPLE_SIZE",
    "_VERIFIED_THRESHOLD",
    "_PROBABLE_THRESHOLD",
    "_DEFAULT_MAX_HOPS",
    "_HIGH_FANOUT_THRESHOLD",
}
# top-level functions of join_graph.py on the parent code (anything else is new)
BASELINE_FUNCS = {
    "normalize_val",
    "_resolve_primary_key",
    "_singularise_table",
    "_find_candidate_edges",
    "_verify_inclusion",
    "build_join_graph",
    "_hop_from",
    "_bfs_distances",
    "_enumerate_shortest_paths",
    "find_paths",
}
DISCOVERY_FUNCS = {"_find_candidate_edges", "_verify_inclusion", "build_join_graph"}
GREP_PATTERNS = {
    "main_entity_id": r"main_entity_id",
    "company_relation": r"company_relation",
    "staff": r"\bstaff\b",
    "companyid": r"companyid",
    "companyname": r"companyname",
    "companyexperience": r"companyexperience",
    "origin_name": r"testdiffbot",
    "corpus_values": r"\bfrance\b|\bberhad\b|\bSAS\b|\bholdings\b",
}
# comment-only hits on the parent code (ratchet; code/string hits must be 0)
GREP_BASELINE = {
    "join_graph.py": {"companyid": 4, "origin_name": 2},
    "analyze_dependencies.py": {"companyid": 2},
}


# ── harness ──────────────────────────────────────────────────────────────────
class _StubOrigin:
    def __init__(self, rows):
        self.rows = rows

    async def sample_distinct_values(self, table, column, limit=500, filter_values=None):
        vals = list(dict.fromkeys(self.rows[table][column]))
        if filter_values is not None:
            keep = {jg.normalize_val(v) for v in filter_values}
            vals = [v for v in vals if jg.normalize_val(v) in keep]
        return vals[:limit]

    async def close(self):
        return None


class _StubLLM:
    def __init__(self, cfg):
        pass

    async def call_json(self, *a, **k):
        return {}


def schema_of(rows, origin="o1"):
    tables = [
        {"name": t, "columns": [{"name": c, "type": "text"} for c in cols]}
        for t, cols in rows.items()
    ]
    return {origin: {"tables": tables}}, [{"id": origin, "type": "stub", "connection": {}}]


def edges_of(graph):
    return {
        (e.child_table, e.child_column, e.parent_table, e.parent_column)
        for lst in graph._adjacency.values()
        for e in lst
    }


def run_graph(rows, monkeypatch):
    monkeypatch.setattr(jg, "create_connector", lambda *a, **k: _StubOrigin(rows))
    schemas, origins = schema_of(rows)
    return asyncio.run(jg.build_join_graph(schemas, origins))


def run_node(rows, monkeypatch):
    monkeypatch.setattr(jg, "create_connector", lambda *a, **k: _StubOrigin(rows))
    monkeypatch.setattr(ad, "LLMClient", _StubLLM)
    schemas, origins = schema_of(rows)
    ents = {t: [{"table": t, "origin_id": "o1"}] for t in rows}
    return asyncio.run(
        ad.analyze_dependencies(
            {
                "target_entities": list(rows),
                "schemas": schemas,
                "origins": origins,
                "entity_tables": ents,
                "llm_config": {"stub": True},
            }
        )
    )


def norm_tree(tree):
    return {k: sorted(v) for k, v in (tree or {}).items()}


def node_edges(node):
    return {
        (d["child_table"], d["child_column"], d["parent_table"], d["parent_column"])
        for d in node.get("column_dependencies", [])
    }


# ── fixtures (planted truth; neutral names; sizes parametrised so no constant can be tuned) ──
def rows_a(n):
    """The P0-6 shape at size n: business-key parents, a name-mismatched child, and the decoy."""
    codes = [f"K{i:05d}" for i in range(1000, 1000 + n)]
    m = max(n // 3, 5)
    return {
        "org": {
            "_id": list(range(1, n + 1)),
            "org_code": codes,
            "orgtitle": [f"t{i}" for i in range(n)],
        },
        "member": {
            "_id": list(range(1, 2 * n + 1)),
            "owner_ref": [codes[i % (3 * n // 4)] for i in range(2 * n)],
            "rolename": ["r"] * (2 * n),
        },
        "note": {
            "_id": list(range(1, n // 2 + 1)),
            "org_id": [codes[(i * 7) % n] for i in range(n // 2)],
            "body": ["b"] * (n // 2),
        },
        "ticket": {
            "_id": list(range(1, m + 1)),
            "orgid": list(range(1, m + 1)),
            "subject": ["s"] * m,
        },
    }


TRUTH_A = {("member", "owner_ref", "org", "org_code"), ("note", "org_id", "org", "org_code")}
TREE_A = {"org": [], "member": ["org"], "note": ["org"], "ticket": []}
DECOY = ("ticket", "orgid", "org", "_id")


def rows_b(n):
    """Report shape: child.main_entity_id -> parent.business_key; exp.entityid is an orphan (the
    'companyid -> company._id' shape: proposed by name, genuinely in no parent column)."""
    keys = [f"BK{i:05d}" for i in range(n)]
    return {
        "entity": {
            "_id": [f"e{i}" for i in range(n)],
            "business_key": keys,
            "title": [f"t{i}" for i in range(n)],
        },
        "link": {
            "_id": [f"l{i}" for i in range(2 * n)],
            "main_entity_id": [keys[i % n] for i in range(2 * n)],
            "note": [f"n{i % 9}" for i in range(2 * n)],
        },
        "exp": {
            "_id": [f"x{i}" for i in range(n)],
            "entityid": [f"X{i:05d}" for i in range(n)],
            "body": [f"b{i}" for i in range(n)],
        },
    }


TRUTH_B = {("link", "main_entity_id", "entity", "business_key")}
TREE_B = {"entity": [], "link": ["entity"], "exp": []}


def rows_c(n):
    """Junction (payload) + self-reference."""
    pc = [f"P{i:05d}" for i in range(n)]
    gc = [f"G{i:04d}" for i in range(max(n // 5, 4))]
    k = 3 * n
    return {
        "person": {
            "_id": [f"p{i}" for i in range(n)],
            "pcode": pc,
            "manager_ref": [pc[i // 4] for i in range(n)],
        },
        "grp": {"_id": [f"g{i}" for i in range(len(gc))], "gcode": gc},
        "membership": {
            "_id": [f"m{i}" for i in range(k)],
            "person_ref": [pc[i % n] for i in range(k)],
            "group_ref": [gc[i % len(gc)] for i in range(k)],
            "since": [f"d{i % 11}" for i in range(k)],
        },
    }


TRUTH_C = {
    ("membership", "person_ref", "person", "pcode"),
    ("membership", "group_ref", "grp", "gcode"),
    ("person", "manager_ref", "person", "pcode"),
}


def rows_d(n):
    """Non-unique RHS: dim.grp repeats; fact.grp_ref is contained in it (and vice versa)."""
    gv = [f"G{i}" for i in range(7)]
    return {
        "dim": {
            "_id": [f"d{i}" for i in range(n)],
            "dk": [f"DK{i:05d}" for i in range(n)],
            "grp": [gv[i % 7] for i in range(n)],
        },
        "fact": {
            "_id": [f"f{i}" for i in range(2 * n)],
            "dim_ref": [f"DK{i % n:05d}" for i in range(2 * n)],
            "grp_ref": [gv[i % 7] for i in range(2 * n)],
        },
    }


TRUTH_D = {("fact", "dim_ref", "dim", "dk")}

SIZES = [40, 200, 480]


def unique_in(rows, table, col):
    v = rows[table][col]
    return len(set(v)) == len(v)


# ── 1. name-mismatched child is found; whole dependency_tree equals planted truth ──
@pytest.mark.parametrize("n", SIZES)
def test_1_main_entity_id_edge_and_exact_edge_set(n, monkeypatch):
    got = edges_of(run_graph(rows_b(n), monkeypatch))
    assert got == TRUTH_B, f"edges {sorted(got)} != truth {sorted(TRUTH_B)}"


@pytest.mark.parametrize("n", SIZES)
def test_1b_dependency_tree_equals_truth_report_shape(n, monkeypatch):
    node = run_node(rows_b(n), monkeypatch)
    assert norm_tree(node.get("dependency_tree")) == TREE_B
    assert node_edges(node) == TRUTH_B


@pytest.mark.parametrize("n", SIZES)
def test_1c_dependency_tree_equals_truth_decoy_shape(n, monkeypatch):
    node = run_node(rows_a(n), monkeypatch)
    assert norm_tree(node.get("dependency_tree")) == TREE_A, "non-empty is not enough"
    assert node_edges(node) == TRUTH_A


def test_1d_phase0_fixture_exact(monkeypatch):
    rows = fx.p3_rows()
    assert edges_of(run_graph(rows, monkeypatch)) == fx.P3_TRUE_EDGES
    node = run_node(rows, monkeypatch)
    assert norm_tree(node["dependency_tree"]) == norm_tree(fx.P3_TRUE_TREE)


# ── 2. decoys / surrogate-key false edges rejected ──
@pytest.mark.parametrize("n", SIZES)
def test_2_coincidental_overlap_decoy_rejected(n, monkeypatch):
    got = edges_of(run_graph(rows_a(n), monkeypatch))
    assert DECOY not in got
    assert not any(e[3] == "_id" for e in got), "no edge may target the surrogate _id here"


def test_2b_surrogate_false_edge_rejected_orphan_child(monkeypatch):
    got = edges_of(run_graph(rows_b(60), monkeypatch))
    assert ("exp", "entityid", "entity", "_id") not in got
    assert not any(e[0] == "exp" for e in got)


def test_2c_pk_subset_of_pk_not_an_edge(monkeypatch):
    # two surrogate keys, one numerically inside the other: containment alone, no relationship
    rows = {
        "alpha": {"_id": list(range(1, 11)), "v": [f"a{i}" for i in range(10)]},
        "beta": {"_id": list(range(1, 51)), "w": [f"b{i}" for i in range(50)]},
    }
    assert edges_of(run_graph(rows, monkeypatch)) == set()


# ── 3. name-agnostic: renaming columns/tables (and reordering) leaves INDs unchanged ──
def rename(rows, truth, keep_id=True):
    tmap, cmap = {}, {}
    for ti, (t, cols) in enumerate(reversed(list(rows.items()))):  # also reorders tables
        tmap[t] = f"t{ti}"
        for ci, c in enumerate(reversed(list(cols))):  # also reorders columns
            cmap[(t, c)] = c if (keep_id and c == "_id") else f"c{ti}_{ci}"
    new = {
        tmap[t]: {cmap[(t, c)]: v for c, v in reversed(list(cols.items()))}
        for t, cols in reversed(list(rows.items()))
    }
    inv = {(tmap[t], cmap[(t, c)]): (t, c) for (t, c) in cmap}
    mapped_truth = {
        (tmap[ct], cmap[(ct, cc)], tmap[pt], cmap[(pt, pc)]) for ct, cc, pt, pc in truth
    }
    return new, inv, mapped_truth


@pytest.mark.parametrize(
    ("make", "truth"), [(rows_a, TRUTH_A), (rows_b, TRUTH_B), (rows_c, TRUTH_C), (rows_d, TRUTH_D)]
)
def test_3_rename_columns_leaves_inds_unchanged(make, truth, monkeypatch):
    rows = make(120)
    orig = edges_of(run_graph(rows, monkeypatch))
    assert orig == truth, "precondition: original discovers the truth (else equality is vacuous)"
    new, inv, mapped_truth = rename(rows, truth)
    got = edges_of(run_graph(new, monkeypatch))
    assert got == mapped_truth
    assert {(*inv[(ct, cc)], *inv[(pt, pc)]) for ct, cc, pt, pc in got} == {
        (a, b, c, d) for a, b, c, d in orig
    }


# ── 4. junction table and self-reference survive (no acyclicity rule) ──
@pytest.mark.parametrize("n", [40, 200])
def test_4_junction_and_self_reference_survive(n, monkeypatch):
    got = edges_of(run_graph(rows_c(n), monkeypatch))
    assert got == TRUTH_C
    assert ("person", "manager_ref", "person", "pcode") in got  # self-IND kept


def test_4b_junction_in_dependency_tree(monkeypatch):
    node = run_node(rows_c(120), monkeypatch)
    tree = norm_tree(node["dependency_tree"])
    assert tree.get("membership") == ["grp", "person"], tree
    assert {
        ("membership", "person_ref", "person", "pcode"),
        ("membership", "group_ref", "grp", "gcode"),
    } <= node_edges(node)


# ── 5. parameter-free mechanisms ──
def test_5_non_unique_rhs_never_a_target(monkeypatch):
    rows = rows_d(100)
    assert not unique_in(rows, "dim", "grp")
    got = edges_of(run_graph(rows, monkeypatch))
    assert got == TRUTH_D
    assert not any(
        (e[2], e[3]) == ("dim", "grp") or (e[2], e[3]) == ("fact", "grp_ref") for e in got
    )


@pytest.mark.parametrize("make", [rows_a, rows_b, rows_c, rows_d])
def test_5b_every_edge_target_is_unique_in_the_data(make, monkeypatch):
    rows = make(90)
    for _, _, pt, pc in edges_of(run_graph(rows, monkeypatch)):
        assert unique_in(rows, pt, pc), f"{pt}.{pc} is not unique but is an IND target"


def _funcs(path):
    return {
        n.name: n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_5c_no_new_numeric_constants_in_owned_files():
    for path in (JG_SRC, AD_SRC):
        for node in ast.parse(path.read_text()).body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, (int, float))
            ):
                for t in node.targets:
                    if isinstance(t, ast.Name) and path is JG_SRC:
                        assert t.id in BASELINE_CONSTS, f"new numeric constant {t.id}"
                    elif isinstance(t, ast.Name) and path is AD_SRC:
                        raise AssertionError(f"new numeric constant {t.id} in {path.name}")


def test_5c2_no_numeric_literals_in_discovery_code():
    """Literals other than 0/1/2 in the discovery functions or in any NEW helper = a fitted cut."""
    fs = _funcs(JG_SRC)
    scope = {n: f for n, f in fs.items() if n in DISCOVERY_FUNCS or n not in BASELINE_FUNCS}
    bad = []
    for name, f in scope.items():
        rounding = {  # display rounding, e.g. round(x, 4), is not a cut-off
            id(a)
            for c in ast.walk(f)
            if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "round"
            for a in c.args[1:]
        }
        for c in ast.walk(f):
            if id(c) in rounding:
                continue
            if (
                isinstance(c, ast.Constant)
                and isinstance(c.value, (int, float))
                and not isinstance(c.value, bool)
                and c.value not in (0, 1, 2, -1)
            ):
                bad.append((name, c.value))
    assert not bad, f"numeric literals in discovery code (cliff must be parameter-free): {bad}"


def test_5c3_no_overlap_threshold_in_build_join_graph():
    src = ast.get_source_segment(JG_SRC.read_text(), _funcs(JG_SRC)["build_join_graph"])
    assert "_VERIFIED_THRESHOLD" not in src and "_PROBABLE_THRESHOLD" not in src, (
        "a fixed overlap cut-off decides edge acceptance; the cliff replaces it"
    )


def test_5d_no_name_suffix_list_in_discovery():
    strs = set()
    fs = _funcs(JG_SRC)
    for n in DISCOVERY_FUNCS:
        for c in ast.walk(fs[n]):
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                strs.add(c.value)
            if isinstance(c, ast.JoinedStr):
                strs.update(v.value for v in c.values if isinstance(v, ast.Constant))
    hits = {s for s in strs if re.fullmatch(r"_?(id|key|nr)", s)}
    assert not hits, f"hand-written name-suffix list (Rostin F9) in discovery: {hits}"


def test_5e_cliff_present():  # weak: only proves the mechanism is named somewhere
    text = JG_SRC.read_text() + AD_SRC.read_text()
    assert CLIFF_WORDS.search(text), "no cliff/largest-gap mechanism found in owned files"


# ── 7. section 2.4 grep over owned files ──
def _hits(path):
    code, comments = {}, {}
    toks = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    for tok in toks:
        if tok.type in (
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
        ):
            continue
        bucket = comments if tok.type == tokenize.COMMENT else code
        for k, pat in GREP_PATTERNS.items():
            n = len(re.findall(pat, tok.string, re.I))
            if n:
                bucket[k] = bucket.get(k, 0) + n
    return code, comments


@pytest.mark.parametrize("path", [JG_SRC, AD_SRC], ids=lambda p: p.name)
def test_7_no_corpus_literals(path):
    code, comments = _hits(path)
    assert not code, f"corpus literal in code/strings of {path.name}: {code}"
    base = GREP_BASELINE.get(path.name, {})
    for k, n in comments.items():
        assert n <= base.get(k, 0), (
            f"new corpus literal '{k}' in comments of {path.name}: {n} > {base.get(k, 0)}"
        )
