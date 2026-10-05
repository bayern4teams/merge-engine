"""P4 attack probes (adversarial; written after the implementation, independent of the worker).

Each test states the property a correct P4 must have.  A failing test is a finding, not a bug in
the test; the verdict file says which findings are blocking.
"""

from __future__ import annotations

import asyncio
import copy
import random
import re
import string
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.mcp_server.tools import join_graph as jg
from src.mcp_server.tools import load_data as tld
from src.mcp_server.tools import map_fields as mf
from src.mcp_server.tools import plan_scope as ps
from tests.part1 import p4_fixtures as fx
from tests.part1.test_p4_acceptance import (
    DEST_SHAPES,
    FakeDest,
    _Origin,
    _schema_of,
)

ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────── classifier harness ─────────────────────────
class _Origin2(_Origin):
    def __init__(self, rows, total_count="auto"):
        super().__init__(rows)
        self.tc = total_count

    async def extract(self, table_name, columns, **kw):
        page = await super().extract(table_name, columns, **kw)
        if self.tc != "auto":
            page["total_count"] = self.tc
        return page


def classify(rows, edges, sample_size=1000, total_count="auto"):
    """rows {table: {col: [..]}}; edges [(child, col, parent, pcol)] -> roles dict."""
    g = jg.JoinGraph()
    for c, cc, p, pc in edges:
        g.add_edge(jg.JoinEdge("o1", c, cc, "o1", p, pc, "verified", 1.0, "t"))
    schemas, origins = _schema_of(rows)
    with patch(
        "src.mcp_server.connectors.create_connector", lambda *a, **k: _Origin2(rows, total_count)
    ):
        return asyncio.run(ps.classify_junction_and_weak_tables(g, schemas, origins, sample_size))


def names(roles, kind):
    return {e["table"] for e in roles[kind]}


def _parents(n=40):
    return {
        "pa": {"code": [f"A{i}" for i in range(n)], "label": [f"la{i}" for i in range(n)]},
        "pb": {"code": [f"B{i}" for i in range(n)], "label": [f"lb{i}" for i in range(n)]},
        "pc": {"code": [f"C{i}" for i in range(n)], "label": [f"lc{i}" for i in range(n)]},
    }


def _pairs(n=40):
    return [(f"A{i}", f"B{(i + j) % n}") for i in range(n) for j in range(2)]


# ── (c) classifier ──
def test_c0_sanity_junction_and_weak_found():
    rows = _parents()
    p = _pairs()
    rows["jx"] = {"a_ref": [x[0] for x in p], "b_ref": [x[1] for x in p], "pay": ["x"] * len(p)}
    r = classify(rows, [("jx", "a_ref", "pa", "code"), ("jx", "b_ref", "pb", "code")])
    assert names(r, "junction") == {"jx"}


def test_c1_surrogate_with_non_unique_fk_pair_is_not_a_junction():
    rows = _parents()
    p = _pairs() + _pairs()  # the FK pair repeats; only the surrogate is unique
    rows["jx"] = {
        "_id": [f"r{i}" for i in range(len(p))],
        "a_ref": [x[0] for x in p],
        "b_ref": [x[1] for x in p],
        "note": [f"n{i % 3}" for i in range(len(p))],
    }
    r = classify(rows, [("jx", "a_ref", "pa", "code"), ("jx", "b_ref", "pb", "code")])
    assert "jx" not in names(r, "junction")


def test_c2_all_fk_duplicates_not_junction():
    rows = _parents()
    p = _pairs() * 2
    rows["jx"] = {"a_ref": [x[0] for x in p], "b_ref": [x[1] for x in p]}
    r = classify(rows, [("jx", "a_ref", "pa", "code"), ("jx", "b_ref", "pb", "code")])
    assert "jx" not in names(r, "junction")


def test_c3_sample_smaller_than_table_sets_complete_false_and_duplicates_inside_sample_reject():
    rows = _parents(200)
    p = _pairs(200)  # 400 unique rows
    rows["jx"] = {"a_ref": [x[0] for x in p], "b_ref": [x[1] for x in p]}
    e = [("jx", "a_ref", "pa", "code"), ("jx", "b_ref", "pb", "code")]
    r = classify(rows, e, sample_size=50)
    assert names(r, "junction") == {"jx"}
    assert r["junction"][0]["complete"] is False, "truncated sample must not claim completeness"
    r = classify(rows, e, sample_size=1000)
    assert r["junction"][0]["complete"] is True
    # duplicate pair planted AFTER the sample window: only a full read can see it
    rows["jx"]["a_ref"][-1], rows["jx"]["b_ref"][-1] = (
        rows["jx"]["a_ref"][0],
        rows["jx"]["b_ref"][0],
    )
    r = classify(rows, e, sample_size=50)
    assert r["junction"] and r["junction"][0]["complete"] is False  # suggestive, flagged
    r = classify(rows, e, sample_size=1000)
    assert "jx" not in names(r, "junction")


@pytest.mark.xfail(
    strict=True,
    reason="F1 classifier TypeError when extract() returns total_count=None; call is unwrapped in analyze_dependencies",
)
def test_c3b_connector_without_total_count_does_not_crash_or_claim_complete():
    rows = _parents()
    p = _pairs()
    rows["jx"] = {"a_ref": [x[0] for x in p], "b_ref": [x[1] for x in p]}
    e = [("jx", "a_ref", "pa", "code"), ("jx", "b_ref", "pb", "code")]
    r = classify(rows, e, sample_size=10, total_count=None)  # keyset-style None
    if r["junction"]:
        assert r["junction"][0]["complete"] is not True


@pytest.mark.xfail(
    strict=True,
    reason="F7 one NULL FK value in 80 rows erases the table from every role (no proposal, no flag)",
)
def test_c4_null_in_fk_column_one_dirty_row_does_not_erase_the_junction_silently():
    rows = _parents()
    p = _pairs()
    rows["jx"] = {"a_ref": [x[0] for x in p], "b_ref": [x[1] for x in p], "pay": ["x"] * len(p)}
    rows["jx"]["b_ref"][5] = None  # one dirty row among 80
    e = [("jx", "a_ref", "pa", "code"), ("jx", "b_ref", "pb", "code")]
    r = classify(rows, e)
    assert "jx" in names(r, "junction") | names(r, "weak"), (
        "one NULL FK value among 80 rows makes the table disappear from every role (no proposal)"
    )


def test_c5_three_fks_two_form_the_key():
    rows = _parents()
    p = _pairs()
    rows["jx"] = {
        "a_ref": [x[0] for x in p],
        "b_ref": [x[1] for x in p],
        "c_ref": ["C0"] * len(p),
    }
    e = [
        ("jx", "a_ref", "pa", "code"),
        ("jx", "b_ref", "pb", "code"),
        ("jx", "c_ref", "pc", "code"),
    ]
    r = classify(rows, e)
    assert names(r, "junction") == {"jx"}
    assert sorted(r["junction"][0]["fk_columns"]) == ["a_ref", "b_ref"], r["junction"]


def test_c5b_three_fks_key_order_independent():
    """Same data, FK-bearing columns permuted: the SAME key must be proposed."""
    base = _parents()
    p = _pairs()
    cols = {
        "c_ref": ["C0"] * len(p),
        "a_ref": [x[0] for x in p],
        "b_ref": [x[1] for x in p],
    }
    e = [
        ("jx", "a_ref", "pa", "code"),
        ("jx", "b_ref", "pb", "code"),
        ("jx", "c_ref", "pc", "code"),
    ]
    seen = set()
    for order in (
        ["c_ref", "a_ref", "b_ref"],
        ["a_ref", "b_ref", "c_ref"],
        ["b_ref", "c_ref", "a_ref"],
    ):
        rows = copy.deepcopy(base)
        rows["jx"] = {k: cols[k] for k in order}
        r = classify(rows, e)
        seen.add(tuple(sorted(r["junction"][0]["fk_columns"])) if r["junction"] else None)
    assert len(seen) == 1, f"role depends on column order: {seen}"


@pytest.mark.xfail(
    strict=True,
    reason="F6 role depends on schema column order when several minimal keys exist (declared ceiling)",
)
def test_c5c_two_overlapping_minimal_keys_order_dependence_is_visible():
    """(a,b) and (b,c) are both minimal unique; the report says 'union of FK sets'. Whatever is
    proposed must still be a junction in every column order (only the key choice may vary)."""
    p = _pairs()
    n = len(p)
    cols = {
        "a_ref": [x[0] for x in p],
        "b_ref": [x[1] for x in p],
        "c_ref": [f"C{i}" for i in range(n)],
    }
    e = [
        ("jx", "a_ref", "pa", "code"),
        ("jx", "b_ref", "pb", "code"),
        ("jx", "c_ref", "pc", "code"),
    ]
    base = _parents(n)
    for order in (["a_ref", "b_ref", "c_ref"], ["c_ref", "b_ref", "a_ref"]):
        rows = copy.deepcopy(base)
        rows["jx"] = {k: cols[k] for k in order}
        r = classify(rows, e)
        # c_ref alone is unique -> single-FK key -> no junction (a 1:1 extension); any answer is
        # fine as long as it is stable.
        assert names(r, "junction") == set(), r["junction"]


def _rename(rows, edges, seed):
    rnd = random.Random(seed)
    tmap, cmap = {}, {}
    rn = lambda: "".join(rnd.choice(string.ascii_lowercase) for _ in range(9))  # noqa: E731
    for t, cols in rows.items():
        tmap[t] = rn()
        for c in cols:
            cmap[(t, c)] = rn()
    nrows = {tmap[t]: {cmap[(t, c)]: v for c, v in cols.items()} for t, cols in rows.items()}
    nedges = [(tmap[c], cmap[(c, cc)], tmap[p], cmap[(p, pc)]) for c, cc, p, pc in edges]
    return nrows, nedges, tmap


def _fx_edges():
    return [(c, cc, p, pc) for c, cc, p, pc in fx.TRUTH]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_c6_name_agnostic_random_names_same_roles(seed):
    rows = fx.rows(60)
    base = classify(rows, _fx_edges())
    nrows, nedges, tmap = _rename(rows, _fx_edges(), seed)
    got = classify(nrows, nedges)
    for kind in ("junction", "weak"):
        assert {tmap[t] for t in names(base, kind)} == names(got, kind), kind
    assert names(base, "junction") >= {"enrolment", "booking"}  # non-vacuous


@pytest.mark.xfail(
    strict=True,
    reason="F2 entity with its own unique column + one FK + varied payload is proposed as weak",
)
def test_c7_entity_with_own_surrogate_key_and_one_fk_is_not_weak():
    """address(_id unique, person_ref repeats, street): it HAS its own key, so it is not a weak
    entity.  (A weak entity's identity comes only from owner FK + local discriminator.)"""
    rows = _parents()
    n = 120
    rows["addr"] = {
        "_id": [f"ad{i}" for i in range(n)],
        "a_ref": [f"A{i % 40}" for i in range(n)],
        "street": [f"s{i % 7}" for i in range(n)],
    }
    r = classify(rows, [("addr", "a_ref", "pa", "code")])
    assert "addr" not in names(r, "weak"), r["weak"]


@pytest.mark.xfail(strict=True, reason="F2 same, table that also has an FK")
def test_c7b_parent_table_that_also_has_an_fk_is_not_weak_when_it_has_its_own_key():
    rows = _parents()
    rows["pa"]["b_ref"] = [f"B{i % 10}" for i in range(40)]
    rows["pa"]["label"] = [f"la{i % 7}" for i in range(40)]
    r = classify(rows, [("pa", "b_ref", "pb", "code")])
    assert "pa" not in names(r, "weak"), r["weak"]


def test_c7c_weak_noise_rate_on_fixture_is_only_the_planted_weak():
    rows = fx.rows(60)
    r = classify(rows, _fx_edges())
    assert names(r, "weak") == {"room", "shift"}, names(r, "weak")


def test_c8_empty_and_unreadable_tables_are_skipped_not_crashed():
    rows = _parents()
    rows["jx"] = {"a_ref": [], "b_ref": []}
    # extract() on an empty table: _Origin2 indexes next(iter(cols.values())) -> []; fine
    r = classify(rows, [("jx", "a_ref", "pa", "code"), ("jx", "b_ref", "pb", "code")])
    assert r == {"junction": [], "weak": []}


# ───────────────────────── (a) Phase 1b hijack ─────────────────────────
def _cols(*names_, types=None):
    types = types or {}
    return [{"name": n, "type": types.get(n, "text")} for n in names_]


def _map(unified, primary_cols, fams, entity="company"):
    return asyncio.run(
        mf.map_fields_impl(
            entity,
            unified,
            {"table": entity, "columns": primary_cols},
            None,
            [{"table": t, "columns": c} for t, c in fams.items()],
        )
    )


def _u(**fields):
    return {
        k: {"type": "str", "sample_value": v[0], "sample_values": list(v)}
        for k, v in fields.items()
    }


@pytest.mark.xfail(
    strict=True,
    reason="F3 case/camel variant of a primary column name is hijacked onto the family table",
)
def test_a1_case_variant_of_a_primary_column_name_stays_on_primary():
    """Primary company HAS a text column `industry`; family table `industry` exists.  A source
    field `Industry` (case variant) must still map onto the primary column (Phase 2 gives 1.0)."""
    res = _map(
        _u(Industry=["tech", "bio"]),
        _cols("id", "name", "industry", "_loaded_at"),
        {"industry": _cols("id", "name", "_loaded_at")},
    )
    m = res["field_mapping"].get("Industry")
    assert m and m["dest_table"] == "company" and m["dest_column"] == "industry", m


@pytest.mark.xfail(strict=True, reason="F3 camelCase variant hijacked")
def test_a1b_camel_case_variant_of_primary_column_stays_on_primary():
    res = _map(
        _u(productCategory=["a", "b"]),
        _cols("id", "name", "product_category", "_loaded_at"),
        {"product_category": _cols("id", "name", "_loaded_at")},
    )
    m = res["field_mapping"].get("productCategory")
    assert m and m["dest_table"] == "company", m


def test_a2_exact_primary_column_still_wins():
    res = _map(
        _u(industry=["tech", "bio"]),
        _cols("id", "name", "industry", "_loaded_at"),
        {"industry": _cols("id", "name", "_loaded_at")},
    )
    m = res["field_mapping"]["industry"]
    assert m["dest_table"] == "company"


@pytest.mark.xfail(
    strict=True,
    reason="F4 dimension label = first non-provenance column even when it is an FK/key column",
)
def test_a3_dimension_label_is_not_a_key_or_fk_column():
    """Dimension whose first non-provenance column is a self-FK: the text must not be mapped there
    (the loader would drop it: 'mapped onto the dimension's own FK')."""
    res = _map(
        _u(segment=["alpha", "beta"]),
        _cols("id", "name", "segment_ref", "_loaded_at"),
        {"segment": _cols("id", "parent_ref", "name", "_loaded_at")},
        entity="account",
    )
    m = res["field_mapping"].get("segment")
    assert m is None or m["dest_column"] == "name", m


def test_a3b_dimension_label_skips_a_numeric_first_column():
    res = _map(
        _u(segment=["alpha", "beta"]),
        _cols("id", "name", "segment_ref", "_loaded_at"),
        {"segment": _cols("id", "rank", "name", "_loaded_at", types={"rank": "integer"})},
        entity="account",
    )
    m = res["field_mapping"].get("segment")
    assert m and m["dest_column"] == "name", m


@pytest.mark.xfail(
    strict=True,
    reason="F5 child family table (FK back to entity): text routed onto the FK-back column",
)
def test_a4_child_family_table_is_not_a_dimension_target():
    """`finance` has a FK BACK to the entity (child table).  A field named `finance` must not be
    routed onto `finance.company_id` (the FK back to the parent)."""
    res = _map(
        _u(finance=["10", "20"]),
        _cols("id", "name", "_loaded_at"),
        {"finance": _cols("id", "company_id", "amount", "_loaded_at", types={"amount": "numeric"})},
    )
    m = res["field_mapping"].get("finance")
    assert m is None or m["dest_column"] != "company_id", m


@pytest.mark.xfail(strict=True, reason="F8 numeric ids routed as dimension labels (observation)")
def test_a5_numeric_ids_in_a_field_named_like_the_table_are_not_made_labels():
    """A field `segment` holding integers (ids/codes) is not repeated TEXT; routing it as a
    dimension label manufactures dimension rows named '1','2'.  Report: 'repeated TEXT value'."""
    res = _map(
        _u(segment=[1, 2, 3]),
        _cols("id", "name", "segment_ref", "_loaded_at"),
        {"segment": _cols("id", "name", "_loaded_at")},
        entity="account",
    )
    for f in ("segment",):
        m = res["field_mapping"].get(f)
        assert m is None or m.get("mapped_by") != "dimension", m


def test_a6_single_table_mapping_unchanged():
    res = _map(_u(name=["x"], segment=["y"]), _cols("id", "name", "_loaded_at"), {})
    assert res["field_mapping"]["name"]["dest_table"] == "company"
    assert res["schema_changes"] == []


@pytest.mark.xfail(
    strict=True, reason="F9 two source fields mapped onto the same dimension label column"
)
def test_a7_two_fields_one_dimension_label_collide():
    """Two source fields both named like the same family table (e.g. `segment`, `Segment`) must
    not both be mapped onto the one label column."""
    res = _map(
        _u(segment=["a", "b"], Segment=["c", "d"]),
        _cols("id", "name", "segment_ref", "_loaded_at"),
        {"segment": _cols("id", "name", "_loaded_at")},
        entity="account",
    )
    tgt = [(m["dest_table"], m["dest_column"]) for m in res["field_mapping"].values()]
    assert len(tgt) == len(set(tgt)), tgt


# ───────────────────────── (b) _materialise_dimensions ─────────────────────────
SHAPE = DEST_SHAPES["report_shape"]


def _recs(texts):
    return [{"_cluster_id": f"c{i}", "title": f"N{i}", "dimtext": t} for i, t in enumerate(texts)]


def load(texts, shape=SHAPE, dest=None, mapping_extra=None, dest_cls=FakeDest):
    d = dest or dest_cls(**shape)
    mapping = {
        "title": {"dest_column": "name", "dest_table": shape["main"], "confidence": 1.0},
        "dimtext": {"dest_column": "name", "dest_table": shape["dim"], "confidence": 0.9},
        **(mapping_extra or {}),
    }
    res = asyncio.run(
        tld.load_data_impl(
            shape["main"], _recs(texts), mapping=mapping, connector=d, table_name=shape["main"]
        )
    )
    return d, res


def test_b1_whitespace_and_case_variants_state_row_count():
    d, res = load(["Tech", "tech", " Tech ", "Tech"])
    n = len(d.rows["industry"])
    # The report: "one dimension row per DISTINCT text".  Exact-text distinctness => 3 rows.
    # Recorded, not asserted as defect: whether trimming/case-folding is wanted is unspecified.
    print(
        "DIM ROWS FOR Tech/tech/' Tech '/Tech:",
        n,
        sorted(r["name"] for r in d.rows["industry"].values()),
    )
    assert n in (1, 3)


def test_b2_mixed_null_blank_whitespace_no_row_no_link():
    d, res = load(["a", None, "", "   ", "a", "\t"])
    names_ = sorted(r["name"] for r in d.rows["industry"].values())
    assert names_ == ["a"], names_
    links = [d.rows["company"][f"c{i}"].get("industry_id") for i in range(6)]
    assert links[1] is None and links[2] is None and links[3] is None and links[5] is None
    assert links[0] == links[4] and links[0] is not None


def test_b2b_all_blank_batch_writes_no_dimension_and_succeeds():
    d, res = load([None, "", "  "])
    assert res["success"] and not d.rows["industry"]


def test_b2c_text_stored_untrimmed_or_trimmed_is_consistent_with_link():
    d, _ = load([" a ", " a "])
    assert len(d.rows["industry"]) == 1


class StrictDest(FakeDest):
    """dimension has a NOT NULL non-FK column nobody maps (e.g. `kind`)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cols["industry"].append("kind")
        self.cols0 = {t: list(c) for t, c in self.cols.items()}

    def get_table_schema(self, table):
        s = super().get_table_schema(table)
        for c in s["columns"]:
            if c["name"] == "kind":
                c["nullable"] = False
        return s

    def load_batch(self, table_name, records, mapping=None, upsert_key="id"):
        if table_name == "industry":
            for r in records:
                if r.get("kind") is None:
                    raise RuntimeError("NOT NULL violation: industry.kind")
        return super().load_batch(table_name, records, mapping, upsert_key)


def test_b3_dimension_with_required_non_fk_column_fails_loudly_or_skips_cleanly():
    d = StrictDest(**SHAPE)
    try:
        _, res = load(["a", "b", "c"], dest=d)
    except Exception as exc:  # a raised error is acceptable ONLY if the parent was not written
        assert not d.rows["company"], f"parent rows written although the dimension failed: {exc}"
        return
    # no exception: either failure is reported, or dimension skipped with dropped_fields reported
    child = res.get("child_tables", {}).get("industry", {})
    assert (not res["success"]) or child.get("dropped_fields") or d.rows["industry"], res


def test_b4_parent_fk_column_name_without_table_name():
    shape = dict(SHAPE, link="classification", self_fk="parent_x")
    d, res = load(["a", "b", "a"], shape=shape)
    assert len(d.rows["industry"]) == 2
    assert all(r.get("classification") for r in d.rows["company"].values())


def test_b5_two_fks_from_parent_to_same_dimension_not_silently_lost():
    class TwoFk(FakeDest):
        def get_foreign_keys(self, table):
            if table == self.main:
                return {"industry_id": "industry", "alt_industry_id": "industry"}
            return super().get_foreign_keys(table)

    shape = dict(SHAPE)
    d = TwoFk(**shape)
    d.cols["company"].append("alt_industry_id")
    d, res = load(["a", "b"], dest=d)
    child = res.get("child_tables", {}).get("industry", {})
    assert d.rows["industry"] or child.get("dropped_fields"), (
        "text neither materialised nor reported as dropped"
    )


def test_b6_rerun_idempotent_and_new_values_add_only_new_rows():
    d, _ = load(["a", "b", "c"])
    first = {k: dict(v) for k, v in d.rows["industry"].items()}
    load(["a", "b", "c"], dest=d)
    assert d.rows["industry"].keys() == first.keys()
    load(["a", "b", "c", "d"], dest=d)
    assert len(d.rows["industry"]) == 4
    # earlier ids unchanged
    assert all(k in d.rows["industry"] for k in first)


def test_b7_second_batch_with_same_text_links_to_same_row():
    d, _ = load(["a", "b"])
    d2 = d
    asyncio.run(
        tld.load_data_impl(
            "company",
            [{"_cluster_id": "z1", "title": "Z", "dimtext": "a"}],
            mapping={
                "title": {"dest_column": "name", "dest_table": "company", "confidence": 1.0},
                "dimtext": {"dest_column": "name", "dest_table": "industry", "confidence": 0.9},
            },
            connector=d2,
            table_name="company",
        )
    )
    assert len(d.rows["industry"]) == 2
    assert d.rows["company"]["z1"]["industry_id"] == d.rows["company"]["c0"]["industry_id"]


@pytest.mark.xfail(
    strict=True,
    reason="F10 pre-existing dimension row with same text is duplicated (declared ceiling)",
)
def test_b8_preexisting_dimension_row_with_same_text_is_found_not_duplicated():
    d = FakeDest(**SHAPE)
    d.rows["industry"]["pre-existing-id"] = {"id": "pre-existing-id", "name": "a"}
    load(["a", "b"], dest=d)
    assert sum(1 for r in d.rows["industry"].values() if r.get("name") == "a") == 1, (
        "find-or-create created a duplicate of an existing row (no 'find')"
    )


@pytest.mark.xfail(
    strict=True, reason="F11 existing link value mapped by another field is overwritten"
)
def test_b9_existing_link_value_from_another_mapping_not_clobbered():
    extra = {"ind_ref": {"dest_column": "industry_id", "dest_table": "company", "confidence": 0.9}}
    d = FakeDest(**SHAPE)
    recs = [{"_cluster_id": "c0", "title": "N", "dimtext": "a", "ind_ref": "known-industry-id"}]
    mapping = {
        "title": {"dest_column": "name", "dest_table": "company", "confidence": 1.0},
        "dimtext": {"dest_column": "name", "dest_table": "industry", "confidence": 0.9},
        **extra,
    }
    asyncio.run(
        tld.load_data_impl("company", recs, mapping=mapping, connector=d, table_name="company")
    )
    assert d.rows["company"]["c0"]["industry_id"] == "known-industry-id"


def test_b10_no_ddl_and_no_new_column_across_all_shapes_and_blank_mix():
    for shape in DEST_SHAPES.values():
        d, _ = load(["a", None, "b", "", "a"], shape=shape)
        assert d.cols == d.cols0
        for t, keys in d.written_keys.items():
            assert keys <= set(d.cols[t]), (t, keys - set(d.cols[t]))
        assert not [
            c for c in d.called if re.search(r"alter|add_col|create|ddl|schema_change", c, re.I)
        ]


def test_b11_dimension_that_is_also_a_primary_entity_is_not_double_written():
    d = FakeDest(**SHAPE)
    mapping = {
        "title": {"dest_column": "name", "dest_table": "company", "confidence": 1.0},
        "dimtext": {"dest_column": "name", "dest_table": "industry", "confidence": 0.9},
    }
    res = asyncio.run(
        tld.load_data_impl(
            "company",
            _recs(["a", "b"]),
            mapping=mapping,
            connector=d,
            table_name="company",
            other_primary_tables={"industry"},
        )
    )
    assert not d.rows["industry"]
    assert res["success"]


def test_b12_single_char_text_and_non_string_text():
    d, res = load([0, 1, False, "x"])
    assert res["success"]


# ───────────────────────── (d) 2.4 grep over the diff ─────────────────────────
def _added_lines():
    out = subprocess.run(
        ["git", "diff", "3ce2fdf", "e8c8047", "--", "src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ln[1:] for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def test_d1_no_corpus_literals_in_added_lines_including_comments():
    pat = re.compile(
        r"industry|product_?categor|company_industry|\bstaff\b|testdiffbot|companyexperience|"
        r"companyname|companyid|main_entity_id|company_relation|samstar|pagila",
        re.I,
    )
    hits = [ln for ln in _added_lines() if pat.search(ln)]
    # report: how many (docstrings quote the report's own example); code lines must be none
    code = [
        ln for ln in hits if not ln.strip().startswith(("#", '"', "'")) and "ponytail" not in ln
    ]
    print("CORPUS-LITERAL HITS IN ADDED LINES:", len(hits))
    for h in hits:
        print("   ", h.strip())
    assert not code, code


def test_d2_new_numeric_literals_in_added_lines():
    nums = []
    for ln in _added_lines():
        s = ln.split("#")[0]
        if "section" in s or s.strip().startswith(("across", "ponytail")):
            continue  # docstring prose
        for m in re.finditer(r"(?<![\w.\"'])(\d+\.\d+|\d+)(?![\w\"'])", s):
            nums.append((m.group(1), ln.strip()))
    allowed = {"0", "1", "2"}  # structural (len(found) < 2, defaults), not thresholds
    bad = [n for n in nums if n[0] not in allowed]
    print("NUMERIC LITERALS:", nums)
    assert not bad, bad


# ───────────────────────── (e) HITL: state survives LangGraph ─────────────────────────
def test_e1_table_roles_declared_in_mergestate_and_survives_a_compiled_graph():
    from langgraph.graph import END, START, StateGraph

    from src.orchestrator.graph import MergeState
    from src.orchestrator.nodes import human_nodes as hn

    assert "table_roles" in MergeState.__annotations__

    roles = {"junction": [], "weak": [{"table": "t", "fk_columns": ["x"]}]}

    async def producer(state):
        return {"table_roles": roles}

    seen = {}

    async def consumer(state):
        seen["payload"] = hn._build_deps_interrupt(dict(state))
        return {}

    g = StateGraph(MergeState)
    g.add_node("p", producer)
    g.add_node("c", consumer)
    g.add_edge(START, "p")
    g.add_edge("p", "c")
    g.add_edge("c", END)
    out = asyncio.run(g.compile().ainvoke({"target_entities": []}))
    assert out["table_roles"] == roles
    assert seen["payload"]["data"]["table_roles"] == roles


def test_e2_checkpoint_roundtrip_keeps_table_roles():
    """HITL pauses go through a checkpointer; the roles are plain dict/list/str/bool, so the
    msgpack serializer must return them unchanged."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    roles = {
        "junction": [
            {
                "table": "t",
                "origin": "o1",
                "fk_columns": ["a", "b"],
                "references": ["a -> p.c"],
                "complete": True,
            }
        ],
        "weak": [
            {
                "table": "w",
                "origin": "o1",
                "fk_columns": ["a"],
                "references": ["a -> p.c"],
                "complete": False,
                "discriminator": ["d"],
            }
        ],
    }
    s = JsonPlusSerializer()
    assert s.loads_typed(s.dumps_typed(roles)) == roles


def test_e3_initial_state_does_not_clobber_and_missing_roles_default_empty():
    from src.orchestrator.nodes import human_nodes as hn

    p = hn._build_deps_interrupt({"dependency_tree": {}, "extraction_order": []})
    assert p["data"]["table_roles"] == {}
